"""Tests for /guard, /verdict and the two-step /panic flow."""

import time
from unittest.mock import MagicMock

import pytest

import strategist.telegram_commands as tc
from strategist import journal


@pytest.fixture
def cmds(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "STATE_DIR", tmp_path)
    monkeypatch.setattr(journal, "DEFAULT_DIR", tmp_path / "journal")
    api = MagicMock()
    c = tc.TelegramCommands(api=api, token="fake-token", chat_id="42")
    sent = []
    c._send = lambda text: sent.append(text)
    return c, api, sent


def msg(text, chat="42"):
    return {"update_id": 1, "message": {"chat": {"id": chat}, "text": text}}


def test_wrong_chat_ignored(cmds):
    c, api, sent = cmds
    c.handle_update(msg("/panic", chat="666"))
    assert sent == [] and c.panic_armed_at is None


def test_guard_command(cmds):
    c, _, sent = cmds
    c.handle_update(msg("/guard"))
    assert len(sent) == 1
    assert "Guard status" in sent[0]
    assert "S7 kill switch" in sent[0]


def test_verdict_command_empty_then_populated(cmds, tmp_path):
    c, _, sent = cmds
    c.handle_update(msg("/verdict"))
    assert "No verdict" in sent[-1]
    journal.append("verdict", {"verdict_id": "abc123",
                               "raw_verdict": {"regime": "trend"},
                               "clamped": {"risk_mode": "neutral"},
                               "rationale": "test"})
    c.handle_update(msg("/verdict"))
    assert "abc123" in sent[-1]
    assert "neutral" in sent[-1]


def test_panic_requires_confirmation(cmds):
    c, api, sent = cmds
    c.handle_update(msg("/panic"))
    assert c.panic_armed_at is not None
    api.stopentry.assert_not_called()

    c.handle_update(msg("CONFIRM PANIC"))
    api.stopentry.assert_called_once()
    api.forceexit_all.assert_called_once()
    assert "PANIC executed" in sent[-1]
    assert c.panic_armed_at is None


def test_panic_confirmation_without_arming_is_noop(cmds):
    c, api, sent = cmds
    c.handle_update(msg("CONFIRM PANIC"))
    api.stopentry.assert_not_called()
    assert "Send /panic first" in sent[-1]


def test_panic_disarmed_by_other_message(cmds):
    c, api, sent = cmds
    c.handle_update(msg("/panic"))
    c.handle_update(msg("actually never mind"))
    assert c.panic_armed_at is None
    c.handle_update(msg("CONFIRM PANIC"))
    api.stopentry.assert_not_called()


def test_panic_confirmation_expires(cmds, monkeypatch):
    c, api, sent = cmds
    c.handle_update(msg("/panic"))
    c.panic_armed_at -= tc.PANIC_CONFIRM_WINDOW_SECONDS + 1
    c.handle_update(msg("CONFIRM PANIC"))
    api.stopentry.assert_not_called()
    assert "expired" in sent[-1] or "Send /panic first" in sent[-1]


def test_panic_reports_partial_failures(cmds):
    c, api, sent = cmds
    api.stopentry.side_effect = ConnectionError("down")
    c.handle_update(msg("/panic"))
    c.handle_update(msg("CONFIRM PANIC"))
    api.forceexit_all.assert_called_once()  # still attempts the exit
    assert "FAILED" in sent[-1]


def test_disabled_without_token():
    c = tc.TelegramCommands(api=MagicMock(), token="", chat_id="42")
    assert not c.enabled
    c.poll()  # must be a silent no-op
