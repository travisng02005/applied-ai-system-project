import json
import logging

import pytest

import agent
import eventlog
from eventlog import (JsonLogHandler, append_entry, clear_entries, delete_entries,
                      is_worth_recording, read_entries)


@pytest.fixture
def logger_to(tmp_path):
    """A fresh logger wired to a JSON log in tmp_path; returns (logger, path)."""
    path = tmp_path / "log.json"
    log = logging.getLogger("pawpal.test_eventlog")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    handler = JsonLogHandler(path)
    log.addHandler(handler)
    yield log, path
    log.removeHandler(handler)


def test_records_become_entries_with_time_level_source_and_message(logger_to):
    log, path = logger_to
    log.warning("Disk %s is nearly full", "C:")

    [entry] = read_entries(path)

    assert entry["level"] == "WARNING"
    assert entry["source"] == "test_eventlog"
    assert entry["message"] == "Disk C: is nearly full"
    assert entry["id"] and entry["time"]


def test_exceptions_record_what_failed_and_why(logger_to):
    log, path = logger_to
    try:
        int("not a number")
    except ValueError:
        log.exception("Could not parse the budget")

    [entry] = read_entries(path)

    assert entry["level"] == "ERROR"
    assert entry["error"].startswith("ValueError: invalid literal")
    assert "Traceback" in entry["traceback"]


def test_the_file_is_plain_readable_json(logger_to):
    log, path = logger_to
    log.info("hello")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["entries"][0]["message"] == "hello"


def test_log_is_capped_and_keeps_the_newest_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(eventlog, "MAX_ENTRIES", 3)
    path = tmp_path / "log.json"
    for n in range(5):
        append_entry({"id": str(n), "message": str(n)}, path)
    assert [e["message"] for e in read_entries(path)] == ["2", "3", "4"]


def test_delete_some_entries_then_clear_the_rest(tmp_path):
    path = tmp_path / "log.json"
    for n in "abc":
        append_entry({"id": n, "message": n}, path)

    assert delete_entries(["a", "c", "missing"], path) == 2
    assert [e["id"] for e in read_entries(path)] == ["b"]
    assert clear_entries(path) == 1
    assert read_entries(path) == []
    assert clear_entries(path) == 0


def test_corrupt_log_is_moved_aside_and_logging_continues(tmp_path):
    path = tmp_path / "log.json"
    path.write_text("{ not json", encoding="utf-8")

    assert read_entries(path) == []
    assert list(tmp_path.glob("log.json.corrupt-*"))
    append_entry({"id": "1", "message": "after"}, path)
    assert [e["message"] for e in read_entries(path)] == ["after"]


def test_a_broken_log_file_never_breaks_the_app(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    log = logging.getLogger("pawpal.test_unwritable")
    log.propagate = False
    handler = JsonLogHandler(blocker / "log.json")  # the parent "directory" is a file
    log.addHandler(handler)
    try:
        log.error("this must not raise")  # the handler swallows its own failure
    finally:
        log.removeHandler(handler)


def test_filter_keeps_warnings_and_agent_activity_but_not_routine_info():
    make = lambda name, level: logging.LogRecord(name, level, __file__, 1, "m", None, None)
    assert is_worth_recording(make("pawpal.storage", logging.ERROR))
    assert is_worth_recording(make("pawpal.agent", logging.INFO))
    assert not is_worth_recording(make("pawpal.storage", logging.INFO))
    assert not is_worth_recording(make("pawpal.devreload", logging.INFO))


def test_agent_failures_show_up_in_the_json_log(tmp_path):
    """End to end: a failing Gemini call is recorded with its reason, and the run too."""
    from datetime import date, datetime
    from types import SimpleNamespace

    from pawpal_system import Owner, Pet, Priority, Scheduler, Task

    path = tmp_path / "log.json"
    root = logging.getLogger("pawpal")
    handler = JsonLogHandler(path)
    handler.addFilter(is_worth_recording)
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.INFO)
    try:
        owner = Owner("T", available_minutes_per_day=240)
        pet = Pet("Rex", "dog")
        owner.add_pet(pet)
        d = datetime(2026, 10, 20, 8)
        pet.add_task(Task("Walk", duration_minutes=30, priority=Priority.HIGH, due=d))
        pet.add_task(Task("Groom", duration_minutes=30, priority=Priority.LOW, due=d))

        def boom(**kwargs):
            raise RuntimeError("429 RESOURCE_EXHAUSTED quota")

        llm = agent.LLMFixer(SimpleNamespace(models=SimpleNamespace(generate_content=boom)))
        agent.PlannerAgent(Scheduler(owner), llm).run(date(2026, 10, 20))
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)

    entries = read_entries(path)
    failure = [e for e in entries if e["level"] == "WARNING" and "LLM fixer failed" in e["message"]]
    assert failure and "429 RESOURCE_EXHAUSTED" in failure[0]["message"]
    assert any(e["source"] == "agent" and "Agent run end" in e["message"] for e in entries)
