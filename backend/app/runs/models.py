"""Run record model.

One row per agent run, holding the run-level summary the dashboard needs:
the task, current status, the PR it produced, and the final message. Per-tool
detail still lives in `audit_log` (joined by run id).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task: Mapped[str] = mapped_column(Text)

    # 'running' -> 'done' | 'error'
    status: Mapped[str] = mapped_column(
        String(16), default="running", server_default="running"
    )
    pr_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    final_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    steps: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Token accounting (populated at run end from LLMClient usage)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=func.now(),
        onupdate=_utcnow,
    )
