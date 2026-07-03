"""Risk presets (defensive / neutral / aggressive).

The canonical values live in ``riskguard.guard.PRESETS`` — the locked
surface — so the orchestrator can never drift from what the guard enforces.
This module only re-exports them and derives the freqtrade-level knobs.
"""

from __future__ import annotations

from riskguard.guard import LOCKED, PRESETS, RiskPreset  # noqa: F401


def freqtrade_overrides(clamped_verdict: dict, current_strategy: str = "TrendRider") -> dict:
    """Build the runtime overrides file content from a CLAMPED verdict.

    The input must already have passed riskguard.clamp_verdict — but the
    values are re-capped here anyway (defense in depth). ``current_strategy``
    is kept loaded when the verdict says ``flat`` (freqtrade always needs a
    strategy; entries are gated off instead).
    """
    strategy = clamped_verdict["active_strategy"]
    entries_enabled = bool(clamped_verdict.get("entries_enabled", False))
    if strategy == "flat":
        strategy_for_config = current_strategy
        entries_enabled = False
    else:
        strategy_for_config = strategy

    overrides: dict = {
        "max_open_trades": min(
            int(clamped_verdict["max_open_trades"]), LOCKED.max_open_trades
        ),
        "santinela": {
            "risk_mode": clamped_verdict["risk_mode"],
            "risk_per_trade_pct": min(
                float(clamped_verdict["risk_per_trade_pct"]),
                LOCKED.max_risk_per_trade_pct,
            ),
            "entries_enabled": entries_enabled,
            "active_strategy": strategy,
            "verdict_id": clamped_verdict.get("verdict_id"),
            "applied_at": clamped_verdict.get("applied_at"),
        },
    }
    if strategy_for_config in ("TrendRider", "MeanRevRanger"):
        overrides["strategy"] = strategy_for_config
    whitelist = clamped_verdict.get("pair_whitelist") or []
    if whitelist:
        overrides["pairlists"] = [{"method": "StaticPairList"}]
        overrides["exchange"] = {"pair_whitelist": whitelist}
    return overrides
