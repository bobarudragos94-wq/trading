"""Santinela brain — scheduler loop.

Responsibilities (one process, sequential loop, no threads):
- every ~5 min: poll equity from freqtrade -> riskguard.record_equity ->
  persist state -> on S6/S7 events alert + journal; S7 additionally writes
  the KILLED marker and stops the bot (S7 hard stop).
- track closed trades since last poll -> riskguard.record_trade_result (S8).
- every 4h at candle close (00/04/08/12/16/20 UTC): market snapshot ->
  Claude verdict -> validate -> clamp -> apply to freqtrade (M4).
- 07:00 Europe/Bucharest: daily report (M6).
- heartbeat file for the docker healthcheck each iteration.

Boot safety:
- refuses to run if the S7 KILLED marker is present, unless it was removed
  manually AND ACKNOWLEDGE_DRAWDOWN=1 is set (S7 restart procedure).
- validates freqtrade config against locked rules (S1/S3) via
  riskguard.validate_boot_config and refuses on violation.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from riskguard.guard import check_breakers, record_equity, record_trade_result, validate_boot_config
from riskguard.state import (
    load_state,
    save_state,
    try_resume_after_kill,
    write_kill_file,
)

from orchestrator.apply_verdict import apply_raw_verdict
from orchestrator.freqtrade_api import FreqtradeAPI
from strategist import journal
from strategist.llm_client import request_verdict
from strategist.market_snapshot import build_snapshot
from strategist.telegram_alerts import alert_breaker, send_alert

logger = logging.getLogger("santinela.brain")

STATE_DIR = Path(os.environ.get("SANTINELA_STATE_DIR", "user_data/riskguard_state"))
HEARTBEAT = Path(os.environ.get("SANTINELA_HEARTBEAT", "journal/heartbeat"))
REPORT_TZ = ZoneInfo(os.environ.get("TZ", "Europe/Bucharest"))

EQUITY_POLL_SECONDS = 300
LOOP_SLEEP_SECONDS = 30
VERDICT_HOURS = (0, 4, 8, 12, 16, 20)  # UTC candle closes
DAILY_REPORT_HOUR = 7  # local (REPORT_TZ)


def _acknowledged() -> bool:
    return os.environ.get("ACKNOWLEDGE_DRAWDOWN", "") in ("1", "true", "yes") or (
        "--acknowledge-drawdown" in sys.argv
    )


def boot_checks(api: FreqtradeAPI) -> "object":
    """Returns the loaded GuardState or exits the process."""
    state = load_state(STATE_DIR)
    ok, reason = try_resume_after_kill(STATE_DIR, state, acknowledged=_acknowledged())
    if not ok:
        journal.append("boot_refused", {"reason": reason})
        logger.critical("BOOT REFUSED: %s", reason)
        sys.exit(2)
    if reason != "no kill state":
        journal.append("boot_resumed_after_kill", {"reason": reason})
        save_state(STATE_DIR, state)

    # S1/S3 validation against the running bot's effective config.
    for attempt in range(30):
        if api.ping():
            break
        time.sleep(5)
    try:
        config = api.show_config()
    except Exception as exc:
        logger.warning("could not fetch config for boot validation: %s", exc)
        return state
    errors = validate_boot_config(config)
    mode = os.environ.get("TRADING_MODE", "dry")
    if config.get("dry_run") is False and mode != "live":
        errors.append("S11: freqtrade runs with dry_run=false but TRADING_MODE!=live")
    if errors:
        journal.append("boot_refused", {"reason": errors})
        logger.critical("BOOT REFUSED, locked-rule violations: %s", errors)
        try:
            api.stop()
        finally:
            sys.exit(3)
    journal.append("boot_ok", {"mode": mode, "strategy": config.get("strategy")})
    return state


def poll_equity(api: FreqtradeAPI, state, now: datetime) -> None:
    try:
        equity = api.equity()
    except Exception as exc:
        logger.warning("equity poll failed: %s", exc)
        return
    events = record_equity(state, equity, now)
    save_state(STATE_DIR, state)
    for event in events:
        journal.append("guard_event", {"event": event, "equity": equity,
                                       "hwm": state.hwm})
        alert_breaker(event, equity)
        if event == "S7_TRIPPED" or event == "S7_KILLED":
            write_kill_file(STATE_DIR, "S7 max drawdown", now)
            try:
                api.stop()  # S7: bot stops entirely
            except Exception as exc:
                logger.error("failed to stop freqtrade after S7: %s", exc)


def poll_closed_trades(api: FreqtradeAPI, state, seen: set, now: datetime) -> None:
    """Feed newly closed trades to the S8 loss-streak brake."""
    try:
        profit = api.profit()
        closed = int(profit.get("closed_trade_count", 0))
    except Exception:
        return
    if "count" in seen and closed > seen["count"]:
        # fetch recent trade results
        try:
            import requests as _rq
            resp = _rq.get(f"{api.base_url}/api/v1/trades?limit={closed - seen['count']}",
                           auth=api.auth, timeout=api.timeout)
            resp.raise_for_status()
            for trade in resp.json().get("trades", []):
                if trade.get("trade_id") in seen["ids"]:
                    continue
                seen["ids"].add(trade.get("trade_id"))
                events = record_trade_result(
                    state, float(trade.get("profit_abs") or 0.0), now
                )
                save_state(STATE_DIR, state)
                journal.append("trade_closed", {
                    "trade_id": trade.get("trade_id"), "pair": trade.get("pair"),
                    "profit_abs": trade.get("profit_abs"),
                    "consecutive_losses": state.consecutive_losses,
                })
                for event in events:
                    journal.append("guard_event", {"event": event})
                    alert_breaker(event)
        except Exception as exc:
            logger.warning("closed-trade poll failed: %s", exc)
    seen["count"] = closed


def universe_from_bot(api: FreqtradeAPI) -> list:
    try:
        return list(api.whitelist().get("whitelist", []))
    except Exception:
        return []


def verdict_cycle(api: FreqtradeAPI, state, now: datetime) -> None:
    universe = universe_from_bot(api)
    if not universe:
        journal.append("verdict_skipped", {"reason": "empty universe"})
        return
    snapshot = build_snapshot(api, state, universe, now)
    raw = request_verdict(snapshot)
    clamped = apply_raw_verdict(raw, universe, state, api=api, now=now)
    breakers = check_breakers(state, now)
    if breakers.s7_killed:
        return
    logger.info("verdict applied: %s / %s / %s", clamped["regime"],
                clamped["risk_mode"], clamped["active_strategy"])


def daily_report(api: FreqtradeAPI, state, now_local: datetime) -> None:
    try:
        from strategist.daily_report import generate_report
    except ImportError:
        journal.append("daily_report_skipped", {"reason": "not implemented yet"})
        return
    try:
        path = generate_report(api, state, now_local)
        journal.append("daily_report", {"path": str(path)})
    except Exception as exc:
        logger.error("daily report failed: %s", exc)
        journal.append("daily_report_failed", {"error": str(exc)[:300]})


def run_forever() -> None:  # pragma: no cover - exercised in M5 demo
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    api = FreqtradeAPI()
    state = boot_checks(api)
    send_alert("🟢 Santinela brain started "
               f"(mode: {os.environ.get('TRADING_MODE', 'dry')}).")

    last_equity_poll = 0.0
    last_verdict_slot: tuple | None = None
    last_report_day: str | None = None
    seen_trades: dict = {"ids": set()}

    while True:
        now = datetime.now(timezone.utc)
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.touch()

        killed = check_breakers(state, now).s7_killed
        if not killed and time.monotonic() - last_equity_poll >= EQUITY_POLL_SECONDS:
            last_equity_poll = time.monotonic()
            poll_equity(api, state, now)
            poll_closed_trades(api, state, seen_trades, now)

        slot = (now.date().isoformat(), now.hour)
        if (not killed and now.hour in VERDICT_HOURS and now.minute >= 1
                and slot != last_verdict_slot):
            last_verdict_slot = slot
            try:
                verdict_cycle(api, state, now)
            except Exception as exc:
                logger.exception("verdict cycle crashed: %s", exc)
                journal.append("verdict_cycle_error", {"error": str(exc)[:300]})

        now_local = now.astimezone(REPORT_TZ)
        if now_local.hour == DAILY_REPORT_HOUR and last_report_day != str(now_local.date()):
            last_report_day = str(now_local.date())
            daily_report(api, state, now_local)

        time.sleep(LOOP_SLEEP_SECONDS)


if __name__ == "__main__":
    run_forever()
