"""The task list in words (#231): how far along the work is.

``transcript.py`` reads the list from TaskCreate, TaskUpdate and TodoWrite
calls into ``Transcript.tasks``. This module says it: a row for each task in
View, Lists, the progress line Turn Status and Properties give, and what's
announced when a task is marked done. No window is needed, so it's all tested
without one.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from dataclasses import dataclass

TASK_PENDING, TASK_IN_PROGRESS, TASK_COMPLETED = "pending", "in_progress", "completed"
TASK_STATUSES = (TASK_PENDING, TASK_IN_PROGRESS, TASK_COMPLETED)

@dataclass
class TaskStep:
    """One step on a provider task list (#231, #318), as its latest update left it."""
    task_id: str
    subject: str
    description: str = ""
    #: What the provider says while it's on the step ("Running the tests").
    active_form: str = ""
    status: str = TASK_PENDING
    #: The turn it was added in, and the turn it last changed in.
    turn: int = 0
    changed_turn: int = 0

    @property
    def done(self) -> bool:
        return self.status == TASK_COMPLETED

#: Each status as the list and Properties say it.
STATE_WORDS = {TASK_PENDING: "To do", TASK_IN_PROGRESS: "In progress", TASK_COMPLETED: "Done"}
#: How many finished steps one announcement names.
DONE_NAMED = 3


def state_word(task: TaskStep) -> str:
    return STATE_WORDS.get(task.status, "To do")


def row(task: TaskStep) -> str:
    """The task's line in the Tasks list: its name first, then its state."""
    return f"{task.subject}. {state_word(task)}"


def detail(task: TaskStep, position: int, total: int) -> str:
    """The task in full, for the box below the list."""
    parts = [task.subject, f"{state_word(task)}. Task {position} of {total}."]
    if task.status == TASK_IN_PROGRESS and task.active_form and task.active_form != task.subject:
        parts.append(f"Now: {task.active_form}")
    if task.description and task.description != task.subject:
        parts.append(task.description)
    return "\n\n".join(parts)


def progress(tasks: Sequence[TaskStep], working: bool = True) -> str:
    """Where Claude is on its list, in one sentence, or "" without one:
    "Task 3 of 7: Running the tests. 2 done." While Claude is ``working``,
    the step it's on is named by what Claude says it's doing, so it reads as
    a sentence; otherwise by its name, as where it stopped."""
    total = len(tasks)
    if not total:
        return ""
    done = sum(1 for t in tasks if t.done)
    if done == total:
        return "All 1 task done." if total == 1 else f"All {total} tasks done."
    current = next(((i, t) for i, t in enumerate(tasks, 1) if t.status == TASK_IN_PROGRESS), None)
    if current is not None:
        position, task = current
        if not working:
            return f"Stopped at task {position} of {total}: {task.subject}. {done} done."
        return f"Task {position} of {total}: {task.active_form or task.subject}. {done} done."
    upcoming = next((t for t in tasks if not t.done), None)
    nouns = "task" if total == 1 else "tasks"
    return f"{done} of {total} {nouns} done. Next: {upcoming.subject}."


def is_current(tasks: Sequence[TaskStep], latest_turn: int) -> bool:
    """Whether the list still says something about the work: some step isn't
    done, and the list changed in the latest turn. An old, finished list
    isn't worth repeating every time Turn Status is asked."""
    return (bool(tasks) and not all(t.done for t in tasks)
            and max(t.changed_turn for t in tasks) >= latest_turn)


def statuses(tasks: Sequence[TaskStep]) -> Dict[str, str]:
    """Each task's status by id: what ``newly_done`` compares against."""
    return {t.task_id: t.status for t in tasks}


def newly_done(before: Dict[str, str], tasks: Sequence[TaskStep]) -> List[TaskStep]:
    """Tasks marked done since ``before`` was taken. A task that's new and
    already done counts too; one that was done before doesn't."""
    return [t for t in tasks if t.done and before.get(t.task_id) != TASK_COMPLETED]


def done_text(finished: Sequence[TaskStep], tasks: Sequence[TaskStep]) -> Optional[str]:
    """What's said when tasks are marked done: "Done: Run the tests. 4 of 7."
    None when nothing finished. Many at once (a list caught up in one read)
    names the last few, so it stays short."""
    if not finished:
        return None
    names = "; ".join(t.subject for t in finished[-DONE_NAMED:])
    if len(finished) > DONE_NAMED:
        names = f"{len(finished) - DONE_NAMED} steps, then {names}"
    done = sum(1 for t in tasks if t.done)
    return f"Done: {names}. {done} of {len(tasks)}."
