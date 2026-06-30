"""Ticket model.

One row per work item in our own (in-app) ticket system. A ticket names an
assignee and a reporter; the consensus to ship is scoped to exactly those two
people (both must agree), which makes "two people agreed" a real authorization
signal instead of anyone-can-self-approve.

Lifecycle: open -> in_progress -> in_review -> done.
- open:        created, no channel yet
- in_progress: a channel was opened and the two are discussing
- in_review:   the agent opened a PR for it
- done:        closed out (manual, for now)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

STATUSES = ("open", "in_progress", "in_review", "done")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(32), unique=True)  # e.g. TKT-1
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")

    status: Mapped[str] = mapped_column(
        String(16), default="open", server_default="open"
    )
    assignee: Mapped[str] = mapped_column(String(120))
    reporter: Mapped[str] = mapped_column(String(120))

    # AI-enriched expansion (summary + acceptance criteria), rendered markdown.
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Where the ticket came from: human via the UI, or the watchdog pipeline.
    source: Mapped[str] = mapped_column(
        String(40), default="manual", server_default="manual"
    )

    # Set once the ticket's discussion channel is opened.
    channel: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # Set once the agent opens a PR for this ticket.
    pr_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Optional link to the repo this ticket is scoped to.
    repo_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        sa.Uuid(as_uuid=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=func.now(),
        onupdate=_utcnow,
    )
