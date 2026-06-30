"""Watchdog service — processes inbound production signals and raises tickets.

Two signal sources today:
  • Sentry   — issue alert webhook (level=error|fatal)
  • GitHub CI — workflow_run or check_suite webhook (conclusion=failure,
                branch = base branch)

Flow for both:
  1. Dedupe: if we already have a webhook_event row for this external_id, skip.
  2. Find the culprit file → look up CODEOWNERS → resolve assignee.
  3. Create ticket (source=sentry|github_ci, reporter=watchdog).
  4. Auto-open the discussion channel.
  5. Post initial context message from the watchdog into the channel.
  6. Notify via Slack (optional; degrades if Slack not configured).
  7. Persist the webhook_event row with the new ticket_id.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.chat import service as chat_service
from app.config import get_settings
from app.db import SessionLocal
from app.tickets import service as tickets_service
from app.watchdog.codeowners import resolve_owner
from app.watchdog.models import WebhookEvent

logger = logging.getLogger(__name__)

# Sentry levels that warrant automatic ticket creation.
_SENTRY_ALERT_LEVELS = {"fatal", "error"}

# GitHub CI conclusions that warrant a ticket.
_CI_FAILURE_CONCLUSIONS = {"failure", "timed_out", "startup_failure"}


# ---------------------------------------------------------------------------
# Sentry
# ---------------------------------------------------------------------------

def process_sentry_event(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Handle a Sentry issue-alert webhook payload.

    Returns the created ticket dict, or None if skipped (duplicate / below
    severity threshold).
    """
    issue = payload.get("data", {}).get("issue") or payload.get("issue") or {}
    level = (issue.get("level") or "").lower()
    if level not in _SENTRY_ALERT_LEVELS:
        logger.info("Watchdog: skipping Sentry event level=%s", level)
        return None

    external_id = str(issue.get("id") or payload.get("id") or "")
    title = issue.get("title") or "Unknown Sentry error"
    culprit = issue.get("culprit") or ""
    permalink = issue.get("permalink") or ""

    # Dedupe — one ticket per Sentry issue
    if external_id and _already_processed(external_id):
        logger.info("Watchdog: duplicate Sentry event %s — skipping", external_id)
        return None

    # Extract the exact commit SHA that was live when the error fired (Time Machine)
    commit_sha = _extract_commit_sha(issue, payload)

    # Best-effort stack trace excerpt for the ticket description
    description = _build_sentry_description(issue, culprit, permalink, commit_sha)

    # Resolve owner from the culprit file path
    culprit_file = culprit.split(" in ")[0].strip() if " in " in culprit else culprit
    assignee = resolve_owner(culprit_file) or get_settings().watchdog_default_assignee
    if not assignee:
        logger.warning(
            "Watchdog: no owner for %s and no WATCHDOG_DEFAULT_ASSIGNEE set — "
            "ticket not created",
            culprit_file,
        )
        _record_event("sentry", "issue_alert", external_id, payload, None, "no assignee resolved")
        return None

    ticket = _create_and_open(
        title=f"[Sentry] {title}",
        description=description,
        assignee=assignee,
        source="sentry",
        external_id=external_id,
        payload=payload,
        channel_intro=(
            f"🚨 **Watchdog detected a production error**\n\n"
            f"**Error:** {title}\n"
            f"**Culprit:** `{culprit}`\n"
            f"**Level:** {level}\n"
            + (f"**Sentry link:** {permalink}\n" if permalink else "")
            + f"\nThis ticket was auto-created. "
            f"Discuss the fix above, then both agree to let the agent open a PR."
        ),
    )
    _notify_slack(ticket, f"🚨 Watchdog auto-created [{ticket['key']}] from Sentry: *{title}*\nAssigned to *{assignee}*.")
    return ticket


