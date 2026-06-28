"""Run orchestration.

Creates a run row, executes the agent on a background thread (the agent loop is
synchronous and ~10-30s, so we don't block the HTTP request), and updates the
row when it finishes. The dashboard reads run status + the audit_log timeline
while it's in flight.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any

from sqlalchemy import select

from app.agent.loop import run_agent
from app.audit.models import AuditLog
from app.db import SessionLocal
from app.runs.models import RunRecord


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
    except Exception as exc:  # noqa: BLE001 - record any failure for the UI
        _update(
            run_id,
            status="error",
            final_text=f"{type(exc).__name__}: {exc}",
        )


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
