import json
import os
from datetime import date, datetime
from types import SimpleNamespace

import config
from agent import LLMFixer, PlannerAgent, check_plan
from config import load_env_file
from main import build_demo_owner
from pawpal_system import Category, Owner, Pet, Priority, Scheduler, Task

DAY = date(2026, 10, 20)


def at(h, m=0):
    return datetime(2026, 10, 20, h, m)


def make(*tasks, budget=240, **prefs):
    owner = Owner("T", available_minutes_per_day=budget, preferences=prefs)
    pet = Pet("Rex", "dog")
    owner.add_pet(pet)
    for t in tasks:
        pet.add_task(t)
    return Scheduler(owner)


def fake_llm(reply):
    """An LLMFixer whose 'API' returns `reply` (a str, or an Exception to raise)."""
    def create(**kwargs):
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=reply)
    client = SimpleNamespace(models=SimpleNamespace(generate_content=create))
    return LLMFixer(client)


# --- core loop ---------------------------------------------------------------


def test_agent_resolves_demo_conflicts_and_changes_task_times():
    owner = build_demo_owner(DAY)
    scheduler = Scheduler(owner)
    scheduler.generate_schedule(DAY)
    assert check_plan(scheduler, DAY)  # the plain scheduler leaves clashes behind

    result = PlannerAgent(scheduler).run(DAY)

    assert result.resolved
    assert check_plan(scheduler, DAY) == []
    assert result.changes  # the agent actually edited task times
    assert len(result.steps) >= 2  # plan, fix, re-check
    assert scheduler.detect_conflicts() == []


def test_agent_changes_nothing_when_plan_is_already_clean():
    scheduler = make(Task("Walk", due=at(8)), Task("Feed", due=at(9)))

    result = PlannerAgent(scheduler).run(DAY)

    assert result.resolved
    assert result.changes == []
    assert len(result.steps) == 1
    assert len(scheduler.schedule) == 2


def test_undo_restores_original_times():
    scheduler = make(
        Task("Walk", duration_minutes=30, priority=Priority.HIGH, due=at(8)),
        Task("Feed", duration_minutes=30, priority=Priority.LOW, due=at(8)),
    )
    result = PlannerAgent(scheduler).run(DAY)
    assert result.changes

    result.undo()

    assert [t.due for t in scheduler.owner.get_all_tasks()] == [at(8), at(8)]


def test_agent_moves_lower_priority_task_not_higher():
    walk = Task("Walk", duration_minutes=30, priority=Priority.HIGH, due=at(8))
    groom = Task("Groom", duration_minutes=30, priority=Priority.LOW, due=at(8))
    scheduler = make(groom, walk)

    PlannerAgent(scheduler).run(DAY)

    assert walk.due == at(8)
    assert groom.due == at(8, 30)


def test_agent_places_task_the_scheduler_rejected_for_delay():
    # With a 10 minute delay tolerance the second task cannot sit at 08:30 ...
    a = Task("Walk", duration_minutes=30, priority=Priority.HIGH, due=at(8))
    b = Task("Feed", duration_minutes=30, priority=Priority.MEDIUM, due=at(8))
    scheduler = make(a, b, max_delay_minutes=10)
    scheduler.generate_schedule(DAY)
    assert scheduler.unplaced

    result = PlannerAgent(scheduler).run(DAY)

    # ... so the agent moves its requested time and the plan then contains both.
    assert result.resolved
    assert scheduler.unplaced == []
    assert len(scheduler.schedule) == 2


# --- guardrails --------------------------------------------------------------


def test_unfixable_problem_is_reported_not_hidden():
    # Two 60-minute meds at the same time: meds may only move 30 minutes.
    a = Task("Pill A", category=Category.MEDS, duration_minutes=60, due=at(8))
    b = Task("Pill B", category=Category.MEDS, duration_minutes=60, due=at(8))
    scheduler = make(a, b)

    result = PlannerAgent(scheduler).run(DAY)

    assert not result.resolved
    assert result.blocking
    assert (a.due, b.due) == (at(8), at(8))  # nothing moved past the guardrail
    assert any(step.rejected for step in result.steps)


def test_iteration_limit_is_respected():
    a = Task("Pill A", category=Category.MEDS, duration_minutes=60, due=at(8))
    b = Task("Pill B", category=Category.MEDS, duration_minutes=60, due=at(8))
    result = PlannerAgent(make(a, b), max_iterations=1).run(DAY)
    assert len(result.steps) == 1


def test_skipped_high_priority_task_becomes_advice_not_a_loop():
    scheduler = make(
        Task("Hike", duration_minutes=50, priority=Priority.HIGH),
        Task("Swim", duration_minutes=50, priority=Priority.HIGH),
        budget=60,
    )

    result = PlannerAgent(scheduler).run(DAY)

    assert result.resolved  # nothing moving tasks could fix
    assert [i.kind for i in result.advisories] == ["skipped_high"]
    assert len(result.steps) == 1


