"""FastAPI app.

Serves the dashboard and the run API. The agent runs on a background thread;
the dashboard polls run status + the audit_log timeline. The audit table stays
the source of truth for "what did the agent do".
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.llm import LLMClient
from app.audit.models import AuditLog
from app.chat import service as chat_service
from app.chat.models import LOCAL_CHANNEL
from app.config import get_settings
from app.db import get_session
from app.observability import init_observability
from app.runs import service as runs_service
from app.slack.client import SlackClient, SlackError
from app.slack.detector import detect_consensus
from app.ai import assist
from app.reports import service as reports_service
from app.tickets import service as tickets_service
from app.tickets.service import TicketError
from app.tools.github_tool import get_pr_file_paths, get_pr_state

init_observability()

app = FastAPI(title="Company Brain OS — Engineering Agent", version="0.1.0")

# Active channel the dashboard/listener is pointed at. Defaults to the
# configured watch channel; switchable at runtime via POST /slack/channel.
_active_channel: dict[str, str] = {"channel": get_settings().slack_watch_channel}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev only
    allow_methods=["*"],
    allow_headers=["*"],
)

_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

# Consensus checks run off the request thread, but we cap concurrency instead of
# spawning an unbounded daemon thread per message — a burst of chatter can't
# exhaust the process. Correctness across these workers (and across processes)
# comes from the row-level claim in chat_service, not from a thread count.
_consensus_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="consensus")


class RunRequest(BaseModel):
    task: str


class ChannelRequest(BaseModel):
    channel: str


class SendRequest(BaseModel):
    text: str


def _resolve_active_channel_id(slack: SlackClient) -> str:
    channel = _active_channel["channel"]
    if not channel:
        raise HTTPException(status_code=400, detail="no active channel selected")
    try:
        return slack.resolve_channel(channel)
    except SlackError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/slack/channels")
def slack_channels() -> dict:
    try:
        channels = SlackClient().list_channels()
    except SlackError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"active": _active_channel["channel"], "channels": channels}


@app.post("/slack/channel")
def set_slack_channel(req: ChannelRequest) -> dict[str, str]:
    channel = req.channel.strip()
    if not channel:
        raise HTTPException(status_code=400, detail="channel is required")
    _active_channel["channel"] = channel
    return {"channel": channel}


@app.get("/slack/messages")
def slack_messages(limit: int = 50) -> dict:
    """Live mirror of the active channel: normalized, oldest-first messages."""
    slack = SlackClient()
    channel_id = _resolve_active_channel_id(slack)
    me = slack.whoami()
    bot_user_id = me.get("user_id", "")
    try:
        raw = slack.history(channel_id, limit=limit)
    except SlackError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    out: list[dict] = []
    for m in raw:
        if m.get("subtype") and m.get("subtype") != "bot_message":
            continue  # joins/leaves/edits — skip channel noise
        text = m.get("text") or ""
        if not text:
            continue
        user_id = m.get("user", "")
        is_bot = bool(m.get("bot_id")) or user_id == bot_user_id
        name = "Brain OS Agent" if is_bot else slack.user_name(user_id)
        out.append(
            {
                "ts": m.get("ts"),
                "user_id": user_id,
                "name": name,
                "text": text,
                "is_bot": is_bot,
            }
        )
    return {"channel": _active_channel["channel"], "messages": out}


@app.post("/slack/send")
def slack_send(req: SendRequest) -> dict:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    slack = SlackClient()
    channel_id = _resolve_active_channel_id(slack)
    try:
        body = slack.post(channel_id, text)
    except SlackError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ts": body.get("ts")}


# --- Local test channel -------------------------------------------------
# An in-app multi-user chat for testing the consensus flow without two real
# Slack accounts. Open two browser windows (?as=User 1 / ?as=User 2), chat, and
# when two distinct people agree the agent fires — same pipeline as Slack.
# State is persisted in Postgres (chat_service), so it survives restarts.


class ChatSend(BaseModel):
    user: str
    text: str


def _chat_append(user: str, text: str, *, is_bot: bool, pr_url: str | None = None) -> None:
    chat_service.append_message(
        LOCAL_CHANNEL, user, text, is_bot=is_bot, pr_url=pr_url
    )


@app.get("/chat/messages")
def chat_messages() -> dict:
    return {"messages": chat_service.list_messages(LOCAL_CHANNEL)}


@app.post("/chat/reset")
def chat_reset() -> dict[str, str]:
    chat_service.clear_channel(LOCAL_CHANNEL)
    return {"status": "cleared"}


@app.post("/chat/send")
def chat_send(req: ChatSend) -> dict[str, bool]:
    user = req.user.strip() or "Anon"
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    _chat_append(user, text, is_bot=False)
    _consensus_pool.submit(_chat_consensus_check)
    return {"ok": True}


def _maybe_suggest_ticket(transcript: str, last_ts: float) -> None:
    """#3 Propose a ticket when the chat surfaces work, at most once per round."""
    draft = assist.draft_ticket(transcript)
    if not (draft.should_file and draft.title):
        return
    # CAS so two concurrent checks don't both post the same suggestion.
    if not chat_service.claim_draft(LOCAL_CHANNEL, last_ts):
        return
    desc = f"\n_{draft.description}_" if draft.description else ""
    _chat_append(
        "Brain OS Agent",
        f"🗂️ Sounds like work worth tracking. Want me to file a ticket?\n"
        f"*{draft.title}*{desc}\n(Create it from the Work Hub.)",
        is_bot=True,
    )


