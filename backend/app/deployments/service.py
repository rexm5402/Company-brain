"""Deployment service — trigger and track ephemeral staging environments."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.deployments.models import Deployment

logger = logging.getLogger(__name__)


def _serialize(d: Deployment) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "ticket_id": str(d.ticket_id),
        "pr_url": d.pr_url,
        "deploy_url": d.deploy_url,
        "branch": d.branch,
        "repo": d.repo,
        "status": d.status,
        "created_at": d.created_at.isoformat(),
    }


def trigger_deploy(
    ticket_id: uuid.UUID,
    pr_url: str,
    branch: str,
    repo: str,
) -> Optional[dict[str, Any]]:
    """POST to the deploy webhook and record the deployment row.

    Returns the serialized Deployment, or None if no webhook is configured.
    """
    s = get_settings()
    if not s.deploy_webhook_url:
        return None

    deploy_url: Optional[str] = None
    status = "pending"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                s.deploy_webhook_url,
                json={
                    "ticket_id": str(ticket_id),
                    "pr_url": pr_url,
                    "branch": branch,
                    "repo": repo,
                },
            )
            resp.raise_for_status()
            body = resp.json() if resp.content else {}
            deploy_url = body.get("deploy_url") or body.get("url")
            status = "live" if deploy_url else "pending"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Deploy webhook failed: %s", exc)
        status = "failed"

    with SessionLocal() as session:
        dep = Deployment(
            id=uuid.uuid4(),
            ticket_id=ticket_id,
            pr_url=pr_url,
            deploy_url=deploy_url,
            branch=branch or None,
            repo=repo or None,
            status=status,
        )
        session.add(dep)
        session.commit()
        return _serialize(dep)


def list_for_ticket(ticket_id: uuid.UUID) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Deployment)
            .where(Deployment.ticket_id == ticket_id)
            .order_by(Deployment.created_at.desc())
        ).all()
        return [_serialize(d) for d in rows]
