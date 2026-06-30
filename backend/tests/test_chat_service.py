"""Durable chat service tests.

Covers message persistence, listing, the CAS claim (idempotency), and channel
teardown. These are the core invariants of the durability + concurrency work.
"""
from __future__ import annotations

import time

from app.chat import service as chat_service


CHANNEL = "test-ticket-abc123"


def test_append_and_list():
    chat_service.clear_channel(CHANNEL)
    chat_service.append_message(CHANNEL, "Alice", "hello", is_bot=False)
    chat_service.append_message(CHANNEL, "Bot", "hi", is_bot=True, pr_url="https://x")
    msgs = chat_service.list_messages(CHANNEL)
    assert len(msgs) == 2
    assert msgs[0]["user"] == "Alice"
    assert not msgs[0]["is_bot"]
    assert msgs[1]["is_bot"]
    assert msgs[1]["pr_url"] == "https://x"


def test_humans_filters_bots():
    chat_service.clear_channel(CHANNEL)
    chat_service.append_message(CHANNEL, "Alice", "agree", is_bot=False)
    chat_service.append_message(CHANNEL, "Bot", "working…", is_bot=True)
    chat_service.append_message(CHANNEL, "Bob", "yes", is_bot=False)
    h = chat_service.humans(CHANNEL)
    assert all(not m["is_bot"] for m in h)
    assert len(h) == 2


def test_claim_cas_exactly_once():
    chat_service.clear_channel(CHANNEL)
    chat_service.append_message(CHANNEL, "Alice", "ship it", is_bot=False)
    ts = chat_service.humans(CHANNEL)[-1]["ts"]
    w1 = chat_service.claim(CHANNEL, ts)
    w2 = chat_service.claim(CHANNEL, ts)
    assert w1 is True
    assert w2 is False


def test_claim_older_ts_returns_false():
    chat_service.clear_channel(CHANNEL)
    ts = time.time()
    chat_service.claim(CHANNEL, ts)
    # A claim for an OLDER timestamp should fail (cursor is already past it).
    assert chat_service.claim(CHANNEL, ts - 1) is False


def test_claim_increments_questions():
    chat_service.clear_channel(CHANNEL)
    ts = time.time()
    state_before = chat_service.get_state(CHANNEL)
    assert state_before["questions_asked"] == 0
    chat_service.claim(CHANNEL, ts, increment_question=True)
    state_after = chat_service.get_state(CHANNEL)
    assert state_after["questions_asked"] == 1


def test_claim_draft_independent_cursor():
    chat_service.clear_channel(CHANNEL)
    ts = time.time()
    assert chat_service.claim_draft(CHANNEL, ts) is True
    assert chat_service.claim_draft(CHANNEL, ts) is False
    # Main claim cursor is still at 0 — draft doesn't advance it.
    assert chat_service.get_state(CHANNEL)["consumed_ts"] == 0.0


def test_clear_channel_removes_all():
    chat_service.clear_channel(CHANNEL)
    chat_service.append_message(CHANNEL, "Alice", "hello", is_bot=False)
    ts = time.time()
    chat_service.claim(CHANNEL, ts)
    chat_service.clear_channel(CHANNEL)
    assert chat_service.list_messages(CHANNEL) == []
    state = chat_service.get_state(CHANNEL)
    assert state["consumed_ts"] == 0.0
    assert state["questions_asked"] == 0
