import pytest
from pawpal_system import Pet, Task


def test_mark_complete_changes_task_status():
    task = Task("Morning walk")
    assert task.completed is False

    task.mark_complete()

    assert task.completed is True


def test_adding_task_to_pet_increases_task_count():
    pet = Pet("Buddy", "dog")
    assert len(pet.tasks) == 0

    pet.add_task(Task("Morning walk"))

    assert len(pet.tasks) == 1


from datetime import datetime, timedelta

from pawpal_system import Category, Frequency, Owner, Priority, Scheduler

D = datetime(2026, 10, 20)


def make(*pets):
    owner = Owner("T")
    for p in pets:
        owner.add_pet(p)
    return owner, Scheduler(owner)


def test_sort_tasks_by_time_puts_undated_last():
    pet = Pet("Buddy", "dog")
    late = Task("late", due=D.replace(hour=18))
    free = Task("free")
    early = Task("early", due=D.replace(hour=8))
    for t in (late, free, early):
        pet.add_task(t)
    _, s = make(pet)
    assert [t.title for t in s.sort_tasks_by_time()] == ["early", "late", "free"]


def test_sort_tasks_by_time_returns_chronological_order_across_pets():
    a, b = Pet("Buddy", "dog"), Pet("Mia", "cat")
    a.add_task(Task("evening", due=D.replace(hour=19)))
    b.add_task(Task("morning", due=D.replace(hour=7)))
    a.add_task(Task("noon", due=D.replace(hour=12)))
    b.add_task(Task("tomorrow", due=D.replace(hour=6) + timedelta(days=1)))
    _, s = make(a, b)
    titles = [t.title for t in s.sort_tasks_by_time()]
    assert titles == ["morning", "noon", "evening", "tomorrow"]


def test_sort_by_time_returns_scheduled_items_in_chronological_order():
    pet = Pet("Buddy", "dog")
    pet.add_task(Task("late", duration_minutes=20, priority=Priority.HIGH,
                      due=D.replace(hour=17)))
    pet.add_task(Task("early", duration_minutes=20, priority=Priority.LOW,
                      due=D.replace(hour=9)))
    pet.add_task(Task("mid", duration_minutes=20, priority=Priority.MEDIUM,
                      due=D.replace(hour=13)))
    s = plan([pet])
    starts = [i.start for i in s.sort_by_time()]
    assert starts == sorted(starts)
    assert [i.task.title for i in s.sort_by_time()] == ["early", "mid", "late"]


def test_filter_by_pet_name_status_and_category():
    a, b = Pet("Buddy", "dog"), Pet("Mia", "cat")
    walk = Task("walk", category=Category.WALK)
    meds = Task("meds", category=Category.MEDS)
    a.add_task(walk)
    b.add_task(meds)
    meds.mark_complete()
    _, s = make(a, b)
    assert s.filter_tasks(pet="buddy") == [walk]
    assert s.filter_tasks(pet=b, status=True) == [meds]
    assert s.filter_tasks(status=False) == [walk]
    assert s.filter_tasks(category=Category.MEDS) == [meds]


def test_daily_recurrence_creates_next_day_task():
    pet = Pet("Buddy", "dog")
    t = Task("walk", due=D.replace(hour=8), frequency=Frequency.DAILY)
    pet.add_task(t)
    nxt = t.mark_complete()
    assert nxt.due == D.replace(hour=8) + timedelta(days=1)
    assert not nxt.completed and nxt in pet.tasks and len(pet.tasks) == 2
    assert t.mark_complete() is None  # no double-spawn


def test_late_completion_skips_past_occurrences():
    t = Task("walk", due=D.replace(hour=8), frequency=Frequency.DAILY)
    nxt = t.mark_complete(completed_at=D + timedelta(days=3, hours=9))
    assert nxt.due == D.replace(hour=8) + timedelta(days=4)


def test_non_recurring_task_spawns_nothing():
    pet = Pet("Buddy", "dog")
    t = Task("bath", due=D)
    pet.add_task(t)
    assert t.mark_complete() is None and len(pet.tasks) == 1


