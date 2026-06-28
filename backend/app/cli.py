"""CLI entrypoint for Weekend 1.

    python -m app.cli "add a /health endpoint that returns {status: ok}"

Runs the agent loop end-to-end: the agent opens a real PR and every tool call
is written to the audit_log table. Prints the PR URL and run id on success.
"""
from __future__ import annotations

import sys

from app.agent.loop import run_agent


def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: python -m app.cli "<task description>"')
        return 2

    task = " ".join(sys.argv[1:])
    print(f"Task: {task}\n")

    run = run_agent(task)

    print("\n--- Run summary ---")
    print(f"run_id: {run.run_id}")
    print(f"steps:  {run.steps}")
    for entry in run.transcript:
        print(f"  [step {entry['step']}] {entry['reasoning']}")
    if run.pr_url:
        print(f"\nPR opened: {run.pr_url}")
    else:
        print(f"\nNo PR opened. Final message: {run.final_text}")
    print(f"\nAudit trail: SELECT * FROM audit_log WHERE run_id = '{run.run_id}';")
    return 0 if run.pr_url else 1


if __name__ == "__main__":
    raise SystemExit(main())
