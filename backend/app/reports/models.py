"""Report model.

One row per completed ticket: the AI-generated minutes-of-meeting wrap-up,
stored as rendered markdown plus the structured JSON it was built from.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ticket_key: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)  # rendered markdown
    data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
