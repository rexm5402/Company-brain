"""Observability: cost estimation tests."""
from __future__ import annotations

from app.observability import estimate_cost


def test_groq_is_free():
    cost = estimate_cost("llama-3.3-70b-versatile", 100_000, 20_000)
    assert cost == 0.0


def test_claude_sonnet_nonzero():
    # 1M input @ $3 + 1M output @ $15 = $18
    cost = estimate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert abs(cost - 18.0) < 0.01


def test_unknown_model_is_free():
    cost = estimate_cost("unknown-model-xyz", 999_999, 999_999)
    assert cost == 0.0


def test_zero_tokens_is_zero():
    cost = estimate_cost("claude-sonnet-4-6", 0, 0)
    assert cost == 0.0
