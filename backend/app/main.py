"""FastAPI app.

Weekend 1 keeps this minimal: a health check and a read-only audit endpoint so
you can inspect what the agent did. The live SSE/WebSocket stream and the
dashboard arrive in Weekend 3.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.db import get_session

app = FastAPI(title="Company Brain OS — Engineering Agent", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