def test_task_conflicts_detected_across_pets_and_ignore_touching_and_undated():
    a, b = Pet("Buddy", "dog"), Pet("Mia", "cat")
    a.add_task(Task("walk", duration_minutes=30, due=D.replace(hour=8)))
    b.add_task(Task("meds", duration_minutes=5, due=D.replace(hour=8, minute=15)))
    b.add_task(Task("play", duration_minutes=10, due=D.replace(hour=8, minute=30)))
    b.add_task(Task("free"))
    _, s = make(a, b)
    pairs = s.detect_task_conflicts()
    assert [(x.title, y.title) for x, y in pairs] == [("walk", "meds")]
    assert "different pets" in s.conflict_warnings()[0]
    assert s.detect_task_conflicts(on_date=(D + timedelta(days=1)).date()) == []


def test_completed_tasks_do_not_conflict():
    a = Pet("Buddy", "dog")
    t1 = Task("x", due=D)
    a.add_task(t1)
    a.add_task(Task("y", due=D))
    t1.mark_complete()
    _, s = make(a)
    assert s.detect_task_conflicts() == []


def test_generated_schedule_has_no_overlaps():
    a = Pet("Buddy", "dog")
    a.add_task(Task("x", duration_minutes=30, due=D.replace(hour=8)))
    a.add_task(Task("y", duration_minutes=30, due=D.replace(hour=8)))
    _, s = make(a)
    s.generate_schedule(D.date())
    assert s.detect_conflicts() == []


def test_scheduler_complete_task_creates_next_weekly_occurrence():
    pet = Pet("Mia", "cat")
    t = Task("bath", due=D.replace(hour=10), frequency=Frequency.WEEKLY)
    pet.add_task(t)
    _, s = make(pet)
    nxt = s.complete_task(t, now=D.replace(hour=11))
    assert nxt.due == D.replace(hour=10) + timedelta(weeks=1)
    assert nxt.pet is pet and nxt in pet.tasks and t.completed


def test_same_pet_tasks_at_exact_same_time_are_flagged():
    pet = Pet("Buddy", "dog")
    pet.add_task(Task("walk", due=D.replace(hour=8)))
    pet.add_task(Task("feed", due=D.replace(hour=8)))
    _, s = make(pet)
    (warning,) = s.conflict_warnings()
    assert "same time" in warning and "same pet" in warning


def test_conflict_warnings_can_be_limited_to_one_pet():
    a, b, c = Pet("Max", "dog"), Pet("Maxine", "cat"), Pet("Zed", "cat")
    a.add_task(Task("x", due=D))
    b.add_task(Task("y", due=D))
    c.add_task(Task("z", due=D + timedelta(hours=5)))
    _, s = make(a, b, c)
    assert len(s.conflict_warnings(pet=a)) == 1
    assert s.conflict_warnings(pet=c) == []


def test_conflict_check_returns_warning_instead_of_crashing_on_bad_data():
    from datetime import timezone
    pet = Pet("Buddy", "dog")
    pet.add_task(Task("aware", due=D.replace(tzinfo=timezone.utc)))
    pet.add_task(Task("naive", due=D))
    _, s = make(pet)
    (warning,) = s.conflict_warnings()
    assert warning.startswith("Could not check for conflicts")


def plan(pets, budget=120, **prefs):
    owner = Owner("T", available_minutes_per_day=budget, preferences=prefs)
    for p in pets:
        owner.add_pet(p)
    s = Scheduler(owner)
    s.generate_schedule(D.date())
    return s


def test_knapsack_prefers_two_short_tasks_over_one_long_one():
    pet = Pet("Rex", "dog")
    pet.add_task(Task("long", duration_minutes=50))
    pet.add_task(Task("a", duration_minutes=25))
    pet.add_task(Task("b", duration_minutes=25))
    s = plan([pet], budget=60)
    assert sorted(i.task.title for i in s.schedule) == ["a", "b"]
    assert [t.title for t in s.skipped_tasks] == ["long"]


