from datetime import date, datetime, timedelta
from html import escape

import streamlit as st

from devreload import reload_if_changed

# Pick up edits to our own modules without a restart (dependency order matters).
PROJECT_MODULES = ["fileutil", "pawpal_system", "storage", "eventlog", "config", "agent", "theme", "sharing"]
reload_if_changed(PROJECT_MODULES)

import json
import logging

from agent import LLMFixer, PlannerAgent
from config import configure_logging
from eventlog import clear_entries, delete_entries, log_path, read_entries
from sharing import schedule_as_text
from storage import StorageError, data_path, load_owner, owner_to_dict, save_owner
from theme import build_css
from pawpal_system import Category, Frequency, Owner, Pet, Priority, Scheduler, Task

reload_if_changed(PROJECT_MODULES)  # records timestamps of what was just imported
configure_logging()
st.set_page_config(page_title="PawPal++", page_icon="🐾", layout="centered")

# --- Theme ------------------------------------------------------------------
_, toggle_col = st.columns([4, 1])
dark_mode = toggle_col.toggle("🌙 Dark mode", key="dark_mode")
st.markdown(build_css("dark" if dark_mode else "light"), unsafe_allow_html=True)

st.markdown(
    '<div class="hero"><h1>🐾 PawPal++</h1>'
    "<p>Plan your pets' day: add tasks, spot clashes, and let the planning agent "
    "tidy the schedule.</p></div>",
    unsafe_allow_html=True,
)
st.caption(
    "PawPal+ is a scheduling helper, not veterinary or medical advice. Always follow your "
    "vet's instructions for medication timing."
)

# After a code change Streamlit reloads our modules but keeps session_state, so the
# owner in memory can be built from an older version of the classes. When that is the
# case, drop it and reload from the saved JSON file (it was saved on the previous run).
_stale = "owner" in st.session_state and not (
    isinstance(st.session_state.owner, Owner)
    and all(isinstance(t, Task) and hasattr(t, "involves")
            for t in st.session_state.owner.get_all_tasks())
)
if _stale:
    for _key in ("owner", "scheduler", "agent_result", "generated"):
        st.session_state.pop(_key, None)

# Owner and Scheduler live in session_state so they survive Streamlit reruns.
if "owner" not in st.session_state:
    loaded = None
    try:
        loaded = load_owner()  # everything the user entered last time
    except StorageError as err:
        st.session_state.load_warning = f"Could not read your saved data, starting fresh: {err}"
    st.session_state.owner = loaded or Owner("Jordan")
    st.session_state.scheduler = Scheduler(st.session_state.owner)
    st.session_state.saved_snapshot = json.dumps(
        owner_to_dict(st.session_state.owner), sort_keys=True) if loaded else None
owner: Owner = st.session_state.owner
scheduler: Scheduler = st.session_state.scheduler
today = date.today()


def persist() -> None:
    """Write the owner's data to the JSON file if anything changed since the last save."""
    snapshot = json.dumps(owner_to_dict(owner), sort_keys=True)
    if snapshot in (st.session_state.get("saved_snapshot"), st.session_state.get("failed_snapshot")):
        return  # already saved, or already failed for exactly this data (don't re-log each rerun)
    saved = save_owner(owner)  # failures are logged by storage.py
    st.session_state.save_failed = not saved
    if saved:
        st.session_state.saved_snapshot = snapshot
        st.session_state.failed_snapshot = None
    else:
        st.session_state.failed_snapshot = snapshot  # retried as soon as the data changes


def save_and_rerun() -> None:
    """Save, then rerun (a rerun stops the script, so the end-of-script save is skipped)."""
    persist()
    st.rerun()


def finish_change(message: str) -> None:
    """Wrap up a change to the task list: show `message`, drop the stale plan, save, rerun.

    Any edit, completion or deletion makes the generated plan and the agent's report out of
    date, so both are cleared and the user regenerates the schedule.
    """
    st.session_state.flash = message
    st.session_state.generated = False
    st.session_state.pop("agent_result", None)
    save_and_rerun()


app_log = logging.getLogger("pawpal.app")
TIME_STEP = timedelta(minutes=5)  # time pickers move in 5-minute steps
LEVEL_PILL = {"ERROR": "p-high", "WARNING": "p-medium", "INFO": "cat"}


