"""Consensus listener.

Watches a Slack channel. When it sees two distinct people agree to implement a
specific change, it distills the task, runs the engineering agent (which opens a
real PR), and posts the result back into the channel thread.

Demo-grade: mutual agreement is treated as the go-ahead. Real authorization
(who is allowed to greenlight, scoped to which repos) is a later layer.

Run:  python -m app.slack.listener "#all-new-workspace"
      (or set SLACK_WATCH_CHANNEL in .env and run with no argument)
"""
from __future__ import annotations

import sys
import time
from typing import Any

from app.agent.llm import LLMClient
from app.config import get_settings
from app.runs import service as runs_service
from app.slack.client import SlackClient, SlackError
from app.slack.detector import detect_consensus

POLL_SECONDS = 4
BUFFER_CAP = 25  # most recent human messages kept as conversation context


def _is_human(msg: dict[str, Any], bot_user_id: str) -> bool:
    if msg.get("subtype"):  # joins, bot_message, edits, etc.
        return False
    if msg.get("bot_id"):
        return False
    if msg.get("user") == bot_user_id:
        return False
    return bool(msg.get("text"))


def _render(buffer: list[dict[str, Any]]) -> tuple[str, dict[str, str]]:
    """Render the buffer as a labelled transcript (Person A/B/…), hiding raw IDs."""
    labels: dict[str, str] = {}
    lines: list[str] = []
    for m in buffer:
        uid = m["user"]
        if uid not in labels:
            labels[uid] = f"Person {chr(ord('A') + len(labels))}"
        lines.append(f"{labels[uid]}: {m['text']}")
    return "\n".join(lines), labels


def run_listener(channel: str) -> None:
    settings = get_settings()
    if not settings.slack_bot_token:
        raise SystemExit("SLACK_BOT_TOKEN is not configured.")

    slack = SlackClient()
    llm = LLMClient()

    me = slack.whoami()
    bot_user_id = me.get("user_id", "")
    channel_id = slack.resolve_channel(channel)
    print(f"[listener] watching {channel} ({channel_id}) as @{me.get('user')}")
    print("[listener] post two messages where two people agree to build something…")

    last_ts = f"{time.time():.6f}"  # ignore history before startup
    seen: set[str] = set()
    buffer: list[dict[str, Any]] = []

    while True:
        try:
            messages = slack.history(channel_id, oldest=last_ts, limit=50)
        except SlackError as exc:
            print(f"[listener] history error: {exc}")
            time.sleep(POLL_SECONDS)
            continue

        new_human = False
        for m in messages:
            ts = m["ts"]
            if float(ts) > float(last_ts):
                last_ts = ts
            if ts in seen:
                continue
            seen.add(ts)
            if _is_human(m, bot_user_id):
                buffer.append({"user": m["user"], "text": m["text"], "ts": ts})
                new_human = True

        buffer[:] = buffer[-BUFFER_CAP:]
        distinct_users = {m["user"] for m in buffer}

        if new_human and len(distinct_users) >= 2:
            transcript, _ = _render(buffer)
            consensus = detect_consensus(transcript, llm)
            print(f"[listener] consensus.ready={consensus.ready} :: {consensus.reason}")
            if consensus.ready and consensus.task:
                trigger_ts = buffer[-1]["ts"]
                _handle_consensus(slack, channel_id, trigger_ts, consensus)
                buffer.clear()  # consume this agreement so it can't refire

        time.sleep(POLL_SECONDS)


def _handle_consensus(
    slack: SlackClient, channel_id: str, thread_ts: str, consensus: Any
) -> None:
    who = " and ".join(consensus.agreers) if consensus.agreers else "the team"
    slack.post(
        channel_id,
        f":robot_face: Consensus detected ({who} agreed). "
        f"Implementing now:\n> {consensus.task}",
        thread_ts=thread_ts,
    )
    print(f"[listener] running agent for task: {consensus.task!r}")
    run = runs_service.run_sync(consensus.task)

    if run.pr_url:
        slack.post(
            channel_id,
            f":white_check_mark: Done — opened a PR with the change:\n{run.pr_url}",
            thread_ts=thread_ts,
        )
        print(f"[listener] PR opened: {run.pr_url}")
        runs_service.watch_ci(
            run.run_id,
            run.pr_url,
            after_step=run.steps,
            on_result=lambda summary: slack.post(
                channel_id, _ci_slack_message(summary), thread_ts=thread_ts
            ),
        )
    else:
        detail = run.final_text or "no PR produced"
        slack.post(
            channel_id,
            f":warning: I couldn't complete that automatically: {detail}",
            thread_ts=thread_ts,
        )
        print(f"[listener] no PR. final_text={run.final_text!r}")


def _ci_slack_message(summary: dict) -> str:
    state = summary.get("state")
    if state == "success":
        return f":white_check_mark: CI passed — all {summary.get('total', 0)} check(s) green."
    if state == "failure":
        failed = [
            c["name"]
            for c in summary.get("checks", [])
            if c.get("conclusion") not in ("success", "neutral", "skipped", None)
        ]
        detail = ", ".join(failed) or "see the PR checks"
        return f":x: CI failed — {detail}. The change needs a fix before merge."
    if state == "pending":
        return ":hourglass_flowing_sand: CI still running — check the PR for status."
    if state == "unknown":
        return ":warning: Couldn't read CI status (token lacks the Checks permission)."
    return ":information_source: No CI configured on this repo yet."


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else get_settings().slack_watch_channel
    if not arg:
        raise SystemExit(
            "Usage: python -m app.slack.listener '#channel'  "
            "(or set SLACK_WATCH_CHANNEL in .env)"
        )
    try:
        run_listener(arg)
    except KeyboardInterrupt:
        print("\n[listener] stopped.")
