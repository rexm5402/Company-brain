"""FastAPI app.

Serves the dashboard and the run API. The agent runs on a background thread;
the dashboard polls run status + the audit_log timeline. The audit table stays
the source of truth for "what did the agent do".
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.llm import LLMClient
from app.audit.models import AuditLog
from app.config import get_settings
from app.db import get_session
from app.runs import service as runs_service
from app.slack.client import SlackClient, SlackError
from app.slack.detector import detect_consensus

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
_chat_lock = threading.Lock()
_chat_messages: list[dict] = []
_chat_consumed_ts: dict[str, float] = {"ts": 0.0}
_consensus_lock = threading.Lock()  # serialize consensus checks


class ChatSend(BaseModel):
    user: str
    text: str


def _chat_append(user: str, text: str, *, is_bot: bool, pr_url: str | None = None) -> None:
    with _chat_lock:
        _chat_messages.append(
            {
                "user": user,
                "text": text,
                "ts": time.time(),
                "is_bot": is_bot,
                "pr_url": pr_url,
            }
        )


@app.get("/chat/messages")
def chat_messages() -> dict:
    with _chat_lock:
        return {"messages": list(_chat_messages)}


@app.post("/chat/reset")
def chat_reset() -> dict[str, str]:
    with _chat_lock:
        _chat_messages.clear()
        _chat_consumed_ts["ts"] = 0.0
    return {"status": "cleared"}


@app.post("/chat/send")
def chat_send(req: ChatSend) -> dict[str, bool]:
    user = req.user.strip() or "Anon"
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    _chat_append(user, text, is_bot=False)
    threading.Thread(target=_chat_consensus_check, daemon=True).start()
    return {"ok": True}


def _chat_consensus_check() -> None:
    """If two distinct people just agreed, distill a task and run the agent."""
    with _consensus_lock:  # one consensus run at a time
        with _chat_lock:
            humans = [m for m in _chat_messages if not m["is_bot"]]
            distinct = {m["user"] for m in humans}
            last_ts = humans[-1]["ts"] if humans else 0.0
            already = _chat_consumed_ts["ts"]
        if len(distinct) < 2 or last_ts <= already:
            return

        transcript = "\n".join(f"{m['user']}: {m['text']}" for m in humans[-25:])
        try:
            consensus = detect_consensus(transcript, LLMClient())
        except Exception:  # noqa: BLE001 - detection failure shouldn't crash chat
            return
        if not (consensus.ready and consensus.task):
            return

        with _chat_lock:
            _chat_consumed_ts["ts"] = last_ts  # consume this agreement

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
    else:
        _chat_append(
            "Brain OS Agent",
            f"⚠️ {run.final_text or 'no PR produced'}",
            is_bot=True,
        )


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
