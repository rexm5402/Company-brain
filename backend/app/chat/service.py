"""Durable chat + consensus-claim service.

All channel reads/writes go through here. The important bit is `claim()`: it's
an atomic compare-and-swap on `channel_state.consumed_ts` (under a row lock), so
two concurrent consensus checks for the same channel can't both "win" and
double-fire a PR. The slow LLM detection happens OUTSIDE this claim — we only
take the lock for the tiny CAS transaction.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.chat.models import ChannelMessage, ChannelState
from app.db import SessionLocal


def _serialize(m: ChannelMessage) -> dict[str, Any]:
    return {
        "user": m.author,
        "text": m.text,
        "ts": m.ts,
        "is_bot": m.is_bot,
        "pr_url": m.pr_url,
    }


def append_message(
    channel: str,
    author: str,
    text: str,
    *,
    is_bot: bool,
    pr_url: str | None = None,
) -> dict[str, Any]:
    with SessionLocal() as s:
        msg = ChannelMessage(
            channel=channel,
            author=author,
            text=text,
            is_bot=is_bot,
            pr_url=pr_url,
            ts=time.time(),
        )
        s.add(msg)
        s.commit()
        s.refresh(msg)
        return _serialize(msg)


def list_messages(channel: str) -> list[dict[str, Any]]:
    with SessionLocal() as s:
        rows = s.scalars(
            select(ChannelMessage)
            .where(ChannelMessage.channel == channel)
            .order_by(ChannelMessage.id)
        ).all()
        return [_serialize(m) for m in rows]


def humans(channel: str) -> list[dict[str, Any]]:
    """Non-bot messages, oldest-first."""
    return [m for m in list_messages(channel) if not m["is_bot"]]


def get_state(channel: str) -> dict[str, Any]:
    """Read-only snapshot of a channel's consensus bookkeeping."""
    with SessionLocal() as s:
        st = s.get(ChannelState, channel)
        if st is None:
            return {"consumed_ts": 0.0, "draft_consumed_ts": 0.0, "questions_asked": 0}
        return {
            "consumed_ts": st.consumed_ts,
            "draft_consumed_ts": st.draft_consumed_ts,
            "questions_asked": st.questions_asked,
        }


def _ensure_state(s: Any, channel: str) -> ChannelState:
    """Return the (row-locked) state row for `channel`, creating it if absent.

    The create races with other workers; the PK on `channel` resolves it — the
    loser retries the locked select and finds the winner's row.
    """
    for _ in range(2):
        st = s.execute(
            select(ChannelState)
            .where(ChannelState.channel == channel)
            .with_for_update()
        ).scalar_one_or_none()
        if st is not None:
            return st
        s.add(ChannelState(channel=channel))
        try:
            s.flush()
        except IntegrityError:
            s.rollback()
            continue
    # Final locked read after a concurrent insert won the race.
    return s.execute(
        select(ChannelState).where(ChannelState.channel == channel).with_for_update()
    ).scalar_one()


def claim(channel: str, up_to_ts: float, *, increment_question: bool = False) -> bool:
    """Atomically advance `consumed_ts` to `up_to_ts` if it's still behind.

    Returns True iff THIS call advanced the cursor — i.e. won the right to act
    on the agreement. A concurrent (or retried) check sees the cursor already at
    or past `up_to_ts` and returns False, so the PR fires exactly once. When
    `increment_question` is set, also bumps `questions_asked` in the same txn.
    """
    with SessionLocal() as s:
        st = _ensure_state(s, channel)
        if st.consumed_ts >= up_to_ts:
            return False
        st.consumed_ts = up_to_ts
        if increment_question:
            st.questions_asked = (st.questions_asked or 0) + 1
        s.commit()
        return True


def claim_draft(channel: str, up_to_ts: float) -> bool:
    """CAS for the 'want me to file a ticket?' suggestion (independent cursor)."""
    with SessionLocal() as s:
        st = _ensure_state(s, channel)
        if st.draft_consumed_ts >= up_to_ts:
            return False
        st.draft_consumed_ts = up_to_ts
        s.commit()
        return True


def clear_channel(channel: str) -> None:
    """Tear a channel down: drop its messages and its consensus state."""
    with SessionLocal() as s:
        s.execute(delete(ChannelMessage).where(ChannelMessage.channel == channel))
        s.execute(delete(ChannelState).where(ChannelState.channel == channel))
        s.commit()