def remove_log_entries() -> None:
    """Button callback: delete the entries picked in the Log tab."""
    removed = delete_entries(st.session_state.get("log_remove", []))
    st.session_state.log_remove = []
    st.session_state.log_flash = f"Removed {removed} log entr{'y' if removed == 1 else 'ies'}."


def clear_log() -> None:
    """Button callback: delete every log entry."""
    cleared = clear_entries()
    st.session_state.log_remove = []
    st.session_state.log_confirm_clear = False
    st.session_state.log_flash = f"Cleared {cleared} log entr{'y' if cleared == 1 else 'ies'}."


def reset_all_data() -> None:
    """Button callback: delete every pet and task, and save the empty state.

    Runs before the script reruns, which is the only time a widget's own session_state
    key (the confirm checkbox) may be changed.
    """
    fresh = Owner("Jordan")
    st.session_state.owner = fresh
    st.session_state.scheduler = Scheduler(fresh)
    st.session_state.pop("agent_result", None)
    st.session_state.generated = False
    st.session_state.confirm_reset = False
    st.session_state.saved_snapshot = None
    save_owner(fresh)


if "load_warning" in st.session_state:
    st.warning(st.session_state.pop("load_warning"))

# Lookups are keyed by value (not enum member) so tasks created before Streamlit
# reloaded pawpal_system.py, whose enum classes are then different objects, still render.
PRIORITY_LABEL = {3: "High", 2: "Medium", 1: "Low"}
CATEGORY_EMOJI = {
    "feeding": "🍖", "walk": "🦮", "meds": "💊", "grooming": "🛁",
    "vet": "🩺", "play": "🎾", "general": "📌",
}


def priority_name(task: Task) -> str:
    return PRIORITY_LABEL.get(int(task.priority), "Medium")


def category_emoji(category) -> str:
    return CATEGORY_EMOJI.get(category.value, "📌")


def pills(task: Task, repeats: bool = True) -> str:
    """Priority, category and repeat badges for a task, as HTML."""
    out = (
        f'<span class="pill p-{priority_name(task).lower()}">{priority_name(task)}</span>'
        f'<span class="pill cat">{category_emoji(task.category)} {task.category.value}</span>'
    )
    if repeats and task.frequency.value != "once":
        out += f'<span class="pill rep">🔁 {task.frequency.value}</span>'
    return out


def task_card(task: Task, when: str, extra: str = "") -> str:
    """One task as an HTML card with a coloured priority edge."""
    return (
        f'<div class="card accent-{priority_name(task).lower()}"><div class="row">'
        f'<div class="time">{escape(when)}</div>'
        f'<div class="grow"><div class="title">{task.pet_icons()} {escape(task.pet_names())}'
        f" · {escape(task.title)}</div>"
        f'<div class="muted">{task.duration_minutes} min{extra}</div></div>'
        f"<div>{pills(task)}</div></div></div>"
    )


def banner(text: str, ok: bool) -> None:
    st.markdown(f'<div class="banner {"ok" if ok else "bad"}">{text}</div>', unsafe_allow_html=True)


tab_pets, tab_tasks, tab_plan, tab_log = st.tabs(["🐾 Pets", "📝 Tasks", "📅 Schedule", "🧾 Log"])

