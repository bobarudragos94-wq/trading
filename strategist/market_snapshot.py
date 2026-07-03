"""Compact market snapshot for the Strategist (§6.4).

Builds one small JSON document (target: well under ~4k tokens) from data
the freqtrade REST API already has — no external feeds in v1. Extension
point: add extra data sources by appending keys to the snapshot dict in
``build_snapshot`` (keep them numeric/summarised; never raw text from the
internet, to avoid prompt-injection and look-ahead issues).

BTC dominance proxy: real dominance needs global market-cap data (external
feed). v1 uses "btc_relative_strength_7d" = BTC 7d return minus the mean
7d return of the other whitelisted pairs — same signal direction, computed
purely from exchange data.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import talib

from orchestrator.freqtrade_api import FreqtradeAPI
from riskguard.guard import check_breakers
from riskguard.state import GuardState
from strategist import journal

logger = logging.getLogger(__name__)

TIMEFRAMES = ("1h", "4h", "1d")
CANDLE_LIMIT = {"1h": 200, "4h": 210, "1d": 120}
MAX_PAIRS = 12  # keep the prompt small; universe order = volume ranked


def _round(value, digits=4):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value, digits)


def _candles_frame(api: FreqtradeAPI, pair: str, timeframe: str) -> pd.DataFrame | None:
    try:
        raw = api.pair_candles(pair, timeframe, limit=CANDLE_LIMIT[timeframe])
        df = pd.DataFrame(raw["data"], columns=raw["columns"])
        if len(df) < 30:
            return None
        return df
    except Exception as exc:
        logger.warning("snapshot: candles %s %s failed: %s", pair, timeframe, exc)
        return None


def _tf_stats(df: pd.DataFrame) -> dict:
    close = df["close"].astype(float).to_numpy()
    high = df["high"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy()
    volume = df["volume"].astype(float).to_numpy()

    atr = talib.ATR(high, low, close, timeperiod=14)
    adx = talib.ADX(high, low, close, timeperiod=14)
    ema20 = talib.EMA(close, timeperiod=20)
    ema50 = talib.EMA(close, timeperiod=50)

    def ret(n: int):
        if len(close) <= n or close[-1 - n] == 0:
            return None
        return _round((close[-1] / close[-1 - n] - 1) * 100, 2)

    vol_window = volume[-30:]
    vol_z = None
    if len(vol_window) >= 10 and np.std(vol_window) > 0:
        vol_z = _round((volume[-1] - np.mean(vol_window)) / np.std(vol_window), 2)

    return {
        "ret_5": ret(5),
        "ret_20": ret(20),
        "atr_pct": _round(atr[-1] / close[-1] * 100, 2) if close[-1] else None,
        "adx": _round(adx[-1], 1),
        "close_vs_ema20_pct": _round((close[-1] / ema20[-1] - 1) * 100, 2)
        if ema20[-1] == ema20[-1] else None,
        "ema20_vs_ema50_pct": _round((ema20[-1] / ema50[-1] - 1) * 100, 2)
        if ema50[-1] == ema50[-1] else None,
        "volume_z": vol_z,
    }


def build_snapshot(
    api: FreqtradeAPI,
    guard_state: GuardState,
    universe: list,
    now: datetime | None = None,
) -> dict:
    """Assemble the full snapshot. Degrades gracefully on partial failures."""
    now = now or datetime.now(timezone.utc)
    pairs = list(universe)[:MAX_PAIRS]

    per_pair: dict = {}
    ret7d: dict = {}
    for pair in pairs:
        stats: dict = {}
        for tf in TIMEFRAMES:
            df = _candles_frame(api, pair, tf)
            if df is not None:
                stats[tf] = _tf_stats(df)
                if tf == "1d":
                    close = df["close"].astype(float).to_numpy()
                    if len(close) > 7 and close[-8] != 0:
                        ret7d[pair] = (close[-1] / close[-8] - 1) * 100
        if stats:
            per_pair[pair] = stats

    btc_pair = next((p for p in pairs if p.startswith("BTC/")), None)
    btc_rel = None
    if btc_pair and btc_pair in ret7d and len(ret7d) > 1:
        alts = [v for k, v in ret7d.items() if k != btc_pair]
        btc_rel = _round(ret7d[btc_pair] - float(np.mean(alts)), 2)

    open_trades: list = []
    equity = None
    try:
        equity = _round(api.equity(), 2)
        for t in api.status():
            open_trades.append({
                "pair": t.get("pair"),
                "profit_pct": _round(t.get("profit_pct"), 2),
                "open_since_hours": _round(
                    (now - pd.Timestamp(t["open_date"], tz="UTC").to_pydatetime())
                    .total_seconds() / 3600, 1,
                ) if t.get("open_date") else None,
                "stake": _round(t.get("stake_amount"), 2),
            })
    except Exception as exc:
        logger.warning("snapshot: positions/equity failed: %s", exc)

    breakers = check_breakers(guard_state, now)
    recent = [
        {
            "verdict_id": r.get("verdict_id"),
            "ts": r.get("ts"),
            "clamped": r.get("clamped"),
        }
        for r in journal.tail("verdict", limit=5)
    ]

    return {
        "generated_at": now.isoformat(),
        "universe": pairs,
        "pairs": per_pair,
        "btc_relative_strength_7d": btc_rel,
        "equity_stake_ccy": equity,
        "open_positions": open_trades,
        "breakers": {
            "s6_daily_loss_pause": breakers.s6_active,
            "s7_killed": breakers.s7_killed,
            "s8_loss_streak_pause": breakers.s8_active,
        },
        "equity_drawdown_from_hwm_pct": _round(
            (guard_state.last_equity - guard_state.hwm) / guard_state.hwm * 100, 2
        ) if guard_state.hwm else None,
        "last_verdicts": recent,
    }
