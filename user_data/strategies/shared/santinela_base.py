"""SantinelaBase — shared plumbing for all Santinela strategies.

Centralises everything safety-related so no strategy can "forget" it:

- S2 position sizing through ``riskguard.position_stake`` (risk-based, capped)
- S3/S4/S5/S6/S7/S8 gate through ``riskguard.can_open_new_trade`` before
  every entry (``confirm_trade_entry``)
- reads the orchestrator's clamped runtime knobs from ``config['santinela']``
  and re-clamps them anyway (defense-in-depth — the strategy never trusts
  its own config file)

The riskguard import is deliberately top-level: if the locked module is
missing, the strategy fails to LOAD and the bot refuses to start (fail
loud, never fail open).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

from riskguard.guard import LOCKED, can_open_new_trade, check_breakers, position_stake
from riskguard.state import GuardState, is_killed, load_state

logger = logging.getLogger(__name__)


class SantinelaBase(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False  # S1: spot only
    position_adjustment_enable = False  # no DCA/pyramiding (out of scope v1)
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    minimal_roi: dict = {}  # exits are strategy-driven, ROI table disabled

    # S5 hard outer stoploss. Every position always has at least this stop;
    # ATR-based custom stops may only tighten it, never widen or remove it.
    stoploss = -0.10
    use_custom_stoploss = True

    startup_candle_count = 250

    # Multiplier for the initial ATR stop; concrete strategies override.
    stop_atr_mult = 2.0

    # ------------------------------------------------------------ guard --

    def _guard_state(self) -> GuardState:
        """Load persisted guard state in live/dry-run; fresh state otherwise
        (backtesting/hyperopt must not be polluted by runtime breakers)."""
        if self.dp and self.dp.runmode.value in ("live", "dry_run"):
            return load_state(self._guard_dir())
        return GuardState()

    def _guard_dir(self) -> Path:
        return Path(self.config["user_data_dir"]) / "riskguard_state"

    def _santinela_conf(self) -> dict:
        conf = self.config.get("santinela", {})
        return conf if isinstance(conf, dict) else {}

    def _risk_per_trade_pct(self) -> float:
        try:
            pct = float(self._santinela_conf().get("risk_per_trade_pct", 0.75))
        except (TypeError, ValueError):
            pct = 0.75
        # Re-clamp regardless of what the orchestrator wrote (S2).
        return max(0.05, min(pct, LOCKED.max_risk_per_trade_pct))

    def _entry_atr(self, pair: str, default: float = 0.0) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty or "atr" not in dataframe.columns:
            return default
        value = dataframe["atr"].iloc[-1]
        return float(value) if value == value else default  # NaN guard

    def _atr_at(self, pair: str, when: datetime) -> float:
        """ATR of the candle the trade opened on (falls back to latest)."""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty or "atr" not in dataframe.columns:
            return 0.0
        rows = dataframe.loc[dataframe["date"] <= when]
        if rows.empty:
            return 0.0
        value = rows["atr"].iloc[-1]
        return float(value) if value == value else 0.0

    # -------------------------------------------------------- S2 sizing --

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        atr_value = self._entry_atr(pair)
        if atr_value <= 0:
            logger.info("%s: no ATR available — refusing to size entry", pair)
            return 0.0
        stop_price = current_rate - self.stop_atr_mult * atr_value
        equity = self.wallets.get_total_stake_amount()

        # Remaining S4 exposure headroom also caps the stake.
        open_stakes = sum(t.stake_amount for t in Trade.get_open_trades())
        headroom = equity * LOCKED.max_total_exposure_pct / 100.0 - open_stakes

        stake = position_stake(
            equity=equity,
            entry_price=current_rate,
            stop_price=stop_price,
            risk_per_trade_pct=self._risk_per_trade_pct(),
            max_stake=min(max_stake, headroom),
        )
        if min_stake and 0 < stake < min_stake:
            # Exchange minimum would force more risk than budgeted — skip.
            logger.info("%s: stake %.2f below exchange min %.2f — skipping",
                        pair, stake, min_stake)
            return 0.0
        return stake

    # ------------------------------------------------- entry gate (S3-8) --

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        if side != "long":  # S1: spot longs only
            return False

        conf = self._santinela_conf()
        if not conf.get("entries_enabled", True):
            logger.info("%s: entries disabled by orchestrator — entry refused", pair)
            return False

        live_like = self.dp and self.dp.runmode.value in ("live", "dry_run")
        if live_like and is_killed(self._guard_dir()):
            logger.error("%s: S7 KILLED marker present — entry refused", pair)
            return False

        state = self._guard_state()
        now = current_time if current_time.tzinfo else current_time.replace(
            tzinfo=timezone.utc
        )
        open_trades = Trade.get_open_trade_count()
        open_stakes = sum(t.stake_amount for t in Trade.get_open_trades())
        equity = self.wallets.get_total_stake_amount()

        decision = can_open_new_trade(
            state,
            now=now,
            open_trades=open_trades,
            current_exposure=open_stakes,
            proposed_stake=amount * rate,
            equity=equity,
            has_stoploss=self.stoploss < 0,  # S5: hard stop always configured
        )
        if not decision.allowed:
            logger.warning("%s: riskguard refused entry: %s", pair,
                           "; ".join(decision.reasons))
            return False
        return True

    # ------------------------------------------------------------ misc ---

    def breaker_status(self, now: datetime):
        """Convenience for logging/telemetry."""
        return check_breakers(self._guard_state(), now)