def _chat_consensus_check() -> None:
    """If two distinct people just agreed, distill a task and run the agent."""
    humans = chat_service.humans(LOCAL_CHANNEL)
    distinct = {m["user"] for m in humans}
    last_ts = humans[-1]["ts"] if humans else 0.0
    already = chat_service.get_state(LOCAL_CHANNEL)["consumed_ts"]
    if len(distinct) < 2 or last_ts <= already:
        return

    transcript = "\n".join(f"{m['user']}: {m['text']}" for m in humans[-25:])
    try:
        consensus = detect_consensus(transcript, LLMClient())
    except Exception:  # noqa: BLE001 - detection failure shouldn't crash chat
        return
    if not (consensus.ready and consensus.task):
        # #3 No agreement yet — but has the discussion surfaced concrete
        # work? If so, propose filing a ticket (once per new discussion).
        _maybe_suggest_ticket(transcript, last_ts)
        return

    # Atomically claim this agreement. If we don't win the claim, another worker
    # already fired for this message — bail so the PR opens exactly once.
    if not chat_service.claim(LOCAL_CHANNEL, last_ts):
        return

    who = " and ".join(consensus.agreers) if consensus.agreers else "the team"
    _chat_append(
        "Brain OS Agent",
        f"🤖 Consensus detected ({who} agreed). Implementing now:\n{consensus.task}",
        is_bot=True,
    )
    try:
        run = runs_service.run_sync(consensus.task)
    except Exception as exc:  # noqa: BLE001 - surface failure in the chat
        _chat_append("Brain OS Agent", f"⚠️ {type(exc).__name__}: {exc}", is_bot=True)
        return
    if run.pr_url:
        _chat_append(
            "Brain OS Agent",
            "✅ Done — opened a PR with the change, reviewed and verified.",
            is_bot=True,
            pr_url=run.pr_url,
        )
        _chat_append("Brain OS Agent", "⏳ Running CI checks…", is_bot=True)
        runs_service.watch_ci(
            run.run_id,
            run.pr_url,
            after_step=run.steps,
            on_result=lambda summary: _chat_append(
                "Brain OS Agent", _ci_message(summary), is_bot=True
            ),
            on_progress=lambda text: _chat_append(
                "Brain OS Agent", text, is_bot=True
            ),
        )
    else:
        _chat_append(
            "Brain OS Agent",
            f"⚠️ {run.final_text or 'no PR produced'}",
            is_bot=True,
        )


def _ci_message(summary: dict) -> str:
    state = summary.get("state")
    if state == "success":
        return f"✅ CI passed — all {summary.get('total', 0)} check(s) green."
    if state == "failure":
        failed = [
            c["name"]
            for c in summary.get("checks", [])
            if c.get("conclusion") not in ("success", "neutral", "skipped", None)
        ]
        detail = ", ".join(failed) or "see the PR checks"
        return f"❌ CI failed — {detail}. The change needs a fix before merge."
    if state == "pending":
        return "⏳ CI still running — check the PR for the latest status."
    if state == "unknown":
        return "⚠️ Couldn't read CI status (the GitHub token lacks the Checks permission)."
    return "ℹ️ No CI configured on this repo yet, so the change wasn't verified by tests."


