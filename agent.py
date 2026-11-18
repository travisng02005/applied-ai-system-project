"""Agentic planning loop for PawPal+: plan -> check -> fix -> repeat.

The agent wraps the Scheduler. It does not just call generate_schedule() once;
it treats the first plan as a draft and works on it:

  1. PLAN   - ask the Scheduler for a plan.
  2. CHECK  - audit the plan with deterministic checks (overlaps, tasks that could
              not be placed, requested times that clash, high-priority tasks that
              were skipped, ...).
  3. FIX    - propose "move this task to HH:MM" actions. Proposals come from an LLM
              when GEMINI_API_KEY is set, otherwise from built-in rules. Every
              proposal passes the same guardrails before it touches any task.
  4. REPEAT - re-plan with the changed times and check again, until the plan is
              clean, no safe fix is left, or the iteration limit is reached.

The agent really changes the system's output: it edits the requested times of
tasks (recorded, and undoable) so the final plan has fewer problems than the one
the Scheduler produced on its own.

Guardrails:
  * the checker is plain code, so an LLM can never declare its own work correct;
  * the LLM may only emit "move" actions, and each one is validated (task exists,
    not completed, same day, bounded shift, medication barely movable);
  * a hard iteration limit, a per-task move limit, and rollback to the best state
    seen if a later iteration is worse;
  * any LLM or data error is logged and falls back to the rule-based fixer;
  * everything is logged to the "pawpal.agent" logger (see config.configure_logging).
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

from config import load_env_file
from pawpal_system import Category, Priority, Scheduler, Task

logger = logging.getLogger("pawpal.agent")

MAX_ITERATIONS = 5
MAX_MOVES_PER_TASK = 2
MAX_SHIFT_MINUTES = 240
MEDS_MAX_SHIFT_MINUTES = 30
# Override with the GEMINI_MODEL environment variable if Google renames the model.
LLM_MODEL = "gemini-3.8-flash"


# --- Data classes ---------------------------------------------------------------


@dataclass
class Issue:
    """A problem the checker found in the current plan."""

    kind: str
    message: str
    tasks: list[Task] = field(default_factory=list)
    # Advisory issues need the owner (more minutes, a different budget); the agent
    # can report them but cannot fix them by moving tasks.
    advisory: bool = False


@dataclass
class Action:
    """A proposed fix: move a task's requested time."""

    task: Task
    new_due: datetime
    why: str
    source: str = "rules"


@dataclass
class Change:
    """A task time the agent changed (final result versus the original)."""

    task: Task
    old_due: Optional[datetime]
    new_due: Optional[datetime]
    why: str

    def __str__(self) -> str:
        pet = self.task.pet_names()
        old = f"{self.old_due:%H:%M}" if self.old_due else "flexible"
        new = f"{self.new_due:%H:%M}" if self.new_due else "flexible"
        return f"{pet}: {self.task.title} {old} -> {new} ({self.why})"


@dataclass
class AgentStep:
    """One pass of the loop, kept for the trace shown to the user."""

    iteration: int
    issues: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    source: str = ""
    note: str = ""


@dataclass
class AgentResult:
    on_date: date
    used_llm: bool = False
    steps: list[AgentStep] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    error: str = ""
    _original: dict = field(default_factory=dict, repr=False)

    @property
    def fixer_used(self) -> str:
        """Who actually produced the applied fixes: Gemini, the rules, or a mix (with a reason).

        Being configured with an LLM is not the same as the LLM doing the work: if its
        call fails or its moves are all rejected, the rules take over, and this says so.
        """
        sources = {step.source for step in self.steps if step.source}
        if "llm" in sources and "llm, then rules" in sources:
            return "Gemini, with the rules finishing the rest"
        if "llm" in sources:
            return "Gemini"
        if "llm, then rules" in sources:
            return "rules (Gemini was unavailable or its suggestions were unusable)"
        return "rules"

    @property
    def blocking(self) -> list[Issue]:
        return [i for i in self.issues if not i.advisory]

    @property
    def advisories(self) -> list[Issue]:
        return [i for i in self.issues if i.advisory]

    @property
    def resolved(self) -> bool:
        """True when no problem remains that moving tasks could have fixed."""
        return not self.blocking and not self.error

    def undo(self) -> int:
        """Restore every task time the agent changed; returns how many were reset.

        The caller should regenerate the schedule afterwards.
        """
        count = 0
        for task, old_due in self._original.values():
            if task.due != old_due:
                task.due = old_due
                count += 1
        self.changes = []
        logger.info("Undo: restored %d task time(s)", count)
        return count

    def summary(self) -> str:
        """Plain-text trace of what the agent did."""
        lines = [f"Planning agent (fixes by: {self.fixer_used})"]
        for step in self.steps:
            lines.append(f"Pass {step.iteration}: {len(step.issues)} issue(s) found")
            lines += [f"  ! {m}" for m in step.issues]
            lines += [f"  -> {a}" for a in step.actions]
            lines += [f"  x rejected: {r}" for r in step.rejected]
            if step.note:
                lines.append(f"  {step.note}")
        if self.error:
            lines.append(f"Error: {self.error}")
        lines.append("Result: " + ("plan passed all checks" if self.resolved
                                   else f"{len(self.blocking)} problem(s) remain"))
        lines += [f"  advice: {i.message}" for i in self.advisories]
        return "\n".join(lines)


