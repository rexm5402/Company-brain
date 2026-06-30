"""Ticket service unit tests.

Covers creation, lifecycle transitions, and the key safety invariant: you
cannot create a ticket where the assignee and reporter are the same person
(which would allow self-approval of code changes).
"""
from __future__ import annotations

import uuid
import pytest
from unittest.mock import patch, MagicMock

from app.tickets import service as tickets_service
from app.tickets.service import TicketError


def _make(title: str = "Add health endpoint", *, assignee: str = "Alice", reporter: str = "Bob") -> dict:
    with patch("app.tickets.service.assist.enrich_ticket") as mock_enrich:
        mock_enrich.return_value = MagicMock(to_markdown=lambda: None)
        return tickets_service.create_ticket(title, "some description", assignee, reporter)


def test_create_assigns_key():
    t = _make()
    assert t["key"].startswith("TKT-")
    assert t["status"] == "open"
    assert t["assignee"] == "Alice"
    assert t["reporter"] == "Bob"


def test_create_rejects_same_person():
    with pytest.raises(TicketError, match="distinct"):
        _make(assignee="Alice", reporter="alice")  # case-insensitive


def test_create_rejects_blank_title():
    with pytest.raises(TicketError, match="title"):
        _make(title="   ")


def test_list_includes_new_ticket():
    _make(title="Ticket A")
    _make(title="Ticket B")
    items = tickets_service.list_tickets()
    titles = [t["title"] for t in items]
    assert "Ticket A" in titles
    assert "Ticket B" in titles


def test_open_channel_transitions_status():
    t = _make()
    tid = uuid.UUID(t["id"])
    assert t["status"] == "open"
    updated = tickets_service.open_channel(tid)
    assert updated["status"] == "in_progress"
    assert updated["channel"] is not None


def test_set_pr_transitions_to_in_review():
    t = _make()
    tid = uuid.UUID(t["id"])
    tickets_service.open_channel(tid)
    updated = tickets_service.set_pr(tid, "https://github.com/test/repo/pull/42")
    assert updated["status"] == "in_review"
    assert updated["pr_url"] == "https://github.com/test/repo/pull/42"


def test_close_ticket_marks_done():
    t = _make()
    tid = uuid.UUID(t["id"])
    tickets_service.open_channel(tid)
    closed = tickets_service.close_ticket(tid)
    assert closed["status"] == "done"
    assert closed["channel"] is None


def test_get_ticket_returns_none_for_missing():
    result = tickets_service.get_ticket(uuid.uuid4())
    assert result is None
