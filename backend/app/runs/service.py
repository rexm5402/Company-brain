"""Run orchestration.

Creates a run row, executes the agent on a background thread (the agent loop is
synchronous and ~10-30s, so we don't block the HTTP request), and updates the
row when it finishes. The dashboard reads run status + the audit_log timeline
while it's in flight.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Optional

from sqlalchemy import select

from app.agent.loop import run_agent
from app.audit.models import AuditLog
from app.audit.recorder import ToolResult, record_tool_call
from app.db import SessionLocal
from app.runs.models import RunRecord
from app.tools.context import RunContext
from app.tools.github_tool import GetPRChecksTool

# CI polling: how long to wait for GitHub Actions to finish before giving up.
_CI_POLL_SECONDS = 8
_CI_MAX_WAIT_SECONDS = 240
# Right after a PR opens, GitHub takes a few seconds to register the workflow
# run, so the first poll legitimately sees zero runs (state "none"). Keep
# waiting for a run to appear during this grace window before concluding that
# the repo has no CI configured.
_CI_NONE_GRACE_SECONDS = 45


def create_run(task: str) -> uuid.UUID:
    run_id = uuid.uuid4()
    with SessionLocal() as session:
        session.add(RunRecord(id=run_id, task=task, status="running"))
        session.commit()
    threading.Thread(
        target=_execute, args=(run_id, task), daemon=True
    ).start()
    return run_id


def run_sync(task: str):
    """Run the agent synchronously, recording a run row the dashboard can see.

    Used by the Slack consensus listener, which needs the result inline (to post
    the PR link back to the channel) while still surfacing the run on the
    dashboard. Returns the AgentRun.
    """
    run_id = uuid.uuid4()
    with SessionLocal() as session:
        session.add(RunRecord(id=run_id, task=task, status="running"))
        session.commit()
    try:
        result = run_agent(task, run_id=run_id)
        _update(
            run_id,
            status="done" if result.pr_url else "error",
            pr_url=result.pr_url,
            final_text=result.final_text,
            steps=result.steps,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - record any failure for the UI
        _update(run_id, status="error", final_text=f"{type(exc).__name__}: {exc}")
        raise


def _execute(run_id: uuid.UUID, task: str) -> None:
    try:
        result = run_agent(task, run_id=run_id)
        _update(
            run_id,
            status="done" if result.pr_url else "error",
            pr_url=result.pr_url,
            final_text=result.final_text,
            steps=result.steps,
        )
        if result.pr_url:  # surface CI status on the dashboard report
            watch_ci(run_id, result.pr_url, after_step=result.steps)
    except Exception as exc:  # noqa: BLE001 - record any failure for the UI
        _update(
            run_id,
            status="error",
            final_text=f"{type(exc).__name__}: {exc}",
        )


# --- CI watcher --------------------------------------------------------
def _pr_number_from_url(pr_url: str) -> Optional[int]:
    try:
        return int(pr_url.rstrip("/").rsplit("/", 1)[-1])
    except (ValueError, AttributeError):
        return None


def watch_ci(
    run_id: uuid.UUID,
    pr_url: str,
    *,
    after_step: int = 0,
    on_result: Optional[Callable[[dict[str, Any]], None]] = None,
) -> None:
    """Poll a PR's GitHub Actions checks in the background.

    Records a single `ci_check` audit step (so the dashboard report/terminal
    show pass/fail) once checks settle, and optionally calls `on_result` with
    the summary (used to post the verdict back into a chat/Slack channel).
    Non-blocking: spawns a daemon thread.
    """
    pr_number = _pr_number_from_url(pr_url)
    if pr_number is None:
        return
    threading.Thread(
        target=_watch_ci,
        args=(run_id, pr_number, after_step + 1, on_result),
        daemon=True,
    ).start()


def _watch_ci(
    run_id: uuid.UUID,
    pr_number: int,
    step: int,
    on_result: Optional[Callable[[dict[str, Any]], None]],
) -> None:
    tool = GetPRChecksTool(RunContext())
    started = time.monotonic()
    deadline = started + _CI_MAX_WAIT_SECONDS
    summary: dict[str, Any] = {"pr_number": pr_number, "state": "none"}
    fail_streak = 0

    while time.monotonic() < deadline:
        result = tool.run(pr_number=pr_number)
        if result.success and result.output:
            fail_streak = 0
            summary = result.output
            state = summary.get("state")
            # "pending" -> still running. "none" right after open -> the run
            # likely hasn't registered yet; keep waiting through the grace
            # window. Any other state (success/failure) is terminal.
            waiting = state == "pending" or (
                state == "none"
                and time.monotonic() - started < _CI_NONE_GRACE_SECONDS
            )
            if not waiting:
                break
        else:
            # A 403 (missing Checks permission) is permanent — don't spin for
            # the full timeout. Give a few tries in case the PR head is briefly
            # not ready, then report that CI couldn't be read.
            fail_streak += 1
            if fail_streak >= 3:
                summary = {
                    "pr_number": pr_number,
                    "state": "unknown",
                    "error": result.error,
                }
                break
        time.sleep(_CI_POLL_SECONDS)

    # Record one audit step so the report/terminal reflect the final verdict.
    final_state = summary.get("state")
    record_tool_call(
        run_id=run_id,
        step=step,
        tool_name="ci_check",
        tool_input={"pr_number": pr_number},
        fn=lambda: ToolResult(
            success=final_state not in ("failure", "unknown"), output=summary
        ),
    )
    if on_result is not None:
        try:
            on_result(summary)
        except Exception:  # noqa: BLE001 - notification must never crash the watcher
            pass


def _update(run_id: uuid.UUID, **fields: Any) -> None:
    with SessionLocal() as session:
        row = session.get(RunRecord, run_id)
        if row is None:
            return
        for key, value in fields.items():
            setattr(row, key, value)
        session.commit()


def get_run(run_id: uuid.UUID) -> dict[str, Any] | None:
    with SessionLocal() as session:
        row = session.get(RunRecord, run_id)
        if row is None:
            return None
        steps = session.scalars(
            select(AuditLog)
            .where(AuditLog.run_id == run_id)
            .order_by(AuditLog.created_at)
        ).all()
        return {
            "run_id": str(row.id),
            "task": row.task,
            "status": row.status,
            "pr_url": row.pr_url,
            "final_text": row.final_text,
            "steps": [
                {
                    "step": s.step,
                    "tool": s.tool_name,
                    "status": s.status,
                    "success": s.success,
                    "latency_ms": s.latency_ms,
                    "error": s.error,
                    "output": s.output_json,
                }
                for s in steps
            ],
            "created_at": row.created_at.isoformat(),
        }


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(RunRecord).order_by(RunRecord.created_at.desc()).limit(limit)
        ).all()
        return [
            {
                "run_id": str(r.id),
                "task": r.task,
                "status": r.status,
                "pr_url": r.pr_url,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