# --- Tickets ------------------------------------------------------------
# Our own in-app ticket system. A ticket names an assignee + reporter; the
# consensus to ship is scoped to exactly those two people (both must agree),
# which makes "two people agreed" a real authorization signal. Each ticket
# gets its own discussion channel (in-app for now; real Slack later).
# Ticket discussion channels are persisted in Postgres (chat_service), keyed by
# the ticket id, with the consensus cursor + questions-asked count alongside.

# Readiness gate: ask at most this many clarifying questions, then just build
# with what's known so the agent can't loop forever asking.
_MAX_READINESS_QUESTIONS = 2
# If anyone in the channel says one of these, skip the gate and build now.
_SKIP_QUESTION_PHRASES = (
    "no further question",
    "no more question",
    "no questions",
    "no other question",
    "no other thing",
    "stop asking",
    "just build",
    "go ahead and build",
    "build based on",
    "build with what",
)


def _wants_to_skip_questions(humans: list[dict]) -> bool:
    """True if a human explicitly asked the agent to stop asking and build."""
    for m in humans:
        text = (m.get("text") or "").lower()
        if any(phrase in text for phrase in _SKIP_QUESTION_PHRASES):
            return True
    return False


class TicketCreate(BaseModel):
    title: str
    description: str = ""
    assignee: str
    reporter: str


class TicketChatSend(BaseModel):
    user: str
    text: str


def _ticket_or_404(ticket_id: str) -> dict:
    try:
        rid = uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="ticket not found")
    ticket = tickets_service.get_ticket(rid)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return ticket


@app.post("/tickets")
def create_ticket(req: TicketCreate) -> dict:
    try:
        return tickets_service.create_ticket(
            req.title, req.description, req.assignee, req.reporter
        )
    except TicketError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/tickets")
def list_tickets() -> list[dict]:
    return tickets_service.list_tickets()


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str) -> dict:
    return _ticket_or_404(ticket_id)


@app.post("/tickets/{ticket_id}/start")
def start_ticket(ticket_id: str) -> dict:
    _ticket_or_404(ticket_id)
    try:
        return tickets_service.open_channel(uuid.UUID(ticket_id))
    except TicketError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _ticket_chat_append(
    ticket_id: str, user: str, text: str, *, is_bot: bool, pr_url: str | None = None
) -> None:
    chat_service.append_message(ticket_id, user, text, is_bot=is_bot, pr_url=pr_url)


@app.get("/tickets/{ticket_id}/messages")
def ticket_messages(ticket_id: str) -> dict:
    _ticket_or_404(ticket_id)
    return {"messages": chat_service.list_messages(ticket_id)}


@app.post("/tickets/{ticket_id}/send")
def ticket_send(ticket_id: str, req: TicketChatSend) -> dict[str, bool]:
    ticket = _ticket_or_404(ticket_id)
    user = req.user.strip()
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    # Only the assignee or reporter may speak in a ticket channel — this is
    # what scopes the consensus to the two authorized people.
    members = {ticket["assignee"].lower(), ticket["reporter"].lower()}
    if user.lower() not in members:
        raise HTTPException(
            status_code=403,
            detail=(
                f"only the assignee ({ticket['assignee']}) or reporter "
                f"({ticket['reporter']}) can post in this ticket"
            ),
        )
    _ticket_chat_append(ticket_id, user, text, is_bot=False)
    _consensus_pool.submit(_ticket_consensus_check, ticket_id)
    return {"ok": True}


