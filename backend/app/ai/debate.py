"""Multi-Agent Spec Debate — Feature 4.

Before an agent writes a single line of code for a complex ticket, three
specialist agents debate it and produce a shared technical blueprint. The
blueprint is then injected into the build task so the engineering agent codes
with the benefit of that collective reasoning.

Agents:
  Security Agent    — auth, secrets, input validation, OWASP risks
  DB Agent          — schema, query performance, migrations, data integrity
  Frontend Agent    — API contracts, backwards compatibility, UX implications

Flow:
  1. Each agent independently reviews the ticket + chat transcript.
  2. A synthesis agent reads all three opinions and produces a TechSpec.
  3. TechSpec is returned as structured data + a markdown block the caller
     can prepend to the build task description.

The whole debate is 4 LLM calls (3 agents + 1 synthesis). Each uses
LLMClient.complete() — the lightweight path with no tools, just text in / text
out. Total adds ~3-5 seconds on Groq (free, fast) or ~8-12s on Anthropic.

Complexity gate: run_debate() only fires for tickets judged "complex" by a
simple heuristic (long description OR high-risk keywords). Simple tickets
(typo fix, README update, add a constant) skip the debate entirely so we
don't slow down the happy path.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.agent.llm import LLMClient

logger = logging.getLogger(__name__)

# Keywords in the title/description that flag a ticket as complex enough to debate.
_COMPLEX_KEYWORDS = (
    "auth", "oauth", "login", "session", "token", "permission", "rbac",
    "database", "migration", "schema", "index", "query", "performance",
    "payment", "billing", "stripe", "webhook", "refactor", "redesign",
    "architecture", "api", "endpoint", "breaking", "security", "encrypt",
    "secret", "credential", "deploy", "infrastructure", "terraform",
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AgentOpinion:
    agent: str          # "Security" | "Database" | "Frontend"
    concerns: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    risk_level: str = "low"   # low | medium | high
    raw: str = ""


@dataclass
class TechSpec:
    recommended_approach: str = ""
    security_notes: list[str] = field(default_factory=list)
    db_notes: list[str] = field(default_factory=list)
    frontend_notes: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    implementation_order: list[str] = field(default_factory=list)
    opinions: list[AgentOpinion] = field(default_factory=list)
    skipped: bool = False   # True when the heuristic decided not to debate

    def to_markdown(self) -> str:
        """Render as a markdown block to prepend to the build task."""
        if self.skipped:
            return ""
        lines = ["## Technical Spec (pre-agreed by specialist agents)", ""]
        if self.recommended_approach:
            lines += [f"**Recommended approach:** {self.recommended_approach}", ""]
        if self.implementation_order:
            lines += ["**Implementation order:**"]
            lines += [f"{i+1}. {step}" for i, step in enumerate(self.implementation_order)]
            lines += [""]
        if self.security_notes:
            lines += ["**Security:**", *[f"- {n}" for n in self.security_notes], ""]
        if self.db_notes:
            lines += ["**Database:**", *[f"- {n}" for n in self.db_notes], ""]
        if self.frontend_notes:
            lines += ["**API / Frontend:**", *[f"- {n}" for n in self.frontend_notes], ""]
        if self.risks:
            lines += ["**Risks to watch:**", *[f"- {r}" for r in self.risks], ""]
        lines += ["---", ""]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Heuristic: should we debate this ticket?
# ---------------------------------------------------------------------------

def _is_complex(title: str, description: str) -> bool:
    text = (title + " " + description).lower()
    if any(kw in text for kw in _COMPLEX_KEYWORDS):
        return True
    # Long description suggests non-trivial scope
    if len(description.strip()) > 250:
        return True
    return False


# ---------------------------------------------------------------------------
# Individual agent prompts
# ---------------------------------------------------------------------------

_SECURITY_SYSTEM = """\
You are the Security Agent on an AI-powered engineering team. A ticket is about
to be implemented by an autonomous coding agent. Your job is to review it from a
pure security and correctness lens BEFORE any code is written.

Look for: authentication / authorisation gaps, injection risks (SQL, XSS, SSRF,
command injection), insecure defaults, secrets in code or logs, missing input
validation, OWASP Top-10 patterns, and anything that could be abused.

Return a plain-text report (no JSON) in this format:
CONCERNS:
- <one concern per line, or "None" if clean>

RECOMMENDATIONS:
- <one concrete recommendation per line>

RISK: low | medium | high
"""

_DB_SYSTEM = """\
You are the Database Performance Agent on an AI-powered engineering team. A
ticket is about to be implemented. Review it from a data-layer lens before any
code is written.

Look for: missing indexes, N+1 queries, unbounded result sets, schema changes
that need a migration, data integrity risks (missing constraints, nullable
columns used as required), transaction scope issues, and anything that could
degrade DB performance at scale.

Return a plain-text report (no JSON) in this format:
CONCERNS:
- <one concern per line, or "None" if none>

RECOMMENDATIONS:
- <one concrete recommendation per line>

RISK: low | medium | high
"""

_FRONTEND_SYSTEM = """\
You are the API Contract & Frontend Agent on an AI-powered engineering team. A
ticket is about to be implemented. Review it from the perspective of API
consumers and any frontend that depends on these endpoints.

Look for: breaking API changes (renamed/removed fields, changed response shapes),
missing backwards compatibility, endpoints that lack pagination or rate-limiting,
response format inconsistencies, and anything a frontend or mobile client would
need to update.

Return a plain-text report (no JSON) in this format:
CONCERNS:
- <one concern per line, or "None" if none>

