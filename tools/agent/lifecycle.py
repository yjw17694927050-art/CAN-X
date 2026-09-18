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

# -- two independent policies (AGENT-01-FIX-2 §4-§5, §10, §34) ----------------
#
# Execution capacity and conflict serialisation look similar and are not the
# same question:
#
#   execution slots   who is *currently* consuming one of ``max_sub_agents``?
#   conflict leases   which earlier task still owns a surface a later, conflicting
#                     task may not enter parallel development against?
#
# Keeping them separate is the whole point. A task may hold a conflict lease
# while consuming no execution slot (it handed off, or it is being integrated),
# and a task may consume a slot without holding any lease (it conflicts with
# nobody).

#: Statuses that consume one of the ``max_sub_agents`` Sub-Agent execution slots.
#: Only an actively running implementation occupies a slot: ``READY`` is a
#: *dispatch candidate*, ``HANDOFF_READY`` has finished implementing,
#: ``INTEGRATING`` is Main-Agent work, and ``DONE`` / ``BLOCKED`` / ``FAILED`` /
#: ``CANCELLED`` are not running a Sub-Agent.
EXECUTION_SLOT_STATUSES: Final[frozenset[TaskStatus]] = frozenset({TaskStatus.IN_PROGRESS})

#: Statuses in which an earlier conflicting task still holds a serialisation
#: lease over the shared surface; the later conflicting task may not become
#: runnable. ``DONE`` releases the lease. ``FAILED`` / ``CANCELLED`` are handled
#: by :data:`CONFLICT_REPLAN_STATUSES`, not by releasing.
CONFLICT_LEASE_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.PLANNED,
        TaskStatus.READY,
        TaskStatus.IN_PROGRESS,
        TaskStatus.HANDOFF_READY,
        TaskStatus.BLOCKED,
        TaskStatus.INTEGRATING,
    }
)

#: Statuses in which a conflicting owner terminated without integrating. The
#: later conflicting task is **not** silently released - letting it proceed would
#: let it implement against a contract whose owner vanished. The Main Agent must
#: re-plan (AGENT-01-FIX-2 §11-§12).
CONFLICT_REPLAN_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.FAILED, TaskStatus.CANCELLED}
)


def occupies_sub_agent_slot(status: TaskStatus) -> bool:
    """True when ``status`` consumes one of the ``max_sub_agents`` slots."""
    return status in EXECUTION_SLOT_STATUSES


def holds_conflict_lease(status: TaskStatus) -> bool:
    """True when an earlier conflicting task still blocks a later one."""
    return status in CONFLICT_LEASE_STATUSES


def requires_conflict_replan(status: TaskStatus) -> bool:
    """True when a terminated conflict owner requires a Main-Agent re-plan."""
    return status in CONFLICT_REPLAN_STATUSES


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
