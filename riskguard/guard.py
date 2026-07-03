"""RiskGuard core — locked limits S1-S12 and the breaker state machine.

LOCKED SURFACE. Every constant below is deliberately a frozen dataclass
field on a module-level singleton. There is no setter, no config hook, no
environment override. The LLM verdict, Telegram commands and hot config
reloads all pass through ``clamp_verdict()`` / ``can_open_new_trade()`` and
can only ever make the system MORE conservative than these caps.

Pure Python, stdlib only, zero network calls, injectable clock (every
time-dependent function takes ``now``) — so the whole breaker state machine
is unit-testable at exact boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from riskguard.state import GuardState

UTC = timezone.utc


@dataclass(frozen=True)
class LockedLimits:
    """S1-S8 hard caps + verdict bounds (§6.4). Frozen — see module docstring."""

    trading_mode: str = "spot"  # S1: spot only, no margin/futures/shorts
    max_risk_per_trade_pct: float = 2.0  # S2: stop distance x size <= 2% equity
    max_open_trades: int = 4  # S3
    max_total_exposure_pct: float = 60.0  # S4: sum of open stakes <= 60% equity
    stoploss_required: bool = True  # S5: hard stoploss on every position
    daily_loss_limit_pct: float = 5.0  # S6: -5% in rolling 24h ...
    daily_loss_pause_hours: int = 24  # ... pauses new entries for 24h
    max_drawdown_pct: float = 20.0  # S7: -20% from HWM -> kill switch
    max_consecutive_losses: int = 6  # S8: 6 losses in a row ...
    consecutive_loss_pause_hours: int = 12  # ... pauses new entries for 12h
    max_position_pct: float = 20.0  # §6.1: single position <= 20% equity
    whitelist_min: int = 2  # §6.4 verdict whitelist bounds
    whitelist_max: int = 8
    min_confidence: float = 0.4  # below this the verdict is forced defensive


LOCKED = LockedLimits()


@dataclass(frozen=True)
class RiskPreset:
    name: str
    risk_per_trade_pct: float
    max_open_trades: int


# Canonical presets (§6.4). orchestrator/presets.py re-exports these; the
# tests assert every preset stays within LOCKED caps.
PRESETS: Mapping[str, RiskPreset] = {
    "defensive": RiskPreset("defensive", 0.75, 2),
    "neutral": RiskPreset("neutral", 1.25, 3),
    "aggressive": RiskPreset(
        "aggressive", LOCKED.max_risk_per_trade_pct, LOCKED.max_open_trades
    ),
}

VALID_REGIMES = frozenset({"trend", "range", "high_volatility", "risk_off"})
VALID_RISK_MODES = frozenset(PRESETS)
VALID_STRATEGIES = frozenset({"TrendRider", "MeanRevRanger", "flat"})

_S6_WINDOW = timedelta(hours=24)
_SAMPLE_RETENTION = timedelta(hours=48)


@dataclass(frozen=True)
class BreakerStatus:
    s6_active: bool = False
    s6_until: str | None = None
    s7_killed: bool = False
    s8_active: bool = False
    s8_until: str | None = None

    @property
    def any_active(self) -> bool:
        return self.s6_active or self.s7_killed or self.s8_active

    @property
    def entries_allowed(self) -> bool:
        return not self.any_active


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reasons: tuple = ()


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def check_breakers(state: GuardState, now: datetime) -> BreakerStatus:
    """Evaluate S6/S7/S8 from persisted state at time ``now``."""
    s6_until = state.s6_until
    s6_active = s6_until is not None and now < _parse_ts(s6_until)
    s8_until = state.s8_until
    s8_active = s8_until is not None and now < _parse_ts(s8_until)
    return BreakerStatus(
        s6_active=s6_active,
        s6_until=s6_until if s6_active else None,
        s7_killed=state.killed,
        s8_active=s8_active,
        s8_until=s8_until if s8_active else None,
    )


def record_equity(state: GuardState, equity: float, now: datetime) -> list:
    """Record an equity sample; update HWM; evaluate S6 and S7.

    Returns a list of event strings ("S6_TRIPPED", "S7_KILLED", ...) so the
    caller can journal and alert. Mutates ``state``; caller persists it.
    """
    events: list = []
    if not isinstance(equity, (int, float)) or equity <= 0:
        return ["INVALID_EQUITY_SAMPLE"]
    equity = float(equity)

    state.last_equity = equity
    state.updated_at = now.isoformat()
    state.equity_samples.append([now.isoformat(), equity])
    cutoff = now - _SAMPLE_RETENTION
    state.equity_samples = [
        s for s in state.equity_samples if _parse_ts(s[0]) >= cutoff
    ]

    if equity > state.hwm:
        state.hwm = equity

    # S7 — max drawdown kill switch (trips at exactly -20.0%).
    drawdown_pct = (equity - state.hwm) / state.hwm * 100.0
    if drawdown_pct <= -LOCKED.max_drawdown_pct and not state.killed:
        state.killed = True
        state.killed_at = now.isoformat()
        events.append("S7_KILLED")

    # S6 — rolling-24h loss breaker (trips at exactly -5.0%).
    already_paused = state.s6_until is not None and now < _parse_ts(state.s6_until)
    if not already_paused:
        # samples is never empty here (the current sample was just added)
        # and every stored equity is > 0, so the baseline is always valid.
        baseline = _s6_baseline(state.equity_samples, now)
        change_pct = (equity - baseline) / baseline * 100.0
        if change_pct <= -LOCKED.daily_loss_limit_pct:
            state.s6_until = (
                now + timedelta(hours=LOCKED.daily_loss_pause_hours)
            ).isoformat()
            events.append("S6_TRIPPED")
    return events


def _s6_baseline(samples: Sequence, now: datetime) -> float:
    """Reference equity for the rolling 24h window.

    The newest sample that is at least 24h old; with less than 24h of
    history, the oldest available sample (conservative: a -5% move during
    the first hours still trips the breaker).
    """
    window_start = now - _S6_WINDOW
    older = [s for s in samples if _parse_ts(s[0]) <= window_start]
    if older:
        return float(older[-1][1])
    return float(samples[0][1])


def record_trade_result(state: GuardState, profit_abs: float, now: datetime) -> list:
    """Track closed-trade results for the S8 consecutive-loss brake."""
    events: list = []
    if profit_abs < 0:
        state.consecutive_losses += 1
        if state.consecutive_losses >= LOCKED.max_consecutive_losses:
            already_paused = state.s8_until is not None and now < _parse_ts(state.s8_until)
            if not already_paused:
                state.s8_until = (
                    now + timedelta(hours=LOCKED.consecutive_loss_pause_hours)
                ).isoformat()
                events.append("S8_TRIPPED")
            state.consecutive_losses = 0
    else:
        state.consecutive_losses = 0
    state.updated_at = now.isoformat()
    return events


def can_open_new_trade(
    state: GuardState,
    now: datetime,
    open_trades: int,
    current_exposure: float,
    proposed_stake: float,
    equity: float,
    has_stoploss: bool = True,
) -> Decision:
    """The single gate every new entry must pass (S3, S4, S5, S6, S7, S8).

    ``current_exposure`` and ``proposed_stake`` are in stake currency.
    """
    reasons: list = []
    breakers = check_breakers(state, now)
    if breakers.s7_killed:
        reasons.append("S7: kill switch active — manual restart required")
    if breakers.s6_active:
        reasons.append(f"S6: daily-loss pause active until {breakers.s6_until}")
    if breakers.s8_active:
        reasons.append(f"S8: consecutive-loss pause active until {breakers.s8_until}")
    if open_trades + 1 > LOCKED.max_open_trades:
        reasons.append(f"S3: max {LOCKED.max_open_trades} concurrent trades")
    if equity <= 0:
        reasons.append("invalid equity")
    elif proposed_stake <= 0:
        reasons.append("invalid stake")
    else:
        exposure_cap = equity * LOCKED.max_total_exposure_pct / 100.0
        if current_exposure + proposed_stake > exposure_cap + 1e-9:
            reasons.append(
                f"S4: exposure {current_exposure + proposed_stake:.2f} would exceed "
                f"{LOCKED.max_total_exposure_pct:.0f}% of equity ({exposure_cap:.2f})"
            )
    if LOCKED.stoploss_required and not has_stoploss:
        reasons.append("S5: entry without a hard stoploss is forbidden")
    return Decision(allowed=not reasons, reasons=tuple(reasons))


def position_stake(
    equity: float,
    entry_price: float,
    stop_price: float,
    risk_per_trade_pct: float,
    max_stake: float | None = None,
) -> float:
    """Risk-based position size (S2) with the 20% per-position cap (§6.1).

    Sizes the position so that (entry - stop) x amount == risk budget,
    where the risk budget is capped at 2% of equity no matter what the
    caller asks for. Returns the stake in stake-currency; 0.0 refuses.
    """
    if equity <= 0 or entry_price <= 0 or stop_price <= 0:
        return 0.0
    if stop_price >= entry_price:  # spot longs only (S1); stop must be below
        return 0.0
    risk_pct = min(float(risk_per_trade_pct), LOCKED.max_risk_per_trade_pct)
    if risk_pct <= 0:
        return 0.0
    risk_budget = equity * risk_pct / 100.0
    stop_distance = entry_price - stop_price
    amount = risk_budget / stop_distance
    stake = amount * entry_price
    stake = min(stake, equity * LOCKED.max_position_pct / 100.0)
    if max_stake is not None:
        stake = min(stake, max(0.0, max_stake))
    return stake


def clamp_verdict(
    raw: Any,
    universe: Sequence[str],
    breakers: BreakerStatus | None = None,
    current_strategy: str = "TrendRider",
) -> dict:
    """Clamp an LLM verdict into hard bounds (S10, §6.4). Never raises.

    ``raw`` may be anything the LLM (or a bug) produced. ``universe`` is the
    exchange-filtered pair universe the whitelist must stay inside, ordered
    by quote volume. Returns the clamped verdict dict, including the applied
    preset numbers and ``clamp_notes`` describing every correction.
    """
    notes: list = []
    v: Mapping = raw if isinstance(raw, Mapping) else {}
    if not isinstance(raw, Mapping):
        notes.append("verdict_not_a_mapping->full_defensive_fallback")

    regime = v.get("regime")
    if regime not in VALID_REGIMES:
        notes.append(f"invalid_regime:{regime!r}->risk_off")
        regime = "risk_off"

    risk_mode = v.get("risk_mode")
    if risk_mode not in VALID_RISK_MODES:
        notes.append(f"invalid_risk_mode:{risk_mode!r}->defensive")
        risk_mode = "defensive"

    strategy = v.get("active_strategy")
    if strategy not in VALID_STRATEGIES:
        if current_strategy not in VALID_STRATEGIES:
            current_strategy = "flat"
        notes.append(f"invalid_strategy:{strategy!r}->keep:{current_strategy}")
        strategy = current_strategy

    try:
        confidence = float(v.get("confidence"))
    except (TypeError, ValueError):
        notes.append("invalid_confidence->0.0")
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))
    if confidence < LOCKED.min_confidence and risk_mode != "defensive":
        notes.append(f"low_confidence:{confidence:.2f}->defensive")
        risk_mode = "defensive"

    whitelist = _clamp_whitelist(v.get("pair_whitelist"), universe, notes)

    rationale = str(v.get("rationale", ""))[:600]

    entries_enabled = strategy != "flat"
    if breakers is not None and breakers.any_active:
        if strategy != "flat":
            notes.append("breakers_active->flat")
        strategy = "flat"
        entries_enabled = False

    preset = PRESETS[risk_mode]
    return {
        "regime": regime,
        "risk_mode": risk_mode,
        "active_strategy": strategy,
        "pair_whitelist": whitelist,
        "confidence": confidence,
        "rationale": rationale,
        "risk_per_trade_pct": min(preset.risk_per_trade_pct, LOCKED.max_risk_per_trade_pct),
        "max_open_trades": min(preset.max_open_trades, LOCKED.max_open_trades),
        "entries_enabled": entries_enabled,
        "clamp_notes": notes,
    }


def _clamp_whitelist(raw: Any, universe: Sequence[str], notes: list) -> list:
    """Whitelist must be a 2-8 pair subset of the filtered universe (§6.4)."""
    universe = [p for p in universe if isinstance(p, str)]
    if not isinstance(raw, (list, tuple)):
        notes.append("invalid_whitelist->universe_top")
        raw = []
    allowed = set(universe)
    cleaned: list = []
    for pair in raw:
        if isinstance(pair, str) and pair in allowed and pair not in cleaned:
            cleaned.append(pair)
        else:
            notes.append(f"whitelist_drop:{pair!r}")
    if len(cleaned) > LOCKED.whitelist_max:
        notes.append(f"whitelist_truncated:{len(cleaned)}->{LOCKED.whitelist_max}")
        cleaned = cleaned[: LOCKED.whitelist_max]
    if len(cleaned) < LOCKED.whitelist_min:
        for pair in universe:  # top up in volume order
            if len(cleaned) >= LOCKED.whitelist_min:
                break
            if pair not in cleaned:
                cleaned.append(pair)
                notes.append(f"whitelist_topup:{pair}")
    return cleaned


def validate_boot_config(config: Mapping) -> list:
    """S1 (and friends) boot validation for a freqtrade config mapping.

    Returns a list of violations; the caller must refuse to start when
    non-empty.
    """
    errors: list = []
    if config.get("trading_mode", "spot") != LOCKED.trading_mode:
        errors.append(
            f"S1: trading_mode must be '{LOCKED.trading_mode}', "
            f"got {config.get('trading_mode')!r}"
        )
    if config.get("margin_mode"):
        errors.append("S1: margin_mode must be empty — margin is forbidden")
    if config.get("position_adjustment_enable"):
        errors.append("no DCA/position adjustment allowed (out of scope v1)")
    try:
        mot = int(config.get("max_open_trades", LOCKED.max_open_trades))
    except (TypeError, ValueError):
        mot = -1
    if mot == -1 or mot > LOCKED.max_open_trades:
        errors.append(
            f"S3: max_open_trades must be a number <= {LOCKED.max_open_trades}, "
            f"got {config.get('max_open_trades')!r}"
        )
    return errors
