"""Tests for the multi-agent spec debate."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.ai.debate import TechSpec, _is_complex, run_debate


def test_is_complex_short_title_not_complex():
    assert _is_complex("Fix typo in README", "") is False


def test_is_complex_long_description_is_complex():
    long_desc = "This is a detailed description. " * 10  # >250 chars
    assert _is_complex("Update config", long_desc) is True


def test_is_complex_keyword_triggers():
    assert _is_complex("Add auth endpoint", "") is True
    assert _is_complex("Database migration", "") is True
    assert _is_complex("Security review", "") is True


def test_run_debate_skips_simple_ticket():
    ticket = {
        "title": "Fix typo",
        "description": "",
        "id": "00000000-0000-0000-0000-000000000001",
    }
    spec = run_debate(ticket, "looks good")
    assert spec.skipped is True


@patch("app.ai.debate.LLMClient")
def test_run_debate_calls_llm_four_times(mock_llm_cls):
    """Four LLM calls: Security, DB, Frontend, then Synthesis."""
    mock_llm = MagicMock()
    mock_llm_cls.return_value = mock_llm
    mock_llm.complete.return_value = (
        "CONCERNS:\n- None\nRECOMMENDATIONS:\n- Use prepared statements\nRISK: low"
    )

    ticket = {
        "title": "Add OAuth login endpoint",
        "description": "Implement OAuth2 authentication flow.",
        "id": "00000000-0000-0000-0000-000000000002",
    }
    spec = run_debate(ticket, "Looks good to implement")
    assert mock_llm.complete.call_count == 4
    assert spec.skipped is False


@patch("app.ai.debate.LLMClient")
def test_run_debate_degrades_on_failure(mock_llm_cls):
    """If LLM calls fail, run_debate should return a skipped/empty spec gracefully."""
    mock_llm = MagicMock()
    mock_llm_cls.return_value = mock_llm
    mock_llm.complete.side_effect = Exception("LLM timeout")

    ticket = {
        "title": "Add OAuth security token",
        "description": "Auth token security review.",
        "id": "00000000-0000-0000-0000-000000000003",
    }
    # Should not raise; degraded spec expected
    spec = run_debate(ticket, "Let's do this")
    assert isinstance(spec, TechSpec)
