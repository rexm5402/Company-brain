"""AI assist layer.

A home for the focused, single-shot LLM calls that add judgment to the pipeline
(as opposed to the multi-step engineering agent in app/agent). Each function is
defensive: on any failure it returns a safe, neutral default so an AI hiccup can
never crash ticket creation, consensus, or the run flow.

Functions:
- enrich_ticket        (#2) expand a bare ticket into summary + acceptance criteria
- judge_readiness      (#4) is the agreed task concrete enough to build?
- assess_risk          (#6) does this change touch sensitive areas (auth/db/config)?
- summarize_change     (#5) human-facing recap of what shipped
- draft_ticket         (#3) propose a ticket from a free-form discussion
- generate_report      (#7) minutes-of-meeting style wrap-up for a finished ticket
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.agent.llm import LLMClient


def _json_complete(system: str, user: str, llm: Optional[LLMClient]) -> dict[str, Any]:
    """Run a plain completion and tolerantly parse a JSON object from it."""
    llm = llm or LLMClient()
    raw = llm.complete(system=system, user=user)
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


# --- #2 Ticket enrichment ----------------------------------------------
@dataclass
class Enrichment:
    summary: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    clarifying_questions: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        parts: list[str] = []
        if self.summary:
            parts.append(self.summary)
        if self.acceptance_criteria:
            parts.append(
                "**Acceptance criteria**\n"
                + "\n".join(f"- {c}" for c in self.acceptance_criteria)
            )
        if self.clarifying_questions:
            parts.append(
                "**Open questions**\n"
                + "\n".join(f"- {q}" for q in self.clarifying_questions)
            )
        return "\n\n".join(parts)


_ENRICH_SYSTEM = """\
You are a tech lead refining a freshly-filed engineering ticket. Given the title
and (possibly empty) description, produce a crisp expansion an engineer could act
on. Do NOT invent scope the title doesn't imply; keep it tight.

Return ONLY a JSON object:
{
  "summary": string,                  // 1-2 sentence restatement of the goal
  "acceptance_criteria": [string],    // 1-4 concrete, checkable conditions for "done"
  "clarifying_questions": [string]    // 0-3 questions ONLY if genuinely ambiguous; else []
}
"""


def enrich_ticket(
    title: str, description: str, llm: Optional[LLMClient] = None
) -> Enrichment:
    try:
        data = _json_complete(
            _ENRICH_SYSTEM, f"Title: {title}\nDescription: {description or '(none)'}", llm
        )
    except Exception:  # noqa: BLE001 - enrichment is best-effort
        return Enrichment()
    return Enrichment(
        summary=(data.get("summary") or "").strip(),
        acceptance_criteria=[str(c) for c in (data.get("acceptance_criteria") or [])][:4],
        clarifying_questions=[str(q) for q in (data.get("clarifying_questions") or [])][:3],
    )


# --- #4 Readiness gate --------------------------------------------------
@dataclass
class Readiness:
    ready: bool = True
    question: str = ""
    reason: str = ""


_READINESS_SYSTEM = """\
Two teammates have agreed to build something. Before an autonomous agent starts
writing code and opens a real PR, judge whether the agreed task is CONCRETE
enough to implement well — a specific, unambiguous change. If essential
information is missing (which file/where, what exact behavior, etc.), it is NOT
ready, and you must ask ONE short clarifying question to put to the team.

