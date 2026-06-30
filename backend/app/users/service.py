"""User service — create, list, and look up team members.

Used by the watchdog to resolve CODEOWNERS entries to real user rows,
and by the frontend to populate assignee/reporter dropdowns (replacing the
static Alice/Bob list once GitHub OAuth is wired up).
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.users.models import User


def _serialize(u: User) -> dict[str, Any]:
    return {
        "id": str(u.id),
        "github_username": u.github_username,
        "display_name": u.display_name,
        "slack_user_id": u.slack_user_id,
        "created_at": u.created_at.isoformat(),
    }


def list_users() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(select(User).order_by(User.display_name)).all()
        return [_serialize(u) for u in rows]


def get_by_username(github_username: str) -> Optional[dict[str, Any]]:
    with SessionLocal() as session:
        u = session.scalar(
            select(User).where(User.github_username == github_username)
        )
        return _serialize(u) if u else None


def create_user(
    github_username: str,
    display_name: str,
    slack_user_id: Optional[str] = None,
) -> dict[str, Any]:
    github_username = github_username.strip().lstrip("@")
    display_name = display_name.strip() or github_username
    with SessionLocal() as session:
        u = User(
            id=uuid.uuid4(),
            github_username=github_username,
            display_name=display_name,
            slack_user_id=slack_user_id or None,
        )
        session.add(u)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            # Already exists — return the existing row.
            existing = session.scalar(
                select(User).where(User.github_username == github_username)
            )
            if existing:
                return _serialize(existing)
            raise
        return _serialize(u)


def upsert_user(
    github_username: str,
    display_name: str,
    slack_user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create if not exists, update display_name and slack_user_id if it does."""
    github_username = github_username.strip().lstrip("@")
    display_name = display_name.strip() or github_username
    with SessionLocal() as session:
        u = session.scalar(
            select(User).where(User.github_username == github_username)
        )
        if u is None:
            u = User(
                id=uuid.uuid4(),
                github_username=github_username,
                display_name=display_name,
                slack_user_id=slack_user_id or None,
            )
            session.add(u)
        else:
            u.display_name = display_name
            if slack_user_id:
                u.slack_user_id = slack_user_id
        session.commit()
        return _serialize(u)
