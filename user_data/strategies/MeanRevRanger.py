"""MeanRevRanger — range mean-reversion strategy (for the `range` regime).

Entry (long only, S1):
- 1h RSI(2) < 10 (deep short-term oversold)
- 1h close at/below the lower Bollinger(20, 2) band
- 4h ADX(14) < 20 (no strong trend — ranging market filter)

Risk / exit:
- stop: 1.5 x ATR(14) below entry (hard outer stop -10%, S5)
- take-profit: close back at/above the Bollinger mid-band
- time-stop: exit after 48h if neither stop nor target hit

Position sizing and the entry gate live in SantinelaBase (riskguard).
Every hyperopt-tunable parameter carries an explicit bounded space.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pandas import DataFrame

from freqtrade.strategy import DecimalParameter, IntParameter, informative
from freqtrade.strategy import stoploss_from_absolute

from shared import indicators as ind
from shared.santinela_base import SantinelaBase


class MeanRevRanger(SantinelaBase):
    # --- hyperopt spaces (bounded by design; see §6.2) ---
    rsi_max = IntParameter(5, 20, default=10, space="buy", optimize=True)
    bb_std = DecimalParameter(1.5, 3.0, default=2.0, decimals=1,
                              space="buy", optimize=True)
    adx_max = IntParameter(15, 30, default=20, space="buy", optimize=True)
    stop_atr = DecimalParameter(1.0, 2.5, default=1.5, decimals=1,
                                space="sell", optimize=True)
    time_stop_hours = IntParameter(24, 96, default=48, space="sell", optimize=True)

    @property
    def stop_atr_mult(self) -> float:  # used by SantinelaBase sizing
        return float(self.stop_atr.value)

    # Defense-in-depth mirrors of S6-S8 at the freqtrade level.
    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 2},
            {
                "method": "StoplossGuard",  # ~S8 mirror
                "lookback_period_candles": 48,
                "trade_limit": 6,
                "stop_duration_candles": 12,
                "only_per_pair": False,
            },
            {
                "method": "MaxDrawdown",  # ~S6/S7 mirror
                "lookback_period_candles": 24,
                "trade_limit": 4,
                "max_allowed_drawdown": 0.05,
                "stop_duration_candles": 24,
            },
        ]

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["adx"] = ind.adx(dataframe, 14)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["atr"] = ind.atr(dataframe, 14)
        dataframe["rsi2"] = ind.rsi(dataframe, 2)
        bands = ind.bollinger_bands(dataframe, 20, float(self.bb_std.value))
        dataframe["bb_lower"] = bands["bb_lower"]
        dataframe["bb_mid"] = bands["bb_mid"]
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["rsi2"] < self.rsi_max.value)
            & (dataframe["close"] <= dataframe["bb_lower"])
            & (dataframe["adx_4h"] < self.adx_max.value)
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "oversold_at_lower_band")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] >= dataframe["bb_mid"])
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "mid_band_target")
        return dataframe

    def custom_exit(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        held = current_time - trade.open_date_utc
        if held >= timedelta(hours=int(self.time_stop_hours.value)):
            return "time_stop"
        return None

    def custom_stoploss(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        entry_atr = self._atr_at(pair, trade.open_date_utc)
        if entry_atr <= 0:
            return None  # keep hard outer stop (S5)
        stop_price = trade.open_rate - self.stop_atr.value * entry_atr
        return stoploss_from_absolute(
            stop_price, current_rate, is_short=trade.is_short
        )