def test_high_priority_outweighs_several_low_tasks():
    pet = Pet("Rex", "dog")
    pet.add_task(Task("big", duration_minutes=40, priority=Priority.HIGH))
    pet.add_task(Task("l1", duration_minutes=20, priority=Priority.LOW))
    pet.add_task(Task("l2", duration_minutes=20, priority=Priority.LOW))
    s = plan([pet], budget=40)
    assert [i.task.title for i in s.schedule] == ["big"]


def test_medication_is_mandatory_even_when_low_priority():
    pet = Pet("Mia", "cat")
    pet.add_task(Task("meds", category=Category.MEDS, duration_minutes=30,
                      priority=Priority.LOW))
    pet.add_task(Task("play", duration_minutes=30, priority=Priority.HIGH))
    s = plan([pet], budget=30)
    assert [i.task.title for i in s.schedule] == ["meds"]


def test_over_budget_medication_is_kept_with_a_warning():
    pet = Pet("Mia", "cat")
    pet.add_task(Task("meds", category=Category.MEDS, duration_minutes=30))
    s = plan([pet], budget=10)
    assert len(s.schedule) == 1 and "Medication" in s.warnings[0]


def test_overdue_task_gets_a_score_boost():
    pet = Pet("Rex", "dog")
    old = Task("old", due=D - timedelta(days=1))
    new = Task("new")
    _, s = make(pet)
    assert s._score(old, D.date()) > s._score(new, D.date())


def test_dated_task_keeps_its_time_and_flexible_task_fills_the_gap():
    pet = Pet("Rex", "dog")
    pet.add_task(Task("noon", duration_minutes=30, due=D.replace(hour=12)))
    pet.add_task(Task("free", duration_minutes=30))
    s = plan([pet])
    times = {i.task.title: i.start.hour for i in s.schedule}
    assert times == {"noon": 12, "free": 8}


def test_clashing_task_shifts_to_next_free_slot_with_reason():
    pet = Pet("Rex", "dog")
    pet.add_task(Task("a", duration_minutes=30, priority=Priority.HIGH, due=D.replace(hour=9)))
    pet.add_task(Task("b", duration_minutes=30, priority=Priority.LOW, due=D.replace(hour=9)))
    s = plan([pet])
    by = {i.task.title: i for i in s.schedule}
    assert by["a"].start.hour == 9 and by["b"].start == D.replace(hour=9, minute=30)
    assert "shifted +30 min" in by["b"].reason
    assert s.detect_conflicts() == []


def test_task_beyond_delay_tolerance_is_reported_unplaced():
    pet = Pet("Rex", "dog")
    pet.add_task(Task("a", duration_minutes=120, priority=Priority.HIGH, due=D.replace(hour=9)))
    pet.add_task(Task("b", duration_minutes=10, priority=Priority.LOW, due=D.replace(hour=9)))
    s = plan([pet], budget=200, max_delay_minutes=60)
    assert [i.task.title for i in s.schedule] == ["a"]
    (task, why), = s.unplaced
    assert task.title == "b" and "within 60 min" in why


def test_unplaced_task_frees_its_gap_for_later_tasks():
    pet = Pet("Rex", "dog")
    pet.add_task(Task("a", duration_minutes=120, priority=Priority.HIGH, due=D.replace(hour=9)))
    pet.add_task(Task("b", duration_minutes=10, priority=Priority.MEDIUM, due=D.replace(hour=9)))
    pet.add_task(Task("c", duration_minutes=10, priority=Priority.LOW))
    s = plan([pet], budget=200, max_delay_minutes=60)
    starts = {i.task.title: i.start for i in s.schedule}
    assert starts["c"] == D.replace(hour=8)  # fills before the 09:00 block


def test_schedule_never_exceeds_budget_or_overlaps():
    pet = Pet("Rex", "dog")
    for i in range(8):
        pet.add_task(Task(f"t{i}", duration_minutes=10 + 7 * i,
                          priority=Priority(1 + i % 3),
                          due=D.replace(hour=8 + i % 3)))
    s = plan([pet], budget=90)
    assert sum(i.task.duration_minutes for i in s.schedule) <= 90
    assert s.detect_conflicts() == []


