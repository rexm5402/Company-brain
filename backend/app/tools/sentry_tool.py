"""Sentry error-context tool.

Lets the agent pull recent production errors from Sentry before attempting a
bug fix, so it has real stack traces and occurrence counts rather than just a
text description.

Requires three env vars:
  SENTRY_AUTH_TOKEN   — a Sentry internal integration token (or user API token)
  SENTRY_ORG          — the Sentry organisation slug (e.g. "acme-corp")
  SENTRY_PROJECT      — the Sentry project slug (e.g. "backend")

All three default to "" — if any are missing the tool returns a clear error
message and does NOT raise, so the agent can continue without Sentry context.
"""
from __future__ import annotations

import httpx

from app.config import get_settings
from app.tools.base import Tool, ToolResult

_SENTRY_API = "https://sentry.io/api/0"


class GetRecentErrorsTool(Tool):
    name = "get_recent_errors"
    description = (
        "Fetch recent Sentry production errors matching a keyword or exception name. "
        "Call this early in a bug-fix task to get real stack traces, occurrence counts, "
        "and affected endpoints before writing any code."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Keyword or Sentry issue search query "
                    "(e.g. 'TypeError', 'is:unresolved payment', 'user.email:*@corp.com')."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Number of issues to return (1–20, default 5).",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    def run(self, *, query: str, limit: int = 5) -> ToolResult:  # type: ignore[override]
        s = get_settings()
        if not s.sentry_auth_token or not s.sentry_org or not s.sentry_project:
            return ToolResult(
                success=False,
                error=(
                    "Sentry not configured. Set SENTRY_AUTH_TOKEN, SENTRY_ORG, "
                    "and SENTRY_PROJECT environment variables to enable this tool."
                ),
            )

        limit = min(max(1, limit), 20)
        url = f"{_SENTRY_API}/projects/{s.sentry_org}/{s.sentry_project}/issues/"
        headers = {
            "Authorization": f"Bearer {s.sentry_auth_token}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=15.0, headers=headers) as c:
                r = c.get(
                    url,
                    params={"query": query, "limit": limit, "sort": "date"},
                )
                r.raise_for_status()
                raw = r.json()
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                error=f"Sentry API returned {exc.response.status_code}: {exc.response.text[:300]}",
            )
        except httpx.HTTPError as exc:
            return ToolResult(success=False, error=f"Sentry request failed: {exc}")

        issues = []
        for item in raw[:limit]:
            # Pull the most recent event for a short stack-trace excerpt
            latest_event = item.get("latestEvent") or {}
            culprit = item.get("culprit") or latest_event.get("culprit", "")
            issues.append(
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "culprit": culprit,
                    "level": item.get("level"),
                    "count": item.get("count"),
                    "user_count": item.get("userCount"),
                    "first_seen": item.get("firstSeen"),
                    "last_seen": item.get("lastSeen"),
                    "permalink": item.get("permalink"),
                    "status": item.get("status"),
                }
            )

        return ToolResult(
            success=True,
            output={
                "issues": issues,
                "total_returned": len(issues),
                "hint": (
                    "Each 'permalink' links to the full Sentry event with stack trace. "
                    "Use 'title' and 'culprit' to locate the relevant code before editing."
                ),
            },
        )
