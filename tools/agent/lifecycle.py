"""Task status set and the transitions the protocol allows.

The lifecycle is deliberately small (AGENT-01 §67): a deterministic state
machine, not a workflow engine. Every transition that the tooling accepts is
listed here; everything else is refused with ``agent.task_invalid``.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final

from tools.agent.errors import TaskInvalidError


class TaskStatus(StrEnum):
    """The states a task occupies from assignment to integration."""

    PLANNED = "PLANNED"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    HANDOFF_READY = "HANDOFF_READY"
    BLOCKED = "BLOCKED"
    INTEGRATING = "INTEGRATING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


#: Allowed transitions by source state. A missing destination is a refusal.
ALLOWED_TRANSITIONS: Final[Mapping[TaskStatus, frozenset[TaskStatus]]] = {
    TaskStatus.PLANNED: frozenset(
        {TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
    ),
    TaskStatus.READY: frozenset(
        {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
    ),
    TaskStatus.IN_PROGRESS: frozenset(
        {
            TaskStatus.HANDOFF_READY,
            TaskStatus.BLOCKED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.HANDOFF_READY: frozenset(
        {TaskStatus.INTEGRATING, TaskStatus.BLOCKED, TaskStatus.FAILED}
    ),
    TaskStatus.BLOCKED: frozenset(
        {TaskStatus.READY, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.INTEGRATING: frozenset(
        {TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.FAILED}
    ),
    TaskStatus.DONE: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

#: Statuses from which no further transition exists. Rework re-enqueues a new
#: task revision rather than reviving a terminal one (AGENT-01 §37, §94).
TERMINAL_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED}
)

#: Statuses in which a task may be handed off / integrated.
IN_FLIGHT_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.PLANNED,
        TaskStatus.READY,
        TaskStatus.IN_PROGRESS,
        TaskStatus.HANDOFF_READY,
        TaskStatus.BLOCKED,
        TaskStatus.INTEGRATING,
    }
)


def can_transition(source: TaskStatus, destination: TaskStatus) -> bool:
    """True when ``source -> destination`` is an allowed transition."""
    return destination in ALLOWED_TRANSITIONS[source]


def assert_transition(source: TaskStatus, destination: TaskStatus) -> None:
    """Raise ``agent.task_invalid`` unless the transition is allowed."""
    if not can_transition(source, destination):
        raise TaskInvalidError(
            f"illegal task transition {source} -> {destination}",
            details={
                "from": str(source),
                "to": str(destination),
                "allowed": sorted(str(item) for item in ALLOWED_TRANSITIONS[source]),
            },
        )