# --- Checking -------------------------------------------------------------------


def _label(task: Task) -> str:
    return f"{task.pet_names()}: {task.title}"


def check_plan(scheduler: Scheduler, on_date: date) -> list[Issue]:
    """Audit the scheduler's current plan with plain, deterministic checks."""
    issues: list[Issue] = []

    for a, b in scheduler.detect_conflicts():
        issues.append(Issue(
            "overlap", f"Plan overlap: {_label(a.task)} and {_label(b.task)}",
            [a.task, b.task]))

    for task, why in scheduler.unplaced:
        issues.append(Issue("unplaced", f"{_label(task)} could not be placed ({why})", [task]))

    try:
        pairs = scheduler.detect_task_conflicts(on_date)
    except (TypeError, ValueError) as err:  # e.g. mixed naive/aware datetimes
        issues.append(Issue("bad_data", f"Could not check requested times: {err}",
                            advisory=True))
        pairs = []
    for a, b in pairs:
        issues.append(Issue(
            "requested_conflict",
            f"Requested times clash: {_label(a)} ({a.due:%H:%M}) and "
            f"{_label(b)} ({b.due:%H:%M})", [a, b]))

    planned = sum(i.task.duration_minutes for i in scheduler.schedule)
    budget = scheduler.owner.get_available_time(on_date)
    for task in scheduler.skipped_tasks:
        if task.priority is Priority.HIGH:
            extra = max(0, planned + task.duration_minutes - budget)
            issues.append(Issue(
                "skipped_high",
                f"High-priority task skipped for lack of time: {_label(task)} "
                f"({task.duration_minutes} min); roughly {extra} more minutes "
                "in the daily budget would help",
                [task], advisory=True))

    for warning in scheduler.warnings:
        issues.append(Issue("budget", warning, advisory=True))
    return issues


def free_gaps(scheduler: Scheduler, on_date: date, ignore: Optional[Task] = None):
    """Free (start, end) intervals in the day, given the current plan."""
    day_start = datetime.combine(
        on_date, scheduler.owner.preferences.get("day_start", time(8, 0)))
    day_end = datetime.combine(on_date + timedelta(days=1), time(0, 0))
    busy = sorted((i.start, i.end) for i in scheduler.schedule if i.task is not ignore)
    gaps, cursor = [], day_start
    for start, end in busy:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < day_end:
        gaps.append((cursor, day_end))
    return gaps


def find_slot(gaps, desired: datetime, minutes: int) -> Optional[datetime]:
    """Earliest start at/after `desired` that fits; else the earliest fit anywhere."""
    need = timedelta(minutes=minutes)
    for g_start, g_end in gaps:
        start = max(g_start, desired)
        if start + need <= g_end:
            return start
    for g_start, g_end in gaps:
        if g_start + need <= g_end:
            return g_start
    return None


# --- Fixers ---------------------------------------------------------------------


def rule_based_fixes(scheduler: Scheduler, issues: list[Issue], on_date: date) -> list[Action]:
    """Propose moves from the issue list using simple rules.

    * unplaced task -> move its requested time to the nearest free slot;
    * clashing requested times -> move one of the two to where the plan actually
      put it (preferring to move the lower-priority, non-medication, later task).
    """
    placed = {id(i.task): i for i in scheduler.schedule}
    actions: dict[int, Action] = {}

    def propose(task: Task, why: str) -> bool:
        if id(task) in actions or task.due is None or task.completed:
            return False
        item = placed.get(id(task))
        if item is not None:
            target = item.start
        else:
            target = find_slot(free_gaps(scheduler, on_date), task.due, task.duration_minutes)
        if target is None or target == task.due:
            return False
        actions[id(task)] = Action(task, target, why)
        return True

    for issue in issues:
        if issue.advisory:
            continue
        if issue.kind == "unplaced":
            propose(issue.tasks[0], "no room near its requested time; moved to a free slot")
        elif issue.kind == "requested_conflict":
            a, b = issue.tasks
            candidates = sorted(
                (a, b),
                key=lambda t: (t.category is Category.MEDS, t.priority, -(t.due.timestamp())),
            )
            for task in candidates:
                other = b if task is a else a
                if propose(task, f"requested time clashed with {_label(other)}"):
                    break
    return list(actions.values())