def two_pets():
    owner = Owner("T")
    rex, mia = Pet("Rex", "dog"), Pet("Mia", "cat")
    owner.add_pet(rex)
    owner.add_pet(mia)
    return owner, rex, mia


def test_shared_task_is_one_task_for_several_pets():
    owner, rex, mia = two_pets()

    task = owner.add_shared_task([rex, mia, rex], Task("Vet visit", due=D))

    assert owner.get_all_tasks() == [task]  # a single task, listed once
    assert task.pet is rex and task.shared_with == [mia]
    assert task.pet_names() == "Rex & Mia"
    assert task.involves(rex) and task.involves(mia)
    assert len(rex.tasks) == 1 and mia.tasks == []


def test_shared_task_shows_up_in_each_pets_views_and_plans_once():
    owner, rex, mia = two_pets()
    task = owner.add_shared_task([rex, mia], Task("Walk", duration_minutes=30, due=D))
    scheduler = Scheduler(owner)

    scheduler.generate_schedule(D.date())

    assert len(scheduler.schedule) == 1  # one slot, not two
    assert scheduler.detect_conflicts() == [] and scheduler.detect_task_conflicts() == []
    assert scheduler.filter_tasks(pet=mia) == [task]
    assert scheduler.filter_tasks(pet="rex") == [task]
    assert [i.task for i in scheduler.get_schedule_for(mia)] == [task]


def test_completing_a_shared_task_completes_it_for_everyone_and_recurrence_stays_shared():
    owner, rex, mia = two_pets()
    task = owner.add_shared_task([rex, mia], Task("Walk", due=D, frequency=Frequency.DAILY))

    nxt = task.mark_complete()

    assert task.completed
    assert nxt.shared_with == [mia] and nxt.shared_with is not task.shared_with
    assert nxt.pet is rex


def test_shared_task_conflicts_with_another_task_for_either_pet():
    owner, rex, mia = two_pets()
    owner.add_shared_task([rex, mia], Task("Walk", due=D))
    mia.add_task(Task("Feed", due=D))
    scheduler = Scheduler(owner)

    warnings = scheduler.conflict_warnings(D.date())

    assert len(warnings) == 1
    assert "same pet" in warnings[0] and "Rex & Mia" in warnings[0]


def test_removing_a_pet_unshares_tasks_but_keeps_them():
    owner, rex, mia = two_pets()
    task = owner.add_shared_task([rex, mia], Task("Walk"))

    owner.remove_pet(mia)

    assert task.shared_with == [] and owner.get_all_tasks() == [task]


def test_add_shared_task_needs_a_valid_pet():
    owner, rex, mia = two_pets()
    with pytest.raises(ValueError):
        owner.add_shared_task([Pet("Ghost", "cat")], Task("Walk"))


def test_flexible_repeating_task_does_not_reappear_today_after_completion():
    owner, rex, _ = two_pets()
    task = Task("Brush", frequency=Frequency.DAILY)  # no requested time
    rex.add_task(task)

    nxt = task.mark_complete(completed_at=D)

    assert nxt.due is None and nxt.available_from == D.date() + timedelta(days=1)
    scheduler = Scheduler(owner)
    scheduler.generate_schedule(D.date())
    assert scheduler.schedule == []  # tomorrow's copy is not planned today
    scheduler.generate_schedule(D.date() + timedelta(days=1))
    assert [i.task for i in scheduler.schedule] == [nxt]


def test_clear_completed_removes_only_completed_tasks():
    owner, rex, mia = two_pets()
    done_a, done_b = Task("Done A"), Task("Done B")
    pending = Task("Pending")
    rex.add_task(done_a)
    rex.add_task(pending)
    shared = owner.add_shared_task([mia, rex], done_b)
    done_a.mark_complete()
    done_b.mark_complete()

    assert owner.clear_completed() == 2

    assert owner.get_all_tasks() == [pending]
    assert shared.pet is None  # removed tasks are detached


