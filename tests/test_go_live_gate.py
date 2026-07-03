"""Go-live gate checks (S11): every precondition must fail closed."""

import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "go_live_gate", REPO / "scripts" / "go_live_gate.py"
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def make_db(path: Path, trades: list) -> Path:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, is_open INTEGER, "
        "open_date TEXT, close_date TEXT, close_profit_abs REAL)"
    )
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, (profit, day) in enumerate(trades):
        od = (start + timedelta(days=day)).strftime("%Y-%m-%d %H:%M:%S")
        cd = (start + timedelta(days=day, hours=6)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO trades VALUES (?, 0, ?, ?, ?)", (i, od, cd, profit))
    conn.commit()
    conn.close()
    return path


def good_trades(n=40, span_days=35):
    """~64% winners, PF comfortably above threshold, shallow drawdown."""
    return [((8.0 if i % 3 else -5.0), i * span_days // n) for i in range(n)]


def test_trading_mode_check(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "dry")
    ok, _ = gate.check_trading_mode()
    assert not ok
    monkeypatch.setenv("TRADING_MODE", "live")
    ok, _ = gate.check_trading_mode()
    assert ok


def test_live_config_missing(tmp_path):
    ok, detail = gate.check_live_config(tmp_path / "config.live.json")
    assert not ok and "missing" in detail


def test_live_config_must_be_spot_live_and_secretless(tmp_path):
    path = tmp_path / "config.live.json"
    bad = {"trading_mode": "futures", "dry_run": True, "stake_currency": "USDT",
           "force_entry_enable": True, "max_open_trades": 10,
           "exchange": {"key": "leaked", "secret": "leaked"}}
    path.write_text(json.dumps(bad))
    ok, detail = gate.check_live_config(path)
    assert not ok
    for frag in ("S1", "dry_run", "USDC", "force_entry", "secrets", "S3"):
        assert frag in detail


def test_live_config_valid(tmp_path):
    path = tmp_path / "config.live.json"
    good = {"trading_mode": "spot", "dry_run": False, "stake_currency": "USDC",
            "max_open_trades": 2, "exchange": {"key": "", "secret": ""}}
    path.write_text(json.dumps(good))
    ok, detail = gate.check_live_config(path)
    assert ok, detail


def test_dry_run_record_missing_db(tmp_path):
    ok, detail = gate.check_dry_run_record(tmp_path / "nope.sqlite")
    assert not ok and "missing" in detail


def test_dry_run_record_passes_with_good_history(tmp_path):
    db = make_db(tmp_path / "t.sqlite", good_trades())
    ok, detail = gate.check_dry_run_record(db)
    assert ok, detail


def test_dry_run_record_fails_on_too_few_trades(tmp_path):
    db = make_db(tmp_path / "t.sqlite", good_trades(n=10, span_days=35))
    ok, detail = gate.check_dry_run_record(db)
    assert not ok and "trades" in detail


def test_dry_run_record_fails_on_short_span(tmp_path):
    db = make_db(tmp_path / "t.sqlite", good_trades(n=40, span_days=10))
    ok, detail = gate.check_dry_run_record(db)
    assert not ok and "span" in detail


def test_dry_run_record_fails_on_bad_profit_factor(tmp_path):
    losers = [((-5.0 if i % 2 else 4.0), i) for i in range(40)]
    db = make_db(tmp_path / "t.sqlite", losers)
    ok, detail = gate.check_dry_run_record(db)
    assert not ok and "PF" in detail


def test_dry_run_record_fails_on_deep_drawdown(tmp_path):
    trades = good_trades(n=30, span_days=35) + [(-200.0, 36), (300.0, 37),
                                                (10.0, 38), (10.0, 39)]
    db = make_db(tmp_path / "t.sqlite", trades)
    ok, detail = gate.check_dry_run_record(db)
    assert not ok and "maxDD" in detail


def test_journal_check(tmp_path):
    ok, detail = gate.check_journal(tmp_path)
    assert not ok
    (tmp_path / "2026-01.jsonl").write_text('{"event": "boot_ok"}\n')
    ok, detail = gate.check_journal(tmp_path)
    assert ok


def test_key_permission_check_fails_without_keys(monkeypatch):
    monkeypatch.delenv("EXCHANGE_KEY", raising=False)
    monkeypatch.delenv("EXCHANGE_SECRET", raising=False)
    ok, detail = gate.check_key_permissions()
    assert not ok


def test_unknown_exchange_fails_closed(monkeypatch):
    from orchestrator.exchange_permissions import verify_no_withdrawal_rights
    result = verify_no_withdrawal_rights("bybit", "k", "s")
    assert not result.ok and "probe" in result.reason
