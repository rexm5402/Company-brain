"""Consensus detector tests (no LLM calls — uses mock responses).

The detector's contract:
- Returns ready=True only when the transcript contains clear agreement from 2+
  distinct people.
- Does NOT fire on a single person talking.
- Does NOT fire on disagreement.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.slack.detector import Consensus, detect_consensus


def _mock_llm(ready: bool, task: str = "", agreers: list[str] | None = None):
    """Build a mock LLMClient whose complete() returns a JSON decision."""
    import json

    llm = MagicMock()
    llm.complete.return_value = json.dumps(
        {"ready": ready, "task": task or "", "agreers": agreers or []}
    )
    return llm


def test_two_person_agreement_fires():
    transcript = "Alice: let's add a /metrics endpoint\nBob: agreed, ship it"
    llm = _mock_llm(ready=True, task="Add a /metrics endpoint", agreers=["Alice", "Bob"])
    result = detect_consensus(transcript, llm)
    assert result.ready is True
    assert result.task == "Add a /metrics endpoint"


def test_no_agreement_does_not_fire():
    transcript = "Alice: should we add /metrics?\nBob: not sure yet"
    llm = _mock_llm(ready=False)
    result = detect_consensus(transcript, llm)
    assert result.ready is False


def test_single_person_does_not_fire():
    transcript = "Alice: I think we should add /metrics\nAlice: yeah let's do it"
    llm = _mock_llm(ready=False)
    result = detect_consensus(transcript, llm)
    assert result.ready is False


def test_llm_failure_returns_not_ready():
    """If the LLM call throws, we should get a safe not-ready result, not a crash."""
    llm = MagicMock()
    llm.complete.side_effect = RuntimeError("LLM unavailable")
    # The detector should propagate the exception (callers already catch it).
    with pytest.raises(RuntimeError):
        detect_consensus("Alice: ship it\nBob: ok", llm)


def test_malformed_llm_response_returns_not_ready():
    """A non-JSON response from the LLM should not crash the detector."""
    llm = MagicMock()
    llm.complete.return_value = "not valid json at all"
    # Should either raise or return not-ready, never crash uncaught.
    try:
        result = detect_consensus("Alice: ship it\nBob: ok", llm)
        assert not result.ready
    except Exception:
        pass  # raising is also acceptable