def test_clear_completed_can_be_limited_to_given_tasks():
    owner, rex, _ = two_pets()
    a, b = Task("A"), Task("B")
    rex.add_task(a)
    rex.add_task(b)
    a.mark_complete()
    b.mark_complete()

    assert owner.clear_completed([a]) == 1
    assert owner.get_all_tasks() == [b]


def test_edit_task_changes_every_field_in_place():
    owner, rex, mia = two_pets()
    task = Task("Walk", Category.WALK, 20, Priority.LOW, D, Frequency.ONCE)
    rex.add_task(task)

    owner.edit_task(task, title="  Long walk ", category=Category.PLAY, duration_minutes=45,
                    priority=Priority.HIGH, due=None, frequency=Frequency.DAILY, pets=[rex])

    assert (task.title, task.category, task.duration_minutes) == ("Long walk", Category.PLAY, 45)
    assert (task.priority, task.due, task.frequency) == (Priority.HIGH, None, Frequency.DAILY)
    assert owner.get_all_tasks() == [task]  # same task, not a copy


def test_edit_task_can_move_it_to_other_pets_and_share_it():
    owner, rex, mia = two_pets()
    task = Task("Walk")
    rex.add_task(task)

    owner.edit_task(task, title="Walk", category=Category.WALK, duration_minutes=20,
                    priority=Priority.MEDIUM, due=None, frequency=Frequency.ONCE, pets=[mia, rex])

    assert task.pet is mia and task.shared_with == [rex]
    assert rex.tasks == [] and mia.tasks == [task]
    assert task.involves(rex) and task.involves(mia)


def test_edit_task_with_bad_input_changes_nothing():
    owner, rex, _ = two_pets()
    task = Task("Walk", duration_minutes=20)
    rex.add_task(task)
    good = dict(category=Category.WALK, priority=Priority.HIGH, due=None,
                frequency=Frequency.ONCE, pets=[rex])

    for bad in (dict(title=" ", duration_minutes=10), dict(title="X", duration_minutes=0),
                dict(title="X", duration_minutes=10, pets=[Pet("Ghost", "cat")])):
        with pytest.raises(ValueError):
            owner.edit_task(task, **{**good, **bad})

    assert (task.title, task.duration_minutes, task.priority) == ("Walk", 20, Priority.MEDIUM)


def test_editing_a_time_clears_the_available_from_wait():
    from datetime import date
    owner, rex, _ = two_pets()
    task = Task("Brush", available_from=date(2026, 10, 25))
    rex.add_task(task)

    owner.edit_task(task, title="Brush", category=Category.GENERAL, duration_minutes=15,
                    priority=Priority.MEDIUM, due=D, frequency=Frequency.ONCE, pets=[rex])

    assert task.available_from is None


def test_delete_task_removes_it_for_all_its_pets():
    owner, rex, mia = two_pets()
    task = owner.add_shared_task([rex, mia], Task("Vet"))

    owner.delete_task(task)

    assert owner.get_all_tasks() == [] and not task.involves(mia)


def test_pet_and_task_icons_come_from_one_place():
    owner = Owner("T")
    rex, mia, koi = Pet("Rex", "dog"), Pet("Mia", "cat"), Pet("Koi", "fish")
    for pet in (rex, mia, koi):
        owner.add_pet(pet)
    shared = owner.add_shared_task([rex, mia, koi], Task("Vet"))

    assert (rex.emoji, mia.emoji, koi.emoji) == ("🐶", "🐱", "🐾")
    assert shared.pet_icons() == "🐶🐱🐾"
    assert Task("Loose task").pet_icons() == "🐾"  # no pet attached


def test_a_completed_task_cannot_be_edited():
    owner, rex, _ = two_pets()
    task = Task("Walk", duration_minutes=20, priority=Priority.LOW)
    rex.add_task(task)
    task.mark_complete()

    with pytest.raises(ValueError, match="completed"):
        owner.edit_task(task, title="Changed", category=Category.PLAY, duration_minutes=45,
                        priority=Priority.HIGH, due=None, frequency=Frequency.DAILY, pets=[rex])

    assert (task.title, task.duration_minutes, task.priority) == ("Walk", 20, Priority.LOW)
