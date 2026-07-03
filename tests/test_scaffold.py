"""Scaffold sanity checks: repo layout, config validity, secret hygiene."""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

REQUIRED_FILES = [
    "docker-compose.yml",
    "docker-compose.live.yml",
    "Dockerfile.brain",
    "Makefile",
    ".env.example",
    ".gitignore",
    "README.md",
    "user_data/config.dryrun.json",
    "user_data/config.live.json.example",
    "user_data/runtime/overrides.example.json",
]


def test_required_files_exist():
    missing = [f for f in REQUIRED_FILES if not (REPO / f).exists()]
    assert not missing, f"missing scaffold files: {missing}"


def test_configs_are_valid_json():
    for name in (
        "user_data/config.dryrun.json",
        "user_data/config.live.json.example",
        "user_data/runtime/overrides.example.json",
    ):
        json.loads((REPO / name).read_text())


def test_dryrun_config_is_spot_dry_and_usdc():
    cfg = json.loads((REPO / "user_data/config.dryrun.json").read_text())
    assert cfg["trading_mode"] == "spot"  # S1
    assert cfg["dry_run"] is True
    assert cfg["dry_run_wallet"] == 1000
    assert cfg["stake_currency"] == "USDC"
    assert cfg["max_open_trades"] <= 4  # S3


def test_live_config_example_is_spot():
    cfg = json.loads((REPO / "user_data/config.live.json.example").read_text())
    assert cfg["trading_mode"] == "spot"  # S1
    assert cfg["max_open_trades"] <= 4  # S3


def test_no_secrets_in_configs():
    """Config files must not carry credentials — env vars only."""
    for name in (
        "user_data/config.dryrun.json",
        "user_data/config.live.json.example",
    ):
        cfg = json.loads((REPO / name).read_text())
        assert cfg["exchange"]["key"] == ""
        assert cfg["exchange"]["secret"] == ""
        assert cfg["telegram"]["token"] == ""
        assert cfg["api_server"]["jwt_secret_key"] == ""


def test_gitignore_covers_secrets():
    gitignore = (REPO / ".gitignore").read_text()
    for entry in (".env", "user_data/config.live.json", "journal/*"):
        assert entry in gitignore, f"{entry} not git-ignored"


def test_env_example_has_required_keys():
    env = (REPO / ".env.example").read_text()
    for key in (
        "TRADING_MODE=dry",
        "ANTHROPIC_API_KEY=",
        "EXCHANGE_ID=kraken",
        "EXCHANGE_KEY=",
        "EXCHANGE_SECRET=",
        "TELEGRAM_TOKEN=",
        "TELEGRAM_CHAT_ID=",
    ):
        assert key in env, f"{key} missing from .env.example"
