"""Tests for the watchdog service."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.watchdog import service as watchdog_service


def _sentry_payload(
    title: str = "TypeError: oops",
    culprit: str = "app/auth.py in get_user",
    level: str = "error",
    issue_id: str = "sentry-001",
) -> dict:
    return {
        "data": {
            "issue": {
                "id": issue_id,
                "title": title,
                "culprit": culprit,
                "level": level,
                "permalink": "",
                "count": "1",
                "lastSeen": "2026-01-01T00:00:00Z",
                "metadata": {"type": "TypeError", "value": "oops"},
            }
        }
    }


def _ci_payload(
    conclusion: str = "failure",
    branch: str = "main",
    run_id: str = "ci-001",
) -> dict:
    return {
        "action": "completed",
        "workflow_run": {
            "id": run_id,
            "name": "CI",
            "conclusion": conclusion,
            "head_branch": branch,
            "head_sha": "abc123",
            "html_url": "https://github.com/test/repo/actions/runs/1",
            "head_commit": {"message": "fix: something"},
        },
    }


@patch("app.watchdog.service.resolve_owner", return_value="alice")
@patch("app.watchdog.service._notify_slack")
@patch("app.tickets.service.assist.enrich_ticket")
def test_process_sentry_event_creates_ticket(mock_enrich, mock_slack, mock_owner):
    mock_enrich.return_value = MagicMock(to_markdown=lambda: None)
    ticket = watchdog_service.process_sentry_event(_sentry_payload())
    assert ticket is not None
    assert ticket["source"] == "sentry"
    assert "Sentry" in ticket["title"]


@patch("app.watchdog.service.resolve_owner", return_value="alice")
@patch("app.watchdog.service._notify_slack")
@patch("app.tickets.service.assist.enrich_ticket")
def test_process_sentry_event_skips_low_severity(mock_enrich, mock_slack, mock_owner):
    mock_enrich.return_value = MagicMock(to_markdown=lambda: None)
    result = watchdog_service.process_sentry_event(
        _sentry_payload(level="warning", issue_id="warn-001")
    )
    assert result is None


@patch("app.watchdog.service.resolve_owner", return_value="alice")
@patch("app.watchdog.service._notify_slack")
@patch("app.tickets.service.assist.enrich_ticket")
def test_process_sentry_event_deduplication(mock_enrich, mock_slack, mock_owner):
    mock_enrich.return_value = MagicMock(to_markdown=lambda: None)
    payload = _sentry_payload(issue_id="dup-sentry-002")
    t1 = watchdog_service.process_sentry_event(payload)
    t2 = watchdog_service.process_sentry_event(payload)
    assert t1 is not None
    assert t2 is None  # duplicate skipped


@patch("app.watchdog.service.resolve_owner", return_value="alice")
@patch("app.watchdog.service._notify_slack")
@patch("app.tickets.service.assist.enrich_ticket")
def test_process_github_ci_event_creates_ticket(mock_enrich, mock_slack, mock_owner):
    mock_enrich.return_value = MagicMock(to_markdown=lambda: None)
    ticket = watchdog_service.process_github_ci_event(
        _ci_payload(branch="main"), "workflow_run"
    )
    assert ticket is not None
    assert ticket["source"] == "github_ci"


@patch("app.watchdog.service.resolve_owner", return_value="alice")
@patch("app.watchdog.service._notify_slack")
@patch("app.tickets.service.assist.enrich_ticket")
def test_process_github_ci_event_skips_success(mock_enrich, mock_slack, mock_owner):
    mock_enrich.return_value = MagicMock(to_markdown=lambda: None)
    result = watchdog_service.process_github_ci_event(
        _ci_payload(conclusion="success", run_id="success-001"), "workflow_run"
    )
    assert result is None


@patch("app.watchdog.service.resolve_owner", return_value="alice")
@patch("app.watchdog.service._notify_slack")
@patch("app.tickets.service.assist.enrich_ticket")
@patch("app.watchdog.service.tickets_service.get_by_pr_url")
@patch("app.watchdog.service.chat_service.append_message")
@patch("app.runs.service.run_agent")
def test_process_pr_review_changes_requested(
    mock_agent, mock_chat, mock_get_by_pr, mock_enrich, mock_slack, mock_owner
):
    mock_enrich.return_value = MagicMock(to_markdown=lambda: None)
    import uuid as _uuid
    ticket = {
        "id": str(_uuid.uuid4()),
        "key": "TKT-1",
        "title": "Test ticket",
        "description": "",
        "status": "in_review",
        "assignee": "alice",
        "reporter": "bob",
        "source": "manual",
        "details": None,
        "channel": "tkt-1",
        "pr_url": "https://github.com/test/repo/pull/1",
        "repo_id": None,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    mock_get_by_pr.return_value = ticket
    mock_agent.return_value = MagicMock(pr_url=None, committed_branch="agent/fix", steps=3, model="test", prompt_tokens=0, completion_tokens=0)

    payload = {
        "review": {
            "state": "changes_requested",
            "body": "Please fix the linting errors.",
            "user": {"login": "reviewer"},
        },
        "pull_request": {"html_url": "https://github.com/test/repo/pull/1"},
    }
    result = watchdog_service.process_pr_review_event(payload)
    assert result is not None
    assert mock_chat.called


def test_process_pr_review_ignores_approvals():
    payload = {
        "review": {
            "state": "approved",
            "body": "LGTM",
            "user": {"login": "reviewer"},
        },
        "pull_request": {"html_url": "https://github.com/test/repo/pull/1"},
    }
    result = watchdog_service.process_pr_review_event(payload)
    assert result is None