# --- Pets -----------------------------------------------------------------------
with tab_pets:
    col1, col2 = st.columns(2)
    with col1:
        owner.name = st.text_input("Owner name", value=owner.name)
    with col2:
        owner.available_minutes_per_day = int(
            st.number_input("Minutes available per day", 10, 720, owner.available_minutes_per_day)
        )

    with st.form("add_pet", clear_on_submit=True):
        st.markdown("**Add a pet**")
        c1, c2, c3 = st.columns(3)
        new_pet_name = c1.text_input("Pet name")
        new_species = c2.selectbox("Species", ["dog", "cat", "other"])
        new_age = c3.number_input("Age", 0, 40, 1)
        if st.form_submit_button("➕ Add pet", type="primary"):
            if not new_pet_name.strip():
                st.error("Give the pet a name.")
            elif any(p.name.lower() == new_pet_name.strip().lower() for p in owner.pets):
                st.error(f"{new_pet_name} already exists.")
            else:
                owner.add_pet(Pet(new_pet_name.strip(), new_species, int(new_age)))
                save_and_rerun()

    if not owner.pets:
        st.info("Add your first pet to get started.")
    else:
        cols = st.columns(min(len(owner.pets), 3))
        for i, p in enumerate(owner.pets):
            needs = (
                f'<div class="muted">⚕️ {escape(p.special_needs)}</div>' if p.special_needs else ""
            )
            cols[i % len(cols)].markdown(
                f'<div class="card petcard"><div class="emoji">{p.emoji}</div>'
                f'<div class="name">{escape(p.name)}</div>'
                f'<div class="muted">{escape(p.species.title())} · {p.age} yr · '
                f"{sum(1 for t in owner.get_all_tasks() if t.involves(p))} task(s)</div>{needs}</div>",
                unsafe_allow_html=True,
            )

        with st.expander("✏️ Edit a pet"):
            edit_index = st.selectbox(
                "Pet to edit", range(len(owner.pets)),
                format_func=lambda i: owner.pets[i].name, key="edit_pet",
            )
            edit_pet = owner.pets[edit_index]
            species_options = ["dog", "cat", "other"]
            if edit_pet.species not in species_options:
                species_options.append(edit_pet.species)
            # Keys include the pet's identity so the fields reset when another pet is picked.
            with st.form("edit_pet_form"):
                e1, e2, e3 = st.columns(3)
                edit_name = e1.text_input("Name", value=edit_pet.name, key=f"en_{id(edit_pet)}")
                edit_species = e2.selectbox(
                    "Species", species_options, index=species_options.index(edit_pet.species),
                    key=f"es_{id(edit_pet)}",
                )
                edit_age = e3.number_input("Age", 0, 40, edit_pet.age, key=f"ea_{id(edit_pet)}")
                edit_needs = st.text_input(
                    "Special needs", value=edit_pet.special_needs, key=f"eh_{id(edit_pet)}"
                )
                if st.form_submit_button("Save changes"):
                    new_name = edit_name.strip()
                    if not new_name:
                        st.error("A pet needs a name.")
                    elif any(p is not edit_pet and p.name.lower() == new_name.lower()
                             for p in owner.pets):
                        st.error(f"{new_name} already exists.")
                    else:
                        edit_pet.name = new_name
                        edit_pet.species = edit_species
                        edit_pet.age = int(edit_age)
                        edit_pet.special_needs = edit_needs.strip()
                        st.success(f"Updated {new_name}.")
                        save_and_rerun()

    with st.expander("💾 Saved data"):
        st.caption(f"Everything you enter is saved automatically to `{data_path()}`.")
        confirm = st.checkbox("I want to delete all pets and tasks", key="confirm_reset")
        st.button("🗑️ Reset all data", disabled=not confirm, on_click=reset_all_data)

