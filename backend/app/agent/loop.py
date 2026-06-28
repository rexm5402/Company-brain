"""The agent loop: plan -> call tool -> observe -> repeat -> finish.

Deliberately simple (no graph/orchestrator). Every tool call goes through the
audit recorder, so each step is persisted before the loop continues.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agent.llm import LLMClient, ToolCall
from app.agent.reviewer import review_files
from app.agent.system_prompt import SYSTEM_PROMPT
from app.audit.recorder import ToolResult, record_tool_call
from app.tools.context import RunContext
from app.tools.registry import build_registry

MAX_STEPS = 8


@dataclass
class AgentRun:
    run_id: uuid.UUID
    final_text: str | None = None
    pr_url: str | None = None
    steps: int = 0
    transcript: list[dict[str, Any]] = field(default_factory=list)


def run_agent(task: str, *, max_steps: int = MAX_STEPS) -> AgentRun:
    run_id = uuid.uuid4()
    llm = LLMClient()
    ctx = RunContext()
    registry = build_registry(ctx)
    tool_schemas = [t.schema() for t in registry.values()]

    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    run = AgentRun(run_id=run_id)

    for step in range(1, max_steps + 1):
        run.steps = step
        try:
            response = llm.chat(
                system=SYSTEM_PROMPT, messages=messages, tools=tool_schemas
            )
        except Exception as exc:  # provider error after retries -> end cleanly
            run.final_text = f"LLM call failed: {type(exc).__name__}: {exc}"
            break

        if response.text:
            run.transcript.append({"step": step, "reasoning": response.text})

        # No tool calls => the agent is done.
        if not response.tool_calls:
            run.final_text = response.text
            break

        _append_assistant_turn(messages, llm.provider, response.text, response.tool_calls)

        for tc in response.tool_calls:
            if tc.name == "open_pull_request" and isinstance(
                tc.arguments.get("files"), list
            ) and tc.arguments["files"]:
                _self_review(run_id, step, task, tc, llm)
            result = _dispatch(run_id, step, registry, tc)
            if result.success and result.output and "pr_url" in result.output:
                run.pr_url = result.output["pr_url"]
            _append_tool_result(messages, llm.provider, tc, result)
    else:
        run.final_text = "Reached max steps without finishing."

    return run


def _self_review(
    run_id: uuid.UUID,
    step: int,
    task: str,
    tc: ToolCall,
    llm: LLMClient,
) -> None:
    """Re-read generated files for bugs before the PR opens, in place.

    Audited as a `self_review_code` step. Any failure leaves the original files
    untouched — the review can only help, never block the PR.
    """
    files = tc.arguments["files"]
    holder: dict[str, Any] = {}

    def fn() -> ToolResult:
        reviewed, changed = review_files(task, files, llm)
        holder["files"] = reviewed
        return ToolResult(
            success=True,
            output={"reviewed": len(files), "changed_paths": changed},
        )

    record_tool_call(
        run_id=run_id,
        step=step,
        tool_name="self_review_code",
        tool_input={"paths": [f.get("path") for f in files]},
        fn=fn,
    )
    if holder.get("files"):
        tc.arguments["files"] = holder["files"]


def _dispatch(
    run_id: uuid.UUID,
    step: int,
    registry: dict[str, Any],
    tc: ToolCall,
) -> ToolResult:
    tool = registry.get(tc.name)
    if tool is None:
        return record_tool_call(
            run_id=run_id,
            step=step,
            tool_name=tc.name,
            tool_input=tc.arguments,
            fn=lambda: ToolResult(success=False, error=f"Unknown tool: {tc.name}"),
        )
    return record_tool_call(
        run_id=run_id,
        step=step,
        tool_name=tc.name,
        tool_input=tc.arguments,
        fn=lambda: tool.run(**tc.arguments),
    )


# --- Message threading (provider-specific shapes) -----------------------
def _append_assistant_turn(
    messages: list[dict[str, Any]],
    provider: str,
    text: str | None,
    tool_calls: list[ToolCall],
) -> None:
    if provider == "groq":
        messages.append(
            {
                "role": "assistant",
                "content": text or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )
    else:  # anthropic
        content: list[dict[str, Any]] = []
        if text:
            content.append({"type": "text", "text": text})
        for tc in tool_calls:
            content.append(
                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
            )
        messages.append({"role": "assistant", "content": content})


def _append_tool_result(
    messages: list[dict[str, Any]],
    provider: str,
    tc: ToolCall,
    result: ToolResult,
) -> None:
    payload = json.dumps(
        {"success": result.success, "output": result.output, "error": result.error}
    )
    if provider == "groq":
        messages.append(
            {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": payload}
        )
    else:  # anthropic
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": payload,
                        "is_error": not result.success,
                    }
                ],
            }
        )
