"""Durable chat channels.

Replaces the in-memory dicts that used to hold the local-test chat and the
per-ticket discussion channels. Persisting them in Postgres means messages
survive a restart and the app can run more than one process (the consensus
"claim" becomes a row-level compare-and-swap instead of an in-process lock).

Two tables:
- `channel_messages`: one row per posted message, keyed by `channel`. The
  local-test chat uses the synthetic channel `__local__`; ticket channels use
  the ticket id (str).
- `channel_state`: one row per channel holding the consensus bookkeeping
  (how far we've consumed, how many clarifying questions we've asked). This is
  what makes a re-fired consensus check idempotent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

LOCAL_CHANNEL = "__local__"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChannelMessage(Base):
    __tablename__ = "channel_messages"

    # Autoincrement id doubles as a stable total order for a channel.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(160), index=True)
    author: Mapped[str] = mapped_column(String(160))
    text: Mapped[str] = mapped_column(Text)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    pr_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Epoch seconds. Kept as the consensus cursor (mirrors the previous
    # in-memory `ts`) and used by the frontend to render/ordering-dedupe.
    ts: Mapped[float] = mapped_column(Float, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class ChannelState(Base):
    __tablename__ = "channel_state"

    channel: Mapped[str] = mapped_column(String(160), primary_key=True)
    # Highest human message ts we've already acted on (consumed). A consensus
    # check only fires for messages strictly newer than this.
    consumed_ts: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0"
    )
    # Same idea, but for the "want me to file a ticket?" suggestion (local chat).
    draft_consumed_ts: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0"
    )
    # How many readiness clarifying questions we've asked on this channel.
    questions_asked: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