# --- Tasks ------------------------------------------------------------------------
with tab_tasks:
    if not owner.pets:
        st.info("Add a pet on the Pets tab first.")
    else:
        # The time field's key changes after every successful add, which gives the browser a
        # brand-new, empty field (clearing session_state alone would not clear the browser's copy).
        add_time_gen = st.session_state.get("add_time_gen", 0)
        with st.form("add_task", clear_on_submit=False):
            st.markdown("**Add a task**")
            c1, c2 = st.columns(2)
            pet_choices = c1.multiselect(
                "Pets", [p.name for p in owner.pets], default=[owner.pets[0].name],
                help="Pick more than one pet to make ONE task that is for all of them "
                "(for example walking two dogs together).",
            )
            title = c2.text_input("Task title", value="Morning walk")
            c3, c4, c5 = st.columns(3)
            category = c3.selectbox(
                "Category", list(Category),
                format_func=lambda c: f"{category_emoji(c)} {c.value}",
            )
            duration = c4.number_input("Duration (min)", 1, 240, 20)
            priority = c5.selectbox(
                "Priority", list(Priority), index=1, format_func=lambda p: p.name.title()
            )
            c6, c7 = st.columns(2)
            frequency = c6.selectbox("Repeats", list(Frequency), format_func=lambda f: f.value)
            due_time = c7.time_input(
                "Time (leave empty for any time)", value=None, step=TIME_STEP, key=f"add_time_{add_time_gen}",
                help="Fill this in to request a specific time of day. Leave it empty and the "
                "planner picks a time for you.",
            )
            if st.form_submit_button("➕ Add task", type="primary"):
                chosen = [p for p in owner.pets if p.name in pet_choices]
                if not chosen:
                    st.error("Pick at least one pet.")
                else:
                    due = datetime.combine(today, due_time) if due_time else None
                    owner.add_shared_task(chosen, Task(
                        title.strip() or "Untitled", category, int(duration),
                        priority, due, frequency))
                    st.session_state.flash = (
                        f"Added \"{title.strip() or 'Untitled'}\" for "
                        + " & ".join(p.name for p in chosen) + "."
                    )
                    st.session_state.add_time_gen = add_time_gen + 1  # next task starts with no time
                    save_and_rerun()

        # Filtering and sorting come from the Scheduler.
        pet_filter = st.selectbox("Show pet", ["All"] + [p.name for p in owner.pets])
        pet_arg = None if pet_filter == "All" else pet_filter
        all_pending = scheduler.sort_tasks_by_time(
            scheduler.filter_tasks(pet=pet_arg, status=False))
        # Tasks due on a later day (e.g. tomorrow's repeat) wait in "Upcoming" so a task
        # you just finished doesn't look like it is still on today's list.
        def is_upcoming(t: Task) -> bool:
            return bool((t.due and t.due.date() > today)
                        or (t.available_from and t.available_from > today))

        pending = [t for t in all_pending if not is_upcoming(t)]
        upcoming = [t for t in all_pending if is_upcoming(t)]
        completed = scheduler.sort_tasks_by_time(scheduler.filter_tasks(pet=pet_arg, status=True))

        if "flash" in st.session_state:
            st.success(st.session_state.pop("flash"))

        m1, m2, m3 = st.columns(3)
        m1.metric("Pending", len(pending))
        m2.metric("Minutes pending", sum(t.duration_minutes for t in pending))
        m3.metric("Completed", len(completed))

        st.markdown("#### ⏳ Pending")
        if pending:
            st.caption("Sorted by requested time; flexible tasks last.")
            for task in pending:
                left, right = st.columns([6, 1], vertical_alignment="center")
                when = f"{task.due:%a %H:%M}" if task.due else "Flexible"
                left.markdown(task_card(task, when), unsafe_allow_html=True)
                if right.button("✓ Done", key=f"done_{id(task)}"):
                    nxt = scheduler.complete_task(task)
                    if nxt is not None:
                        nxt_when = f" due {nxt.due:%a %H:%M}" if nxt.due else ""
                        message = (
                            f"Completed {task.title}. Next {nxt.frequency.value} "
                            f"occurrence added to Upcoming{nxt_when}."
                        )
                    else:
                        message = f"Completed {task.title}."
                    finish_change(message)
        else:
            st.info("Nothing left to do today.")

        if upcoming:
            with st.expander(f"📆 Upcoming ({len(upcoming)})"):
                for task in upcoming:
                    when = (f"{task.due:%a %H:%M}" if task.due
                            else f"From {task.available_from:%a %b %d}")
                    st.markdown(task_card(task, when), unsafe_allow_html=True)

        if completed:
            with st.expander(f"✅ Completed ({len(completed)})"):
                for task in completed:
                    when = f"{task.due:%a %H:%M}" if task.due else "Flexible"
                    st.markdown(task_card(task, when), unsafe_allow_html=True)
                if st.button("🧹 Clear completed", key="clear_completed",
                             help="Permanently deletes the completed tasks shown here."):
                    removed = owner.clear_completed(completed)
                    finish_change(f"Cleared {removed} completed task(s).")

        # Edit or delete any task that is not finished yet (today's and upcoming). Finished
        # tasks are never offered here, and Owner.edit_task refuses them as a second guard.
        editable = {id(t): t for t in all_pending if not t.completed}
        if editable:
            with st.expander("✏️ Edit a task"):
                # The dropdown remembers a task, not a list position, so completing or deleting
                # a task can never make it point at a different one. A remembered choice that is
                # gone (or of an unexpected type) falls back to the first task.
                if st.session_state.get("edit_task_pick") not in editable:
                    st.session_state.edit_task_pick = next(iter(editable))

                def task_label(t: Task) -> str:
                    when = f"{t.due:%a %H:%M}" if t.due else "flexible"
                    return f"{t.pet_names()}: {t.title} ({when})"

                pick = st.selectbox(
                    "Task to edit", list(editable), key="edit_task_pick",
                    format_func=lambda task_id: task_label(editable[task_id]),
                )
                et = editable[pick]
                k = id(et)  # keys include the task so the fields reset when another is picked
                with st.form("edit_task_form"):
                    e1, e2 = st.columns(2)
                    new_title = e1.text_input("Task title", value=et.title, key=f"et_{k}")
                    new_pets = e2.multiselect(
                        "Pets", [p.name for p in owner.pets], key=f"ep_{k}",
                        default=[p.name for p in et.pets() if p in owner.pets],
                        help="More than one pet makes it ONE task for all of them.",
                    )
                    f1, f2, f3 = st.columns(3)
                    new_category = f1.selectbox(
                        "Category", list(Category), index=list(Category).index(et.category),
                        format_func=lambda c: f"{category_emoji(c)} {c.value}", key=f"ec_{k}",
                    )
                    new_duration = f2.number_input(
                        "Duration (min)", 1, 240, min(et.duration_minutes, 240), key=f"ed_{k}")
                    new_priority = f3.selectbox(
                        "Priority", list(Priority), index=list(Priority).index(et.priority),
                        format_func=lambda p: p.name.title(), key=f"eg_{k}",
                    )
                    g1, g2 = st.columns(2)
                    new_frequency = g1.selectbox(
                        "Repeats", list(Frequency), index=list(Frequency).index(et.frequency),
                        format_func=lambda f: f.value, key=f"ef_{k}",
                    )
                    new_time = g2.time_input(
                        "Time (leave empty for any time)",
                        value=et.due.time() if et.due else None, step=TIME_STEP, key=f"et2_{k}")
                    remove_time = False
                    if et.due is not None:
                        remove_time = st.checkbox(
                            "Make this task flexible (remove its time)", key=f"ex_{k}",
                            help="Tick to drop the requested time. Otherwise the time above is kept.")
                    save_col, delete_col = st.columns(2)
                    save = save_col.form_submit_button("💾 Save changes", type="primary")
                    delete = delete_col.form_submit_button("🗑️ Delete task")

                if save:
                    chosen = [p for p in owner.pets if p.name in new_pets]
                    keep_day = et.due.date() if et.due else today  # editing the time keeps the day
                    try:
                        owner.edit_task(
                            et, title=new_title, category=new_category,
                            duration_minutes=int(new_duration), priority=new_priority,
                            due=(None if remove_time or not new_time
                                 else datetime.combine(keep_day, new_time)),
                            frequency=new_frequency, pets=chosen,
                        )
                    except ValueError as err:
                        app_log.warning("Rejected task edit for %r: %s", et.title, err)
                        st.error(f"Could not save: {err}")
                    else:
                        finish_change(f"Saved changes to {et.title}.")
                if delete:
                    owner.delete_task(et)
                    finish_change(f"Deleted {et.title}.")

        # Conflict warnings about requested times, before any planning.
        conflicts = scheduler.conflict_warnings(on_date=today)
        if conflicts:
            st.warning(
                f"⚠️ {len(conflicts)} scheduling conflict(s) in the times you requested. "
                "The planner will shift tasks apart when you generate a schedule."
            )
            for warning in conflicts:
                st.caption(f"• {warning}")
        elif scheduler.filter_tasks(status=False):
            st.success("✅ No conflicts between requested times.")

