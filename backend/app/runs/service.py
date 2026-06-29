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
from app.tools.github_tool import (
    GetPRChecksTool,
    get_failing_ci_logs,
    get_pr_branch,
)

# CI polling: how long to wait for GitHub Actions to finish before giving up.
_CI_POLL_SECONDS = 8
_CI_MAX_WAIT_SECONDS = 240
# Right after a PR opens, GitHub takes a few seconds to register the workflow
# run, so the first poll legitimately sees zero runs (state "none"). Keep
# waiting for a run to appear during this grace window before concluding that
# the repo has no CI configured.
_CI_NONE_GRACE_SECONDS = 45
# Iterate-on-red: when CI fails, the agent reads the logs and pushes a fix to
# the same PR branch, then we re-watch. Bound the attempts so a persistently
# broken build can't loop forever.
_MAX_FIX_ATTEMPTS = 2


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
    on_progress: Optional[Callable[[str], None]] = None,
    fix_attempt: int = 0,
) -> None:
    """Poll a PR's GitHub Actions checks in the background.

    Records a single `ci_check` audit step (so the dashboard report/terminal
    show pass/fail) once checks settle. When CI fails, runs the iterate-on-red
    fix loop (read logs -> push a fix to the PR branch -> re-watch), bounded by
    `_MAX_FIX_ATTEMPTS`. Calls `on_result` with the final summary once CI is
    green or the fix attempts are exhausted; `on_progress(text)` is called with
    human-readable status updates during the fix loop. Non-blocking: spawns a
    daemon thread.
    """
    pr_number = _pr_number_from_url(pr_url)
    if pr_number is None:
        return
    threading.Thread(
        target=_watch_ci,
        args=(run_id, pr_number, after_step + 1, on_result, on_progress, fix_attempt),
        daemon=True,
    ).start()


def _watch_ci(
    run_id: uuid.UUID,
    pr_number: int,
    step: int,
    on_result: Optional[Callable[[dict[str, Any]], None]],
    on_progress: Optional[Callable[[str], None]] = None,
    fix_attempt: int = 0,
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

    # Iterate-on-red: a failing build is the one state we can act on. If we have
    # attempts left, read the logs, let the agent push a fix to the same branch,
    # and re-enter the watcher. The fix run continues on the next audit step.
    if final_state == "failure" and fix_attempt < _MAX_FIX_ATTEMPTS:
        if _attempt_fix(run_id, pr_number, step + 1, fix_attempt, on_progress):
            _watch_ci(
                run_id,
                pr_number,
                step + 2,
                on_result,
                on_progress,
                fix_attempt + 1,
            )
            return

    if on_result is not None:
        try:
            on_result(summary)
        except Exception:  # noqa: BLE001 - notification must never crash the watcher
            pass


def _attempt_fix(
    run_id: uuid.UUID,
    pr_number: int,
    step: int,
    fix_attempt: int,
    on_progress: Optional[Callable[[str], None]],
) -> bool:
    """Read the failing CI logs and let the agent push a fix to the PR branch.

    Returns True if a fix was committed (so the caller should re-watch CI),
    False if we couldn't act (no branch/logs, or the fix run didn't commit).
    """
    attempt_no = fix_attempt + 1
    if on_progress is not None:
        try:
            on_progress(
                f"🔧 CI failed — attempting an automatic fix "
                f"(attempt {attempt_no}/{_MAX_FIX_ATTEMPTS})…"
            )
        except Exception:  # noqa: BLE001 - progress posting must never crash the loop
            pass

    branch = get_pr_branch(pr_number)
    logs = get_failing_ci_logs(pr_number)
    if not branch or not logs:
        if on_progress is not None:
            try:
                on_progress(
                    "⚠️ Couldn't read the CI logs or PR branch — skipping the "
                    "automatic fix. A human should take a look."
                )
            except Exception:  # noqa: BLE001
                pass
        return False

    fix_task = (
        f"The CI build for PR #{pr_number} (branch `{branch}`) is failing. "
        f"Read the failing build logs below, diagnose the root cause, and "
        f"commit a minimal fix to branch `{branch}` using commit_to_branch.\n\n"
        f"--- FAILING CI LOGS ---\n{logs}"
    )
    try:
        result = run_agent(fix_task, run_id=run_id, fix_mode=True)
    except Exception as exc:  # noqa: BLE001 - a failed fix run must not crash the watcher
        if on_progress is not None:
            try:
                on_progress(f"⚠️ Automatic fix run errored: {type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001
                pass
        return False

    if not result.committed_branch:
        if on_progress is not None:
            try:
                on_progress(
                    "⚠️ The agent couldn't produce a fix for this failure. "
                    "A human should take a look."
                )
            except Exception:  # noqa: BLE001
                pass
        return False

    if on_progress is not None:
        try:
            on_progress(
                f"✅ Pushed a fix to `{branch}` — re-running CI to check if it's green."
            )
        except Exception:  # noqa: BLE001
            pass
    return True


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
