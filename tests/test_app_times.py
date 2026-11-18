"""The requested time must reach the saved JSON file when a task is added or edited."""

import json
from datetime import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from pawpal_system import Pet

TIME_LABEL = "Time (leave empty for any time)"
APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A running app with one pet, saving into tmp_path; returns (AppTest, saved_dues)."""
    data = tmp_path / "data.json"
    monkeypatch.setenv("PAWPAL_DATA_FILE", str(data))
    monkeypatch.setenv("PAWPAL_LOG_FILE", str(tmp_path / "log.json"))
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.session_state.owner.add_pet(Pet("Rex", "dog"))
    at.run()

    def saved():
        pets = json.loads(data.read_text(encoding="utf-8"))["pets"]
        return {t["title"]: (t["due"][-8:] if t["due"] else None) for p in pets for t in p["tasks"]}

    return at, saved


def button(at, text):
    return next(b for b in at.button if text in b.label)


def times(at):
    return [w for w in at.time_input if w.label == TIME_LABEL]


def titles(at):
    return [w for w in at.text_input if w.label == "Task title"]


def test_a_time_entered_when_adding_is_saved_to_the_file(app):
    at, saved = app
    titles(at)[0].set_value("Walk")
    times(at)[0].set_value(time(9, 30))

    button(at, "Add task").click().run()

    assert saved() == {"Walk": "09:30:00"}  # no checkbox to forget


def test_leaving_the_time_empty_saves_a_flexible_task_and_the_field_resets(app):
    at, saved = app
    titles(at)[0].set_value("Walk")
    times(at)[0].set_value(time(9, 30))
    button(at, "Add task").click().run()

    assert times(at)[0].value is None  # the next task does not inherit 09:30
    titles(at)[0].set_value("Brush")
    button(at, "Add task").click().run()

    assert saved() == {"Walk": "09:30:00", "Brush": None}


def test_editing_a_task_can_change_and_remove_its_time(app):
    at, saved = app
    titles(at)[0].set_value("Walk")
    times(at)[0].set_value(time(9, 30))
    button(at, "Add task").click().run()

    times(at)[-1].set_value(time(14, 5))  # the edit panel's field
    next(b for b in at.button if "💾" in b.label).click().run()
    assert saved() == {"Walk": "14:05:00"}

    next(c for c in at.checkbox if "flexible" in c.label).check()
    next(b for b in at.button if "💾" in b.label).click().run()
    assert saved() == {"Walk": None}


def picker(at):
    return next(s for s in at.selectbox if s.label == "Task to edit")


def test_completing_a_task_removes_it_from_the_edit_list(app):
    at, saved = app
    for title in ("Walk", "Feed"):
        titles(at)[0].set_value(title)
        button(at, "Add task").click().run()
    assert len(picker(at).options) == 2

    next(b for b in at.button if "Done" in b.label).click().run()  # finishes the first task

    assert not at.exception
    assert len(picker(at).options) == 1  # the finished task can no longer be picked
    remaining = at.session_state.owner.get_all_tasks()
    assert sorted(t.title for t in remaining if not t.completed) == ["Feed"]
    assert [t.title for t in remaining if t.completed] == ["Walk"]
    assert "Walk" not in picker(at).options[0] and "Feed" in picker(at).options[0]


def test_the_edit_panel_disappears_when_nothing_is_left_to_edit(app):
    at, saved = app
    titles(at)[0].set_value("Walk")
    button(at, "Add task").click().run()

    next(b for b in at.button if "Done" in b.label).click().run()

    assert not at.exception
    assert not [s for s in at.selectbox if s.label == "Task to edit"]


def test_a_stale_or_wrong_typed_remembered_choice_does_not_crash(app):
    at, saved = app
    titles(at)[0].set_value("Walk")
    button(at, "Add task").click().run()

    at.session_state["edit_task_pick"] = "Rex: Walk (flexible)"  # what the crash report had: a str
    at.run()

    assert not at.exception
    assert len(picker(at).options) == 1


def test_new_tasks_default_to_medium_priority_so_nothing_starts_as_urgent(app):
    at, saved = app
    add_form_priority = next(s for s in at.selectbox if s.label == "Priority")
    assert add_form_priority.value.name == "MEDIUM"


def test_the_page_says_it_is_not_medical_advice(app):
    at, saved = app
    assert any("not veterinary or medical advice" in c.value for c in at.caption)
