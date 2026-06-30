"""Tests for GetRecentErrorsTool.

Uses httpx.MockTransport to avoid real network calls. No database, no LLM.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import httpx
import pytest

# Ensure env vars present so Settings doesn't error on missing required fields
os.environ.setdefault("GROQ_API_KEY", "test")
os.environ.setdefault("GITHUB_TOKEN", "test")
os.environ.setdefault("GITHUB_REPO", "test/repo")


def _fake_response(status: int, body: object, url: str = "https://sentry.io/api/0/") -> httpx.Response:
    """Build an httpx.Response with the request attribute set (required for raise_for_status)."""
    req = httpx.Request("GET", url)
    resp = httpx.Response(
        status_code=status,
        headers={"Content-Type": "application/json"},
        content=json.dumps(body).encode(),
        request=req,
    )
    return resp


@pytest.fixture()
def _sentry_env(monkeypatch):
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "tok_test")
    monkeypatch.setenv("SENTRY_ORG", "acme")
    monkeypatch.setenv("SENTRY_PROJECT", "backend")
    # Clear settings cache so env changes take effect.
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_returns_issues_when_sentry_configured(_sentry_env):
    issues_payload = [
        {
            "id": "123",
            "title": "TypeError: NoneType has no attribute 'id'",
            "culprit": "app/views.py in get_user",
            "level": "error",
            "count": "42",
            "userCount": 7,
            "firstSeen": "2024-01-01T00:00:00Z",
            "lastSeen": "2024-01-02T00:00:00Z",
            "permalink": "https://sentry.io/acme/backend/issues/123/",
            "status": "unresolved",
        }
    ]
    from app.tools.sentry_tool import GetRecentErrorsTool

    tool = GetRecentErrorsTool()
    with patch("httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.get.return_value = _fake_response(200, issues_payload)
        result = tool.run(query="TypeError")

    assert result.success
    assert result.output is not None
    assert len(result.output["issues"]) == 1
    assert result.output["issues"][0]["title"].startswith("TypeError")


def test_returns_error_when_not_configured(monkeypatch):
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "")
    monkeypatch.setenv("SENTRY_ORG", "")
    monkeypatch.setenv("SENTRY_PROJECT", "")
    from app.config import get_settings
    get_settings.cache_clear()

    from app.tools.sentry_tool import GetRecentErrorsTool

    tool = GetRecentErrorsTool()
    result = tool.run(query="anything")

    assert not result.success
    assert "not configured" in (result.error or "").lower()
    get_settings.cache_clear()


def test_limit_capped_at_20(_sentry_env):
    from app.tools.sentry_tool import GetRecentErrorsTool

    tool = GetRecentErrorsTool()
    captured: list[dict] = []

    def fake_get(url, *, params=None, **kwargs):
        captured.append(params or {})
        return _fake_response(200, [])

    with patch("httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.get.side_effect = fake_get
        tool.run(query="err", limit=999)

    assert captured[0]["limit"] == 20


def test_http_error_returns_failure(_sentry_env):
    from app.tools.sentry_tool import GetRecentErrorsTool

    tool = GetRecentErrorsTool()
    with patch("httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.get.side_effect = httpx.ConnectError("refused")
        result = tool.run(query="crash")

    assert not result.success
    assert result.error is not None
