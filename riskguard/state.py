"""Persistent RiskGuard state: equity high-water mark, rolling PnL window,
breaker timers and the kill marker (S7).

Design notes:
- State is a plain dataclass serialised to JSON with atomic replace, so a
  crash mid-write can never corrupt the previous snapshot.
- The S7 kill marker is a SEPARATE plain file (``KILLED``), deliberately
  independent of the JSON state: even if the JSON is deleted or corrupted,
  the kill marker survives and keeps the system stopped.
- Restart after S7 requires BOTH manual removal of the ``KILLED`` file AND
  an explicit acknowledgement flag (``--acknowledge-drawdown`` /
  ``ACKNOWLEDGE_DRAWDOWN=1``). Neither alone is sufficient.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATE_FILENAME = "state.json"
KILL_FILENAME = "KILLED"


@dataclass
class GuardState:
    """Mutable guard state. All timestamps are ISO-8601 UTC strings."""

    hwm: float = 0.0
    last_equity: float = 0.0
    # Rolling window of (iso_timestamp, equity) samples for the S6 breaker.
    equity_samples: list = field(default_factory=list)
    consecutive_losses: int = 0
    s6_until: str | None = None
    s8_until: str | None = None
    killed: bool = False
    killed_at: str | None = None
    updated_at: str | None = None


def _state_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / STATE_FILENAME


def kill_file_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / KILL_FILENAME


def save_state(state_dir: str | Path, state: GuardState) -> None:
    """Atomically persist state (write temp file, fsync, rename)."""
    path = _state_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(asdict(state), fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_state(state_dir: str | Path) -> GuardState:
    """Load state; a missing or corrupted file yields a fresh state.

    A corrupted JSON never bricks the system because the S7 kill marker is
    stored as a separate file — ``is_killed()`` does not depend on the JSON.
    """
    path = _state_path(state_dir)
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return GuardState()
    known = {f for f in GuardState.__dataclass_fields__}
    return GuardState(**{k: v for k, v in raw.items() if k in known})


def write_kill_file(state_dir: str | Path, reason: str, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    path = kill_file_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{now.isoformat()} {reason}\n")


def is_killed(state_dir: str | Path) -> bool:
    return kill_file_path(state_dir).exists()


def try_resume_after_kill(
    state_dir: str | Path, state: GuardState, acknowledged: bool
) -> tuple[bool, str]:
    """Gate for restarting after an S7 kill (see module docstring).

    Returns (ok, reason). On success the killed flag is cleared and the
    high-water mark is re-based to current equity so the system does not
    instantly re-trip; the caller must persist the state afterwards.
    """
    if is_killed(state_dir):
        return (
            False,
            "S7 kill marker present — a human must review and manually remove "
            f"'{kill_file_path(state_dir)}' before restart",
        )
    if state.killed:
        if not acknowledged:
            return (
                False,
                "state records an S7 kill — restart requires the explicit "
                "--acknowledge-drawdown flag (ACKNOWLEDGE_DRAWDOWN=1)",
            )
        state.killed = False
        state.killed_at = None
        if state.last_equity > 0:
            state.hwm = state.last_equity
        state.equity_samples = []
        return True, "S7 kill acknowledged — high-water mark re-based to current equity"
    return True, "no kill state"
