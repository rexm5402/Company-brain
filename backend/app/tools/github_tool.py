"""GitHub tools: read, list, write, and comment.

Design choice: the agent supplies FULL file contents per changed file, not a
unified diff. Agent-generated diffs are the #1 cause of failed PR creation;
full file contents are far more reliable and we let GitHub compute the diff.

Read-before-write guarantee: rewriting a file with full content is only safe if
the agent based that content on the file's CURRENT bytes. `get_file_contents`
records every read into the per-run RunContext (path -> blob sha). Before
committing, `open_pull_request` refuses to overwrite any existing file that was
not read this run, or whose sha changed since it was read.

Robustness: `open_pull_request` uses a unique branch name (never silently
reuses an existing branch), validates .py/.json syntax before committing, and
rolls back (deletes the branch) if anything fails partway through.
"""
from __future__ import annotations

import ast
import base64
import json
import uuid
from typing import Any

import httpx

from app.audit.recorder import ToolResult
from app.config import get_settings
from app.tools.base import Tool
from app.tools.context import RunContext

_API = "https://api.github.com"
# GitHub Contents API only inlines file content up to ~1MB.
_MAX_INLINE_BYTES = 1_000_000


class _GitHubTool(Tool):
    """Shared GitHub config (repo, base branch, auth headers)."""

    def __init__(self, ctx: RunContext) -> None:
        s = get_settings()
        self.ctx = ctx
        self._repo = s.github_repo
        self._base = s.github_base_branch
        self._headers = {
            "Authorization": f"Bearer {s.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=30.0, headers=self._headers)

    def _base_file_sha(self, client: httpx.Client, path: str) -> str | None:
        """Blob sha of `path` on the base branch, or None if it doesn't exist."""
        r = client.get(
            f"{_API}/repos/{self._repo}/contents/{path}", params={"ref": self._base}
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):  # it's a directory, not a file
            return None
        return data["sha"]


class GetFileContentsTool(_GitHubTool):
    name = "get_file_contents"
    description = (
        "Read the current contents of a file from the base branch BEFORE you "
        "modify it. Returns the full text and a sha. You must call this for any "
        "existing file you intend to change, then supply the complete updated "
        "file to open_pull_request. Returns exists=false for new paths."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Repo-relative file path, e.g. 'README.md'.",
            }
        },
        "required": ["path"],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        path = kwargs["path"]
        if not self._repo:
            return ToolResult(success=False, error="GITHUB_REPO is not configured.")
        try:
            with self._client() as client:
                r = client.get(
                    f"{_API}/repos/{self._repo}/contents/{path}",
                    params={"ref": self._base},
                )
                if r.status_code == 404:
                    return ToolResult(
                        success=True, output={"path": path, "exists": False}
                    )
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                error=f"GitHub API {exc.response.status_code}: {exc.response.text[:500]}",
            )

        if isinstance(data, list):
            return ToolResult(
                success=False,
                error=f"'{path}' is a directory, not a file. Use list_repo_files.",
            )

        # Files over the inline limit aren't returned with content.
        if data.get("size", 0) > _MAX_INLINE_BYTES or not data.get("content"):
            return ToolResult(
                success=True,
                output={
                    "path": path,
                    "exists": True,
                    "sha": data.get("sha"),
                    "content": None,
                    "reason": "file exceeds the 1MB inline limit; not readable here",
                },
            )

        try:
            content = base64.b64decode(data["content"]).decode("utf-8")
        except UnicodeDecodeError:
            # Binary file: do NOT record a read (so the write guard still blocks it).
            return ToolResult(
                success=True,
                output={
                    "path": path,
                    "exists": True,
                    "sha": data["sha"],
                    "content": None,
                    "reason": "binary file; cannot be read or edited as text",
                },
            )

        # Only a successful TEXT read counts toward the read-before-write guard.
        self.ctx.record_read(path, data["sha"])
        return ToolResult(
            success=True,
            output={"path": path, "exists": True, "sha": data["sha"], "content": content},
        )


