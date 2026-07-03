"""Custom Telegram commands: /guard, /verdict, /panic (spec §6.6).

Runs inside the brain's scheduler loop via short getUpdates polling.
IMPORTANT: this uses a SECOND bot token (SANTINELA_TELEGRAM_TOKEN) — the
main TELEGRAM_TOKEN is polled by freqtrade itself and one token supports
only a single getUpdates consumer.

Only messages from TELEGRAM_CHAT_ID are honoured. /panic is two-step:
it arms and requires an explicit "CONFIRM PANIC" reply within 5 minutes
before stopping entries and market-exiting every open position.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from riskguard.guard import check_breakers
from riskguard.state import load_state

from orchestrator.freqtrade_api import FreqtradeAPI
from strategist import journal

logger = logging.getLogger(__name__)

PANIC_CONFIRM_WINDOW_SECONDS = 300
STATE_DIR = Path(os.environ.get("SANTINELA_STATE_DIR", "user_data/riskguard_state"))


class TelegramCommands:
    def __init__(self, api: FreqtradeAPI | None = None,
                 token: str | None = None, chat_id: str | None = None):
        self.api = api or FreqtradeAPI()
        self.token = token or os.environ.get("SANTINELA_TELEGRAM_TOKEN", "")
        self.chat_id = str(chat_id or os.environ.get("TELEGRAM_CHAT_ID", ""))
        self.offset = 0
        self.panic_armed_at: float | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    # --- transport (overridden in tests) --------------------------------
    def _send(self, text: str) -> None:
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text,
                      "parse_mode": "Markdown"},
                timeout=10,
            ).raise_for_status()
        except requests.RequestException as exc:
            logger.error("telegram send failed: %s", exc)

    def poll(self) -> None:
        """One short getUpdates cycle; safe to call every loop iteration."""
        if not self.enabled:
            return
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{self.token}/getUpdates",
                params={"offset": self.offset + 1, "timeout": 0},
                timeout=10,
            )
            resp.raise_for_status()
            for update in resp.json().get("result", []):
                self.offset = max(self.offset, int(update.get("update_id", 0)))
                self.handle_update(update)
        except requests.RequestException as exc:
            logger.warning("telegram poll failed: %s", exc)

    # --- command handling ------------------------------------------------
    def handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        chat = str((message.get("chat") or {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if chat != self.chat_id or not text:
            return
        journal.append("telegram_command", {"text": text[:100]})
        if text.startswith("/guard"):
            self._send(self.guard_text())
        elif text.startswith("/verdict"):
            self._send(self.verdict_text())
        elif text.startswith("/panic"):
            self.panic_armed_at = time.monotonic()
            self._send(
                "⚠️ *PANIC armed.* Reply exactly `CONFIRM PANIC` within 5 "
                "minutes to stop all entries and market-exit EVERY open "
                "position. Any other message disarms."
            )
        elif text == "CONFIRM PANIC":
            self._confirm_panic()
        elif self.panic_armed_at is not None:
            self.panic_armed_at = None
            self._send("Panic disarmed.")

    def _confirm_panic(self) -> None:
        armed = self.panic_armed_at
        self.panic_armed_at = None
        if armed is None or time.monotonic() - armed > PANIC_CONFIRM_WINDOW_SECONDS:
            self._send("No armed panic (or it expired). Send /panic first.")
            return
        results = []
        try:
            self.api.stopentry()
            results.append("entries stopped")
        except Exception as exc:
            results.append(f"stopentry FAILED: {type(exc).__name__}")
        try:
            self.api.forceexit_all()
            results.append("market-exit sent for all positions")
        except Exception as exc:
            results.append(f"forceexit FAILED: {type(exc).__name__}")
        journal.append("panic_executed", {"results": results})
        self._send("🚨 *PANIC executed*: " + "; ".join(results))

    def guard_text(self) -> str:
        state = load_state(STATE_DIR)
        b = check_breakers(state, datetime.now(timezone.utc))
        return (
            "*Guard status*\n"
            f"S6 daily-loss pause: {'ACTIVE until ' + str(b.s6_until) if b.s6_active else 'off'}\n"
            f"S7 kill switch: {'KILLED' if b.s7_killed else 'off'}\n"
            f"S8 loss-streak pause: {'ACTIVE until ' + str(b.s8_until) if b.s8_active else 'off'}\n"
            f"Loss streak: {state.consecutive_losses}\n"
            f"Equity: {state.last_equity:.2f} | HWM: {state.hwm:.2f}\n"
            f"Entries allowed: {b.entries_allowed}"
        )

    def verdict_text(self) -> str:
        records = journal.tail("verdict", limit=1)
        if not records:
            return "No verdict recorded yet."
        v = records[-1]
        raw = json.dumps(v.get("raw_verdict", {}), indent=0)[:800]
        clamped = json.dumps(v.get("clamped", {}), indent=0)[:800]
        return (
            f"*Last verdict* `{v.get('verdict_id')}` @ {v.get('ts', '')[:19]}\n"
            f"raw: `{raw}`\n"
            f"clamped: `{clamped}`\n"
            f"rationale: {str(v.get('rationale', ''))[:300]}"
        )
