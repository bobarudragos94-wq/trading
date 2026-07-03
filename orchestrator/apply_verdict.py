"""Apply a clamped verdict to the running Freqtrade bot.

Pipeline (S10, S12):

    raw LLM text -> parse_verdict (pydantic) -> riskguard.clamp_verdict
        -> overrides.json (atomic write) -> freqtrade /reload_config
        -> /stopentry when flat

Any failure at any stage degrades to the defensive preset — never to an
unguarded state — and everything is journaled.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from riskguard.guard import check_breakers, clamp_verdict
from riskguard.state import GuardState

from orchestrator.freqtrade_api import FreqtradeAPI
from orchestrator.presets import freqtrade_overrides
from strategist import journal
from strategist.verdict_schema import parse_verdict

logger = logging.getLogger(__name__)

OVERRIDES_PATH = Path(
    os.environ.get("SANTINELA_OVERRIDES", "user_data/runtime/overrides.json")
)

DEFENSIVE_FALLBACK = {
    "regime": "risk_off",
    "risk_mode": "defensive",
    "active_strategy": None,  # filled with current strategy at apply time
    "pair_whitelist": [],
    "confidence": 0.0,
    "rationale": "fallback: strategist unavailable or verdict invalid",
}


def current_overrides() -> dict:
    try:
        return json.loads(OVERRIDES_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _write_overrides(data: dict) -> None:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(OVERRIDES_PATH.parent), prefix=".overrides-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=4)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, OVERRIDES_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply_raw_verdict(
    raw_text: str | None,
    universe: list,
    guard_state: GuardState,
    api: FreqtradeAPI | None = None,
    now: datetime | None = None,
) -> dict:
    """Validate, clamp and apply. Returns the clamped verdict that was applied."""
    now = now or datetime.now(timezone.utc)
    api = api or FreqtradeAPI()
    prev = current_overrides()
    current_strategy = prev.get("strategy", "TrendRider")

    verdict_dict: dict
    if raw_text is None:
        verdict_dict = dict(DEFENSIVE_FALLBACK)
        verdict_dict["active_strategy"] = current_strategy
        parse_error = "no LLM response"
    else:
        verdict, parse_error = parse_verdict(raw_text)
        if verdict is None:
            journal.append("verdict_rejected", {"error": parse_error,
                                                "raw": str(raw_text)[:500]})
            verdict_dict = dict(DEFENSIVE_FALLBACK)
            verdict_dict["active_strategy"] = current_strategy
            verdict_dict["rationale"] = f"fallback: {parse_error}"[:600]
        else:
            verdict_dict = verdict.model_dump()

    breakers = check_breakers(guard_state, now)
    clamped = clamp_verdict(
        verdict_dict, universe, breakers=breakers, current_strategy=current_strategy
    )
    clamped["verdict_id"] = uuid.uuid4().hex[:12]
    clamped["applied_at"] = now.isoformat()

    journal.append("verdict", {
        "verdict_id": clamped["verdict_id"],
        "raw_verdict": verdict_dict,
        "parse_error": parse_error,
        "clamped": {k: v for k, v in clamped.items() if k != "rationale"},
        "rationale": clamped.get("rationale", ""),
        "breakers_active": breakers.any_active,
    })

    overrides = freqtrade_overrides(clamped, current_strategy=current_strategy)
    _write_overrides(overrides)

    # Push to the running bot. Transport errors leave the file in place —
    # the next reload (or restart) picks it up; the strategy-side gates in
    # santinela config are already re-read from the file by riskguard-aware
    # code paths.
    try:
        api.reload_config()
        if not clamped["entries_enabled"]:
            api.stopentry()
        journal.append("verdict_applied", {"verdict_id": clamped["verdict_id"]})
    except Exception as exc:
        logger.error("failed to push verdict to freqtrade: %s", exc)
        journal.append("verdict_apply_failed", {
            "verdict_id": clamped["verdict_id"], "error": str(exc)[:300],
        })
    return clamped
