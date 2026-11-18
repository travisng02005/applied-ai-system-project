"""A JSON activity log the user can view, trim and clear from the app.

JsonLogHandler plugs into Python's logging. Everything the agent does, and every warning
or error from anywhere in PawPal+ (a failed save, a Gemini error, a rejected edit), becomes
one entry in pawpal_log.json with a time, level, source, message and, for exceptions, the
error and its traceback. That answers "what failed, and why" without reading a text file.

Design rules:
  * logging must never break the app: the handler swallows its own errors;
  * the file is bounded (the oldest entries are dropped past MAX_ENTRIES);
  * writes are atomic, and a corrupt log file is moved aside, not crashed on;
  * the plain-text pawpal_agent.log still keeps the full detail.

Location: pawpal_log.json next to this module, or $PAWPAL_LOG_FILE.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fileutil import atomic_write_text, move_aside_corrupt

DEFAULT_PATH = Path(__file__).parent / "pawpal_log.json"
MAX_ENTRIES = 500
VERSION = 1
LEVELS = ("ERROR", "WARNING", "INFO")

_lock = threading.Lock()


def log_path() -> Path:
    """The log file to use: $PAWPAL_LOG_FILE if set, else the default."""
    return Path(os.environ.get("PAWPAL_LOG_FILE") or DEFAULT_PATH)


def _read(path: Path) -> list[dict]:
    """Entries on disk, oldest first. A corrupt file is moved aside and treated as empty."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        entries = json.loads(text)["entries"]
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise ValueError("entries is not a list of objects")
        return entries
    except (ValueError, KeyError, TypeError):
        move_aside_corrupt(path)
        return []


def _write(path: Path, entries: list[dict]) -> bool:
    """Atomically replace the log file. Returns False on failure."""
    try:
        atomic_write_text(path, json.dumps({"version": VERSION, "entries": entries}, indent=2,
                                           ensure_ascii=False))
        return True
    except OSError:
        return False


def append_entry(entry: dict, path: Optional[Path] = None) -> bool:
    """Add one entry, dropping the oldest ones beyond MAX_ENTRIES."""
    path = Path(path) if path else log_path()
    with _lock:
        entries = _read(path)
        entries.append(entry)
        return _write(path, entries[-MAX_ENTRIES:])


def read_entries(path: Optional[Path] = None) -> list[dict]:
    """All entries, oldest first (an empty list if there is no log yet)."""
    path = Path(path) if path else log_path()
    with _lock:
        return _read(path)


def delete_entries(ids: list[str], path: Optional[Path] = None) -> int:
    """Remove the entries with these ids; returns how many were removed."""
    path = Path(path) if path else log_path()
    wanted = set(ids)
    with _lock:
        entries = _read(path)
        kept = [e for e in entries if e.get("id") not in wanted]
        removed = len(entries) - len(kept)
        if removed:
            _write(path, kept)
        return removed


def clear_entries(path: Optional[Path] = None) -> int:
    """Remove every entry; returns how many there were."""
    path = Path(path) if path else log_path()
    with _lock:
        count = len(_read(path))
        if count:
            _write(path, [])
        return count


class JsonLogHandler(logging.Handler):
    """A logging handler that appends each record to the JSON log."""

    def __init__(self, path: Optional[Path] = None, level: int = logging.INFO):
        super().__init__(level)
        self.path = path

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "id": uuid.uuid4().hex[:12],
                "time": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
                "level": record.levelname,
                "source": record.name.removeprefix("pawpal.") or "pawpal",
                "message": record.getMessage(),
            }
            if record.exc_info and record.exc_info[1] is not None:
                kind, err = record.exc_info[0].__name__, record.exc_info[1]
                entry["error"] = f"{kind}: {err}"
                entry["traceback"] = "".join(traceback.format_exception(*record.exc_info))
            append_entry(entry, self.path)
        except Exception:  # logging must never take the app down
            pass


def is_worth_recording(record: logging.LogRecord) -> bool:
    """Keep every warning and error, plus the agent's own activity.

    Routine INFO from other parts (every autosave, every module reload) would drown the
    entries that matter, so those only go to the plain-text log.
    """
    return record.levelno >= logging.WARNING or record.name.startswith("pawpal.agent")
