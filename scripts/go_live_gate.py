#!/usr/bin/env python3
"""Go-live gate (S11, spec §7.4). `make live` runs this before anything else.

Every precondition is checked mechanically; ANY failure refuses live mode.
Passing requires, in order:

  1. full pytest suite green
  2. TRADING_MODE=live in the environment (.env)
  3. user_data/config.live.json present, valid, spot-only, dry_run=false
  4. secrets configured (exchange key/secret, non-default API password)
  5. S9: exchange key verified trade-only (withdrawal probe DENIED)
  6. dry-run record meets §7.3: >=28 days span, >=30 closed trades,
     profit factor >= 1.15, max drawdown <= 12%
  7. journal present for the dry-run period (S12)
  8. operator types the exact phrase: INTELEG RISCUL

The S1-S12 table is printed before the confirmation prompt.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from riskguard.guard import validate_boot_config  # noqa: E402

CONFIRMATION_PHRASE = "INTELEG RISCUL"

MIN_DRYRUN_DAYS = 28
MIN_CLOSED_TRADES = 30
MIN_PROFIT_FACTOR = 1.15
MAX_DRAWDOWN_PCT = 12.0

S_RULES = """
+-----+----------------------------------------------+--------------------------------------+
| S1  | Spot only - no margin/futures/leverage       | enforced at boot                     |
| S2  | Max risk per trade                           | 2.0% of equity                       |
| S3  | Max concurrent open trades                   | 4                                    |
| S4  | Max total exposure                           | 60% of equity                        |
| S5  | Hard stoploss on every position              | mandatory; tighten only              |
| S6  | Daily loss circuit breaker                   | -5% / 24h -> pause entries 24h       |
| S7  | Max drawdown kill switch                     | -20% from HWM -> full stop           |
| S8  | Consecutive-loss brake                       | 6 losses -> pause entries 12h        |
| S9  | API keys trade-only                          | withdrawal rights refused at boot    |
| S10 | LLM output advisory only                     | validated + clamped; outage->defense |
| S11 | Live mode gate                               | this script                          |
| S12 | Append-only audit journal                    | journal/*.jsonl                      |
+-----+----------------------------------------------+--------------------------------------+
"""


def check_tests() -> tuple:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                          cwd=REPO, capture_output=True, text=True)
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "?"
    return proc.returncode == 0, tail


def check_trading_mode() -> tuple:
    mode = os.environ.get("TRADING_MODE", "")
    return mode == "live", f"TRADING_MODE={mode or '(unset)'}"


def check_live_config(path: Path) -> tuple:
    if not path.exists():
        return False, f"{path} missing (copy config.live.json.example and review)"
    try:
        cfg = json.loads(path.read_text())
    except ValueError as exc:
        return False, f"invalid JSON: {exc}"
    problems = validate_boot_config(cfg)
    if cfg.get("dry_run") is not False:
        problems.append("dry_run must be false in config.live.json")
    if cfg.get("stake_currency") != "USDC":
        problems.append("stake_currency must be USDC")
    if cfg.get("force_entry_enable"):
        problems.append("force_entry_enable must be false in live")
    if cfg.get("exchange", {}).get("key") or cfg.get("exchange", {}).get("secret"):
        problems.append("secrets must come from env, not the config file")
    return (False, "; ".join(problems)) if problems else (True, "config.live.json valid")


def check_secrets() -> tuple:
    problems = []
    if not os.environ.get("EXCHANGE_KEY") or not os.environ.get("EXCHANGE_SECRET"):
        problems.append("EXCHANGE_KEY/EXCHANGE_SECRET not set")
    password = os.environ.get("FT_API_PASSWORD", "")
    if not password or "change-me" in password or len(password) < 12:
        problems.append("FT_API_PASSWORD unset/default/too short")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        problems.append("(warning only) ANTHROPIC_API_KEY unset — strategist "
                        "will run permanently defensive")
    hard = [p for p in problems if not p.startswith("(warning")]
    return not hard, "; ".join(problems) or "secrets configured"


def check_key_permissions() -> tuple:
    from orchestrator.exchange_permissions import verify_no_withdrawal_rights
    result = verify_no_withdrawal_rights()
    return result.ok, result.reason


def dry_run_stats(db_path: Path, start_capital: float = 1000.0) -> dict:
    """Closed-trade stats from the dry-run DB (never invented)."""
    if not db_path.exists():
        return {"error": f"{db_path} missing — no dry-run record"}
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT open_date, close_date, close_profit_abs FROM trades "
            "WHERE is_open = 0 AND close_date IS NOT NULL ORDER BY close_date"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return {"error": "no closed trades in dry-run DB"}
    profits = [float(r[2] or 0.0) for r in rows]
    first = datetime.fromisoformat(rows[0][0]).replace(tzinfo=timezone.utc)
    last = datetime.fromisoformat(rows[-1][1]).replace(tzinfo=timezone.utc)
    gross_profit = sum(p for p in profits if p > 0)
    gross_loss = abs(sum(p for p in profits if p < 0))
    equity = start_capital
    peak = start_capital
    max_dd = 0.0
    for p in profits:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)
    return {
        "closed_trades": len(profits),
        "span_days": (last - first).days,
        "profit_factor": (gross_profit / gross_loss) if gross_loss else float("inf"),
        "max_drawdown_pct": max_dd,
        "first": first.date().isoformat(),
        "last": last.date().isoformat(),
    }


def check_dry_run_record(db_path: Path) -> tuple:
    stats = dry_run_stats(db_path)
    if "error" in stats:
        return False, stats["error"]
    problems = []
    if stats["span_days"] < MIN_DRYRUN_DAYS:
        problems.append(f"span {stats['span_days']}d < {MIN_DRYRUN_DAYS}d")
    if stats["closed_trades"] < MIN_CLOSED_TRADES:
        problems.append(f"{stats['closed_trades']} trades < {MIN_CLOSED_TRADES}")
    if stats["profit_factor"] < MIN_PROFIT_FACTOR:
        problems.append(f"PF {stats['profit_factor']:.2f} < {MIN_PROFIT_FACTOR}")
    if stats["max_drawdown_pct"] > MAX_DRAWDOWN_PCT:
        problems.append(f"maxDD {stats['max_drawdown_pct']:.1f}% > {MAX_DRAWDOWN_PCT}%")
    detail = (f"{stats['closed_trades']} trades over {stats['span_days']}d "
              f"({stats['first']}..{stats['last']}), PF {stats['profit_factor']:.2f}, "
              f"maxDD {stats['max_drawdown_pct']:.1f}%")
    return (False, detail + " | FAILS: " + "; ".join(problems)) if problems \
        else (True, detail)


def check_journal(journal_dir: Path) -> tuple:
    files = sorted(journal_dir.glob("*.jsonl"))
    if not files:
        return False, "journal/ empty — S12 audit trail missing"
    return True, f"{len(files)} journal file(s), latest: {files[-1].name}"


def main() -> int:
    print("\n=== SANTINELA GO-LIVE GATE (S11) ===\n")
    checks = [
        ("pytest suite", *check_tests()),
        ("TRADING_MODE", *check_trading_mode()),
        ("config.live.json", *check_live_config(REPO / "user_data/config.live.json")),
        ("secrets", *check_secrets()),
        ("S9 key permissions", *check_key_permissions()),
        ("dry-run record (§7.3)",
         *check_dry_run_record(REPO / "user_data/tradesv3.dryrun.sqlite")),
        ("journal (S12)", *check_journal(REPO / "journal")),
    ]
    failed = False
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        failed = failed or not ok
    if failed:
        print("\nREFUSED: fix every FAIL above. Dry-run remains the mode.\n")
        return 1

    print("\nLocked rules that will govern the live account:")
    print(S_RULES)
    print("Recommended starting stake: only money you can afford to lose "
          "entirely.\nVerify your exchange is MiCA-authorised (ESMA CASP "
          "register) for your country.\n")
    if not sys.stdin.isatty():
        print("REFUSED: interactive confirmation required (no TTY).")
        return 1
    answer = input(f'Type "{CONFIRMATION_PHRASE}" to go live: ').strip()
    if answer != CONFIRMATION_PHRASE:
        print("REFUSED: confirmation phrase mismatch.")
        return 1
    print("\nGate passed. Starting live stack...\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
