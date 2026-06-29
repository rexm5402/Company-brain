"""Tool registry.

Tools are built per run so the GitHub tools share one RunContext (the read set
that backs the read-before-write guard). The agent loop discovers tools through
this registry, so adding a tool is a one-line change.
"""
from __future__ import annotations

from app.tools.base import Tool
from app.tools.context import RunContext
from app.tools.github_tool import (
    CommentOnPRTool,
    GetFileContentsTool,
    GetPRChecksTool,
    ListRepoFilesTool,
    OpenPullRequestTool,
)
from app.tools.slack_tool import PostSlackMessageTool


def build_registry(ctx: RunContext) -> dict[str, Tool]:
    tools: list[Tool] = [
        ListRepoFilesTool(ctx),
        GetFileContentsTool(ctx),
        OpenPullRequestTool(ctx),
        CommentOnPRTool(ctx),
        GetPRChecksTool(ctx),
        PostSlackMessageTool(),
    ]
    return {t.name: t for t in tools}
