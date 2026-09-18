"""The Main Agent's planning surface: readiness, conflicts and merge order.

``plan`` composes the dependency graph and the conflict classifier into the one
verdict the Main Agent acts on: which tasks may run in parallel, which are
blocked, which are deferred because they would race another task, and in what
order the survivors integrate (AGENT-01 §10, §21, §22, §74).

Two independent questions are answered separately (AGENT-01-FIX-2 §4-§5, §54):

```text
execution capacity     who is currently consuming one of ``max_sub_agents``?
conflict serialisation which earlier task still owns a surface a later,
                       conflicting task must not enter parallel work against?
```

Capacity and conflict reach the same output - "this task may not be dispatched
now" - for entirely different reasons, so they are computed by two separate
predicates and reported as two distinct deferral kinds. `plan` is pure and
deterministic: it never mutates a task's status. The Main Agent performs the
lifecycle transition; the next call to `plan` then sees the new occupancy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from tools.agent.config import AgentConfig
from tools.agent.conflicts import (
    PairConflict,
    classify_all,
    requires_serial_review,
)
from tools.agent.contracts import TaskContract
from tools.agent.graph import TaskGraph
from tools.agent.lifecycle import (
    TaskStatus,
    holds_conflict_lease,
    occupies_sub_agent_slot,
    requires_conflict_replan,
)

#: Deferral is deterministic: of two tasks that conflict above the
#: auto-resolvable ceiling, the one that comes later in integration order waits
#: for the earlier one for as long as the earlier one holds a serialisation
#: lease - not merely while both happen to be ``READY``.
DEFERRAL_RULE: Final[str] = "later-in-integration-order defers to earlier"


@dataclass(frozen=True, kw_only=True)
class TaskPlanEntry:
    """One task's schedulability verdict."""

    task_id: str
    status: TaskStatus
    deferred_by: tuple[str, ...]
    serial_review_required: bool
    capacity_deferred: bool = False
    replan_required_by: tuple[str, ...] = ()

    @property
    def runnable(self) -> bool:
        """True when the task may be dispatched now."""
        return (
            self.status is TaskStatus.READY
            and not self.deferred_by
            and not self.capacity_deferred
            and not self.replan_required_by
        )

    def deferral_kind(self) -> str:
        """Which deferral reason applies, in precedence order.

        ``replan`` outranks ``conflict``: a task blocked because a conflicting
        owner terminated needs a Main-Agent decision, not merely to wait.
        """
        if self.status is TaskStatus.BLOCKED:
            return "dependency"
        if self.replan_required_by:
            return "replan"
        if self.deferred_by:
            return "conflict"
        if self.capacity_deferred:
            return "capacity"
        return "none"

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "status": str(self.status),
            "runnable": self.runnable,
            "deferred_by": list(self.deferred_by),
            "replan_required_by": list(self.replan_required_by),
            "deferral_kind": self.deferral_kind(),
            "capacity_deferred": self.capacity_deferred,
            "serial_review_required": self.serial_review_required,
        }


@dataclass(frozen=True, kw_only=True)
class OrchestrationPlan:
    """A whole-plan verdict."""

    entries: tuple[TaskPlanEntry, ...]
    conflicts: tuple[PairConflict, ...]
    integration_order: tuple[str, ...]
    max_sub_agents: int
    active_ids: tuple[str, ...]
    available_slots: int
    #: How many more Sub-Agents are already running than the cap allows. The
    #: planner never creates this state (it only ever *adds* up to
    #: ``available_slots``), but an over-dispatched caller must be told rather
    #: than have the excess silently rounded away.
    active_over_capacity: int = 0
    deferral_rule: str = DEFERRAL_RULE

    def runnable_ids(self) -> tuple[str, ...]:
        """Ids that may be dispatched now, in integration order."""
        runnable = {entry.task_id for entry in self.entries if entry.runnable}
        return tuple(task_id for task_id in self.integration_order if task_id in runnable)

    def blocked_ids(self) -> tuple[str, ...]:
        """Ids waiting on a dependency, in integration order."""
        blocked = {entry.task_id for entry in self.entries if entry.status is TaskStatus.BLOCKED}
        return tuple(task_id for task_id in self.integration_order if task_id in blocked)

    def deferred_ids(self) -> tuple[str, ...]:
        """Ids held back because they would race an earlier task."""
        deferred = {entry.task_id for entry in self.entries if entry.deferred_by}
        return tuple(task_id for task_id in self.integration_order if task_id in deferred)

    def capacity_deferred_ids(self) -> tuple[str, ...]:
        """Ready, non-conflicting ids held back only by the parallelism cap."""
        held = {entry.task_id for entry in self.entries if entry.capacity_deferred}
        return tuple(task_id for task_id in self.integration_order if task_id in held)

    def replan_required_ids(self) -> tuple[str, ...]:
        """Ids blocked because an earlier conflicting owner terminated unresolved."""
        flagged = {entry.task_id for entry in self.entries if entry.replan_required_by}
        return tuple(task_id for task_id in self.integration_order if task_id in flagged)

    def blocking_conflicts(self) -> tuple[PairConflict, ...]:
        """Conflicts at or above the serial-review ceiling."""
        return tuple(conflict for conflict in self.conflicts if not conflict.auto_resolvable)

    def to_dict(self) -> dict[str, object]:
        return {
            "deferral_rule": self.deferral_rule,
            "integration_order": list(self.integration_order),
            "runnable": list(self.runnable_ids()),
            "blocked": list(self.blocked_ids()),
            "deferred": list(self.deferred_ids()),
            "capacity_deferred": list(self.capacity_deferred_ids()),
            "replan_required": list(self.replan_required_ids()),
            "capacity": {
                "max_sub_agents": self.max_sub_agents,
                "active": list(self.active_ids),
                "active_over_capacity": self.active_over_capacity,
                "available_slots": self.available_slots,
                "selected": list(self.runnable_ids()),
                "capacity_deferred": list(self.capacity_deferred_ids()),
            },
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "entries": [entry.to_dict() for entry in self.entries],
        }


