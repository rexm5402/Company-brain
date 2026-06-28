"""Minimal Slack Web API client for the consensus listener.

Only the few methods the listener needs: identify the bot, resolve a channel
name to an ID, read recent messages, and post (optionally threaded). Slack
returns HTTP 200 even on logical failures, so every call checks the `ok` field.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from app.config import get_settings

_API = "https://slack.com/api/"


class SlackError(RuntimeError):
    pass


class SlackClient:
    def __init__(self, token: Optional[str] = None) -> None:
        self._token = token or get_settings().slack_bot_token
        self._headers = {"Authorization": f"Bearer {self._token}"}

    def _get(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(_API + method, headers=self._headers, params=params)
            r.raise_for_status()
            body = r.json()
        if not body.get("ok"):
            raise SlackError(f"{method}: {body.get('error')}")
        return body

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(_API + method, headers=self._headers, json=payload)
            r.raise_for_status()
            body = r.json()
        if not body.get("ok"):
            raise SlackError(f"{method}: {body.get('error')}")
        return body

    def whoami(self) -> dict[str, Any]:
        return self._get("auth.test", {})

    def resolve_channel(self, name_or_id: str) -> str:
        """Accept a channel ID (C…/G…) or a #name and return the channel ID."""
        target = name_or_id.lstrip("#")
        if name_or_id.startswith(("C", "G")) and name_or_id.isupper():
            return name_or_id
        cursor: Optional[str] = None
        while True:
            params: dict[str, Any] = {
                "types": "public_channel",
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            body = self._get("conversations.list", params)
            for ch in body.get("channels", []):
                if ch.get("id") == name_or_id or ch.get("name") == target:
                    return ch["id"]
            cursor = body.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        raise SlackError(f"channel not found: {name_or_id}")

    def history(
        self, channel: str, *, oldest: Optional[str] = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return messages oldest-first (Slack returns newest-first)."""
        params: dict[str, Any] = {"channel": channel, "limit": limit}
        if oldest:
            params["oldest"] = oldest
        body = self._get("conversations.history", params)
        return list(reversed(body.get("messages", [])))

    def post(
        self, channel: str, text: str, *, thread_ts: Optional[str] = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return self._post("chat.postMessage", payload)
