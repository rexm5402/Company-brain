"""Provider-agnostic LLM client.

The rest of the codebase only talks to `LLMClient.chat(...)` and gets back a
normalized `LLMResponse`. Groq (OpenAI-compatible) and Anthropic have different
tool-calling schemas, so all of that divergence is contained here. Switching
providers is a one-env-var change (LLM_PROVIDER).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient:
    """Thin wrapper that normalizes tool-calling across providers."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self.provider = self._settings.llm_provider
        if self.provider == "groq":
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self._settings.groq_api_key,
                base_url="https://api.groq.com/openai/v1",
            )
            self._model = self._settings.groq_model
        elif self.provider == "anthropic":
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
            self._model = self._settings.anthropic_model
        else:  # pragma: no cover - guarded by config Literal
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        if self.provider == "groq":
            return self._chat_openai(system=system, messages=messages, tools=tools)
        return self._chat_anthropic(system=system, messages=messages, tools=tools)

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        """Plain text completion (no tools). Used by the consensus detector."""
        if self.provider == "groq":
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "\n".join(b.text for b in resp.content if b.type == "text")

    # --- Groq / OpenAI-compatible ---------------------------------------
    def _chat_openai(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        from openai import BadRequestError

        oai_tools = [
            {"type": "function", "function": t} for t in tools
        ]
        # Open models occasionally emit an unparseable tool call (Groq returns
        # 400 tool_use_failed). It's usually a transient sampling artifact, so
        # retry a few times before giving up.
        attempts = 4
        resp = None
        for attempt in range(1, attempts + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "system", "content": system}, *messages],
                    tools=oai_tools,
                    tool_choice="auto",
                    temperature=0.2,
                )
                break
            except BadRequestError as exc:
                if "tool_use_failed" in str(exc) and attempt < attempts:
                    time.sleep(0.5 * attempt)
                    continue
                raise
        assert resp is not None
        msg = resp.choices[0].message
        tool_calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=_safe_json(tc.function.arguments),
                )
            )
        return LLMResponse(text=msg.content, tool_calls=tool_calls)

    # --- Anthropic ------------------------------------------------------
    def _chat_anthropic(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        anthropic_tools = [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t["parameters"],
            }
            for t in tools
        ]
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            messages=messages,
            tools=anthropic_tools,
        )
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )
        return LLMResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
        )


def _safe_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
