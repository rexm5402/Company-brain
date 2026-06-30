"""SearchRepoDocsTool — semantic search over indexed repo documentation."""
from __future__ import annotations

import logging
from typing import Any

from app.audit.recorder import ToolResult
from app.tools.base import Tool
from app.tools.context import RunContext

logger = logging.getLogger(__name__)


class SearchRepoDocsTool(Tool):
    name = "search_repo_docs"
    description = (
        "Search the indexed repository documentation for context relevant to your task. "
        "Call this before writing code to understand existing patterns, architecture decisions, "
        "or deployment procedures documented in README.md, CONTRIBUTING.md, or docs/. "
        "Returns up to 5 matching document chunks ranked by semantic similarity."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language query to search for in the repo docs.",
            }
        },
        "required": ["query"],
    }

    def __init__(self, ctx: RunContext, *, repo_id: str | None = None) -> None:
        self.ctx = ctx
        self._repo_id = repo_id

    def run(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query", "")
        if not query or not self._repo_id:
            return ToolResult(
                success=True,
                output={"results": [], "note": "docs not indexed or pgvector not available"},
            )
        try:
            from app.memory.embeddings import embed
            from app.db import SessionLocal
            import sqlalchemy as sa

            vector = embed(query)
            vector_literal = "[" + ",".join(str(v) for v in vector) + "]"

            with SessionLocal() as session:
                rows = session.execute(
                    sa.text(
                        "SELECT path, chunk_index, content, "
                        "embedding <=> :vec::vector AS distance "
                        "FROM repo_docs "
                        "WHERE repo_id = :repo_id AND embedding IS NOT NULL "
                        "ORDER BY embedding <=> :vec::vector "
                        "LIMIT 5"
                    ),
                    {"vec": vector_literal, "repo_id": self._repo_id},
                ).fetchall()

            results = [
                {
                    "path": r[0],
                    "chunk_index": r[1],
                    "content": r[2],
                    "distance": float(r[3]),
                }
                for r in rows
            ]
            return ToolResult(success=True, output={"results": results})
        except Exception as exc:  # noqa: BLE001
            logger.warning("search_repo_docs failed: %s", exc)
            return ToolResult(
                success=True,
                output={"results": [], "note": "docs not indexed or pgvector not available"},
            )
