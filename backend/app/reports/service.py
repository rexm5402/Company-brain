"""Report service: persist + read the per-ticket wrap-up reports."""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from sqlalchemy import select

from app.db import SessionLocal
from app.reports.models import Report


def _serialize(r: Report) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "ticket_id": str(r.ticket_id),
        "ticket_key": r.ticket_key,
        "title": r.title,
        "content": r.content,
        "data": json.loads(r.data) if r.data else None,
        "created_at": r.created_at.isoformat(),
    }


def save_report(
    ticket_id: uuid.UUID,
    ticket_key: str,
    title: str,
    content: str,
    data: dict[str, Any] | None,
) -> dict[str, Any]:
    with SessionLocal() as session:
        report = Report(
            id=uuid.uuid4(),
            ticket_id=ticket_id,
            ticket_key=ticket_key,
            title=title,
            content=content,
            data=json.dumps(data) if data is not None else None,
        )
        session.add(report)
        session.commit()
        return _serialize(report)


def list_reports() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Report).order_by(Report.created_at.desc())
        ).all()
        return [_serialize(r) for r in rows]


def get_report(report_id: uuid.UUID) -> Optional[dict[str, Any]]:
    with SessionLocal() as session:
        r = session.get(Report, report_id)
        return _serialize(r) if r else None


def get_for_ticket(ticket_id: uuid.UUID) -> Optional[dict[str, Any]]:
    with SessionLocal() as session:
        r = session.scalar(
            select(Report)
            .where(Report.ticket_id == ticket_id)
            .order_by(Report.created_at.desc())
        )
        return _serialize(r) if r else None