# --- Schedule ---------------------------------------------------------------------
with tab_plan:
    if not owner.pets:
        st.info("Add a pet and some tasks first.")
    else:
        o1, o2 = st.columns(2)
        use_agent = o1.toggle(
            "Planning agent",
            value=True,
            help="The agent checks the draft plan, fixes clashes by adjusting requested "
            "times, and re-plans until the plan is clean.",
        )
        llm_available = LLMFixer.from_env() is not None
        use_llm = o2.toggle(
            "Gemini proposes the fixes",
            value=llm_available,
            disabled=not (use_agent and llm_available),
            help="Needs GEMINI_API_KEY in your .env file (see the README). "
            "Without it the agent uses built-in rules.",
        )
        if st.button("✨ Generate schedule", type="primary"):
            st.session_state.pop("agent_result", None)
            if use_agent:
                fixer = LLMFixer.from_env() if use_llm else None
                st.session_state.agent_result = PlannerAgent(scheduler, fixer).run(today)
            else:
                scheduler.generate_schedule(today)
            st.session_state.generated = True

        agent_result = st.session_state.get("agent_result")
        if agent_result is not None:
            fixer_name = agent_result.fixer_used
            with st.expander(
                f"🤖 Planning agent report · {len(agent_result.changes)} time change(s)",
                expanded=True,
            ):
                if agent_result.error:
                    banner("The agent hit an error and kept the best plan it had.", False)
                    st.caption(agent_result.error)
                elif agent_result.resolved:
                    banner(
                        f"✅ Plan passed all checks after {len(agent_result.steps)} pass(es) "
                        f"(fixes by {fixer_name}).",
                        True,
                    )
                else:
                    banner(f"⚠️ {len(agent_result.blocking)} problem(s) the agent could not fix.",
                           False)
                    for issue in agent_result.blocking:
                        st.markdown(f"- {issue.message}")
                for change in agent_result.changes:
                    old = f"{change.old_due:%H:%M}" if change.old_due else "flexible"
                    new = f"{change.new_due:%H:%M}" if change.new_due else "flexible"
                    st.markdown(
                        f'<div class="card change">{change.task.pet_icons()} '
                        f"<b>{escape(change.task.pet_names())}</b> · {escape(change.task.title)}"
                        f'<span class="arrow">{old} → {new}</span>'
                        f'<span class="muted">{escape(change.why)}</span></div>',
                        unsafe_allow_html=True,
                    )
                for issue in agent_result.advisories:
                    st.info(issue.message)
                with st.expander("Show the agent's step-by-step trace"):
                    st.text(agent_result.summary())
                if agent_result.changes and st.button("↩️ Undo the agent's time changes"):
                    agent_result.undo()
                    scheduler.generate_schedule(today)
                    st.session_state.pop("agent_result", None)
                    save_and_rerun()

        if st.session_state.get("generated"):
            v1, v2 = st.columns(2)
            order = v1.radio("Order by", ["Time", "Priority"], horizontal=True)
            view_pet = v2.selectbox("Pet", ["All"] + [p.name for p in owner.pets], key="view_pet")

            if view_pet == "All":
                items = scheduler.sort_by_time() if order == "Time" else scheduler.sort_by_priority()
            else:
                pet_obj = next(p for p in owner.pets if p.name == view_pet)
                items = scheduler.get_schedule_for(pet_obj)
                if order == "Priority":
                    items = sorted(items, key=lambda i: (-i.task.priority, i.start))

            total = sum(i.task.duration_minutes for i in scheduler.schedule)
            budget = owner.available_minutes_per_day
            s1, s2, s3 = st.columns(3)
            s1.metric("Planned", f"{len(scheduler.schedule)} tasks")
            s2.metric("Time used", f"{total} / {budget} min")
            s3.metric("Not scheduled", len(scheduler.skipped_tasks) + len(scheduler.unplaced))
            st.progress(min(total / budget, 1.0))

            st.markdown(f"#### 📅 {today:%A, %B %d}")
            if items:
                for i in items:
                    st.markdown(
                        task_card(
                            i.task, f"{i.start:%H:%M} – {i.end:%H:%M}",
                            f" · {escape(i.reason)}",
                        ),
                        unsafe_allow_html=True,
                    )
            else:
                st.info("Nothing scheduled.")

            if not scheduler.detect_conflicts():
                st.success("✅ The generated plan has no overlapping tasks.")
            else:
                st.error("The generated plan has overlapping tasks.")

            for w in scheduler.warnings:
                st.warning(w)
            for task, why in scheduler.unplaced:
                st.warning(
                    f"Could not place {task.pet_names()}: {task.title} - {why}"
                )
            skipped = scheduler.skipped_tasks
            if view_pet != "All":
                skipped = scheduler.get_skipped_for(pet_obj)
            if skipped:
                st.info(
                    "**Skipped (did not fit in the time budget):**\n"
                    + "\n".join(
                        f"- {t.pet_names()}: {t.title} ({t.duration_minutes} min)"
                        for t in skipped
                    )
                )

            with st.expander("📋 Share this schedule", expanded=True):
                st.caption("Copy the text below (use the copy icon at its top right) and paste it "
                           "into a message, or download it. It follows the pet shown above.")
                t1, t2 = st.columns(2)
                share_emoji = t1.toggle("Emojis", value=True, key="share_emoji")
                share_24h = t2.toggle("24-hour clock", value=False, key="share_24h")
                share_text = schedule_as_text(
                    scheduler, today, pet=None if view_pet == "All" else pet_obj,
                    emoji=share_emoji, use_24h=share_24h,
                )
                st.code(share_text, language=None)
                st.download_button(
                    "⬇️ Download as .txt", share_text, file_name=f"pawpal-{today:%Y-%m-%d}.txt",
                    mime="text/plain",
                )

            with st.expander("Plan details (with reasons)"):
                st.text(scheduler.explain_plan())
        else:
            st.caption("Click **Generate schedule** to build today's plan.")

