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
from app.tools.validation import validate_files

_API = "https://api.github.com"
# GitHub Contents API only inlines file content up to ~1MB.
_MAX_INLINE_BYTES = 1_000_000


class _GitHubTool(Tool):
    """Shared GitHub config (repo, base branch, auth headers)."""

    def __init__(self, ctx: RunContext, *, repo: str | None = None, token: str | None = None) -> None:
        s = get_settings()
        self.ctx = ctx
        self._repo = repo or s.github_repo
        self._base = s.github_base_branch
        _token = token or s.github_token
        self._headers = {
            "Authorization": f"Bearer {_token}",
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
        "Read the contents of a file from the repository. "
        "By default reads from the base branch (current HEAD). "
        "Pass an optional 'ref' (commit SHA or branch name) to read the file "
        "as it existed at a specific point in time — use this for the Time Machine "
        "debugger to see the exact code that was live when a production error occurred. "
        "You must call this for any existing file you intend to change before "
        "supplying the complete updated file to open_pull_request."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Repo-relative file path, e.g. 'app/auth.py'.",
            },
            "ref": {
                "type": "string",
                "description": (
                    "Git ref to read from: a commit SHA, branch name, or tag. "
                    "Omit to read the current base branch (default). "
                    "Use the commit SHA from a Sentry/CI event to read historical code."
                ),
            },
        },
        "required": ["path"],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        path = kwargs["path"]
        ref = kwargs.get("ref") or self._base
        if not self._repo:
            return ToolResult(success=False, error="GITHUB_REPO is not configured.")
        try:
            with self._client() as client:
                r = client.get(
                    f"{_API}/repos/{self._repo}/contents/{path}",
                    params={"ref": ref},
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

        # Only reads from the CURRENT base branch count toward the read-before-write
        # guard. Historical reads (Time Machine: ref = a past commit SHA) are for
        # context only — the guard should still require a current read before writing.
        is_current = (ref == self._base)
        if is_current:
            self.ctx.record_read(path, data["sha"])
        return ToolResult(
            success=True,
            output={
                "path": path,
                "exists": True,
                "sha": data["sha"],
                "content": content,
                "ref": ref,
                "is_historical": not is_current,
            },
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

        # --- Local validation (no side effects on the repo) ---
        syntax_errors = self._validate_syntax(files)
        if syntax_errors:
            return ToolResult(
                success=False,
                error="Fix these before opening a PR: " + "; ".join(syntax_errors),
            )
        # Deeper pre-PR gate (lint / install+test, per PREPR_VALIDATION). Catches
        # bugs that still parse, so we open green PRs instead of relying solely on
        # reactive iterate-on-red. Returns the problems for the agent to fix.
        validation = validate_files(files)
        if not validation.passed:
            return ToolResult(
                success=False,
                error="Pre-PR validation failed; fix and resubmit. "
                + "; ".join(validation.errors),
                output={"validation_log": validation.log[:4000]} if validation.log else None,
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
                "repo": self._repo,
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


class GetPRChecksTool(_GitHubTool):
    name = "get_pr_checks"
    description = (
        "Get the CI/CD status (GitHub Actions workflow runs) for a pull request "
        "by number. Returns an overall state of 'success', 'failure', 'pending', "
        "or 'none' (no workflows configured), plus per-workflow details including "
        "the failing workflow name. Use this after opening a PR to see if the "
        "build/tests passed."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pr_number": {"type": "integer", "description": "The pull request number."},
        },
        "required": ["pr_number"],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        pr_number = kwargs["pr_number"]
        if not self._repo:
            return ToolResult(success=False, error="GITHUB_REPO is not configured.")
        try:
            with self._client() as client:
                pr = client.get(f"{_API}/repos/{self._repo}/pulls/{pr_number}")
                pr.raise_for_status()
                head_sha = pr.json()["head"]["sha"]
                wr = client.get(
                    f"{_API}/repos/{self._repo}/actions/runs",
                    params={"head_sha": head_sha},
                )
                wr.raise_for_status()
                runs = wr.json().get("workflow_runs", [])
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                error=f"GitHub API {exc.response.status_code}: {exc.response.text[:500]}",
            )

        checks = [
            {
                "name": c.get("name"),
                "status": c.get("status"),  # queued | in_progress | completed
                "conclusion": c.get("conclusion"),  # success | failure | ... | None
                "url": c.get("html_url"),
            }
            for c in runs
        ]
        state = self._overall_state(checks)
        return ToolResult(
            success=True,
            output={
                "pr_number": pr_number,
                "head_sha": head_sha,
                "state": state,
                "total": len(checks),
                "checks": checks,
            },
        )

    @staticmethod
    def _overall_state(checks: list[dict[str, Any]]) -> str:
        if not checks:
            return "none"
        if any(c["status"] != "completed" for c in checks):
            return "pending"
        if any(c["conclusion"] not in ("success", "neutral", "skipped") for c in checks):
            return "failure"
        return "success"


def get_pr_branch(pr_number: int) -> str | None:
    """The head branch name for a PR, or None on failure."""
    s = get_settings()
    if not s.github_repo:
        return None
    headers = {
        "Authorization": f"Bearer {s.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with httpx.Client(timeout=30.0, headers=headers) as client:
            r = client.get(f"{_API}/repos/{s.github_repo}/pulls/{pr_number}")
            r.raise_for_status()
            return r.json().get("head", {}).get("ref")
    except httpx.HTTPError:
        return None


def _relevant_log_slice(text: str, max_chars: int) -> str:
    """Return a window of the log centred on the actual error.

    GitHub appends checkout/cleanup steps *after* the failing step, so a blind
    tail of the log often captures only that trailing noise and truncates the
    real error out. Instead, find the last error marker and keep the context
    just before it (where the diagnostic detail lives) plus a little after.
    """
    if len(text) <= max_chars:
        return text
    markers = (
        "##[error]",
        "SyntaxError",
        "Traceback (most recent call last)",
        "Error compiling",
        "AssertionError",
        "FAILED",
        "error:",
    )
    pos = max((text.rfind(m) for m in markers), default=-1)
    if pos == -1:
        return text[-max_chars:]
    after = 400  # a little context past the marker
    start = max(0, pos - (max_chars - after))
    end = min(len(text), pos + after)
    return text[start:end]


def get_failing_ci_logs(pr_number: int, *, max_chars: int = 6000) -> str:
    """Fetch the log text of the failed jobs for a PR's latest workflow run.

    Reads via the Actions API (needs Actions:read). Returns the region of the
    combined failing-job logs around the actual error (see _relevant_log_slice),
    truncated to max_chars. Empty string if nothing is retrievable.
    """
    s = get_settings()
    if not s.github_repo:
        return ""
    headers = {
        "Authorization": f"Bearer {s.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with httpx.Client(timeout=30.0, headers=headers, follow_redirects=True) as client:
            pr = client.get(f"{_API}/repos/{s.github_repo}/pulls/{pr_number}")
            pr.raise_for_status()
            head_sha = pr.json()["head"]["sha"]
            runs = client.get(
                f"{_API}/repos/{s.github_repo}/actions/runs",
                params={"head_sha": head_sha},
            )
            runs.raise_for_status()
            workflow_runs = runs.json().get("workflow_runs", [])
            if not workflow_runs:
                return ""
            run_id = workflow_runs[0]["id"]
            jobs = client.get(
                f"{_API}/repos/{s.github_repo}/actions/runs/{run_id}/jobs"
            )
            jobs.raise_for_status()
            chunks: list[str] = []
            for job in jobs.json().get("jobs", []):
                if job.get("conclusion") in ("success", "neutral", "skipped", None):
                    continue
                log = client.get(
                    f"{_API}/repos/{s.github_repo}/actions/jobs/{job['id']}/logs"
                )
                if log.status_code == 200 and log.text:
                    chunks.append(f"--- job: {job.get('name')} ---\n{log.text}")
            combined = "\n\n".join(chunks)
            return _relevant_log_slice(combined, max_chars) if combined else ""
    except (httpx.HTTPError, KeyError):
        return ""


def get_pr_state(pr_number: int) -> dict[str, Any]:
    """Return {state, merged} for a PR. state is 'open'|'closed'. Best-effort."""
    s = get_settings()
    if not s.github_repo:
        return {"state": "unknown", "merged": False}
    headers = {
        "Authorization": f"Bearer {s.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with httpx.Client(timeout=30.0, headers=headers) as client:
            r = client.get(f"{_API}/repos/{s.github_repo}/pulls/{pr_number}")
            r.raise_for_status()
            data = r.json()
            return {
                "state": data.get("state", "unknown"),
                "merged": bool(data.get("merged")),
            }
    except httpx.HTTPError:
        return {"state": "unknown", "merged": False}


def get_pr_file_paths(pr_number: int) -> list[str]:
    """Best-effort list of file paths a PR changed. Empty list on any failure."""
    s = get_settings()
    if not s.github_repo:
        return []
    headers = {
        "Authorization": f"Bearer {s.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with httpx.Client(timeout=30.0, headers=headers) as client:
            r = client.get(
                f"{_API}/repos/{s.github_repo}/pulls/{pr_number}/files",
                params={"per_page": 100},
            )
            r.raise_for_status()
            return [f.get("filename") for f in r.json() if f.get("filename")]
    except httpx.HTTPError:
        return []


class CommitToBranchTool(_GitHubTool):
    name = "commit_to_branch"
    description = (
        "Commit fixed files to an EXISTING branch (e.g. a PR branch whose CI "
        "failed) without opening a new PR. Supply the full content of every file "
        "you are changing — not a diff. Python and JSON are syntax-checked before "
        "committing. Use this to push a fix so CI re-runs on the same PR."
    )
    parameters = {
        "type": "object",
        "properties": {
            "branch": {"type": "string", "description": "Existing branch to commit to."},
            "files": {
                "type": "array",
                "description": "Files to overwrite, with full contents.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
            "commit_message": {"type": "string"},
        },
        "required": ["branch", "files"],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        branch = kwargs["branch"]
        files = kwargs["files"]
        commit_message = kwargs.get("commit_message") or "Fix CI failure"
        if not self._repo:
            return ToolResult(success=False, error="GITHUB_REPO is not configured.")
        if not files:
            return ToolResult(success=False, error="No files supplied.")

        syntax_errors = OpenPullRequestTool._validate_syntax(files)
        if syntax_errors:
            return ToolResult(
                success=False,
                error="Fix these before committing: " + "; ".join(syntax_errors),
            )
        try:
            with self._client() as client:
                if not self._branch_exists(client, branch):
                    return ToolResult(
                        success=False, error=f"Branch '{branch}' does not exist."
                    )
                for f in files:
                    self._put_file(
                        client, branch, f["path"], f["content"], commit_message
                    )
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                error=f"GitHub API {exc.response.status_code}: {exc.response.text[:500]}",
            )
        return ToolResult(
            success=True,
            output={"branch": branch, "files_changed": [f["path"] for f in files]},
        )

    # Reuse OpenPullRequestTool's branch/put helpers by sharing the methods.
    _branch_exists = OpenPullRequestTool._branch_exists
    _put_file = OpenPullRequestTool._put_file


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


def get_pr_comments(
    pr_number: int,
    *,
    repo_slug: str | None = None,
    token: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch all review + issue comments for a PR, sorted by created_at."""
    s = get_settings()
    repo = repo_slug or s.github_repo
    if not repo:
        return []
    _token = token or s.github_token
    headers = {
        "Authorization": f"Bearer {_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    comments: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=30.0, headers=headers) as client:
            # Pull request review comments (line-level)
            r1 = client.get(
                f"{_API}/repos/{repo}/pulls/{pr_number}/comments",
                params={"per_page": 100},
            )
            if r1.status_code == 200:
                for c in r1.json():
                    comments.append({
                        "id": c.get("id"),
                        "user": (c.get("user") or {}).get("login"),
                        "body": c.get("body"),
                        "created_at": c.get("created_at"),
                        "type": "review_comment",
                        "url": c.get("html_url"),
                    })
            # Issue comments (general PR comments)
            r2 = client.get(
                f"{_API}/repos/{repo}/issues/{pr_number}/comments",
                params={"per_page": 100},
            )
            if r2.status_code == 200:
                for c in r2.json():
                    comments.append({
                        "id": c.get("id"),
                        "user": (c.get("user") or {}).get("login"),
                        "body": c.get("body"),
                        "created_at": c.get("created_at"),
                        "type": "issue_comment",
                        "url": c.get("html_url"),
                    })
    except httpx.HTTPError:
        return []
    comments.sort(key=lambda c: c.get("created_at") or "")
    return comments
