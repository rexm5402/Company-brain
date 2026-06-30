"""Ticket service.

CRUD + lifecycle transitions for our in-app ticket system, plus key
generation (TKT-1, TKT-2, …). The consensus-to-ship is scoped to the
ticket's assignee + reporter, so creation requires two DISTINCT people.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import func, select

from app.ai import assist
from app.db import SessionLocal
from app.tickets.models import Ticket


class TicketError(ValueError):
    """Raised on invalid ticket operations (bad input, illegal transition)."""


def _serialize(t: Ticket) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "key": t.key,
        "title": t.title,
        "description": t.description,
        "status": t.status,
        "assignee": t.assignee,
        "reporter": t.reporter,
        "source": t.source,
        "details": t.details,
        "channel": t.channel,
        "pr_url": t.pr_url,
        "repo_id": str(t.repo_id) if t.repo_id else None,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


def _next_key(session) -> str:
    """TKT-N where N is one past the current ticket count (sequential, simple)."""
    count = session.scalar(select(func.count()).select_from(Ticket)) or 0
    return f"TKT-{count + 1}"


def create_ticket(
    title: str,
    description: str,
    assignee: str,
    reporter: str,
    source: str = "manual",
    repo_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    title = title.strip()
    assignee = assignee.strip()
    reporter = reporter.strip()
    if not title:
        raise TicketError("title is required")
    if not assignee or not reporter:
        raise TicketError("assignee and reporter are required")
    if assignee.lower() == reporter.lower():
        raise TicketError(
            "assignee and reporter must be two different people "
            "(consensus to ship needs two distinct approvers)"
        )
    # #2 Ticket enrichment: AI expands the bare ticket into a crisp spec.
    # Best-effort — enrich_ticket already degrades gracefully on any failure.
    details = assist.enrich_ticket(title, description).to_markdown() or None

    with SessionLocal() as session:
        ticket = Ticket(
            id=uuid.uuid4(),
            key=_next_key(session),
            title=title,
            description=description.strip(),
            assignee=assignee,
            reporter=reporter,
            source=source,
            status="open",
            details=details,
            repo_id=repo_id,
        )
        session.add(ticket)
        session.commit()
        return _serialize(ticket)


def list_tickets() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Ticket).order_by(Ticket.created_at.desc())
        ).all()
        return [_serialize(t) for t in rows]


def get_ticket(ticket_id: uuid.UUID) -> Optional[dict[str, Any]]:
    with SessionLocal() as session:
        t = session.get(Ticket, ticket_id)
        return _serialize(t) if t else None


def get_by_pr_url(pr_url: str) -> Optional[dict[str, Any]]:
    with SessionLocal() as session:
        t = session.scalar(select(Ticket).where(Ticket.pr_url == pr_url))
        return _serialize(t) if t else None


def get_by_channel(channel: str) -> Optional[dict[str, Any]]:
    with SessionLocal() as session:
        t = session.scalar(select(Ticket).where(Ticket.channel == channel))
        return _serialize(t) if t else None


def open_channel(ticket_id: uuid.UUID) -> dict[str, Any]:
    """Open the discussion channel for a ticket and move it to in_progress."""
    with SessionLocal() as session:
        t = session.get(Ticket, ticket_id)
        if t is None:
            raise TicketError("ticket not found")
        if t.channel is None:
            t.channel = t.key.lower()  # e.g. tkt-1
        if t.status == "open":
            t.status = "in_progress"
        session.commit()
        return _serialize(t)


def set_pr(ticket_id: uuid.UUID, pr_url: str) -> dict[str, Any]:
    """Record the PR and move the ticket to in_review."""
    with SessionLocal() as session:
        t = session.get(Ticket, ticket_id)
        if t is None:
            raise TicketError("ticket not found")
        t.pr_url = pr_url
        t.status = "in_review"
        session.commit()
        return _serialize(t)


def close_ticket(ticket_id: uuid.UUID) -> dict[str, Any]:
    """Mark a ticket done and detach its channel (the channel is torn down)."""
    with SessionLocal() as session:
        t = session.get(Ticket, ticket_id)
        if t is None:
            raise TicketError("ticket not found")
        t.status = "done"
        t.channel = None
        session.commit()
        return _serialize(t)
