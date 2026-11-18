"""App-wide setup that is not about the agent itself: the .env file and logging.

* load_env_file() reads KEY=value lines (for example GEMINI_API_KEY) from `.env`.
* configure_logging() sends PawPal+'s logs to a text file and to the JSON activity log.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from eventlog import JsonLogHandler, is_worth_recording

LOG_FILE = "pawpal_agent.log"
ENV_FILE = Path(__file__).parent / ".env"  # where you can paste GEMINI_API_KEY


def configure_logging(path: str = LOG_FILE, level: int = logging.INFO) -> None:
    """Send PawPal+'s logs to a text file and to the JSON activity log.

    Idempotent, so it is safe to call on every Streamlit rerun. The text file keeps
    everything; the JSON log (viewable in the app) keeps warnings, errors and the
    agent's own activity.
    """
    root = logging.getLogger("pawpal")  # parent of pawpal.agent, .storage, .devreload, .app
    root.setLevel(level)
    if not any(getattr(h, "_pawpal", False) for h in root.handlers):
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handler._pawpal = True  # marks our handler so we never add it twice
        root.addHandler(handler)
    if not any(getattr(h, "_pawpal_json", False) for h in root.handlers):
        json_handler = JsonLogHandler()
        json_handler.addFilter(is_worth_recording)
        json_handler._pawpal_json = True
        root.addHandler(json_handler)


def load_env_file(path: Optional[Path] = None) -> None:
    """Load KEY=value lines from the .env file into os.environ.

    Variables already set in the real environment win, so a .env file never
    overrides what you export in your shell. A missing file is fine.
    """
    path = ENV_FILE if path is None else path
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))
