"""Repo doc indexer — fetches, chunks, embeds and upserts repo documentation."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import get_settings
from app.db import SessionLocal
from app.memory.embeddings import embed
from app.memory.models import RepoDoc

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 3200
_CHUNK_OVERLAP = 200


def _fetch_file(client: httpx.Client, repo: str, path: str, token: str) -> Optional[str]:
    """Fetch raw file content from GitHub. Returns None if not found."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.raw+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        r = client.get(
            f"https://api.github.com/repos/{repo}/contents/{path}",
            headers=headers,
            timeout=15.0,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        # Raw content endpoint returns text directly when Accept is raw
        return r.text
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch %s: %s", path, exc)
        return None


def _fetch_docs_dir(client: httpx.Client, repo: str, token: str) -> list[tuple[str, str]]:
    """List and fetch all *.md files under docs/."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    results: list[tuple[str, str]] = []
    try:
        r = client.get(
            f"https://api.github.com/repos/{repo}/contents/docs",
            headers=headers,
            timeout=15.0,
        )
        if r.status_code != 200:
            return results
        for item in r.json():
            if item.get("type") == "file" and item.get("name", "").endswith(".md"):
                content = _fetch_file(client, repo, item["path"], token)
                if content:
                    results.append((item["path"], content))
    except httpx.HTTPError:
        pass
    return results


def _chunk(text: str) -> list[str]:
    """Split text into overlapping chunks of ~_CHUNK_SIZE chars."""
    if len(text) <= _CHUNK_SIZE:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + _CHUNK_SIZE
        chunks.append(text[start:end])
        start += _CHUNK_SIZE - _CHUNK_OVERLAP
    return chunks


def index_repo(repo_id: uuid.UUID) -> int:
    """Fetch, chunk, embed, and upsert repo docs. Returns total chunk count."""
    s = get_settings()
    repo_slug = s.github_repo
    token = s.github_token
    if not repo_slug or not token:
        logger.warning("index_repo: GITHUB_REPO or GITHUB_TOKEN not set")
        return 0

    files_to_index: list[tuple[str, str]] = []
    with httpx.Client(timeout=30.0) as client:
        for path in ("README.md", "CONTRIBUTING.md"):
            content = _fetch_file(client, repo_slug, path, token)
            if content:
                files_to_index.append((path, content))
        files_to_index.extend(_fetch_docs_dir(client, repo_slug, token))

    total = 0
    with SessionLocal() as session:
        for path, content in files_to_index:
            # Delete old rows for this (repo_id, path)
            from sqlalchemy import delete
            session.execute(
                delete(RepoDoc).where(
                    RepoDoc.repo_id == repo_id,
                    RepoDoc.path == path,
                )
            )
            chunks = _chunk(content)
            for i, chunk in enumerate(chunks):
                try:
                    vector = embed(chunk)
                    # Format as Postgres array literal for pgvector
                    vector_str = "[" + ",".join(str(v) for v in vector) + "]"
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Embedding failed for %s chunk %d: %s", path, i, exc)
                    vector_str = None

                doc = RepoDoc(
                    id=uuid.uuid4(),
                    repo_id=repo_id,
                    path=path,
                    chunk_index=i,
                    content=chunk,
                    embedding=vector_str,
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(doc)
                total += 1
        session.commit()

    logger.info("index_repo: indexed %d chunks for repo_id=%s", total, repo_id)
    return total
