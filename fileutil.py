"""Small file helpers shared by the data file (storage.py) and the log file (eventlog.py)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` so readers never see a half-written file.

    The text goes to a temporary file in the same folder, which then replaces the real
    file in one step. Missing folders are created. Raises OSError if the write fails;
    the temporary file is always cleaned up.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def move_aside_corrupt(path: Path) -> Path:
    """Rename an unreadable file to '<name>.corrupt-<timestamp>' so it is never overwritten.

    Returns the new path, or the original path if the rename itself failed.
    """
    path = Path(path)
    backup = path.with_name(f"{path.name}.corrupt-{datetime.now():%Y%m%d-%H%M%S}")
    try:
        os.replace(path, backup)
    except OSError:
        return path
    return backup