# --- Log --------------------------------------------------------------------------
with tab_log:
    if "log_flash" in st.session_state:
        st.success(st.session_state.pop("log_flash"))
    log_entries = list(reversed(read_entries()))  # newest first
    st.caption(
        f"What the app did and what went wrong, saved to `{log_path()}`. Warnings, errors and the "
        "planning agent's activity are recorded; remove single entries or clear the whole log."
    )
    n_error = sum(1 for e in log_entries if e.get("level") == "ERROR")
    n_warn = sum(1 for e in log_entries if e.get("level") == "WARNING")
    l1, l2, l3 = st.columns(3)
    l1.metric("Errors", n_error)
    l2.metric("Warnings", n_warn)
    l3.metric("Entries", len(log_entries))

    if not log_entries:
        st.info("Nothing logged yet. Generate a schedule or make a change and entries will appear.")
    else:
        levels = st.multiselect("Show", ["ERROR", "WARNING", "INFO"],
                                default=["ERROR", "WARNING", "INFO"], key="log_levels")
        shown = [e for e in log_entries if e.get("level") in levels][:100]
        if len([e for e in log_entries if e.get("level") in levels]) > 100:
            st.caption("Showing the newest 100 entries.")

        def stamp(e: dict) -> str:
            try:
                return f"{datetime.fromisoformat(e['time']):%b %d %H:%M:%S}"
            except (KeyError, ValueError, TypeError):
                return "?"

        for e in shown:
            level = str(e.get("level", "INFO"))
            problem = (f'<div class="muted">{escape(str(e["error"]))}</div>'
                       if e.get("error") else "")
            st.markdown(
                f'<div class="card"><div class="row"><div class="time">{escape(stamp(e))}</div>'
                f'<div class="grow"><div class="title">{escape(str(e.get("message", "")))}</div>'
                f'<div class="muted">{escape(str(e.get("source", "")))}</div>{problem}</div>'
                f'<div><span class="pill {LEVEL_PILL.get(level, "cat")}">{escape(level)}</span>'
                f"</div></div></div>",
                unsafe_allow_html=True,
            )
            if e.get("traceback"):
                with st.expander("Technical details"):
                    st.code(str(e["traceback"]), language=None)

        valid_ids = [str(e.get("id")) for e in shown]
        st.session_state.log_remove = [
            i for i in st.session_state.get("log_remove", []) if i in valid_ids]
        by_id = {str(e.get("id")): e for e in shown}
        st.multiselect(
            "Pick entries to remove", valid_ids, key="log_remove",
            format_func=lambda i: f"{stamp(by_id[i])}  {by_id[i].get('level', '')}  "
                                  f"{str(by_id[i].get('message', ''))[:70]}",
        )
        st.button("Remove selected", on_click=remove_log_entries,
                  disabled=not st.session_state.get("log_remove"))
        st.checkbox("I want to clear the whole log", key="log_confirm_clear")
        st.button("🗑️ Clear log", on_click=clear_log,
                  disabled=not st.session_state.get("log_confirm_clear"))
        st.download_button(
            "⬇️ Download log (.json)", json.dumps({"entries": read_entries()}, indent=2),
            file_name="pawpal-log.json", mime="application/json",
        )

# Save anything changed during this run (name, budget, agent edits, ...).
persist()
if st.session_state.get("save_failed"):
    st.warning(
        f"⚠️ Your latest changes could not be saved to `{data_path()}`. "
        "The reason is on the Log tab; fix it and make any change to try again."
    )