def _earlier_and_later(
    conflict: PairConflict, position: dict[str, int]
) -> tuple[str, str]:
    """The pair's owner and waiter, decided by deterministic integration order."""
    if position[conflict.left] <= position[conflict.right]:
        return conflict.left, conflict.right
    return conflict.right, conflict.left


def plan(tasks: tuple[TaskContract, ...], config: AgentConfig) -> OrchestrationPlan:
    """Compute the orchestration verdict for a set of tasks.

    A planning cycle reasons about the work already in flight, not only the tasks
    that happen to be ``READY`` at this instant (AGENT-01-FIX-2 §54):

    * **Conflict leases are held across status transitions.** A task that
      conflicts above C1 keeps blocking the later task until it *lands*
      (``DONE``). Merely dispatching it (``READY -> IN_PROGRESS``) does not
      release the surface. A conflicting owner that terminates (``FAILED`` /
      ``CANCELLED``) does not silently release it either - the later task is
      flagged as needing a re-plan instead.
    * **Capacity counts the agents already running.** ``max_sub_agents`` bounds
      the *total* concurrent Sub-Agents, never "new tasks per planning cycle":
      ``available_slots = max(0, max_sub_agents - active)``.

    ``plan`` mutates nothing: the Main Agent performs the lifecycle transition,
    and the next call then sees the slot occupied. The wave is cut
    deterministically from the integration order, so the same task set, statuses
    and config always yield the same dispatch wave (AGENT-01-FIX-2 §8).
    """
    graph = TaskGraph.build(tasks)
    readiness = graph.readiness()
    order = graph.integration_order(config)
    position = {task_id: index for index, task_id in enumerate(order)}
    conflicts = classify_all(tasks, config)

    # Conflict serialisation. For every blocking pair, the earlier task holds a
    # lease while it is non-terminal and unresolved; the later task waits.
    deferred: dict[str, list[str]] = {task.task_id: [] for task in tasks}
    replan: dict[str, list[str]] = {task.task_id: [] for task in tasks}
    for conflict in conflicts:
        if conflict.auto_resolvable:
            continue
        earlier, later = _earlier_and_later(conflict, position)
        owner_status = readiness[earlier]
        if holds_conflict_lease(owner_status):
            deferred[later].append(earlier)
        elif requires_conflict_replan(owner_status):
            replan[later].append(earlier)

    # Execution capacity. Only slots actually occupied by a running Sub-Agent are
    # subtracted, so the planner never *adds* more than the free slots:
    #
    #     len(active) + len(newly dispatched) <= max(len(active), max_sub_agents)
    #
    # If a caller has already over-dispatched (``len(active) > max_sub_agents``)
    # the planner adds nothing and reports the excess as ``active_over_capacity``
    # rather than pretending the invariant holds.
    active = tuple(task_id for task_id in order if occupies_sub_agent_slot(readiness[task_id]))
    available_slots = max(0, config.max_sub_agents - len(active))
    active_over_capacity = max(0, len(active) - config.max_sub_agents)

    candidates = [
        task_id
        for task_id in order
        if readiness[task_id] is TaskStatus.READY
        and not deferred[task_id]
        and not replan[task_id]
    ]
    over_capacity = set(candidates[available_slots:])

    by_id = {task.task_id: task for task in tasks}
    entries = tuple(
        TaskPlanEntry(
            task_id=task_id,
            status=readiness[task_id],
            deferred_by=tuple(sorted(set(deferred[task_id]))),
            serial_review_required=requires_serial_review(by_id[task_id], config),
            capacity_deferred=task_id in over_capacity,
            replan_required_by=tuple(sorted(set(replan[task_id]))),
        )
        for task_id in order
    )
    return OrchestrationPlan(
        entries=entries,
        conflicts=conflicts,
        integration_order=order,
        max_sub_agents=config.max_sub_agents,
        active_ids=active,
        available_slots=available_slots,
        active_over_capacity=active_over_capacity,
    )
