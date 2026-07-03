"""All presets must stay within the locked caps (spec §8)."""

from riskguard.guard import LOCKED, PRESETS

from orchestrator.presets import PRESETS as ORCH_PRESETS
from orchestrator.presets import freqtrade_overrides


def test_presets_are_the_riskguard_singletons():
    # the orchestrator must not maintain its own copy that could drift
    assert ORCH_PRESETS is PRESETS


def test_every_preset_within_locked_caps():
    for name, preset in PRESETS.items():
        assert 0 < preset.risk_per_trade_pct <= LOCKED.max_risk_per_trade_pct, name
        assert 0 < preset.max_open_trades <= LOCKED.max_open_trades, name


def test_overrides_never_exceed_caps_even_with_hostile_input():
    hostile = {
        "active_strategy": "TrendRider",
        "risk_mode": "aggressive",
        "risk_per_trade_pct": 50.0,  # should already be clamped, but still
        "max_open_trades": 99,
        "entries_enabled": True,
        "pair_whitelist": ["BTC/USDC"],
    }
    overrides = freqtrade_overrides(hostile)
    assert overrides["max_open_trades"] <= LOCKED.max_open_trades
    assert overrides["santinela"]["risk_per_trade_pct"] <= LOCKED.max_risk_per_trade_pct


def test_flat_keeps_current_strategy_and_disables_entries():
    flat = {
        "active_strategy": "flat",
        "risk_mode": "defensive",
        "risk_per_trade_pct": 0.75,
        "max_open_trades": 2,
        "entries_enabled": True,  # lies — flat must force this off
        "pair_whitelist": [],
    }
    overrides = freqtrade_overrides(flat, current_strategy="MeanRevRanger")
    assert overrides["strategy"] == "MeanRevRanger"
    assert overrides["santinela"]["entries_enabled"] is False
    assert "pairlists" not in overrides  # empty whitelist -> keep dynamic list


def test_unknown_current_strategy_never_written():
    flat = {
        "active_strategy": "flat",
        "risk_mode": "defensive",
        "risk_per_trade_pct": 0.75,
        "max_open_trades": 2,
        "entries_enabled": False,
        "pair_whitelist": [],
    }
    overrides = freqtrade_overrides(flat, current_strategy="Injected'; DROP")
    assert "strategy" not in overrides
