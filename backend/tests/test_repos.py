"""Tests for the repos service."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.repos import service as repos_service
from app.tickets import service as tickets_service


def test_create_repo():
    r = repos_service.create_repo("My Repo", "acme", "acme/my-repo")
    assert r["name"] == "My Repo"
    assert r["owner"] == "acme"
    assert r["slug"] == "acme/my-repo"
    assert r["has_token_override"] is False
    assert "id" in r


def test_create_repo_with_token_override():
    r = repos_service.create_repo("Secure Repo", "acme", "acme/secure", "ghp_secret")
    assert r["has_token_override"] is True
    # Token must never be in the serialized output
    assert "github_token_override" not in r
    assert "ghp_secret" not in str(r)


def test_create_repo_duplicate_slug_raises():
    repos_service.create_repo("First", "acme", "acme/dup-slug")
    with pytest.raises(ValueError, match="already exists"):
        repos_service.create_repo("Second", "acme", "acme/dup-slug")


def test_list_repos():
    repos_service.create_repo("Repo A", "owner", "owner/repo-a")
    repos_service.create_repo("Repo B", "owner", "owner/repo-b")
    items = repos_service.list_repos()
    slugs = [r["slug"] for r in items]
    assert "owner/repo-a" in slugs
    assert "owner/repo-b" in slugs


def test_get_repo_missing_returns_none():
    result = repos_service.get_repo(uuid.uuid4())
    assert result is None


def test_get_primary_repo_empty():
    result = repos_service.get_primary_repo()
    assert result is None


def test_get_primary_repo_returns_first():
    r1 = repos_service.create_repo("First", "o", "o/first")
    repos_service.create_repo("Second", "o", "o/second")
    primary = repos_service.get_primary_repo()
    assert primary is not None
    assert primary["slug"] == r1["slug"]


def test_ticket_with_repo_id():
    repo = repos_service.create_repo("TR", "owner", "owner/tr")
    rid = uuid.UUID(repo["id"])
    with patch("app.tickets.service.assist.enrich_ticket") as mock_enrich:
        mock_enrich.return_value = MagicMock(to_markdown=lambda: None)
        t = tickets_service.create_ticket(
            "Ticket with repo", "", "alice", "bob", repo_id=rid
        )
    assert t["repo_id"] == repo["id"]
