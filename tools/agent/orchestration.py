"""The Main Agent's planning surface: readiness, conflicts and merge order.

``plan`` composes the dependency graph and the conflict classifier into the one
verdict the Main Agent acts on: which tasks may run in parallel, which are
blocked, which are deferred because they would race another task, and in what
order the survivors integrate (AGENT-01 §10, §21, §22, §74).
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
from tools.agent.lifecycle import TaskStatus

#: Deferral is deterministic: of two ready tasks that conflict above the
#: auto-resolvable ceiling, the one that comes later in integration order waits.
DEFERRAL_RULE: Final[str] = "later-in-integration-order defers to earlier"


@dataclass(frozen=True, kw_only=True)
class TaskPlanEntry:
    """One task's schedulability verdict."""

    task_id: str
    status: TaskStatus
    deferred_by: tuple[str, ...]
    serial_review_required: bool
    capacity_deferred: bool = False

    @property
    def runnable(self) -> bool:
        """True when the task may be dispatched now."""
        return (
            self.status is TaskStatus.READY
            and not self.deferred_by
            and not self.capacity_deferred
        )

    def deferral_kind(self) -> str:
        """Which of the three deferral reasons applies, in precedence order."""
        if self.status is TaskStatus.BLOCKED:
            return "dependency"
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
            "capacity": {
                "max_sub_agents": self.max_sub_agents,
                "selected": list(self.runnable_ids()),
                "capacity_deferred": list(self.capacity_deferred_ids()),
            },
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "entries": [entry.to_dict() for entry in self.entries],
        }


def plan(tasks: tuple[TaskContract, ...], config: AgentConfig) -> OrchestrationPlan:
    """Compute the orchestration verdict for a set of tasks.

    ``max_sub_agents`` is enforced here, not merely validated in the config: a
    plan that claims "1 Main Agent + up to 4 Sub-Agents" must never hand back
    five runnable tasks. The wave is cut deterministically from the integration
    order, so the same input always yields the same wave, and the tasks held
    back only by capacity are reported as such rather than as blocked or
    conflicting (FIX-1 §21-22).
    """
    graph = TaskGraph.build(tasks)
    readiness = graph.readiness()
    order = graph.integration_order(config)
    position = {task_id: index for index, task_id in enumerate(order)}
    conflicts = classify_all(tasks, config)

    deferred: dict[str, list[str]] = {task.task_id: [] for task in tasks}
    for conflict in conflicts:
        if conflict.auto_resolvable:
            continue
        left_ready = readiness[conflict.left] is TaskStatus.READY
        right_ready = readiness[conflict.right] is TaskStatus.READY
        if not (left_ready and right_ready):
            continue
        later = (
            conflict.right
            if position[conflict.right] > position[conflict.left]
            else conflict.left
        )
        earlier = conflict.left if later == conflict.right else conflict.right
        deferred[later].append(earlier)

    candidates = [
        task_id
        for task_id in order
        if readiness[task_id] is TaskStatus.READY and not deferred[task_id]
    ]
    over_capacity = set(candidates[config.max_sub_agents :])

    by_id = {task.task_id: task for task in tasks}
    entries = tuple(
        TaskPlanEntry(
            task_id=task_id,
            status=readiness[task_id],
            deferred_by=tuple(sorted(set(deferred[task_id]))),
            serial_review_required=requires_serial_review(by_id[task_id], config),
            capacity_deferred=task_id in over_capacity,
        )
        for task_id in order
    )
    return OrchestrationPlan(
        entries=entries,
        conflicts=conflicts,
        integration_order=order,
        max_sub_agents=config.max_sub_agents,
    )