def _extract_commit_sha(issue: dict, payload: dict) -> Optional[str]:
    """Best-effort: pull the commit SHA that was live when the error fired.

    Sentry embeds it in several places depending on SDK + release config:
      issue.tags            — list of {key, value} dicts; look for 'commit'
      issue.releaseVersion  — sometimes a git SHA directly
      payload.release       — top-level release string
    """
    # 1. Look in tags (most reliable when sentry-sdk is configured with releases)
    for tag in (issue.get("tags") or []):
        if isinstance(tag, dict) and tag.get("key") in ("commit", "git_sha", "revision"):
            val = tag.get("value", "")
            if val and len(val) >= 7:
                return val[:40]  # normalize to full or short SHA

    # 2. releaseVersion — Sentry often uses the commit SHA as the release name
    release = (
        issue.get("releaseVersion")
        or issue.get("release")
        or payload.get("release")
        or ""
    )
    # A valid git SHA is 7-40 hex chars; version strings look like "1.2.3" or "v1.2"
    import re
    if re.fullmatch(r"[0-9a-f]{7,40}", release, re.IGNORECASE):
        return release

    return None


def _build_sentry_description(issue: dict, culprit: str, permalink: str, commit_sha: Optional[str] = None) -> str:
    lines = []
    if culprit:
        lines.append(f"**Culprit:** `{culprit}`")
    if permalink:
        lines.append(f"**Sentry:** {permalink}")
    # Embed the commit SHA with a sentinel marker so the agent can parse it
    if commit_sha:
        lines.append(f"**Commit at time of error:** `{commit_sha}`")
        lines.append(
            f"*Time Machine: call `get_file_contents` with `ref=\"{commit_sha}\"` "
            f"to read the exact code that was live when this crash happened.*"
        )
    # Try to pull the first exception value for extra context
    metadata = issue.get("metadata") or {}
    exc_type = metadata.get("type") or ""
    exc_value = metadata.get("value") or ""
    if exc_type or exc_value:
        lines.append(f"\n**Exception:** {exc_type}: {exc_value}")
    count = issue.get("count")
    if count:
        lines.append(f"**Occurrences:** {count}")
    last_seen = issue.get("lastSeen") or ""
    if last_seen:
        lines.append(f"**Last seen:** {last_seen}")
    lines.append("\n*Auto-created by the watchdog. Review the Sentry link for the full stack trace.*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GitHub CI
# ---------------------------------------------------------------------------

def process_github_ci_event(payload: dict[str, Any], event_type: str) -> Optional[dict[str, Any]]:
    """Handle a GitHub workflow_run or check_suite webhook payload.

    Only creates tickets for failures on the base branch (main/master).
    PR-level failures are handled by the iterate-on-red loop.
    """
    s = get_settings()

    if event_type == "workflow_run":
        return _process_workflow_run(payload, s)
    if event_type == "check_suite":
        return _process_check_suite(payload, s)
    return None


def _process_workflow_run(payload: dict[str, Any], s: Any) -> Optional[dict[str, Any]]:
    action = payload.get("action", "")
    if action != "completed":
        return None

    run = payload.get("workflow_run") or {}
    conclusion = (run.get("conclusion") or "").lower()
    if conclusion not in _CI_FAILURE_CONCLUSIONS:
        return None

    branch = run.get("head_branch") or ""
    if branch != s.github_base_branch:
        logger.info("Watchdog: CI failure on branch %s (not base) — skipping", branch)
        return None

    external_id = f"workflow_run_{run.get('id', '')}"
    if _already_processed(external_id):
        return None

    workflow_name = run.get("name") or "CI"
    commit_sha = run.get("head_sha") or ""
    run_url = run.get("html_url") or ""
    commit_msg = (run.get("head_commit") or {}).get("message", "").splitlines()[0]

    title = f"[CI Failure] {workflow_name} failed on {branch}"
    description = _build_ci_description(workflow_name, branch, commit_sha, commit_msg, run_url, conclusion)

    # For CI failures: use CODEOWNERS on the repo root as a proxy, or default
    assignee = resolve_owner("") or s.watchdog_default_assignee
    if not assignee:
        logger.warning("Watchdog: no assignee for CI failure — skipping")
        _record_event("github_ci", event_type, external_id, payload, None, "no assignee resolved")
        return None

    ticket = _create_and_open(
        title=title,
        description=description,
        assignee=assignee,
        source="github_ci",
        external_id=external_id,
        payload=payload,
        channel_intro=(
            f"🔴 **CI failure on `{branch}`**\n\n"
            f"**Workflow:** {workflow_name}\n"
            f"**Conclusion:** {conclusion}\n"
            + (f"**Commit:** `{commit_sha[:8]}` — {commit_msg}\n" if commit_sha else "")
            + (f"**Run:** {run_url}\n" if run_url else "")
            + f"\nDiscuss the fix, then both agree to let the agent open a PR."
        ),
    )
    _notify_slack(ticket, f"🔴 Watchdog auto-created [{ticket['key']}]: *{title}*\nAssigned to *{assignee}*.")
    return ticket


def _process_check_suite(payload: dict[str, Any], s: Any) -> Optional[dict[str, Any]]:
    action = payload.get("action", "")
    if action != "completed":
        return None

    suite = payload.get("check_suite") or {}
    conclusion = (suite.get("conclusion") or "").lower()
    if conclusion not in _CI_FAILURE_CONCLUSIONS:
        return None

    # Skip check suites triggered by PRs — iterate-on-red handles those
    pull_requests = suite.get("pull_requests") or []
    if pull_requests:
        return None

    branch = suite.get("head_branch") or ""
    if branch != s.github_base_branch:
        return None

    external_id = f"check_suite_{suite.get('id', '')}"
    if _already_processed(external_id):
        return None

    commit_sha = suite.get("head_sha") or ""
    app_name = (suite.get("app") or {}).get("name") or "CI"
    suite_url = (
        f"https://github.com/{s.github_repo}/commit/{commit_sha}/checks"
        if commit_sha
        else ""
    )

    title = f"[CI Failure] {app_name} check suite failed on {branch}"
    description = _build_ci_description(app_name, branch, commit_sha, "", suite_url, conclusion)

    assignee = resolve_owner("") or s.watchdog_default_assignee
    if not assignee:
        _record_event("github_ci", "check_suite", external_id, payload, None, "no assignee resolved")
        return None

    ticket = _create_and_open(
        title=title,
        description=description,
        assignee=assignee,
        source="github_ci",
        external_id=external_id,
        payload=payload,
        channel_intro=(
            f"🔴 **Check suite failed on `{branch}`**\n\n"
            f"**App:** {app_name}  |  **Conclusion:** {conclusion}\n"
            + (f"**Commit:** `{commit_sha[:8]}`\n" if commit_sha else "")
            + (f"**Checks:** {suite_url}\n" if suite_url else "")
            + f"\nDiscuss the fix, then both agree to let the agent open a PR."
        ),
    )
    _notify_slack(ticket, f"🔴 Watchdog auto-created [{ticket['key']}]: *{title}*\nAssigned to *{assignee}*.")
    return ticket


def _build_ci_description(
    workflow: str, branch: str, sha: str, msg: str, url: str, conclusion: str
) -> str:
    lines = [
        f"**Workflow:** {workflow}",
        f"**Branch:** `{branch}`",
        f"**Conclusion:** {conclusion}",
    ]
    if sha:
        lines.append(f"**Commit:** `{sha[:8]}`" + (f" — {msg}" if msg else ""))
    if url:
        lines.append(f"**Run:** {url}")
    lines.append("\n*Auto-created by the watchdog.*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def process_pr_review_event(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Handle a GitHub pull_request_review webhook payload.

    Only triggers agent fix runs for state=changes_requested.
    Returns the ticket dict if a fix was triggered, else None.
    """
    review = payload.get("review") or {}
    state = (review.get("state") or "").lower()
    if state != "changes_requested":
        logger.info("Watchdog: ignoring PR review state=%s", state)
        return None

    pr_url = (payload.get("pull_request") or {}).get("html_url", "")
    review_body = review.get("body") or ""
    reviewer = ((review.get("user") or {}).get("login") or "reviewer")

    if not pr_url:
        return None

    ticket = tickets_service.get_by_pr_url(pr_url)
    if ticket is None:
        logger.info("Watchdog: no ticket found for PR %s", pr_url)
        return None

    # Post review body as bot message in the channel
    message = (
        f"🔁 **{reviewer}** requested changes on the PR:\n\n"
        + (review_body or "(no review body provided)")
    )
    chat_service.append_message(ticket["id"], "Brain OS Watchdog", message, is_bot=True)

    # Trigger a fix run
    fix_task = (
        f"A reviewer ({reviewer}) requested changes on PR {pr_url}.\n"
        f"Review comments:\n{review_body}\n\n"
        f"Ticket: {ticket['title']}\n{ticket.get('description', '')}\n\n"
        "Read the PR files, address the reviewer's feedback, and commit a fix."
    )
    try:
        from app.runs import service as runs_service
        run = runs_service.run_sync(
            fix_task,
            ticket_id=__import__("uuid").UUID(ticket["id"]),
        )
        if run.pr_url or run.committed_branch:
            chat_service.append_message(
                ticket["id"],
                "Brain OS Watchdog",
                "✅ Applied fix based on PR review feedback.",
                is_bot=True,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("PR review fix run failed: %s", exc)

    return ticket


def _already_processed(external_id: str) -> bool:
    """True if we already have a webhook_event row for this external_id."""
    with SessionLocal() as session:
        row = session.scalar(
            select(WebhookEvent).where(WebhookEvent.external_id == external_id)
        )
        return row is not None


def _record_event(
    source: str,
    event_type: str,
    external_id: str,
    payload: dict,
    ticket_id: Optional[uuid.UUID],
    error: Optional[str] = None,
) -> None:
    with SessionLocal() as session:
        ev = WebhookEvent(
            id=uuid.uuid4(),
            source=source,
            event_type=event_type,
            external_id=external_id or None,
            payload_json=json.dumps(payload),
            ticket_id=ticket_id,
            processed_at=datetime.now(timezone.utc),
            error=error,
        )
        session.add(ev)
        session.commit()


def _create_and_open(
    *,
    title: str,
    description: str,
    assignee: str,
    source: str,
    external_id: str,
    payload: dict,
    channel_intro: str,
) -> dict:
    """Create the ticket, open its channel, post the intro message, record event."""
    s = get_settings()
    reporter = s.watchdog_reporter or "watchdog"

    # Guard: assignee and reporter must differ
    if assignee.lower() == reporter.lower():
        reporter = "brain-os-watchdog"

    ticket = tickets_service.create_ticket(
        title=title,
        description=description,
        assignee=assignee,
        reporter=reporter,
        source=source,
    )
    ticket_id = uuid.UUID(ticket["id"])

    # Auto-open the channel immediately (no need for human to click "Start")
    tickets_service.open_channel(ticket_id)

    # Post context into the channel so the assignee sees it when they open the ticket
    chat_service.append_message(
        ticket["id"],
        "Brain OS Watchdog",
        channel_intro,
        is_bot=True,
    )

    _record_event(source, source, external_id, payload, ticket_id)
    logger.info(
        "Watchdog: created ticket %s (%s) assigned to %s",
        ticket["key"], source, assignee,
    )

    # In-app notification to the assignee
    try:
        from app.notifications import service as notif_service
        notif_service.create(
            recipient=assignee,
            type="watchdog",
            title=f"Watchdog assigned you {ticket['key']}: {title[:120]}",
            body=f"Source: {source}. Open the ticket to discuss and ship a fix.",
            ticket_id=ticket_id,
            ticket_key=ticket["key"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to create watchdog notification: %s", exc)

    return ticket


def _notify_slack(ticket: dict, message: str) -> None:
    """Post to the watchdog Slack channel. Best-effort — never raises."""
    channel = get_settings().watchdog_slack_channel
    if not channel:
        return
    try:
        from app.slack.client import SlackClient, SlackError
        slack = SlackClient()
        cid = slack.resolve_channel(channel)
        slack.post(cid, message)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Watchdog Slack notify failed: %s", exc)