RECOMMENDATIONS:
- <one concrete recommendation per line>

RISK: low | medium | high
"""

_SYNTHESIS_SYSTEM = """\
You are the Tech Lead synthesising a pre-implementation debate between three
specialist agents (Security, Database, Frontend/API). Given their individual
reports and the original ticket, produce a concise technical blueprint the
coding agent will follow.

Return ONLY a JSON object:
{
  "recommended_approach": string,       // 1-2 sentences: the agreed strategy
  "security_notes": [string],           // top 0-3 security actions the coder must take
  "db_notes": [string],                 // top 0-3 DB actions
  "frontend_notes": [string],           // top 0-3 API/frontend actions
  "risks": [string],                    // top 0-3 risks to flag in the PR description
  "implementation_order": [string]      // ordered list of implementation steps (3-6)
}

Be concise. If a section has nothing important, use an empty list. Do NOT repeat
the full agent reports — distil only what the coder needs to act on.
"""


# ---------------------------------------------------------------------------
# Per-agent runner
# ---------------------------------------------------------------------------

def _run_agent(
    system: str, agent_name: str, ticket: dict[str, Any], transcript: str, llm: LLMClient
) -> AgentOpinion:
    user = (
        f"Ticket: {ticket.get('title', '')}\n"
        f"Description: {ticket.get('description', '') or ticket.get('details', '') or '(none)'}\n\n"
        f"Discussion so far:\n{transcript or '(no discussion yet)'}"
    )
    try:
        raw = llm.complete(system=system, user=user, temperature=0.3)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Debate agent %s failed: %s", agent_name, exc)
        return AgentOpinion(agent=agent_name, raw="(failed)")

    concerns = _extract_section(raw, "CONCERNS")
    recommendations = _extract_section(raw, "RECOMMENDATIONS")
    risk_line = ""
    for line in raw.splitlines():
        if line.upper().startswith("RISK:"):
            risk_line = line.split(":", 1)[-1].strip().lower()
            break
    risk = risk_line if risk_line in ("low", "medium", "high") else "low"

    return AgentOpinion(
        agent=agent_name,
        concerns=[c.lstrip("- ").strip() for c in concerns if c.strip() and c.strip() != "None"],
        recommendations=[r.lstrip("- ").strip() for r in recommendations if r.strip()],
        risk_level=risk,
        raw=raw,
    )


def _extract_section(text: str, header: str) -> list[str]:
    """Pull bullet lines between `header:` and the next ALL-CAPS header."""
    lines = text.splitlines()
    capturing = False
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith(f"{header}:"):
            capturing = True
            continue
        if capturing:
            # Stop at the next section header (all-caps word followed by colon)
            if re.match(r"^[A-Z][A-Z ]+:", stripped):
                break
            if stripped:
                result.append(stripped)
    return result


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

def _synthesize(
    ticket: dict[str, Any],
    opinions: list[AgentOpinion],
    llm: LLMClient,
) -> dict[str, Any]:
    import json as _json

    debate_text = "\n\n".join(
        f"=== {op.agent} Agent ===\n{op.raw}" for op in opinions
    )
    user = (
        f"Ticket: {ticket.get('title', '')}\n"
        f"Description: {ticket.get('description', '') or '(none)'}\n\n"
        f"Agent reports:\n{debate_text}"
    )
    try:
        raw = llm.complete(system=_SYNTHESIS_SYSTEM, user=user, temperature=0.2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Debate synthesis failed: %s", exc)
        return {}

    try:
        return _json.loads(raw)
    except _json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return _json.loads(match.group(0))
        except _json.JSONDecodeError:
            pass
    return {}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_debate(
    ticket: dict[str, Any],
    transcript: str,
    llm: Optional[LLMClient] = None,
) -> TechSpec:
    """Run the three-agent debate and return a TechSpec.

    Returns TechSpec(skipped=True) immediately for simple tickets so the
    caller doesn't need to check complexity itself.
    """
    title = ticket.get("title", "")
    description = ticket.get("description", "") or ticket.get("details", "") or ""

    if not _is_complex(title, description):
        logger.info("Debate skipped — ticket not complex enough: %s", title)
        return TechSpec(skipped=True)

    llm = llm or LLMClient()
    logger.info("Running multi-agent debate for ticket: %s", title)

    # Run the three specialist agents
    security_op = _run_agent(_SECURITY_SYSTEM, "Security", ticket, transcript, llm)
    db_op = _run_agent(_DB_SYSTEM, "Database", ticket, transcript, llm)
    frontend_op = _run_agent(_FRONTEND_SYSTEM, "Frontend", ticket, transcript, llm)
    opinions = [security_op, db_op, frontend_op]

    # Synthesise into a blueprint
    data = _synthesize(ticket, opinions, llm)

    spec = TechSpec(
        recommended_approach=(data.get("recommended_approach") or "").strip(),
        security_notes=[str(n) for n in (data.get("security_notes") or [])][:3],
        db_notes=[str(n) for n in (data.get("db_notes") or [])][:3],
        frontend_notes=[str(n) for n in (data.get("frontend_notes") or [])][:3],
        risks=[str(r) for r in (data.get("risks") or [])][:3],
        implementation_order=[str(s) for s in (data.get("implementation_order") or [])][:6],
        opinions=opinions,
        skipped=False,
    )
    logger.info(
        "Debate complete. Security=%s DB=%s Frontend=%s",
        security_op.risk_level, db_op.risk_level, frontend_op.risk_level,
    )
    return spec
