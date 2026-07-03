"""Strategy smoke tests (spec §8): strategies load through freqtrade's
resolver, produce entry signals on crafted data, and always carry a hard
stoploss (S5). Uses synthetic OHLCV so no network or downloaded data is
needed.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # riskguard importable
sys.path.insert(0, str(REPO / "user_data" / "strategies"))  # shared/ importable

from freqtrade.configuration import Configuration  # noqa: E402
from freqtrade.resolvers import StrategyResolver  # noqa: E402


def load_strategy(name: str):
    config = Configuration.from_files([str(REPO / "user_data" / "config.backtest.json")])
    config["strategy"] = name
    config["user_data_dir"] = REPO / "user_data"
    config["strategy_path"] = REPO / "user_data" / "strategies"
    return StrategyResolver.load_strategy(config)


def synthetic_ohlcv(rows: int = 600, seed: int = 7, trend: float = 0.0) -> pd.DataFrame:
    """Random-walk 1h candles with optional drift; volume always positive."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(loc=trend, scale=0.01, size=rows)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0.001, 0.01, rows))
    low = close * (1 - rng.uniform(0.001, 0.01, rows))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    dates = pd.date_range("2024-01-01", periods=rows, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(100, 1000, rows),
        }
    )


def with_fake_informative(df: pd.DataFrame, columns: dict) -> pd.DataFrame:
    for col, value in columns.items():
        df[col] = value
    return df


@pytest.fixture(scope="module")
def trend_rider():
    return load_strategy("TrendRider")


@pytest.fixture(scope="module")
def mean_rev():
    return load_strategy("MeanRevRanger")


def test_strategies_load_with_hard_stoploss(trend_rider, mean_rev):
    for strat in (trend_rider, mean_rev):
        assert strat.stoploss < 0  # S5: hard stop always configured
        assert strat.can_short is False  # S1
        assert strat.position_adjustment_enable is False  # no DCA
        assert strat.timeframe == "1h"


def test_trendrider_signals_on_breakout(trend_rider):
    df = synthetic_ohlcv(trend=0.0)
    # force a clean breakout at the end: flat range then a surge
    df.loc[df.index[-60]:, "close"] = df["close"].iloc[-60]
    df.loc[df.index[-60]:, "high"] = df["close"].iloc[-60] * 1.001
    df.loc[df.index[-60]:, "low"] = df["close"].iloc[-60] * 0.999
    last = df.index[-1]
    df.loc[last, "close"] = df["close"].iloc[-60] * 1.10
    df.loc[last, "high"] = df["close"].iloc[-60] * 1.11

    df = trend_rider.populate_indicators(df, {"pair": "BTC/USDT"})
    # fake the 4h informative columns (merged by freqtrade at runtime)
    df = with_fake_informative(df, {"ema50_4h": 2.0, "ema200_4h": 1.0})
    # force ADX above threshold on the breakout candle to isolate the
    # breakout condition itself
    df.loc[last, "adx"] = 50.0
    df = trend_rider.populate_entry_trend(df, {"pair": "BTC/USDT"})

    assert "enter_long" in df.columns
    assert df.loc[last, "enter_long"] == 1
    # no signal without the higher-timeframe uptrend
    df2 = df.drop(columns=["enter_long", "enter_tag"])
    df2 = with_fake_informative(df2, {"ema50_4h": 1.0, "ema200_4h": 2.0})
    df2 = trend_rider.populate_entry_trend(df2, {"pair": "BTC/USDT"})
    assert df2["enter_long"].fillna(0).sum() == 0


def test_trendrider_exit_below_ema(trend_rider):
    df = synthetic_ohlcv(seed=11)
    df = trend_rider.populate_indicators(df, {"pair": "BTC/USDT"})
    df = with_fake_informative(df, {"ema50_4h": 2.0, "ema200_4h": 1.0})
    last = df.index[-1]
    df.loc[last, "close"] = df["ema_exit"].iloc[-1] * 0.90  # well below EMA
    df = trend_rider.populate_exit_trend(df, {"pair": "BTC/USDT"})
    assert df.loc[last, "exit_long"] == 1


def test_meanrev_signals_on_oversold_at_lower_band(mean_rev):
    df = synthetic_ohlcv(seed=3)
    df = mean_rev.populate_indicators(df, {"pair": "BTC/USDT"})
    df = with_fake_informative(df, {"adx_4h": 10.0})
    last = df.index[-1]
    # craft: deep oversold at/below lower band
    df.loc[last, "rsi2"] = 2.0
    df.loc[last, "close"] = df["bb_lower"].iloc[-1] * 0.99
    df = mean_rev.populate_entry_trend(df, {"pair": "BTC/USDT"})
    assert df.loc[last, "enter_long"] == 1

    # trending market (high 4h ADX) must veto the same setup
    df2 = df.drop(columns=["enter_long", "enter_tag"])
    df2 = with_fake_informative(df2, {"adx_4h": 40.0})
    df2 = mean_rev.populate_entry_trend(df2, {"pair": "BTC/USDT"})
    assert df2["enter_long"].fillna(0).sum() == 0


def test_meanrev_exit_at_mid_band(mean_rev):
    df = synthetic_ohlcv(seed=5)
    df = mean_rev.populate_indicators(df, {"pair": "BTC/USDT"})
    df = with_fake_informative(df, {"adx_4h": 10.0})
    last = df.index[-1]
    df.loc[last, "close"] = df["bb_mid"].iloc[-1] * 1.01
    df = mean_rev.populate_exit_trend(df, {"pair": "BTC/USDT"})
    assert df.loc[last, "exit_long"] == 1


def test_indicators_have_no_lookahead():
    """Donchian breakout reference must exclude the current candle."""
    from shared import indicators as ind

    df = synthetic_ohlcv(seed=13, rows=100)
    don = ind.donchian_high(df, 20)
    # spike the last candle's high: the donchian value of that candle must
    # NOT change (it only looks at previous candles)
    before = don.iloc[-1]
    df.loc[df.index[-1], "high"] *= 5
    after = ind.donchian_high(df, 20).iloc[-1]
    assert before == after
