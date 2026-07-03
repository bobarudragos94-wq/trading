"""Anthropic client for the Strategist (S10).

- 60s timeout, exactly one manual retry (SDK-level retries disabled so the
  retry count is deterministic).
- Structured outputs (output_config.format) so the response is guaranteed
  machine-parseable JSON; our own pydantic validation + riskguard clamping
  still run afterwards as the safety net.
- Token usage and cost logged per call to the journal; never logs secrets.
- On any failure after the retry, returns None — the caller falls back to
  the defensive preset and keeps trading rules-based.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time

from strategist import journal
from strategist.verdict_schema import VERDICT_JSON_SCHEMA

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("STRATEGIST_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 1024
TIMEOUT_SECONDS = 60.0

# USD per 1M tokens, used for cost logging only (not billing-accurate for
# every model; keyed by model prefix).
PRICING = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus": (5.00, 25.00),
}


def _price_per_mtok(model: str) -> tuple[float, float]:
    for prefix, price in PRICING.items():
        if model.startswith(prefix):
            return price
    return (0.0, 0.0)


def _system_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "prompts", "strategist_system.md")
    with open(path) as fh:
        return fh.read()


def request_verdict(snapshot: dict, model: str | None = None) -> str | None:
    """One strategist call: snapshot in, raw JSON text out (or None).

    Journals every attempt (S12): input hash, tokens, latency, model, cost.
    """
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic SDK not installed — strategist disabled")
        return None

    if not os.environ.get("ANTHROPIC_API_KEY"):
        journal.append("llm_call_skipped", {"reason": "no ANTHROPIC_API_KEY"})
        logger.warning("no ANTHROPIC_API_KEY — falling back to defensive")
        return None

    model = model or DEFAULT_MODEL
    snapshot_json = json.dumps(snapshot, sort_keys=True, default=str)
    input_hash = hashlib.sha256(snapshot_json.encode()).hexdigest()[:16]
    client = anthropic.Anthropic(timeout=TIMEOUT_SECONDS, max_retries=0)

    last_error: str | None = None
    for attempt in (1, 2):  # spec: one retry on failure
        started = time.monotonic()
        try:
            response = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=_system_prompt(),
                output_config={
                    "format": {"type": "json_schema", "schema": VERDICT_JSON_SCHEMA}
                },
                messages=[{"role": "user", "content": snapshot_json}],
            )
            latency_ms = int((time.monotonic() - started) * 1000)

            if response.stop_reason not in ("end_turn", "stop_sequence"):
                last_error = f"unexpected stop_reason: {response.stop_reason}"
                journal.append("llm_call_failed", {
                    "attempt": attempt, "model": model, "input_hash": input_hash,
                    "error": last_error, "latency_ms": latency_ms,
                })
                continue

            text = next(
                (b.text for b in response.content if b.type == "text"), ""
            )
            usage = response.usage
            in_price, out_price = _price_per_mtok(model)
            cost_usd = (
                usage.input_tokens * in_price + usage.output_tokens * out_price
            ) / 1_000_000
            journal.append("llm_call", {
                "attempt": attempt,
                "model": response.model,
                "input_hash": input_hash,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0),
                "latency_ms": latency_ms,
                "cost_usd": round(cost_usd, 6),
            })
            return text
        except Exception as exc:  # timeout, API error, network — all retryable once
            latency_ms = int((time.monotonic() - started) * 1000)
            last_error = f"{type(exc).__name__}: {exc}"
            journal.append("llm_call_failed", {
                "attempt": attempt, "model": model, "input_hash": input_hash,
                "error": last_error[:300], "latency_ms": latency_ms,
            })
            logger.warning("strategist call attempt %d failed: %s", attempt, exc)

    journal.append("llm_fallback_defensive", {"reason": last_error and last_error[:300]})
    return None
