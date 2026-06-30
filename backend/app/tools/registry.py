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
from app.tools.sentry_tool import GetRecentErrorsTool
from app.tools.slack_tool import PostSlackMessageTool
from app.tools.test_tool import RunTestsTool


def build_registry(
    ctx: RunContext,
    *,
    fix_mode: bool = False,
    repo_slug: str | None = None,
    token: str | None = None,
    repo_id: str | None = None,
) -> dict[str, Tool]:
    """Tools for a run.

    Normal runs open a PR. In fix_mode (iterate-on-red), the agent instead
    commits to the existing PR branch, so swap open_pull_request for
    commit_to_branch. run_tests is also excluded in fix_mode — CI is already
    running on the existing branch.
    """
    gh_kwargs: dict = {}
    if repo_slug is not None:
        gh_kwargs["repo"] = repo_slug
    if token is not None:
        gh_kwargs["token"] = token

    tools: list[Tool] = [
        ListRepoFilesTool(ctx, **gh_kwargs),
        GetFileContentsTool(ctx, **gh_kwargs),
        CommentOnPRTool(ctx, **gh_kwargs),
        GetPRChecksTool(ctx, **gh_kwargs),
        PostSlackMessageTool(),
        GetRecentErrorsTool(),
    ]
    if fix_mode:
        tools.append(CommitToBranchTool(ctx, **gh_kwargs))
    else:
        tools.append(OpenPullRequestTool(ctx, **gh_kwargs))
        tools.append(RunTestsTool())

    # Feature 3: repo memory search (only when a repo_id is provided)
    if repo_id is not None:
        try:
            from app.tools.search_repo_docs_tool import SearchRepoDocsTool
            tools.append(SearchRepoDocsTool(ctx, repo_id=repo_id))
        except Exception:  # noqa: BLE001
            pass

    return {t.name: t for t in tools}
