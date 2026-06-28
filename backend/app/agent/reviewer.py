"""Self-review pass.

Before a PR opens, each generated file is re-read by the model against the
original task to catch bugs the first pass missed (logic slips, broken string
concatenation, typos). Review is an easier job than generation, so even the same
model often fixes its own obvious mistakes here.

Per-file, raw-text output (not JSON) so source containing quotes/newlines can't
break parsing. Any failure falls back to the original content — the review can
only help, never block the PR.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.agent.llm import LLMClient

_SYSTEM = """\
You are a meticulous senior engineer doing a FINAL review of one file before its
pull request opens. You are given the original TASK and a single FILE the agent
wrote for it.

Re-read the file line by line and fix any REAL bugs: logic errors, typos, broken
string concatenation, off-by-one errors, wrong variable names, syntax errors, or
incomplete code. Make sure the file actually accomplishes the task and runs.

Do NOT rewrite style, rename things, add features, or change behavior that is
already correct. If the file is already correct, return it exactly as-is.

Return ONLY the complete, corrected file contents. No explanation, no commentary,
no markdown code fences.
"""

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\n(.*)\n```$", re.DOTALL)


def _strip_fence(text: str) -> str:
    t = text.strip()
    m = _FENCE.match(t)
    return m.group(1) if m else t


def review_files(
    task: str,
    files: list[dict[str, Any]],
    llm: Optional[LLMClient] = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (reviewed_files, changed_paths).

    reviewed_files is aligned 1:1 with the input (same paths, same order); each
    file's content is the corrected version, or the original if review failed or
    found nothing to fix.
    """
    llm = llm or LLMClient()
    reviewed: list[dict[str, Any]] = []
    changed: list[str] = []
    for f in files:
        path = f["path"]
        original = f["content"]
        user = (
            f"TASK:\n{task}\n\n"
            f"FILE: {path}\n"
            f"----- BEGIN FILE -----\n{original}\n----- END FILE -----"
        )
        try:
            out = _strip_fence(llm.complete(system=_SYSTEM, user=user))
        except Exception:
            out = ""
        new_content = out if out.strip() else original
        reviewed.append({"path": path, "content": new_content})
        if new_content != original:
            changed.append(path)
    return reviewed, changed
