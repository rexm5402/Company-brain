# Project: Company Brain OS — Engineering Agent (v1)

## What this is
A single AI agent that does real engineering work, not just chat about it.
Given a task, it reads relevant context, opens a real PR on GitHub, and posts
status to Slack. This is the wedge for a larger "Company Brain OS" concept —
v1 proves one agent can *execute*, not just retrieve/summarize.

**North star for v1:** a 90-second demo of the agent opening a real PR and
notifying a Slack channel, with a visible audit trail of every step it took.

## Current scope — DO NOT EXCEED THIS YET
This is a weekend project, not the full platform. Explicitly out of scope
until v1 is working end-to-end:
- Multiple agents / agent marketplace / agent-to-agent comms
- Neo4j, Qdrant, Kafka, Temporal, gRPC, GraphQL
- Recruiting/Sales/Marketing/DevOps agents — only the Engineering Agent exists
- Notion/Jira/Confluence/Gmail/Drive integrations — only GitHub + Slack
- Full RBAC system — a simple scoped-token + audit-log table is enough

If a task seems to require any of the above, stop and flag it instead of
building it.

## Cost posture (v1)
Run **free** first. LLM provider is **Groq** (free API, no org/billing) via its
OpenAI-compatible endpoint. The LLM layer is provider-agnostic — flip
`LLM_PROVIDER=anthropic` once Anthropic billing/org is set up. No code change.

## Tech stack
- **Backend:** FastAPI, PostgreSQL (+ `pgvector` extension for embeddings, W2)
- **LLM:** Groq (OpenAI-compatible) now → Anthropic later. One client
  abstraction (`app/agent/llm.py`) hides the tool-calling schema differences.
- **Agent loop:** direct tool-calling loop (LangGraph only if a step genuinely
  needs resumable multi-turn state)
- **Frontend:** Next.js + TypeScript, Tailwind, shadcn/ui (W3)
- **Realtime:** Server-Sent Events (SSE) or WebSocket (W3)
- **Integrations:** GitHub REST API, Slack Web API (W2)
- **Audit:** Postgres table logging every tool call (input, output, latency,
  timestamp, success/failure)

## Repo structure (target)
```
/backend
  /app
    /agent          # agent loop, system prompt, llm client, tool defs
    /tools          # github_tool.py, (slack_tool.py W2)
    /memory         # embedding + pgvector retrieval for repo docs (W2)
    /audit          # audit log models + recorder
    main.py         # FastAPI app
  /alembic          # migrations
/frontend           # W3
/CLAUDE.md
```

## Agent design notes
- System prompt states the agent's *goal*, available tools, and an explicit
  instruction to log its reasoning before each tool call.
- Tools to implement, in order:
  1. `open_pull_request(branch, title, description, files)` — DONE (W1).
     Agent supplies FULL file contents per file, NOT a diff (diffs are the #1
     cause of failed PR creation; we let GitHub compute the diff).
  2. `post_slack_message(channel, text)` — W2
  3. `read_repo_context(query)` — pgvector lookup over embedded docs — W2
  4. `comment_on_pr(pr_number, text)` — W2
- Every tool call must write a row to the audit log *before* returning its
  result to the agent loop — never log after the fact. (`audit/recorder.py`)
- Keep the agent loop simple: plan → call tool → observe → repeat → finish.

## Security (minimal but real)
- One scoped API token per integration (GitHub fine-grained PAT scoped to the
  test repo only, Slack bot token), stored in `.env`, never logged.
- Secrets are read only in `app/config.py`.
- Audit log table is the source of truth for "what did the agent do" — the
  dashboard timeline reads from this table, not from agent output text.

## Environment variables
See `backend/.env.example`. Key ones:
```
LLM_PROVIDER=groq          # groq | anthropic
GROQ_API_KEY=
DATABASE_URL=
GITHUB_TOKEN=
GITHUB_REPO=
SLACK_BOT_TOKEN=           # W2
```

## Commands
```bash
# backend
uvicorn app.main:app --reload
python -m app.cli "<task description>"   # run the agent

# migrations
alembic upgrade head

# frontend (W3)
npm run dev
```

## Roadmap
- **Weekend 1 (built):** Agent loop + GitHub `open_pull_request` tool + audit
  log. End-to-end from CLI (no frontend). DoD: agent takes a task and opens a
  real PR on a test repo, with a full audit trail in Postgres.
- **Weekend 2:** Slack tool. Embed repo docs into pgvector, wire
  `read_repo_context`. Agent answers "how do we deploy" before acting.
- **Weekend 3:** Next.js dashboard — live SSE/WebSocket stream of agent steps +
  audit trail view. Demo surface for the YC application.

## Code conventions
- Python: type hints everywhere, `ruff` for linting, no bare `except:`
- Commit small, working increments — each commit leaves the agent loop runnable
- Prefer explicit tool schemas (JSON schema per tool) over loosely-typed dicts
