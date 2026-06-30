"""Tests for the notifications service."""
from __future__ import annotations

import uuid

from app.notifications import service as notif_service


def test_create_notification():
    n = notif_service.create(
        recipient="alice",
        type="ticket_assigned",
        title="You were assigned TKT-1",
        body="Check the ticket.",
    )
    assert n["recipient"] == "alice"
    assert n["type"] == "ticket_assigned"
    assert n["read"] is False


def test_list_for_user():
    notif_service.create(recipient="bob", type="watchdog", title="New incident")
    notif_service.create(recipient="bob", type="pr_opened", title="PR opened")
    notif_service.create(recipient="carol", type="watchdog", title="Other user")
    items = notif_service.list_for_user("bob")
    assert len(items) >= 2
    assert all(n["recipient"] == "bob" for n in items)


def test_unread_count():
    notif_service.create(recipient="dave", type="watchdog", title="Unread 1")
    notif_service.create(recipient="dave", type="watchdog", title="Unread 2")
    count = notif_service.unread_count("dave")
    assert count >= 2


def test_mark_read():
    n = notif_service.create(recipient="eve", type="watchdog", title="Mark me read")
    nid = uuid.UUID(n["id"])
    assert notif_service.mark_read(nid) is True
    items = notif_service.list_for_user("eve", unread_only=True)
    ids = [i["id"] for i in items]
    assert n["id"] not in ids


def test_mark_all_read():
    notif_service.create(recipient="frank", type="watchdog", title="One")
    notif_service.create(recipient="frank", type="watchdog", title="Two")
    marked = notif_service.mark_all_read("frank")
    assert marked >= 2
    assert notif_service.unread_count("frank") == 0
