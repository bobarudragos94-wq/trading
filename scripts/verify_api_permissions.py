#!/usr/bin/env python3
"""CLI wrapper for the S9 key-permission check (refuses withdrawal rights).

Usage: python3 scripts/verify_api_permissions.py
Reads EXCHANGE_ID / EXCHANGE_KEY / EXCHANGE_SECRET from the environment
(or .env via the Makefile). Exit code 0 = key is trade-only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.exchange_permissions import verify_no_withdrawal_rights  # noqa: E402


def main() -> int:
    result = verify_no_withdrawal_rights()
    status = "OK" if result.ok else "REFUSED"
    print(f"[S9 key check] {status}: {result.reason}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
