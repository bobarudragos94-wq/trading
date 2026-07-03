"""Custom guard alerts via Telegram (S6-S8 notifications).

Send-only: uses the Bot API ``sendMessage`` endpoint, which does not
conflict with freqtrade's own polling of the same bot token. Custom
interactive commands (M6) use a SECOND token (SANTINELA_TELEGRAM_TOKEN)
because only one consumer may poll getUpdates per token.

Degrades to logging when Telegram is not configured — alerts must never
crash the guard loop.
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)


def _config() -> tuple[str, str] | None:
    enabled = os.environ.get("TELEGRAM_ENABLED", "false").lower() in ("1", "true", "yes")
    token = os.environ.get("SANTINELA_TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if enabled and token and chat_id:
        return token, chat_id
    return None


def send_alert(text: str) -> bool:
    """Send a Telegram message; returns True when actually delivered."""
    conf = _config()
    if conf is None:
        logger.warning("TELEGRAM ALERT (not configured, log only): %s", text)
        return False
    token, chat_id = conf
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.error("telegram alert failed: %s", exc)
        return False


BREAKER_MESSAGES = {
    "S6_TRIPPED": "🛑 *S6 daily-loss breaker tripped* — equity fell ≥5% in 24h. "
                  "New entries paused for 24h. Open positions keep their stops.",
    "S7_KILLED": "☠️ *S7 MAX-DRAWDOWN KILL SWITCH* — equity fell ≥20% from its "
                 "high-water mark. Bot STOPPED. Manual restart required: remove "
                 "the KILLED file and restart with ACKNOWLEDGE_DRAWDOWN=1.",
    "S8_TRIPPED": "🛑 *S8 losing-streak breaker tripped* — 6 consecutive losing "
                  "trades. New entries paused for 12h.",
}


def alert_breaker(event: str, equity: float | None = None) -> None:
    text = BREAKER_MESSAGES.get(event, f"Guard event: {event}")
    if equity is not None:
        text += f"\nEquity now: {equity:.2f}"
    send_alert(text)
