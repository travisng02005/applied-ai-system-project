"""Save and load the owner, pets and tasks as a JSON file.

The file (default: pawpal_data.json next to this module, or the path in the
PAWPAL_DATA_FILE environment variable) holds everything the user enters: owner
name and time budget, preferences, pets and every task with its completion state.

Safety:
  * writes are atomic (temp file, then replace), so a crash mid-save can't leave
    a half-written file behind;
  * a corrupt or unreadable file is never overwritten silently: load_owner() moves
    it aside as "<name>.corrupt-<timestamp>" and raises StorageError;
  * all saves and loads are logged to the "pawpal.storage" logger.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

from fileutil import atomic_write_text, move_aside_corrupt
from pawpal_system import Category, Frequency, Owner, Pet, Priority, Task

logger = logging.getLogger("pawpal.storage")

SCHEMA_VERSION = 1
DEFAULT_PATH = Path(__file__).parent / "pawpal_data.json"


class StorageError(Exception):
    """The data file exists but could not be read as valid PawPal+ data."""


def data_path() -> Path:
    """The data file to use: $PAWPAL_DATA_FILE if set, else the default."""
    return Path(os.environ.get("PAWPAL_DATA_FILE") or DEFAULT_PATH)


# --- to / from plain dicts ----------------------------------------------------


def _encode_pref(value):
    if isinstance(value, time):
        return {"__time__": value.strftime("%H:%M")}
    return value


def _decode_pref(value):
    if isinstance(value, dict) and "__time__" in value:
        hh, mm = value["__time__"].split(":")
        return time(int(hh), int(mm))
    return value


def task_to_dict(task: Task) -> dict:
    return {
        "title": task.title,
        "category": task.category.value,
        "duration_minutes": task.duration_minutes,
        "priority": int(task.priority),
        "due": task.due.isoformat() if task.due else None,
        "frequency": task.frequency.value,
        "completed": task.completed,
        "shared_with": [p.name for p in task.shared_with],
        "available_from": task.available_from.isoformat() if task.available_from else None,
    }


def task_from_dict(data: dict) -> Task:
    return Task(
        title=str(data["title"]),
        category=Category(data["category"]),
        duration_minutes=int(data["duration_minutes"]),
        priority=Priority(int(data["priority"])),
        due=datetime.fromisoformat(data["due"]) if data.get("due") else None,
        frequency=Frequency(data["frequency"]),
        completed=bool(data.get("completed", False)),
        available_from=(date.fromisoformat(data["available_from"])
                        if data.get("available_from") else None),
    )


def owner_to_dict(owner: Owner) -> dict:
    return {
        "version": SCHEMA_VERSION,
        "owner": {
            "name": owner.name,
            "available_minutes_per_day": owner.available_minutes_per_day,
            "preferences": {k: _encode_pref(v) for k, v in owner.preferences.items()},
        },
        "pets": [
            {
                "name": pet.name,
                "species": pet.species,
                "age": pet.age,
                "special_needs": pet.special_needs,
                "tasks": [task_to_dict(t) for t in pet.tasks],
            }
            for pet in owner.pets
        ],
    }


def owner_from_dict(data: dict) -> Owner:
    """Rebuild an Owner; raises StorageError if the data is malformed."""
    try:
        if data.get("version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported data version {data.get('version')!r}")
        info = data["owner"]
        owner = Owner(
            name=str(info["name"]),
            available_minutes_per_day=int(info["available_minutes_per_day"]),
            preferences={k: _decode_pref(v) for k, v in info.get("preferences", {}).items()},
        )
        for p in data["pets"]:
            pet = Pet(str(p["name"]), str(p["species"]), int(p["age"]),
                      str(p.get("special_needs", "")))
            for t in p["tasks"]:
                pet.add_task(task_from_dict(t))
            owner.add_pet(pet)
        # Second pass: pets now exist, so shared tasks can point at them by name.
        by_name = {p.name: p for p in owner.pets}
        for p, saved in zip(owner.pets, data["pets"]):
            for task, t in zip(p.tasks, saved["tasks"]):
                task.shared_with = [by_name[n] for n in t.get("shared_with", [])
                                    if n in by_name and by_name[n] is not p]
        return owner
    except (KeyError, TypeError, ValueError, AttributeError) as err:
        raise StorageError(f"invalid data: {type(err).__name__}: {err}") from err


# --- files ----------------------------------------------------------------------


def save_owner(owner: Owner, path: Optional[Path] = None) -> bool:
    """Write the owner's data to disk atomically. Returns False (and logs) on failure."""
    path = Path(path) if path else data_path()
    text = json.dumps(owner_to_dict(owner), indent=2, ensure_ascii=False)
    try:
        atomic_write_text(path, text)
    except OSError as err:
        logger.error("Could not save %s: %s", path, err)
        return False
    logger.info("Saved %d pet(s), %d task(s) to %s", len(owner.pets),
                len(owner.get_all_tasks()), path)
    return True


def load_owner(path: Optional[Path] = None) -> Optional[Owner]:
    """Load saved data. Returns None if there is no file yet.

    Raises StorageError for an unreadable or invalid file, after moving it aside
    so the next save cannot overwrite it.
    """
    path = Path(path) if path else data_path()
    if not path.exists():
        logger.info("No data file at %s; starting fresh", path)
        return None
    try:
        owner = owner_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, StorageError) as err:
        backup = move_aside_corrupt(path)
        logger.error("Could not load %s (%s); kept a copy at %s", path, err, backup)
        raise StorageError(f"{err} (the file was moved to {backup.name})") from err
    logger.info("Loaded %d pet(s), %d task(s) from %s", len(owner.pets),
                len(owner.get_all_tasks()), path)
    return owner
