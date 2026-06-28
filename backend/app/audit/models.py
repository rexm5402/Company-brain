"""Audit log model.

This table is the source of truth for "what did the agent do". The dashboard
timeline (Weekend 3) reads from here, NOT from agent output text.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Correlates all tool calls within one agent run.
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    step: Mapped[int] = mapped_column(Integer)

    tool_name: Mapped[str] = mapped_column(String(128), index=True)
    input_json: Mapped[dict] = mapped_column(JSONB)
    output_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Lifecycle: 'pending' (written before the tool runs) -> 'success' | 'error'.
    # A row left at 'pending' means the process died mid-tool-call.
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