class LLMFixer:
    """Asks Gemini to propose moves; its output is parsed and then guardrailed."""

    SYSTEM = (
        "You are the fixing step of a pet-care scheduling agent. You receive the "
        "current day plan, the problems found by a checker, and the free time slots. "
        "Propose the smallest set of changes that resolves the problems, ONLY by moving "
        "a task's requested time to a time that fits a free slot. Prefer moving "
        "low-priority tasks. Medication tasks (category 'meds') should barely move. "
        "Never invent task ids. Reply with JSON only, in exactly this shape: "
        '{"moves": [{"task_id": "T1", "new_time": "HH:MM", "reason": "short reason"}]}'
    )

    def __init__(self, client, model: Optional[str] = None):
        self.client = client
        self.model = model or os.environ.get("GEMINI_MODEL") or LLM_MODEL

    @classmethod
    def from_env(cls) -> Optional["LLMFixer"]:
        """An LLMFixer if GEMINI_API_KEY (or GOOGLE_API_KEY) is set and the SDK is
        installed, else None."""
        load_env_file()
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            return None
        try:
            from google import genai
        except ImportError:
            logger.warning("A Gemini API key is set but the 'google-genai' package is missing")
            return None
        return cls(genai.Client(api_key=key))

    def propose(self, context: dict) -> list[dict]:
        """Return the raw list of move dicts the model suggested (may raise)."""
        response = self.client.models.generate_content(
            model=self.model,
            contents=json.dumps(context),
            config={
                "system_instruction": self.SYSTEM,
                "response_mime_type": "application/json",
                "max_output_tokens": 800,
            },
        )
        text = (response.text or "").strip()
        if text.startswith("```"):  # tolerate a fenced code block
            text = text.strip("`").removeprefix("json").strip()
        moves = json.loads(text).get("moves", [])
        if not isinstance(moves, list):
            raise ValueError("'moves' is not a list")
        return moves


# --- The agent ------------------------------------------------------------------


