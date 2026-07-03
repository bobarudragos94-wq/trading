"""Build the per-window comparison table from exported backtest results.

Usage: python3 scripts/summarize_backtests.py [user_data/backtest_results]
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from freqtrade.data.btanalysis import load_backtest_stats

# Window names keyed by backtest start date (must match backtest_suite.sh).
WINDOW_BY_START = {
    "2022-04-01": "bear",
    "2023-10-01": "bull",
    "2024-04-01": "range",
}


def fmt(value, spec=".2f", missing="-"):
    if value is None:
        return missing
    try:
        return format(value, spec)
    except (TypeError, ValueError):
        return str(value)


def main(out_dir: str) -> None:
    rows = []
    for result in sorted(Path(out_dir).glob("*.zip")):
        stats = load_backtest_stats(result)
        for strat_name, s in stats["strategy"].items():
            start_ts = s["backtest_start_ts"]
            if start_ts > 1e12:  # milliseconds
                start_ts /= 1000
            start = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime(
                "%Y-%m-%d"
            )
            total = s.get("total_trades") or 0
            rows.append(
                {
                    "strategy": strat_name,
                    "window": WINDOW_BY_START.get(start, start),
                    "trades": total,
                    "win%": (s.get("wins", 0) / total * 100) if total else None,
                    "profit%": (s.get("profit_total") or 0) * 100,
                    "pf": s.get("profit_factor"),
                    "maxDD%": (s.get("max_drawdown_account") or 0) * 100,
                    "sharpe": s.get("sharpe"),
                    "cagr%": (s.get("cagr") or 0) * 100,
                }
            )
    if not rows:
        print("no backtest results found in", out_dir)
        return

    header = ["strategy", "window", "trades", "win%", "profit%", "pf", "maxDD%",
              "sharpe", "cagr%"]
    widths = {h: max(len(h), 13) for h in header}
    print(" | ".join(h.ljust(widths[h]) for h in header))
    print("-+-".join("-" * widths[h] for h in header))
    order = {"bear": 0, "bull": 1, "range": 2}
    for r in sorted(rows, key=lambda x: (x["strategy"], order.get(x["window"], 9))):
        cells = [
            str(r["strategy"]).ljust(widths["strategy"]),
            str(r["window"]).ljust(widths["window"]),
            fmt(r["trades"], "d").ljust(widths["trades"]),
            fmt(r["win%"]).ljust(widths["win%"]),
            fmt(r["profit%"]).ljust(widths["profit%"]),
            fmt(r["pf"]).ljust(widths["pf"]),
            fmt(r["maxDD%"]).ljust(widths["maxDD%"]),
            fmt(r["sharpe"]).ljust(widths["sharpe"]),
            fmt(r["cagr%"]).ljust(widths["cagr%"]),
        ]
        print(" | ".join(cells))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "user_data/backtest_results")
