"""Observability: token/cost accounting + structured logging + error capture.

Two concerns live here so the rest of the app stays clean:

1. Cost accounting. Every agent run records the tokens it consumed; this turns
   those tokens into a dollar estimate from a small per-model price table. Groq
   is free (price 0) but we still track tokens so usage is visible and the
   number stays honest the moment we flip to a paid provider.

2. Logging + error capture. `init_observability()` sets up structured logging
   and, if a SENTRY_DSN is configured, wires Sentry so unhandled run failures
   are reported. Both are best-effort: missing Sentry never breaks the app.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("brain_os")

# USD per 1M tokens, (input, output). Groq is free today; keep it at 0 so the
# accounting is correct now and just needs a number when that changes.
_PRICING: dict[str, tuple[float, float]] = {
    "llama-3.3-70b-versatile": (0.0, 0.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-6": (15.0, 75.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """USD estimate for a run's token usage. Unknown models price at 0."""
    in_rate, out_rate = _PRICING.get(model, (0.0, 0.0))
    return round(
        (prompt_tokens / 1_000_000) * in_rate
        + (completion_tokens / 1_000_000) * out_rate,
        6,
    )


_sentry_ready = False


def init_observability() -> None:
    """Configure logging once at startup; enable Sentry if a DSN is present."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return
    global _sentry_ready
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.init(dsn=dsn, traces_sample_rate=0.0)
        _sentry_ready = True
        logger.info("Sentry error tracking enabled")
    except Exception:  # noqa: BLE001 - never let observability setup crash boot
        logger.warning("SENTRY_DSN set but sentry_sdk unavailable; skipping")


def capture_exception(exc: BaseException) -> None:
    """Report an exception to Sentry if enabled; always log it."""
    logger.exception("captured exception: %s", exc)
    if _sentry_ready:
        try:
            import sentry_sdk  # type: ignore

            sentry_sdk.capture_exception(exc)
        except Exception:  # noqa: BLE001
            pass
