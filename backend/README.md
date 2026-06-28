# Company Brain OS — Engineering Agent (backend, Weekend 1)

A single agent that takes a task, makes a code change, and opens a **real PR**
on GitHub — with every tool call written to a Postgres audit log.

Provider-agnostic LLM layer: runs on **Groq's free API** today (no org/billing),
flips to **Anthropic** later via a single env var.

## Setup

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill it in
```

Fill in `.env`:
- `GROQ_API_KEY` — free from https://console.groq.com/keys
- `GITHUB_TOKEN` — fine-grained PAT scoped to ONLY your test repo
  (Contents + Pull requests = read/write)
- `GITHUB_REPO` — e.g. `yourhandle/agent-sandbox`
- `DATABASE_URL` — local Postgres (install: `brew install postgresql@16`)

## Database

```bash
createdb brain_os          # one-time
alembic upgrade head       # creates the audit_log table
```

## Run the agent (Weekend 1 DoD)

```bash
python -m app.cli "add a CONTRIBUTING.md with a short setup section"
```

On success it prints the PR URL and the `run_id`. Inspect the audit trail:

```sql
SELECT step, tool_name, success, latency_ms FROM audit_log
WHERE run_id = '<run_id>' ORDER BY step;
```

## API (read-only for now)

```bash
uvicorn app.main:app --reload
# GET /health
# GET /audit/{run_id}
```

## Switching to Anthropic later

In `.env`: set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=...`.
No code changes — the tool-calling differences are handled in `app/agent/llm.py`.

## Layout

```
app/
  config.py            # settings / secrets (read here only)
  db.py                # SQLAlchemy engine + session
  main.py              # FastAPI (health + audit read)
  cli.py               # `python -m app.cli "<task>"`
  agent/
    llm.py             # provider-agnostic tool-calling (Groq | Anthropic)
    system_prompt.py
    loop.py            # plan -> call tool -> observe -> repeat -> finish
  tools/
    base.py            # Tool contract (explicit JSON schema per tool)
    github_tool.py     # open_pull_request (full file contents, not diffs)
    registry.py
  audit/
    models.py          # audit_log (source of truth)
    recorder.py        # logs BEFORE returning to the loop
alembic/               # migrations
```

## Scope

Weekend 1 only: agent loop + GitHub tool + audit log, end-to-end from the CLI.
Slack, pgvector `read_repo_context`, and the Next.js dashboard are W2/W3.
