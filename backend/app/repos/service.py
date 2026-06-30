"""Repo service — CRUD for tracked GitHub repositories."""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.repos.models import Repo


def _serialize(r: Repo) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "name": r.name,
        "owner": r.owner,
        "slug": r.slug,
        "has_token_override": r.github_token_override is not None,
        "created_at": r.created_at.isoformat(),
    }


def create_repo(
    name: str,
    owner: str,
    slug: str,
    github_token_override: Optional[str] = None,
) -> dict[str, Any]:
    with SessionLocal() as session:
        repo = Repo(
            id=uuid.uuid4(),
            name=name,
            owner=owner,
            slug=slug,
            github_token_override=github_token_override or None,
        )
        session.add(repo)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise ValueError(f"A repo with slug '{slug}' already exists")
        return _serialize(repo)


def list_repos() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(select(Repo).order_by(Repo.created_at.asc())).all()
        return [_serialize(r) for r in rows]


def get_repo(repo_id: uuid.UUID) -> Optional[dict[str, Any]]:
    with SessionLocal() as session:
        r = session.get(Repo, repo_id)
        return _serialize(r) if r else None


def get_primary_repo() -> Optional[dict[str, Any]]:
    """Return the first repo by created_at asc, or None if no repos exist."""
    with SessionLocal() as session:
        r = session.scalar(select(Repo).order_by(Repo.created_at.asc()))
        return _serialize(r) if r else None


def get_repo_by_slug(slug: str) -> Optional[dict[str, Any]]:
    """Return a repo by its slug, or None if not found."""
    with SessionLocal() as session:
        r = session.scalar(select(Repo).where(Repo.slug == slug))
        return _serialize(r) if r else None


def get_repo_token(slug: str) -> Optional[str]:
    """Return the github_token_override for a repo slug, or None."""
    with SessionLocal() as session:
        r = session.scalar(select(Repo).where(Repo.slug == slug))
        return r.github_token_override if r else None
