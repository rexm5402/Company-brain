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
    CommitToBranchTool,
    GetFileContentsTool,
    GetPRChecksTool,
    ListRepoFilesTool,
    OpenPullRequestTool,
)
from app.tools.slack_tool import PostSlackMessageTool


def build_registry(ctx: RunContext, *, fix_mode: bool = False) -> dict[str, Tool]:
    """Tools for a run.

    Normal runs open a PR. In fix_mode (iterate-on-red), the agent instead
    commits to the existing PR branch, so swap open_pull_request for
    commit_to_branch.
    """
    tools: list[Tool] = [
        ListRepoFilesTool(ctx),
        GetFileContentsTool(ctx),
        CommentOnPRTool(ctx),
        GetPRChecksTool(ctx),
        PostSlackMessageTool(),
    ]
    if fix_mode:
        tools.append(CommitToBranchTool(ctx))
    else:
        tools.append(OpenPullRequestTool(ctx))
    return {t.name: t for t in tools}
