"""S9: verify exchange API key permissions — refuse keys that can withdraw.

The check must FAIL CLOSED: unless we can positively demonstrate that the
key cannot withdraw, the answer is "refuse". Implemented for Kraken (the
default exchange). Other exchanges return a refusal with instructions —
add an explicit probe before allowing them in live mode.

Kraken probe: call the private ``WithdrawInfo`` endpoint with dummy
parameters. A key WITHOUT the "Withdraw funds" permission gets
``EGeneral:Permission denied`` (good). A key WITH the permission gets a
funding/argument error instead (bad — refuse). No withdrawal can result
from the probe: it is a read-only quote endpoint and the dummy withdraw
key does not exist.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class PermissionCheckResult:
    def __init__(self, ok: bool, reason: str):
        self.ok = ok
        self.reason = reason

    def __repr__(self):
        return f"PermissionCheckResult(ok={self.ok}, reason={self.reason!r})"


def verify_no_withdrawal_rights(
    exchange_id: str | None = None,
    api_key: str | None = None,
    secret: str | None = None,
) -> PermissionCheckResult:
    exchange_id = (exchange_id or os.environ.get("EXCHANGE_ID", "kraken")).lower()
    api_key = api_key or os.environ.get("EXCHANGE_KEY", "")
    secret = secret or os.environ.get("EXCHANGE_SECRET", "")

    if not api_key or not secret:
        return PermissionCheckResult(False, "no API key/secret configured")

    if exchange_id != "kraken":
        return PermissionCheckResult(
            False,
            f"no withdrawal-permission probe implemented for '{exchange_id}' — "
            "verify manually AND add a probe here before going live (S9)",
        )

    try:
        import ccxt
    except ImportError:
        return PermissionCheckResult(False, "ccxt not installed")

    exchange = ccxt.kraken({"apiKey": api_key, "secret": secret,
                            "enableRateLimit": True})

    # 1) the key must at least be able to read the balance (trade key)
    try:
        exchange.fetch_balance()
    except Exception as exc:
        return PermissionCheckResult(
            False, f"key cannot read balance — not a working trade key: {exc}"
        )

    # 2) the withdrawal probe must be DENIED
    try:
        exchange.private_post_withdrawinfo({
            "asset": "XBT", "key": "santinela-nonexistent-key", "amount": "0.1",
        })
    except ccxt.PermissionDenied:
        return PermissionCheckResult(
            True, "withdrawal permission denied by exchange — key is trade-only"
        )
    except ccxt.BaseError as exc:
        message = str(exc)
        if "EGeneral:Permission denied" in message:
            return PermissionCheckResult(True, "withdrawal permission denied — key is trade-only")
        return PermissionCheckResult(
            False,
            "withdrawal probe was NOT rejected with a permission error "
            f"({message[:200]}) — the key may have withdrawal rights. REFUSING (S9). "
            "Create a new key with only 'Query funds' + 'Create & modify orders'.",
        )
    return PermissionCheckResult(
        False,
        "withdrawal probe unexpectedly succeeded — key HAS withdrawal rights. "
        "REFUSING (S9). Delete this key and create a trade-only key.",
    )