class ListRepoFilesTool(_GitHubTool):
    name = "list_repo_files"
    description = (
        "List the files in the repository (from the base branch) so you can find "
        "which file to read or change. Optionally filter by a path prefix, e.g. "
        "'app/' to list only files under that directory."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path_prefix": {
                "type": "string",
                "description": "Optional path prefix filter, e.g. 'src/'. Omit for all.",
            }
        },
        "required": [],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        prefix = kwargs.get("path_prefix") or ""
        if not self._repo:
            return ToolResult(success=False, error="GITHUB_REPO is not configured.")
        try:
            with self._client() as client:
                r = client.get(
                    f"{_API}/repos/{self._repo}/git/trees/{self._base}",
                    params={"recursive": "1"},
                )
                r.raise_for_status()
                tree = r.json()
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                error=f"GitHub API {exc.response.status_code}: {exc.response.text[:500]}",
            )

        paths = [
            e["path"]
            for e in tree.get("tree", [])
            if e.get("type") == "blob" and e["path"].startswith(prefix)
        ]
        return ToolResult(
            success=True,
            output={
                "count": len(paths),
                "truncated": tree.get("truncated", False),
                "files": paths[:500],
            },
        )


class OpenPullRequestTool(_GitHubTool):
    name = "open_pull_request"
    description = (
        "Open a real pull request on GitHub. Supply the full content of every "
        "file you want to create or change (not a diff). Creates a new branch "
        "off the base branch, commits each file, then opens the PR. For any file "
        "that already exists you must have called get_file_contents on it first. "
        "Python and JSON files are syntax-checked before the PR is opened."
    )
    parameters = {
        "type": "object",
        "properties": {
            "branch": {
                "type": "string",
                "description": "New branch name, e.g. 'agent/add-health-endpoint'.",
            },
            "title": {"type": "string", "description": "Pull request title."},
            "description": {
                "type": "string",
                "description": "Pull request body / summary of changes.",
            },
            "files": {
                "type": "array",
                "description": "Files to create or overwrite, with full contents.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
            "commit_message": {
                "type": "string",
                "description": "Commit message for the file changes.",
            },
        },
        "required": ["branch", "title", "description", "files"],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        branch = kwargs["branch"]
        title = kwargs["title"]
        description = kwargs["description"]
        files = kwargs["files"]
        commit_message = kwargs.get("commit_message") or f"{title}"

        if not self._repo:
            return ToolResult(success=False, error="GITHUB_REPO is not configured.")

        # --- Local validation (no API calls, no side effects) ---
        syntax_errors = self._validate_syntax(files)
        if syntax_errors:
            return ToolResult(
                success=False,
                error="Fix these before opening a PR: " + "; ".join(syntax_errors),
            )

        try:
            with self._client() as client:
                # --- Read-before-write guard ---
                violations = self._guard(client, files)
                if violations:
                    return ToolResult(
                        success=False,
                        error="Refusing to overwrite existing file(s) without a "
                        "current read. Call get_file_contents first, then resubmit "
                        "the full updated file(s). Details: " + "; ".join(violations),
                    )

                base_sha = self._base_sha(client)
                branch = self._unique_branch(client, branch)
                self._create_branch(client, branch, base_sha)

                # --- Commit + open PR, with rollback on any failure ---
                try:
                    for f in files:
                        self._put_file(
                            client, branch, f["path"], f["content"], commit_message
                        )
                    pr = self._open_pr(client, branch, title, description)
                except Exception:
                    self._delete_branch(client, branch)  # best-effort cleanup
                    raise
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                error=f"GitHub API {exc.response.status_code}: {exc.response.text[:500]}",
            )

        return ToolResult(
            success=True,
            output={
                "pr_number": pr["number"],
                "pr_url": pr["html_url"],
                "branch": branch,
                "files_changed": [f["path"] for f in files],
            },
        )

    # --- Validation -----------------------------------------------------
    @staticmethod
    def _validate_syntax(files: list[dict[str, Any]]) -> list[str]:
        """Syntax-check .py/.json content locally. Does NOT execute anything."""
        errors: list[str] = []
        for f in files:
            path, content = f["path"], f["content"]
            if path.endswith(".py"):
                try:
                    ast.parse(content)
                except SyntaxError as exc:
                    errors.append(f"{path}: Python syntax error: {exc.msg} (line {exc.lineno})")
            elif path.endswith(".json"):
                try:
                    json.loads(content)
                except json.JSONDecodeError as exc:
                    errors.append(f"{path}: invalid JSON: {exc.msg} (line {exc.lineno})")
        return errors

    # --- Guard ----------------------------------------------------------
    def _guard(self, client: httpx.Client, files: list[dict[str, Any]]) -> list[str]:
        violations: list[str] = []
        for f in files:
            path = f["path"]
            current_sha = self._base_file_sha(client, path)
            if current_sha is None:
                continue  # new file -> nothing to clobber
            read_sha = self.ctx.read_sha(path)
            if read_sha is None:
                violations.append(f"{path} already exists but was not read this run")
            elif read_sha != current_sha:
                violations.append(f"{path} changed since you read it (stale); re-read it")
        return violations

    # --- Branch helpers -------------------------------------------------
    def _branch_exists(self, client: httpx.Client, branch: str) -> bool:
        r = client.get(f"{_API}/repos/{self._repo}/git/ref/heads/{branch}")
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return True

    def _unique_branch(self, client: httpx.Client, branch: str) -> str:
        """Never reuse an existing branch; suffix until the name is free."""
        candidate = branch
        while self._branch_exists(client, candidate):
            candidate = f"{branch}-{uuid.uuid4().hex[:6]}"
        return candidate

    def _create_branch(self, client: httpx.Client, branch: str, sha: str) -> None:
        r = client.post(
            f"{_API}/repos/{self._repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        r.raise_for_status()

    def _delete_branch(self, client: httpx.Client, branch: str) -> None:
        try:
            client.delete(f"{_API}/repos/{self._repo}/git/refs/heads/{branch}")
        except httpx.HTTPError:
            pass  # cleanup is best-effort

    # --- API steps ------------------------------------------------------
    def _base_sha(self, client: httpx.Client) -> str:
        r = client.get(f"{_API}/repos/{self._repo}/git/ref/heads/{self._base}")
        r.raise_for_status()
        return r.json()["object"]["sha"]

    def _put_file(
        self,
        client: httpx.Client,
        branch: str,
        path: str,
        content: str,
        message: str,
    ) -> None:
        existing = client.get(
            f"{_API}/repos/{self._repo}/contents/{path}", params={"ref": branch}
        )
        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if existing.status_code == 200:
            payload["sha"] = existing.json()["sha"]

        r = client.put(f"{_API}/repos/{self._repo}/contents/{path}", json=payload)
        r.raise_for_status()

    def _open_pr(
        self, client: httpx.Client, branch: str, title: str, body: str
    ) -> dict[str, Any]:
        r = client.post(
            f"{_API}/repos/{self._repo}/pulls",
            json={"title": title, "body": body, "head": branch, "base": self._base},
        )
        r.raise_for_status()
        return r.json()


class CommentOnPRTool(_GitHubTool):
    name = "comment_on_pr"
    description = "Post a comment on an existing pull request (by PR number)."
    parameters = {
        "type": "object",
        "properties": {
            "pr_number": {"type": "integer", "description": "The pull request number."},
            "text": {"type": "string", "description": "The comment body (Markdown)."},
        },
        "required": ["pr_number", "text"],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        pr_number = kwargs["pr_number"]
        text = kwargs["text"]
        if not self._repo:
            return ToolResult(success=False, error="GITHUB_REPO is not configured.")
        try:
            with self._client() as client:
                r = client.post(
                    f"{_API}/repos/{self._repo}/issues/{pr_number}/comments",
                    json={"body": text},
                )
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                error=f"GitHub API {exc.response.status_code}: {exc.response.text[:500]}",
            )
        return ToolResult(
            success=True,
            output={"comment_id": data["id"], "url": data["html_url"]},
        )
