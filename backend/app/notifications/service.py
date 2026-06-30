from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import desc, select

from app.db import SessionLocal
from app.notifications.models import Notification


def _serialize(n: Notification) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "recipient": n.recipient,
        "type": n.type,
        "title": n.title,
        "body": n.body,
        "ticket_id": str(n.ticket_id) if n.ticket_id else None,
        "ticket_key": n.ticket_key,
        "pr_url": n.pr_url,
        "read": n.read,
        "created_at": n.created_at.isoformat(),
    }


def create(
    recipient: str,
    type: str,
    title: str,
    body: Optional[str] = None,
    ticket_id: Optional[uuid.UUID] = None,
    ticket_key: Optional[str] = None,
    pr_url: Optional[str] = None,
) -> dict[str, Any]:
    with SessionLocal() as session:
        n = Notification(
            id=uuid.uuid4(),
            recipient=recipient,
            type=type,
            title=title,
            body=body,
            ticket_id=ticket_id,
            ticket_key=ticket_key,
            pr_url=pr_url,
            read=False,
        )
        session.add(n)
        session.commit()
        return _serialize(n)


def list_for_user(recipient: str, unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        q = select(Notification).where(Notification.recipient == recipient)
        if unread_only:
            q = q.where(Notification.read.is_(False))
        q = q.order_by(desc(Notification.created_at)).limit(limit)
        return [_serialize(n) for n in session.scalars(q).all()]


def unread_count(recipient: str) -> int:
    with SessionLocal() as session:
        from sqlalchemy import func
        return session.scalar(
            select(func.count()).select_from(Notification)
            .where(Notification.recipient == recipient, Notification.read.is_(False))
        ) or 0


def mark_read(notification_id: uuid.UUID) -> bool:
    with SessionLocal() as session:
        n = session.get(Notification, notification_id)
        if n is None:
            return False
        n.read = True
        session.commit()
        return True


def mark_all_read(recipient: str) -> int:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Notification).where(
                Notification.recipient == recipient,
                Notification.read.is_(False),
            )
        ).all()
        for n in rows:
            n.read = True
        session.commit()
        return len(rows)
