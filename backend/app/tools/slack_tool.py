"""Slack `post_slack_message` tool (Weekend 2).

Posts to a channel via the Slack Web API (chat.postMessage). Slack returns HTTP
200 even on logical failures, so we check the `ok` field in the body.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.audit.recorder import ToolResult
from app.config import get_settings
from app.tools.base import Tool

_SLACK_API = "https://slack.com/api/chat.postMessage"


class PostSlackMessageTool(Tool):
    name = "post_slack_message"
    description = (
        "Post a status message to a Slack channel, e.g. to announce that a pull "
        "request was opened. Channel can be a name like '#engineering' or an ID."
    )
    parameters = {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "description": "Channel name or ID."},
            "text": {"type": "string", "description": "Message text (Markdown)."},
        },
        "required": ["channel", "text"],
    }

    def __init__(self) -> None:
        self._token = get_settings().slack_bot_token

    def run(self, **kwargs: Any) -> ToolResult:
        channel = kwargs["channel"]
        text = kwargs["text"]
        if not self._token:
            return ToolResult(success=False, error="SLACK_BOT_TOKEN is not configured.")
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.post(
                    _SLACK_API,
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={"channel": channel, "text": text},
                )
                r.raise_for_status()
                body = r.json()
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                error=f"Slack API {exc.response.status_code}: {exc.response.text[:300]}",
            )

        if not body.get("ok"):
            return ToolResult(success=False, error=f"Slack error: {body.get('error')}")
        return ToolResult(
            success=True,
            output={"channel": body.get("channel"), "ts": body.get("ts")},
        )
