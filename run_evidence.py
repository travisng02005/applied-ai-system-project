"""Generate reproducible execution evidence for PawPal+ (no API key and no network needed).

Run:  python run_evidence.py

It runs real commands and scripted scenarios against the real code, prints a report, and
writes it to evidence/evidence_report.txt (plus evidence/pytest_output.txt). Gemini is
replaced by a scripted stand-in so every run gives the same output; the stand-ins are clearly
labelled in the report. Dates are fixed, and everything is saved into a temporary folder, so
your own data and logs are never touched.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agent import LLMFixer, PlannerAgent  # noqa: E402
from eventlog import JsonLogHandler, clear_entries, is_worth_recording, read_entries  # noqa: E402
from main import build_demo_owner, format_schedule  # noqa: E402
from pawpal_system import Category, Frequency, Owner, Pet, Priority, Scheduler, Task  # noqa: E402
from sharing import schedule_as_text  # noqa: E402
from storage import StorageError, load_owner, save_owner  # noqa: E402

DAY = date(2026, 10, 20)
OUT_DIR = ROOT / "evidence"
REPORT: list[str] = []


def say(text: str = "") -> None:
    REPORT.append(text)
    print(text)


def heading(title: str) -> None:
    say()
    say("=" * 78)
    say(title)
    say("=" * 78)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(DAY.year, DAY.month, DAY.day, hour, minute)


def tasks_table(owner: Owner) -> str:
    rows = [f"  {t.pet_names():<12} {t.title:<16} {t.duration_minutes:>3} min  "
            f"{t.priority.name:<6} {'requested ' + format(t.due, '%H:%M') if t.due else 'flexible'}"
            for t in owner.get_all_tasks()]
    return "\n".join(rows)


def fake_gemini(reply):
    """A scripted stand-in for the Gemini client: returns `reply`, or raises it if an Exception."""
    def generate_content(**kwargs):
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=reply)
    return LLMFixer(SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))


def stable(text: str, workdir: Path) -> str:
    """Hide the parts that change on every run (temp folder, timestamps) so output is repeatable."""
    text = text.replace(str(workdir), "<tmp>").replace("\\", "/")  # same on every OS
    return re.sub(r"corrupt-\d{8}-\d{6}", "corrupt-<timestamp>", text)


def show_log(log_path: Path, workdir: Path, levels: tuple, limit: int = 110) -> None:
    """Print the JSON activity log entries of the given levels, then empty the log."""
    for entry in read_entries(log_path):
        if entry["level"] in levels:
            line = stable(f"  [{entry['level']}] {entry['source']}: {entry['message']}", workdir)
            say(line if len(line) <= limit else line[:limit] + "...")
    clear_entries(log_path)


def run_command(args: list[str], env_extra: dict) -> str:
    """Run a real command in the project folder and return its output."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **env_extra}
    done = subprocess.run([sys.executable, *args], cwd=ROOT, env=env, capture_output=True,
                          text=True, encoding="utf-8")
    return (done.stdout + done.stderr).rstrip()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    workdir = Path(tempfile.mkdtemp(prefix="pawpal-evidence-"))
    env_extra = {"PAWPAL_LOG_FILE": str(workdir / "cmd-log.json"), "GEMINI_API_KEY": "",
                 "PAWPAL_DATA_FILE": str(workdir / "cmd-data.json")}

    # Send the agent's own log into a temp JSON log so the report can show what was recorded.
    log_path = workdir / "log.json"
    handler = JsonLogHandler(log_path)
    handler.addFilter(is_worth_recording)
    root_logger = logging.getLogger("pawpal")
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    root_logger.propagate = False

    say("PawPal+ reproducible execution evidence")
    say(f"Python {sys.version.split()[0]} | planned day: {DAY:%A, %B %d, %Y}")
    say("Reproduce with:  python run_evidence.py")

    # ------------------------------------------------------------------ END-TO-END
    heading("PART 1. END-TO-END RUNS")

    say()
    say("--- Case 1: two pets, six tasks, clashing requested times (real CLI commands)")
    say("INPUT (built into main.py):")
    say(tasks_table(build_demo_owner(DAY)))
    say()
    say("$ python main.py --no-agent --date 2026-10-20        # the scheduler on its own")
    say(run_command(["main.py", "--no-agent", "--date", "2026-10-20"], env_extra))
    say()
    say("$ python main.py --date 2026-10-20                   # with the planning agent")
    say(run_command(["main.py", "--date", "2026-10-20"], env_extra))

    say()
    say("--- Case 2: a task shared by two pets, saved, edited, finished (modules, temp files)")
    owner = Owner("Travis", available_minutes_per_day=120)
    mochi, mia = Pet("Mochi", "dog"), Pet("Mia", "cat")
    owner.add_pet(mochi)
    owner.add_pet(mia)
    vet = owner.add_shared_task([mochi, mia], Task("Vet call", Category.VET, 30, Priority.HIGH,
                                                   at(7, 30), Frequency.ONCE))
    mochi.add_task(Task("Brush coat", Category.GROOMING, 20, Priority.LOW))
    say("INPUT: one 'Vet call' for Mochi AND Mia at 07:30 (30 min, HIGH), plus a flexible 'Brush coat'")
    say()
    data_file = workdir / "pawpal_data.json"
    save_owner(owner, data_file)
    saved = json.loads(data_file.read_text(encoding="utf-8"))["pets"][0]["tasks"][0]
    say("OUTPUT 1: saved data file (pawpal_data.json), the shared task is ONE entry:")
    say("  " + json.dumps({k: saved[k] for k in ("title", "due", "shared_with", "completed")}))
    reloaded = load_owner(data_file)
    say(f"OUTPUT 2: reloaded from disk: {len(reloaded.get_all_tasks())} tasks, "
        f"'{reloaded.pets[0].tasks[0].title}' is for {reloaded.pets[0].tasks[0].pet_names()}")
    owner.edit_task(vet, title="Vet call", category=Category.VET, duration_minutes=45,
                    priority=Priority.HIGH, due=at(9, 0), frequency=Frequency.ONCE, pets=[mochi, mia])
    say(f"OUTPUT 3: edited in place: now {vet.duration_minutes} min at {vet.due:%H:%M}")
    scheduler = Scheduler(owner)
    PlannerAgent(scheduler).run(DAY)
    say("OUTPUT 4: planned and shared as text:")
    say("\n".join("  " + line for line in schedule_as_text(scheduler, DAY).splitlines()))
    vet.mark_complete()
    try:
        owner.edit_task(vet, title="Hacked", category=Category.VET, duration_minutes=5,
                        priority=Priority.LOW, due=None, frequency=Frequency.ONCE, pets=[mochi])
    except ValueError as err:
        say(f"OUTPUT 5: editing the finished task is refused -> ValueError: {err}")
    say(f"OUTPUT 6: 'Clear completed' removed {owner.clear_completed()} task; "
        f"left: {[t.title for t in owner.get_all_tasks()]}")

    say()
    say("--- Case 3: an overbooked day (60 minutes available, 105 minutes of HIGH priority work)")
    owner = Owner("Travis", available_minutes_per_day=60)
    rex = Pet("Rex", "dog")
    owner.add_pet(rex)
    for title, minutes in (("Long hike", 50), ("Swim", 50), ("Eye drops", 5)):
        category = Category.MEDS if title == "Eye drops" else Category.WALK
        rex.add_task(Task(title, category, minutes, Priority.HIGH))
    say("INPUT:")
    say(tasks_table(owner))
    scheduler = Scheduler(owner)
    result = PlannerAgent(scheduler).run(DAY)
    say("OUTPUT:")
    say(format_schedule(scheduler, DAY))
    say(result.summary())

    # ------------------------------------------------------------------ AI FEATURE
    heading("PART 2. AI FEATURE BEHAVIOUR (planning agent: plan, check, fix, repeat)")
    say("The Gemini replies below are SCRIPTED stand-ins (deterministic and offline). A real, live")
    say("Gemini excerpt is in the README (Sample interactions, example 2).")

    say()
    say("--- Case A: Gemini proposes a fix and the agent applies it")
    owner = build_demo_owner(DAY)
    breakfast_id = next(f"T{i}" for i, t in enumerate(owner.get_all_tasks(), 1) if t.title == "Breakfast")
    reply = json.dumps({"moves": [{"task_id": breakfast_id, "new_time": "09:10",
                                   "reason": "Move breakfast after the walk"}]})
    say(f"SCRIPTED Gemini reply: {reply}")
    clear_entries(log_path)
    result = PlannerAgent(Scheduler(owner), fake_gemini(reply)).run(DAY)
    say("OUTPUT:")
    say(result.summary())
    say("(Pass 2: the scripted Gemini repeats its same move, which is now a no-op and is rejected, "
        "so the rules finish the remaining clash.)")

    say()
    say("--- Case B: Gemini fails (quota error), the agent falls back to its rules")
    owner = build_demo_owner(DAY)
    err = RuntimeError("429 RESOURCE_EXHAUSTED: You exceeded your current quota")
    say(f"SCRIPTED Gemini failure: {err}")
    clear_entries(log_path)
    result = PlannerAgent(Scheduler(owner), fake_gemini(err)).run(DAY)
    say("OUTPUT:")
    say(result.summary())
    say("Recorded in the JSON activity log (what the app's Log tab shows):")
    show_log(log_path, workdir, ("WARNING", "ERROR"))

    # ------------------------------------------------------------------ GUARDRAILS
    heading("PART 3. RELIABILITY AND GUARDRAIL RESULTS")

    say()
    say("--- Guardrail 1: unsafe or invalid Gemini moves are rejected")
    owner = build_demo_owner(DAY)
    ids = {t.title: f"T{i}" for i, t in enumerate(owner.get_all_tasks(), 1)}
    reply = json.dumps({"moves": [
        {"task_id": ids["Thyroid meds"], "new_time": "14:00", "reason": "meds six hours late"},
        {"task_id": "T99", "new_time": "09:00", "reason": "a task that does not exist"},
        {"task_id": ids["Breakfast"], "new_time": "25:99", "reason": "not a real time"},
    ]})
    say(f"SCRIPTED Gemini reply: {reply}")
    meds = next(t for t in owner.get_all_tasks() if t.title == "Thyroid meds")
    requested = meds.due
    clear_entries(log_path)
    result = PlannerAgent(Scheduler(owner), fake_gemini(reply)).run(DAY)
    say("OUTPUT:")
    say(result.summary())
    say("Invalid moves that never got that far (recorded in the activity log):")
    show_log(log_path, workdir, ("WARNING",))
    shift = abs((meds.due - requested).total_seconds()) / 60
    say(f"RESULT: Thyroid meds moved {shift:.0f} min (limit 30), not 345; no unknown task was touched")

    say()
    say("--- Guardrail 2: a problem the agent cannot safely fix is reported, not forced")
    owner = Owner("Travis", available_minutes_per_day=180)
    mia = Pet("Mia", "cat")
    owner.add_pet(mia)
    mia.add_task(Task("Thyroid pill A", Category.MEDS, 60, due=at(8)))
    mia.add_task(Task("Thyroid pill B", Category.MEDS, 60, due=at(8)))
    result = PlannerAgent(Scheduler(owner)).run(DAY)
    say("OUTPUT:")
    say(result.summary())
    say(f"RESULT: agent resolved the plan: {result.resolved} (expected False: it refuses to push "
        "a medication an hour late)")

    say()
    say("--- Guardrail 3: a corrupt data file is moved aside, never overwritten")
    bad = workdir / "corrupt_data.json"
    bad.write_text("{ this is not valid json", encoding="utf-8")
    clear_entries(log_path)
    try:
        load_owner(bad)
    except StorageError as err:
        say(stable(f"OUTPUT: StorageError: {err}", workdir))
    backups = [stable(p.name, workdir) for p in workdir.glob("corrupt_data.json.corrupt-*")]
    say(f"RESULT: the corrupt file was kept as a backup, not overwritten: {backups}")
    show_log(log_path, workdir, ("ERROR",))

    say()
    say("--- Guardrail 4: a failed save is logged (once) and reported, not hidden")
    unwritable = workdir / "a_file"
    unwritable.write_text("x")
    clear_entries(log_path)
    ok = save_owner(build_demo_owner(DAY), unwritable / "data.json")
    say(f"OUTPUT: save_owner returned {ok} (it did not raise)")
    show_log(log_path, workdir, ("ERROR",))

    # ------------------------------------------------------------------ TESTS
    heading("PART 4. AUTOMATED TEST SUITE")
    say("$ python -m pytest -q")
    pytest_out = run_command(["-m", "pytest", "-q", "-p", "no:cacheprovider"], env_extra)
    say(pytest_out.splitlines()[-1] if pytest_out else "(no output)")
    listing = run_command(["-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"], env_extra)
    per_file: dict[str, int] = {}
    for line in listing.splitlines():
        if "::" in line:
            per_file[line.split("::")[0]] = per_file.get(line.split("::")[0], 0) + 1
    say("Tests per file: " + ", ".join(f"{Path(f).name}={n}" for f, n in sorted(per_file.items())))

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "evidence_report.txt").write_text("\n".join(REPORT) + "\n", encoding="utf-8")
    (OUT_DIR / "pytest_output.txt").write_text(pytest_out + "\n", encoding="utf-8")
    say("(Only the pytest timing varies between runs, and the operating system's own wording in the")
    say("failed-save error line differs between Windows, macOS and Linux; everything else is identical.)")
    say()
    say(f"Wrote {OUT_DIR / 'evidence_report.txt'} and {OUT_DIR / 'pytest_output.txt'}")
    shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
