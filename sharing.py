"""Turn a generated schedule into plain text that is easy to paste into a message."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pawpal_system import Pet, Scheduler, Task


def _clock(moment: datetime, use_24h: bool) -> str:
    """'08:30' or '8:30 AM'."""
    if use_24h:
        return f"{moment:%H:%M}"
    return f"{moment:%I:%M %p}".lstrip("0")


def _who(task: Task, emoji: bool) -> str:
    names = task.pet_names()
    if not emoji:
        return names
    return f"{task.pet_icons()} {names}"


def schedule_as_text(
    scheduler: Scheduler,
    on_date: date,
    pet: Optional[Pet] = None,
    emoji: bool = True,
    use_24h: bool = False,
) -> str:
    """The day's plan as shareable plain text.

    Pass `pet` to include only that pet's tasks (shared tasks count for each of their
    pets). Tasks that did not fit are listed at the end so nobody assumes they happen.
    """
    if pet is None:
        items, skipped = scheduler.sort_by_time(), list(scheduler.skipped_tasks)
        unplaced = [t for t, _ in scheduler.unplaced]
    else:
        items, skipped = scheduler.get_schedule_for(pet), scheduler.get_skipped_for(pet)
        unplaced = [t for t, _ in scheduler.unplaced if t.involves(pet)]

    paw = "🐾 " if emoji else ""
    title = f"{paw}Pet care schedule for {scheduler.owner.name}"
    if pet is not None:
        title += f" ({pet.name})"
    lines = [title, f"{on_date:%A, %B %d}", ""]

    if not items:
        lines.append("Nothing scheduled.")
    for item in items:
        start, end = _clock(item.start, use_24h), _clock(item.end, use_24h)
        lines.append(f"{start} - {end}  {_who(item.task, emoji)}: {item.task.title}")

    not_done = skipped + unplaced
    if not_done:
        lines += ["", "Couldn't fit in today:"]
        lines += [f"- {_who(t, emoji)}: {t.title} ({t.duration_minutes} min)" for t in not_done]

    total = sum(i.task.duration_minutes for i in items)
    budget = scheduler.owner.get_available_time(on_date)
    lines += ["", f"Total: {total} min" + (f" of {budget} available" if pet is None else "")]
    return "\n".join(lines)
