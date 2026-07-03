"""TrendRider — trend-following breakout strategy (for the `trend` regime).

Entry (long only, S1):
- 1h close breaks above the Donchian(20) high of the previous 20 candles
- 4h EMA50 > 4h EMA200 (higher-timeframe uptrend filter)
- 1h ADX(14) > 20 (trend strength filter)

Risk / exit:
- initial stop: 2 x ATR(14) below entry (hard outer stop -10%, S5)
- after price moves +1R in our favour: trail at 1.5 x ATR below price
- exit signal: 1h close below EMA20

Position sizing and the entry gate live in SantinelaBase (riskguard).
Every hyperopt-tunable parameter carries an explicit bounded space.
"""

from __future__ import annotations

from datetime import datetime

from pandas import DataFrame

from freqtrade.strategy import DecimalParameter, IntParameter, informative
from freqtrade.strategy import stoploss_from_absolute

from shared import indicators as ind
from shared.santinela_base import SantinelaBase


class TrendRider(SantinelaBase):
    # --- hyperopt spaces (bounded by design; see §6.2) ---
    donchian_len = IntParameter(15, 40, default=20, space="buy", optimize=True)
    adx_min = IntParameter(15, 35, default=20, space="buy", optimize=True)
    stop_atr = DecimalParameter(1.5, 3.0, default=2.0, decimals=1,
                                space="sell", optimize=True)
    trail_atr = DecimalParameter(1.0, 2.5, default=1.5, decimals=1,
                                 space="sell", optimize=True)
    ema_exit_len = IntParameter(10, 30, default=20, space="sell", optimize=True)

    @property
    def stop_atr_mult(self) -> float:  # used by SantinelaBase sizing
        return float(self.stop_atr.value)

    # Defense-in-depth mirrors of S6-S8 at the freqtrade level.
    # RiskGuard remains the authority; these just add a second net.
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
        dataframe["ema50"] = ind.ema(dataframe, 50)
        dataframe["ema200"] = ind.ema(dataframe, 200)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["atr"] = ind.atr(dataframe, 14)
        dataframe["adx"] = ind.adx(dataframe, 14)
        dataframe["donchian_high"] = ind.donchian_high(
            dataframe, self.donchian_len.value
        )
        dataframe["ema_exit"] = ind.ema(dataframe, self.ema_exit_len.value)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] > dataframe["donchian_high"])
            & (dataframe["ema50_4h"] > dataframe["ema200_4h"])
            & (dataframe["adx"] > self.adx_min.value)
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "donchian_breakout")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] < dataframe["ema_exit"])
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "close_below_ema")
        return dataframe

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
        initial_risk = self.stop_atr.value * entry_atr / trade.open_rate

        if current_profit >= initial_risk:  # +1R reached -> ATR trail
            current_atr = self._atr_at(pair, current_time) or entry_atr
            stop_price = current_rate - self.trail_atr.value * current_atr
        else:
            stop_price = trade.open_rate - self.stop_atr.value * entry_atr
        # freqtrade only ever tightens the stop; returning a looser value
        # than the current stop is ignored (S5: tighten, never remove).
        return stoploss_from_absolute(
            stop_price, current_rate, is_short=trade.is_short
        )
