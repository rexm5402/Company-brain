"""Audit recorder (two-phase).

A `pending` row is written BEFORE the tool executes, then updated with the
result after it returns. This means:
  * the row is committed before the result reaches the agent loop, and
  * if the process dies mid-tool-call, the row is left at status='pending',
    so we always know a tool was attempted (not silently lost).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from app.audit.models import AuditLog
from app.db import SessionLocal


@dataclass
class ToolResult:
    success: bool
    output: dict[str, Any] | None = None
    error: str | None = None


def record_tool_call(
    *,
    run_id: uuid.UUID,
    step: int,
    tool_name: str,
    tool_input: dict[str, Any],
    fn: Callable[[], ToolResult],
) -> ToolResult:
    audit_id = _begin(run_id=run_id, step=step, tool_name=tool_name, tool_input=tool_input)

    start = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:  # tool raised -> still record the failure
        result = ToolResult(success=False, error=f"{type(exc).__name__}: {exc}")
    latency_ms = int((time.perf_counter() - start) * 1000)

    _finish(audit_id=audit_id, result=result, latency_ms=latency_ms)
    return result


def _begin(
    *, run_id: uuid.UUID, step: int, tool_name: str, tool_input: dict[str, Any]
) -> uuid.UUID:
    session = SessionLocal()
    try:
        row = AuditLog(
            run_id=run_id,
            step=step,
            tool_name=tool_name,
            input_json=tool_input,
            status="pending",
            success=False,
        )
        session.add(row)
        session.commit()
        return row.id
    finally:
        session.close()


def _finish(*, audit_id: uuid.UUID, result: ToolResult, latency_ms: int) -> None:
    session = SessionLocal()
    try:
        row = session.get(AuditLog, audit_id)
        if row is None:  # pragma: no cover - row was just created
            return
        row.output_json = result.output
        row.latency_ms = latency_ms
        row.success = result.success
        row.error = result.error
        row.status = "success" if result.success else "error"
        session.commit()
    finally:
        session.close()