Be pragmatic: small, obvious changes (e.g. "add a LICENSE file", "fix the typo
in the README heading") ARE ready. Only block when a competent engineer truly
couldn't proceed without more detail.

Return ONLY a JSON object:
{
  "ready": boolean,
  "question": string,   // the single clarifying question; empty when ready=true
  "reason": string      // one short sentence
}
"""


def judge_readiness(
    task: str, transcript: str, llm: Optional[LLMClient] = None
) -> Readiness:
    try:
        data = _json_complete(
            _READINESS_SYSTEM,
            f"Agreed task: {task}\n\nConversation:\n{transcript}",
            llm,
        )
    except Exception:  # noqa: BLE001 - on failure, don't block the pipeline
        return Readiness(ready=True)
    if "ready" not in data:  # parsing failed -> fail open (don't block)
        return Readiness(ready=True)
    return Readiness(
        ready=bool(data.get("ready")),
        question=(data.get("question") or "").strip(),
        reason=(data.get("reason") or "").strip(),
    )


# --- #6 Risk / impact triage -------------------------------------------
@dataclass
class Risk:
    level: str = "low"  # low | medium | high
    areas: list[str] = field(default_factory=list)
    needs_human: bool = False
    note: str = ""


_RISK_SYSTEM = """\
You are a release-safety reviewer. Given a task and the list of file paths a
change touched, judge how risky it is to merge without a human review. Sensitive
areas raise risk: authentication/authorization, database migrations/schema,
secrets/credentials, CI or deploy config, payment/billing, anything security
related. Docs, READMEs, comments, and additive non-critical files are low risk.

Return ONLY a JSON object:
{
  "level": "low" | "medium" | "high",
  "areas": [string],        // the sensitive areas touched, if any; else []
  "needs_human": boolean,   // true for medium/high
  "note": string            // one short sentence for humans
}
"""


def assess_risk(
    task: str, file_paths: list[str], llm: Optional[LLMClient] = None
) -> Risk:
    try:
        data = _json_complete(
            _RISK_SYSTEM,
            f"Task: {task}\nFiles changed: {', '.join(file_paths) or '(unknown)'}",
            llm,
        )
    except Exception:  # noqa: BLE001
        return Risk()
    level = (data.get("level") or "low").strip().lower()
    if level not in ("low", "medium", "high"):
        level = "low"
    return Risk(
        level=level,
        areas=[str(a) for a in (data.get("areas") or [])][:6],
        needs_human=bool(data.get("needs_human")) or level in ("medium", "high"),
        note=(data.get("note") or "").strip(),
    )


# --- #5 Change summary --------------------------------------------------
def summarize_change(
    task: str, file_paths: list[str], llm: Optional[LLMClient] = None
) -> str:
    system = (
        "You write a one or two sentence, plain-English recap of a code change "
        "for a team chat. Say what changed and why it matters. No preamble, no "
        "markdown headers. Return only the recap text."
    )
    try:
        text = (llm or LLMClient()).complete(
            system=system,
            user=f"Task: {task}\nFiles changed: {', '.join(file_paths) or '(unknown)'}",
        )
    except Exception:  # noqa: BLE001
        return ""
    return (text or "").strip()


# --- #3 Auto-draft a ticket from discussion ----------------------------
@dataclass
class DraftTicket:
    should_file: bool = False
    title: str = ""
    description: str = ""
    reason: str = ""


_DRAFT_SYSTEM = """\
You watch a team chat and decide whether the discussion has surfaced a concrete
piece of ENGINEERING WORK that deserves to be captured as a ticket (a bug to fix,
a feature to build, a change to make). Casual chat, questions, or vague musings
do NOT warrant a ticket.

Return ONLY a JSON object:
{
  "should_file": boolean,
  "title": string,        // short imperative ticket title; empty if should_file=false
  "description": string,  // 1-2 sentences of context; empty if should_file=false
  "reason": string        // one short sentence
}
"""


def draft_ticket(transcript: str, llm: Optional[LLMClient] = None) -> DraftTicket:
    try:
        data = _json_complete(_DRAFT_SYSTEM, transcript, llm)
    except Exception:  # noqa: BLE001
        return DraftTicket()
    return DraftTicket(
        should_file=bool(data.get("should_file")),
        title=(data.get("title") or "").strip(),
        description=(data.get("description") or "").strip(),
        reason=(data.get("reason") or "").strip(),
    )


# --- #7 Final report (minutes-of-meeting wrap-up) ----------------------
@dataclass
class Report:
    summary: str = ""
    decisions: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    key_contributor: str = ""
    participants: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


_REPORT_SYSTEM = """\
You are writing the closing record (minutes of meeting) for a completed work item.
You are given the ticket details and the full channel transcript that led to a
shipped, merged pull request. Produce a faithful wrap-up.

Return ONLY a JSON object:
{
  "summary": string,            // 2-4 sentences: what was decided and shipped
  "decisions": [string],        // key decisions made in the discussion
  "action_items": [string],     // any follow-ups raised (may be empty)
  "key_contributor": string,    // the person who most drove this to completion, with a brief why
  "participants": [string]      // distinct people who took part
}
"""


def generate_report(
    ticket: dict[str, Any], transcript: str, llm: Optional[LLMClient] = None
) -> Report:
    header = (
        f"Ticket {ticket.get('key')}: {ticket.get('title')}\n"
        f"Assignee: {ticket.get('assignee')} | Reporter: {ticket.get('reporter')}\n"
        f"PR: {ticket.get('pr_url') or '(none)'}\n\n"
        f"Transcript:\n{transcript}"
    )
    try:
        data = _json_complete(_REPORT_SYSTEM, header, llm)
    except Exception:  # noqa: BLE001
        data = {}
    return Report(
        summary=(data.get("summary") or "").strip(),
        decisions=[str(d) for d in (data.get("decisions") or [])],
        action_items=[str(a) for a in (data.get("action_items") or [])],
        key_contributor=(data.get("key_contributor") or "").strip(),
        participants=[str(p) for p in (data.get("participants") or [])],
        raw=data,
    )
