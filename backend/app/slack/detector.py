"""Consensus detector.

Given a short Slack transcript, decide whether at least TWO distinct people
have explicitly agreed to go ahead and implement a specific change right now.
If so, distill a concrete engineering task from the discussion. This is the
trigger + (demo-grade) authorization signal for the listener.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.agent.llm import LLMClient

_SYSTEM = """\
You read a short Slack conversation between teammates and decide ONE thing:
have at least TWO DISTINCT people explicitly agreed to go ahead and implement a
specific software change right now?

Only answer ready=true when there is genuine MUTUAL go-ahead — both people signal
"let's do it" (e.g. "done", "let's ship it", "go ahead", "lgtm", "agreed, build
it"). A single person deciding, or people still debating/asking questions, is
ready=false.

Return ONLY a JSON object, no prose, with these keys:
{
  "ready": boolean,
  "task": string,     // a clear, self-contained implementation instruction an
                      // engineer could act on, distilled from the discussion.
                      // Empty string when ready=false.
  "agreers": [string],// the people who agreed (use their labels from the transcript)
  "reason": string    // one short sentence explaining the decision
}
"""


@dataclass
class Consensus:
    ready: bool
    task: str = ""
    agreers: list[str] = field(default_factory=list)
    reason: str = ""


def detect_consensus(transcript: str, llm: Optional[LLMClient] = None) -> Consensus:
    llm = llm or LLMClient()
    raw = llm.complete(system=_SYSTEM, user=transcript)
    data = _parse_json(raw)
    return Consensus(
        ready=bool(data.get("ready")),
        task=(data.get("task") or "").strip(),
        agreers=[str(a) for a in (data.get("agreers") or [])],
        reason=(data.get("reason") or "").strip(),
    )


def _parse_json(raw: str) -> dict[str, Any]:
    """Tolerant JSON extraction — models sometimes wrap output in fences/prose."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return {}