class PlannerAgent:
    """Runs the plan -> check -> fix loop on top of a Scheduler."""

    def __init__(self, scheduler: Scheduler, llm: Optional[LLMFixer] = None,
                 max_iterations: int = MAX_ITERATIONS):
        self.scheduler = scheduler
        self.llm = llm
        self.max_iterations = max(1, max_iterations)

    # -- public ------------------------------------------------------------

    def run(self, on_date: date) -> AgentResult:
        """Plan, then check and fix until clean or out of safe options.

        On return, scheduler.schedule holds the final plan. Never raises: errors
        are logged, the best state seen is restored, and result.error is set.
        """
        scheduler = self.scheduler
        result = AgentResult(on_date, used_llm=self.llm is not None)
        tasks = scheduler.owner.get_all_tasks()
        result._original = {id(t): (t, t.due) for t in tasks}
        reasons: dict[int, str] = {}
        moves: Counter = Counter()
        best: Optional[tuple[int, dict]] = None
        logger.info("Agent run start: %d task(s), date %s, fixer=%s",
                    len(tasks), on_date, "llm" if self.llm else "rules")

        try:
            for i in range(1, self.max_iterations + 1):
                scheduler.generate_schedule(on_date)
                issues = check_plan(scheduler, on_date)
                blocking = [x for x in issues if not x.advisory]
                step = AgentStep(i, [x.message for x in issues])
                result.steps.append(step)
                result.issues = issues
                logger.info("Pass %d: %d issue(s), %d blocking", i, len(issues), len(blocking))
                if best is None or len(blocking) < best[0]:
                    best = (len(blocking), self._snapshot())

                if not blocking:
                    step.note = "Plan passed all checks."
                    break
                if i == self.max_iterations:
                    step.note = "Iteration limit reached."
                    logger.warning("Iteration limit reached with %d blocking issue(s)",
                                   len(blocking))
                    break

                applied = self._fix(issues, on_date, step, moves, reasons)
                if not applied:
                    step.note = "No safe fix available; stopping."
                    logger.info("No safe fix available; stopping")
                    break
        except Exception as err:  # guardrail: the agent must never crash the app
            logger.exception("Agent run failed")
            result.error = f"{type(err).__name__}: {err}"

        # Rollback guardrail: never end worse than the best state seen.
        if best is not None and len(result.blocking) > best[0]:
            logger.warning("Rolling back to best state (%d blocking issues)", best[0])
            self._restore(best[1])
            try:
                scheduler.generate_schedule(on_date)
                result.issues = check_plan(scheduler, on_date)
                result.steps[-1].note += " Rolled back to the best earlier plan."
            except Exception as err:
                result.error = result.error or f"{type(err).__name__}: {err}"

        result.changes = [
            Change(task, old, task.due, reasons.get(id(task), "adjusted by agent"))
            for task, old in result._original.values()
            if task.due != old
        ]
        logger.info("Agent run end: resolved=%s, %d change(s)", result.resolved,
                    len(result.changes))
        return result

    # -- internals ---------------------------------------------------------

    def _snapshot(self) -> dict:
        return {id(t): (t, t.due) for t in self.scheduler.owner.get_all_tasks()}

    @staticmethod
    def _restore(snapshot: dict) -> None:
        for task, due in snapshot.values():
            task.due = due

    def _fix(self, issues, on_date, step: AgentStep, moves: Counter, reasons: dict) -> int:
        """Propose and apply moves for this pass; returns how many were applied."""
        applied = 0
        if self.llm is not None:
            actions = self._llm_actions(issues, on_date)
            applied = self._apply_all(actions, on_date, step, moves, reasons)
            step.source = "llm"
        if not applied:
            actions = rule_based_fixes(self.scheduler, issues, on_date)
            applied = self._apply_all(actions, on_date, step, moves, reasons)
            step.source = "rules" if not self.llm else "llm, then rules"
        return applied

    def _llm_actions(self, issues, on_date) -> list[Action]:
        ids = {f"T{n}": t for n, t in enumerate(self.scheduler.owner.get_all_tasks(), 1)}
        label = {id(t): k for k, t in ids.items()}
        context = {
            "date": on_date.isoformat(),
            "tasks": [
                {"id": k, "pet": t.pet_names(), "title": t.title,
                 "category": t.category.value, "priority": t.priority.name,
                 "minutes": t.duration_minutes,
                 "requested": f"{t.due:%H:%M}" if t.due else None,
                 "completed": t.completed}
                for k, t in ids.items()
            ],
            "plan": [
                {"id": label.get(id(i.task)), "start": f"{i.start:%H:%M}", "end": f"{i.end:%H:%M}"}
                for i in self.scheduler.sort_by_time()
            ],
            "free_slots": [f"{s:%H:%M}-{e:%H:%M}"
                           for s, e in free_gaps(self.scheduler, on_date)],
            "problems": [
                {"kind": x.kind, "message": x.message,
                 "task_ids": [label.get(id(t)) for t in x.tasks]}
                for x in issues if not x.advisory
            ],
        }
        try:
            raw = self.llm.propose(context)
        except Exception as err:  # network, auth, bad JSON ... fall back to rules
            detail = " ".join(str(err).split())  # API errors can be long and multi-line
            if len(detail) > 220:
                detail = detail[:220] + "..."
            logger.warning("LLM fixer failed (%s: %s); falling back to rules",
                           type(err).__name__, detail)
            return []
        actions = []
        for move in raw:
            try:
                task = ids[str(move["task_id"])]
                hh, mm = str(move["new_time"]).split(":")
                new_due = datetime.combine(on_date, time(int(hh), int(mm)))
                actions.append(Action(task, new_due, str(move.get("reason", "LLM suggestion")),
                                      source="llm"))
            except (KeyError, ValueError, TypeError) as err:
                logger.warning("Ignoring malformed LLM move %r (%s)", move, err)
        logger.info("LLM proposed %d move(s)", len(actions))
        return actions

    def _apply_all(self, actions, on_date, step, moves, reasons) -> int:
        applied = 0
        for action in actions:
            problem = self._validate(action, on_date, moves)
            label = _label(action.task)
            if problem:
                step.rejected.append(f"{label}: {problem}")
                logger.warning("Rejected move for %s: %s", label, problem)
                continue
            old = action.task.due
            action.task.reschedule(action.new_due)
            moves[id(action.task)] += 1
            reasons[id(action.task)] = action.why
            step.actions.append(f"move {label} {old:%H:%M} -> {action.new_due:%H:%M} ({action.why})")
            logger.info("Applied [%s]: %s %s -> %s (%s)", action.source, label,
                        f"{old:%H:%M}", f"{action.new_due:%H:%M}", action.why)
            applied += 1
        return applied

    def _validate(self, action: Action, on_date: date, moves: Counter) -> str:
        """Guardrails for one move. Returns a problem description, or '' if it is safe."""
        task = action.task
        if task.completed:
            return "task is already completed"
        if task.due is None:
            return "flexible tasks have no time to move"
        if action.new_due.date() != on_date:
            return "new time is not on the planned day"
        if moves[id(task)] >= MAX_MOVES_PER_TASK:
            return "moved too many times already"
        shift = abs((action.new_due - task.due).total_seconds()) / 60
        limit = MEDS_MAX_SHIFT_MINUTES if task.category is Category.MEDS else MAX_SHIFT_MINUTES
        if shift > limit:
            return f"shift of {int(shift)} min exceeds the {limit} min limit"
        if shift == 0:
            return "no change"
        return ""
