"""Shared indicator helpers used by both Santinela strategies.

Thin wrappers around TA-Lib plus pandas implementations for indicators
TA-Lib does not ship (Donchian). Kept together so both strategies and any
future notebook analysis compute indicators identically.
"""

from __future__ import annotations

import pandas as pd
import talib.abstract as ta


def ema(dataframe: pd.DataFrame, period: int) -> pd.Series:
    return ta.EMA(dataframe, timeperiod=period)


def rsi(dataframe: pd.DataFrame, period: int) -> pd.Series:
    return ta.RSI(dataframe, timeperiod=period)


def adx(dataframe: pd.DataFrame, period: int = 14) -> pd.Series:
    return ta.ADX(dataframe, timeperiod=period)


def atr(dataframe: pd.DataFrame, period: int = 14) -> pd.Series:
    return ta.ATR(dataframe, timeperiod=period)


def bollinger_bands(
    dataframe: pd.DataFrame, period: int = 20, stds: float = 2.0
) -> pd.DataFrame:
    """Returns DataFrame with bb_upper / bb_mid / bb_lower columns."""
    upper, mid, lower = ta.BBANDS(
        dataframe["close"], timeperiod=period, nbdevup=stds, nbdevdn=stds
    )
    return pd.DataFrame({"bb_upper": upper, "bb_mid": mid, "bb_lower": lower})


def donchian_high(dataframe: pd.DataFrame, period: int = 20) -> pd.Series:
    """Highest high of the *previous* `period` candles (shifted by one so a
    close above it is a true breakout and can never look at the current
    candle — lookahead-safe)."""
    return dataframe["high"].rolling(period).max().shift(1)


def donchian_low(dataframe: pd.DataFrame, period: int = 20) -> pd.Series:
    return dataframe["low"].rolling(period).min().shift(1)
