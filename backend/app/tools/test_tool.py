"""RunTestsTool — let the agent validate its own code before opening a PR.

The agent calls this with the same `files` list it would pass to
`open_pull_request`. Internally it materialises the files over a fresh
checkout of the base branch (identical to the "full" pre-PR validation gate)
and runs pytest.

If tests pass the agent can open the PR with confidence. If they fail it gets
the pytest tail output and can fix its code and try again — the self-healing
loop. The tool is intentionally EXCLUDED from fix_mode (the agent is already
iterating on a broken branch via commit_to_branch + CI).

Security note: this DOES execute agent-generated Python (same as
prepr_validation="full"). Run it only in environments where you trust the
generated code, or in a container. The default config leaves
prepr_validation="lint", which is safe; this tool is an opt-in step the agent
chooses to take.
"""
from __future__ import annotations

from app.tools.base import Tool, ToolResult

_MAX_LOG_CHARS = 4000  # truncate long pytest output before sending to LLM


class RunTestsTool(Tool):
    name = "run_tests"
    description = (
        "Run the full project test suite with your proposed file changes applied "
        "on top of the current base branch. "
        "Call this BEFORE open_pull_request when you write new code or fix a bug "
        "to confirm tests pass. If they fail, read the output, fix your files, "
        "and call run_tests again before opening the PR."
    )
    parameters = {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "description": (
                    "The files you want to include in the PR, each with 'path' and "
                    "'content'. Same format as open_pull_request."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Repo-relative file path."},
                        "content": {"type": "string", "description": "Full file content."},
                    },
                    "required": ["path", "content"],
                },
                "minItems": 1,
            }
        },
        "required": ["files"],
    }

    def run(self, *, files: list[dict]) -> ToolResult:  # type: ignore[override]
        if not files:
            return ToolResult(success=False, error="No files provided.")

        # Import here to avoid circular imports; validation module is standalone.
        from app.tools.validation import _run_full  # noqa: PLC0415

        result = _run_full(files)

        if result.passed:
            return ToolResult(
                success=True,
                output={
                    "status": "all tests passed",
                    "files_tested": [f.get("path") for f in files],
                },
            )

        log_tail = (result.log or "").strip()
        if len(log_tail) > _MAX_LOG_CHARS:
            log_tail = "...(truncated)...\n" + log_tail[-_MAX_LOG_CHARS:]

        return ToolResult(
            success=False,
            error="Tests failed. Fix the issues shown in test_output and run again.",
            output={
                "errors": result.errors,
                "test_output": log_tail,
            },
        )
