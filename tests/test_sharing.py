from datetime import date, datetime

from pawpal_system import Category, Owner, Pet, Priority, Scheduler, Task
from sharing import schedule_as_text

DAY = date(2026, 10, 20)


def at(h, m=0):
    return datetime(2026, 10, 20, h, m)


def planned():
    owner = Owner("Jordan", available_minutes_per_day=60)
    rex, mia = Pet("Rex", "dog"), Pet("Mia", "cat")
    owner.add_pet(rex)
    owner.add_pet(mia)
    rex.add_task(Task("Morning walk", Category.WALK, 30, Priority.HIGH, at(8)))
    mia.add_task(Task("Breakfast", Category.FEEDING, 10, Priority.HIGH, at(13, 5)))
    owner.add_shared_task([rex, mia], Task("Vet call", Category.VET, 15, Priority.MEDIUM, at(17)))
    scheduler = Scheduler(owner)
    scheduler.generate_schedule(DAY)
    return scheduler, rex, mia


def test_text_lists_the_plan_in_time_order_with_a_header_and_total():
    scheduler, _, _ = planned()

    text = schedule_as_text(scheduler, DAY, emoji=False)

    assert text.splitlines()[:2] == ["Pet care schedule for Jordan", "Tuesday, October 20"]
    body = [line for line in text.splitlines() if " - " in line and ":" in line]
    assert body == [
        "8:00 AM - 8:30 AM  Rex: Morning walk",
        "1:05 PM - 1:15 PM  Mia: Breakfast",
        "5:00 PM - 5:15 PM  Rex & Mia: Vet call",
    ]
    assert text.endswith("Total: 55 min of 60 available")


def test_24_hour_clock_and_emoji_options():
    scheduler, _, _ = planned()

    text = schedule_as_text(scheduler, DAY, emoji=True, use_24h=True)

    assert "13:05 - 13:15  🐱 Mia: Breakfast" in text
    assert "🐶🐱 Rex & Mia: Vet call" in text
    assert text.startswith("🐾 Pet care schedule")
    assert "PM" not in text


def test_one_pets_view_includes_shared_tasks_only_for_that_pet():
    scheduler, rex, mia = planned()

    text = schedule_as_text(scheduler, DAY, pet=mia, emoji=False)

    assert "(Mia)" in text.splitlines()[0]
    assert "Breakfast" in text and "Vet call" in text and "Morning walk" not in text
    assert text.endswith("Total: 25 min")


def test_tasks_that_did_not_fit_are_listed_so_nobody_assumes_they_happen():
    owner = Owner("J", available_minutes_per_day=30)
    rex = Pet("Rex", "dog")
    owner.add_pet(rex)
    rex.add_task(Task("Hike", duration_minutes=30, priority=Priority.HIGH))
    rex.add_task(Task("Brush", duration_minutes=20, priority=Priority.LOW))
    scheduler = Scheduler(owner)
    scheduler.generate_schedule(DAY)

    text = schedule_as_text(scheduler, DAY, emoji=False)

    assert "Couldn't fit in today:" in text and "- Rex: Brush (20 min)" in text


def test_empty_plan_says_so():
    owner = Owner("J")
    owner.add_pet(Pet("Rex", "dog"))
    scheduler = Scheduler(owner)
    scheduler.generate_schedule(DAY)
    assert "Nothing scheduled." in schedule_as_text(scheduler, DAY)