def _ticket_consensus_check(ticket_id: str) -> None:
    """Fire the agent when BOTH the assignee and reporter have agreed."""
    ticket = tickets_service.get_ticket(uuid.UUID(ticket_id))
    if ticket is None:
        return
    members = {ticket["assignee"], ticket["reporter"]}
    humans = chat_service.humans(ticket_id)
    last_ts = humans[-1]["ts"] if humans else 0.0
    state = chat_service.get_state(ticket_id)
    already = state["consumed_ts"]
    # Both named people must have posted, and there must be something new.
    posted = {m["user"] for m in humans}
    both_present = all(
        any(p.lower() == m.lower() for p in posted) for m in members
    )
    if not both_present or last_ts <= already:
        return

    transcript = "\n".join(f"{m['user']}: {m['text']}" for m in humans[-25:])
    try:
        consensus = detect_consensus(transcript, LLMClient())
    except Exception:  # noqa: BLE001 - detection failure shouldn't crash chat
        return
    if not (consensus.ready and consensus.task):
        return

    # #4 Readiness gate: is the agreed task concrete enough to build? If
    # not, ask ONE clarifying question and DON'T consume the agreement, so
    # the team can answer and re-trigger. But cap it: at most
    # _MAX_READINESS_QUESTIONS, and skip entirely if anyone said "no further
    # questions" — then build with what's in the ticket/chat. This stops the
    # agent looping on clarifications forever.
    asked = state["questions_asked"]
    skip_gate = _wants_to_skip_questions(humans) or asked >= _MAX_READINESS_QUESTIONS
    if not skip_gate:
        readiness = assist.judge_readiness(consensus.task, transcript)
        if not readiness.ready:
            # Claim (advance cursor + count the question) so we don't re-ask on
            # every poll. If another worker already claimed, stay quiet.
            if chat_service.claim(ticket_id, last_ts, increment_question=True):
                _ticket_chat_append(
                    ticket_id,
                    "Brain OS Agent",
                    f"🤔 Before I build this, one question: {readiness.question}",
                    is_bot=True,
                )
            return

    # Atomically claim this agreement. Losing the claim means another worker is
    # already building for this message — bail so the PR opens exactly once.
    if not chat_service.claim(ticket_id, last_ts):
        return

    # Build from the ticket description as the source of truth, with the agreed
    # chat task as extra context. This keeps the agent grounded in what the
    # ticket actually asked for rather than drifting on chat clarifications.
    desc = (ticket.get("description") or "").strip()
    build_task = consensus.task
    if desc:
        build_task = (
            f"Ticket: {ticket['title']}\n{desc}\n\n"
            f"Agreed change to implement:\n{consensus.task}"
        )

    _ticket_chat_append(
        ticket_id,
        "Brain OS Agent",
        f"🤖 Consensus reached — {ticket['assignee']} and {ticket['reporter']} "
        f"both agreed. Implementing now:\n{consensus.task}",
        is_bot=True,
    )
    try:
        run = runs_service.run_sync(build_task)
    except Exception as exc:  # noqa: BLE001 - surface failure in the chat
        _ticket_chat_append(
            ticket_id, "Brain OS Agent", f"⚠️ {type(exc).__name__}: {exc}", is_bot=True
        )
        return
    if run.pr_url:
        try:
            tickets_service.set_pr(uuid.UUID(ticket_id), run.pr_url)
        except TicketError:
            pass
        _ticket_chat_append(
            ticket_id,
            "Brain OS Agent",
            "✅ Done — opened a PR with the change. Ticket moved to In Review.",
            is_bot=True,
            pr_url=run.pr_url,
        )

        # #5 summary + #6 risk triage, from the PR's actual changed files.
        pr_number = runs_service._pr_number_from_url(run.pr_url)
        paths = get_pr_file_paths(pr_number) if pr_number else []
        summary = assist.summarize_change(consensus.task, paths)
        if summary:
            _ticket_chat_append(
                ticket_id, "Brain OS Agent", f"📝 What changed: {summary}", is_bot=True
            )
        risk = assist.assess_risk(consensus.task, paths)
        _ticket_chat_append(
            ticket_id, "Brain OS Agent", _risk_message(risk), is_bot=True
        )

        _ticket_chat_append(ticket_id, "Brain OS Agent", "⏳ Running CI checks…", is_bot=True)
        runs_service.watch_ci(
            run.run_id,
            run.pr_url,
            after_step=run.steps,
            on_result=lambda summary: _ticket_chat_append(
                ticket_id, "Brain OS Agent", _ci_message(summary), is_bot=True
            ),
            on_progress=lambda text: _ticket_chat_append(
                ticket_id, "Brain OS Agent", text, is_bot=True
            ),
        )
    else:
        _ticket_chat_append(
            ticket_id,
            "Brain OS Agent",
            f"⚠️ {run.final_text or 'no PR produced'}",
            is_bot=True,
        )


