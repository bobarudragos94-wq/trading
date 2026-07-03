"""Append-only JSONL journal (S12).

Every decision — entries, exits, LLM verdicts, guard triggers — is appended
as one JSON line to a monthly journal file. Lines are never rewritten or
deleted by code; the file is opened in append mode and fsync'd per write.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DIR = Path(os.environ.get("SANTINELA_JOURNAL_DIR", "journal"))


def journal_path(now: datetime | None = None, directory: str | Path | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    directory = Path(directory) if directory else DEFAULT_DIR
    return directory / f"{now:%Y-%m}.jsonl"


def append(event: str, payload: dict, directory: str | Path | None = None) -> dict:
    """Append one journal record; returns the record written."""
    now = datetime.now(timezone.utc)
    record = {"ts": now.isoformat(), "event": event, **payload}
    path = journal_path(now, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str, ensure_ascii=False)
    with open(path, "a") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return record


def tail(event: str | None = None, limit: int = 5,
         directory: str | Path | None = None) -> list:
    """Read the most recent records (optionally filtered by event type).

    Scans the current and previous monthly files — enough for 'last N
    verdicts' style queries without loading the full history.
    """
    directory = Path(directory) if directory else DEFAULT_DIR
    files = sorted(directory.glob("*.jsonl"))[-2:]
    records: list = []
    for path in files:
        for line in path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # tolerate a torn write at the tail
            if event is None or rec.get("event") == event:
                records.append(rec)
    return records[-limit:]