def test_agent_never_crashes_on_bad_data():
    aware = datetime(2026, 10, 20, 8, 0).astimezone()
    scheduler = make(Task("A", due=aware), Task("B", due=at(8)))

    result = PlannerAgent(scheduler).run(DAY)  # must not raise

    assert result is not None


# --- LLM fixer ---------------------------------------------------------------

CLASH = dict(duration_minutes=30)


def clash_scheduler():
    a = Task("Walk", priority=Priority.HIGH, due=at(8), **CLASH)
    b = Task("Groom", priority=Priority.LOW, due=at(8), **CLASH)
    return make(a, b), a, b


def test_llm_moves_are_applied_through_the_guardrails():
    scheduler, a, b = clash_scheduler()
    reply = json.dumps({"moves": [{"task_id": "T2", "new_time": "10:00", "reason": "free"}]})

    result = PlannerAgent(scheduler, fake_llm(reply)).run(DAY)

    assert result.used_llm
    assert result.resolved
    assert b.due == at(10)
    assert a.due == at(8)


def test_llm_garbage_falls_back_to_rules():
    scheduler, a, b = clash_scheduler()

    result = PlannerAgent(scheduler, fake_llm("not json at all")).run(DAY)

    assert result.resolved  # the rule-based fixer took over
    assert b.due == at(8, 30)


def test_llm_exception_falls_back_to_rules():
    scheduler, a, b = clash_scheduler()

    result = PlannerAgent(scheduler, fake_llm(RuntimeError("network down"))).run(DAY)

    assert result.resolved


def test_llm_unsafe_moves_are_rejected():
    med = Task("Pill", category=Category.MEDS, duration_minutes=5, due=at(8))
    walk = Task("Walk", duration_minutes=30, due=at(8))
    scheduler = make(med, walk)
    reply = json.dumps({"moves": [
        {"task_id": "T1", "new_time": "14:00", "reason": "meds 6h late"},  # too far
        {"task_id": "T99", "new_time": "09:00", "reason": "no such task"},  # unknown id
        {"task_id": "T2", "new_time": "25:99", "reason": "bad time"},       # bad time
    ]})

    result = PlannerAgent(scheduler, fake_llm(reply)).run(DAY)

    assert med.due == at(8)  # the medication did not move 6 hours
    assert any("exceeds" in r for step in result.steps for r in step.rejected)
    assert result.resolved  # the rules then fixed the clash safely


def test_llm_fixer_unavailable_without_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert LLMFixer.from_env() is None


def test_llm_fixer_accepts_fenced_json():
    reply = '```json\n{"moves": [{"task_id": "T1", "new_time": "09:00", "reason": "x"}]}\n```'
    assert fake_llm(reply).propose({})[0]["new_time"] == "09:00"


def test_env_file_is_loaded_but_never_overrides_the_shell(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(["# comment", "GEMINI_API_KEY='from-file'", "GEMINI_MODEL=file-model"]),
        encoding="utf-8",
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", "from-shell")

    load_env_file(env)

    assert os.environ["GEMINI_API_KEY"] == "from-file"
    assert os.environ["GEMINI_MODEL"] == "from-shell"
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_result_says_who_really_made_the_fixes():
    # Gemini configured and useful -> credited.
    scheduler, _, b = clash_scheduler()
    reply = json.dumps({"moves": [{"task_id": "T2", "new_time": "10:00", "reason": "free"}]})
    assert PlannerAgent(scheduler, fake_llm(reply)).run(DAY).fixer_used == "Gemini"

    # Gemini configured but failing -> the rules did the work, and the result says so.
    scheduler, _, _ = clash_scheduler()
    failed = PlannerAgent(scheduler, fake_llm(RuntimeError("429 quota"))).run(DAY)
    assert failed.used_llm  # it was configured ...
    assert failed.fixer_used.startswith("rules (Gemini was unavailable")  # ... but did not help
    assert "fixes by: rules (Gemini was unavailable" in failed.summary()

    # No Gemini at all.
    scheduler, _, _ = clash_scheduler()
    assert PlannerAgent(scheduler).run(DAY).fixer_used == "rules"


def test_long_api_errors_are_shortened_in_the_log(caplog):
    scheduler, _, _ = clash_scheduler()
    huge = RuntimeError("429 RESOURCE_EXHAUSTED " + "x" * 5000)

    with caplog.at_level("WARNING", logger="pawpal.agent"):
        PlannerAgent(scheduler, fake_llm(huge)).run(DAY)

    [message] = [r.getMessage() for r in caplog.records if "LLM fixer failed" in r.getMessage()]
    assert "429 RESOURCE_EXHAUSTED" in message and len(message) < 400
