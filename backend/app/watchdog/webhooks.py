"""Webhook endpoints for inbound production signals.

Real endpoints (signature-verified):
  POST /webhooks/sentry      — Sentry issue-alert webhook
  POST /webhooks/github      — GitHub workflow_run / check_suite webhook

Simulation endpoints (no secret required — for demo / local testing):
  POST /webhooks/simulate/sentry     — fire a fake Sentry error
  POST /webhooks/simulate/github_ci  — fire a fake CI failure

GitHub webhook setup:
  Repo → Settings → Webhooks → Add webhook
    Payload URL: https://<your-host>/webhooks/github
    Content type: application/json
    Secret: value of GITHUB_WEBHOOK_SECRET in your .env
    Events: check_suite, workflow_run  (or "Let me select" → tick both)

Sentry webhook setup:
  sentry.io → Settings → Developer Settings → Internal Integration
    Webhook URL: https://<your-host>/webhooks/sentry
    Tick "Issue" under "Webhooks" tab
    Copy the Client Secret → SENTRY_WEBHOOK_SECRET in your .env
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import get_settings
from app.watchdog import service as watchdog_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ---------------------------------------------------------------------------
# Signature verification helpers
# ---------------------------------------------------------------------------

def _verify_github_signature(body: bytes, signature_header: str) -> bool:
    """Verify X-Hub-Signature-256: sha256=<hex>."""
    secret = get_settings().github_webhook_secret
    if not secret:
        logger.warning("GITHUB_WEBHOOK_SECRET not set — skipping signature check")
        return True  # open (dev mode); tighten in prod
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _verify_sentry_signature(body: bytes, signature_header: str) -> bool:
    """Verify sentry-hook-signature: HMAC-SHA256 of the body with client secret."""
    secret = get_settings().sentry_webhook_secret
    if not secret:
        logger.warning("SENTRY_WEBHOOK_SECRET not set — skipping signature check")
        return True
    if not signature_header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# Real webhook endpoints
# ---------------------------------------------------------------------------

@router.post("/sentry")
async def sentry_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_sentry_hook_signature: str = Header(default=""),
) -> dict[str, str]:
    """Receive Sentry issue-alert webhooks."""
    body = await request.body()
    if not _verify_sentry_signature(body, x_sentry_hook_signature):
        raise HTTPException(status_code=401, detail="invalid Sentry webhook signature")

    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON")

    # Process in background so Sentry's 10-second timeout is never hit
    background_tasks.add_task(_run_sentry, payload)
    return {"status": "accepted"}


@router.post("/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
) -> dict[str, str]:
    """Receive GitHub workflow_run / check_suite webhooks."""
    body = await request.body()
    if not _verify_github_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid GitHub webhook signature")

    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON")

    event_type = x_github_event  # "workflow_run" | "check_suite" | "ping" | ...
    if event_type == "ping":
        return {"status": "pong"}

    if event_type == "pull_request_review":
        background_tasks.add_task(_run_pr_review, payload)
        return {"status": "accepted"}

    if event_type == "pull_request_review_comment":
        background_tasks.add_task(_run_pr_review_comment, payload)
        return {"status": "accepted"}

    if event_type not in ("workflow_run", "check_suite"):
        return {"status": "ignored", "event": event_type}

    background_tasks.add_task(_run_github_ci, payload, event_type)
    return {"status": "accepted"}


# ---------------------------------------------------------------------------
# Simulation endpoints (no auth — local dev / demo only)
# ---------------------------------------------------------------------------

class SimulateSentryRequest(BaseModel):
    title: str = "TypeError: Cannot read property 'id' of undefined"
    culprit: str = "app/auth.py in get_user"
    level: str = "error"
    issue_id: str = "sim-001"
    permalink: str = ""
    count: str = "42"


class SimulateGitHubCIRequest(BaseModel):
    workflow_name: str = "CI"
    conclusion: str = "failure"
    branch: str = ""          # defaults to github_base_branch
    commit_sha: str = "abc1234"
    commit_message: str = "fix: update auth middleware"
    run_url: str = ""
    run_id: str = "sim-ci-001"


@router.post("/simulate/sentry")
def simulate_sentry(
    req: SimulateSentryRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Fire a simulated Sentry error alert (no signature needed)."""
    payload: dict[str, Any] = {
        "data": {
            "issue": {
                "id": req.issue_id,
                "title": req.title,
                "culprit": req.culprit,
                "level": req.level,
                "permalink": req.permalink,
                "count": req.count,
                "lastSeen": "2024-01-01T00:00:00Z",
                "metadata": {
                    "type": req.title.split(":")[0] if ":" in req.title else "",
                    "value": req.title.split(":", 1)[1].strip() if ":" in req.title else req.title,
                },
            }
        }
    }
    background_tasks.add_task(_run_sentry, payload)
    return {"status": "simulated", "issue_id": req.issue_id}


@router.post("/simulate/github_ci")
def simulate_github_ci(
    req: SimulateGitHubCIRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Fire a simulated GitHub CI failure (no signature needed)."""
    branch = req.branch or get_settings().github_base_branch or "main"
    payload: dict[str, Any] = {
        "action": "completed",
        "workflow_run": {
            "id": req.run_id,
            "name": req.workflow_name,
            "conclusion": req.conclusion,
            "head_branch": branch,
            "head_sha": req.commit_sha,
            "html_url": req.run_url,
            "head_commit": {"message": req.commit_message},
        },
    }
    background_tasks.add_task(_run_github_ci, payload, "workflow_run")
    return {"status": "simulated", "run_id": req.run_id}


# ---------------------------------------------------------------------------
# Background task wrappers (keep errors out of the request/response cycle)
# ---------------------------------------------------------------------------

def _run_sentry(payload: dict[str, Any]) -> None:
    try:
        ticket = watchdog_service.process_sentry_event(payload)
        if ticket:
            logger.info("Watchdog Sentry → ticket %s", ticket["key"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Watchdog Sentry processing error: %s", exc)


def _run_github_ci(payload: dict[str, Any], event_type: str) -> None:
    try:
        ticket = watchdog_service.process_github_ci_event(payload, event_type)
        if ticket:
            logger.info("Watchdog GitHub CI → ticket %s", ticket["key"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Watchdog GitHub CI processing error: %s", exc)


def _run_pr_review(payload: dict[str, Any]) -> None:
    try:
        result = watchdog_service.process_pr_review_event(payload)
        if result:
            logger.info("Watchdog PR review → ticket %s", result.get("key"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Watchdog PR review processing error: %s", exc)


def _run_pr_review_comment(payload: dict[str, Any]) -> None:
    """Append a review comment as a bot message; no agent trigger."""
    try:
        pr_url = (payload.get("pull_request") or {}).get("html_url", "")
        comment_body = (payload.get("comment") or {}).get("body", "")
        commenter = ((payload.get("comment") or {}).get("user") or {}).get("login", "")
        if not (pr_url and comment_body):
            return
        from app.tickets import service as tickets_service
        from app.chat import service as chat_service
        ticket = tickets_service.get_by_pr_url(pr_url)
        if ticket is None:
            return
        chat_service.append_message(
            ticket["id"],
            "Brain OS Watchdog",
            f"💬 PR comment from **{commenter}**: {comment_body}",
            is_bot=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Watchdog PR review comment processing error: %s", exc)