# --- #7 Completion report + channel teardown ---------------------------
def _render_report_markdown(ticket: dict, report: assist.Report) -> str:
    lines = [f"# {ticket['key']} — {ticket['title']}", ""]
    if report.summary:
        lines += [report.summary, ""]
    lines += [
        f"**Assignee:** {ticket['assignee']}  ·  **Reporter:** {ticket['reporter']}",
        f"**PR:** {ticket.get('pr_url') or '(none)'}",
        "",
    ]
    if report.key_contributor:
        lines += [f"**Key contributor:** {report.key_contributor}", ""]
    if report.participants:
        lines += [f"**Participants:** {', '.join(report.participants)}", ""]
    if report.decisions:
        lines += ["## Decisions", *[f"- {d}" for d in report.decisions], ""]
    if report.action_items:
        lines += ["## Action items", *[f"- {a}" for a in report.action_items], ""]
    return "\n".join(lines).strip()


@app.post("/tickets/{ticket_id}/complete")
def complete_ticket(ticket_id: str, require_merge: bool = False) -> dict:
    """Generate the wrap-up report, store it, tear down the channel, close ticket.

    If require_merge=true, refuses unless the PR is actually merged on GitHub.
    """
    ticket = _ticket_or_404(ticket_id)
    rid = uuid.UUID(ticket_id)

    if require_merge:
        pr_number = (
            runs_service._pr_number_from_url(ticket["pr_url"])
            if ticket.get("pr_url")
            else None
        )
        if pr_number is None or not get_pr_state(pr_number).get("merged"):
            raise HTTPException(
                status_code=409, detail="PR is not merged yet — cannot close."
            )

    # Build the transcript from the channel's persisted messages.
    msgs = chat_service.list_messages(ticket_id)
    transcript = "\n".join(
        f"{m['user']}: {m['text']}" for m in msgs if m.get("text")
    )

    report = assist.generate_report(ticket, transcript)
    content = _render_report_markdown(ticket, report)
    saved = reports_service.save_report(
        ticket_id=rid,
        ticket_key=ticket["key"],
        title=ticket["title"],
        content=content,
        data=report.raw,
    )

    # Tear down the channel: wipe its messages + consensus state, close ticket.
    chat_service.clear_channel(ticket_id)
    tickets_service.close_ticket(rid)
    return saved


@app.get("/reports")
def list_reports() -> list[dict]:
    return reports_service.list_reports()


@app.get("/reports/{report_id}")
def get_report(report_id: str) -> dict:
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="report not found")
    report = reports_service.get_report(rid)
    if report is None:
        raise HTTPException(status_code=404, detail="report not found")
    return report


def _risk_message(risk: assist.Risk) -> str:
    icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(risk.level, "🟢")
    base = f"{icon} Risk: {risk.level}"
    if risk.areas:
        base += f" (touches {', '.join(risk.areas)})"
    if risk.note:
        base += f" — {risk.note}"
    if risk.needs_human:
        base += "\n⚠️ Recommend a human review before merge."
    return base


@app.post("/runs")
def create_run(req: RunRequest) -> dict[str, str]:
    if not req.task.strip():
        raise HTTPException(status_code=400, detail="task is required")
    run_id = runs_service.create_run(req.task.strip())
    return {"run_id": str(run_id)}


@app.get("/runs")
def list_runs() -> list[dict]:
    return runs_service.list_runs()


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="run not found")
    run = runs_service.get_run(rid)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@app.get("/audit/{run_id}")
def audit_for_run(run_id: str, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(
        select(AuditLog).where(AuditLog.run_id == run_id).order_by(AuditLog.step)
    ).all()
    return [
        {
            "step": r.step,
            "tool": r.tool_name,
            "success": r.success,
            "latency_ms": r.latency_ms,
            "error": r.error,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


# Serve the dashboard last so explicit API routes above take precedence.
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
