"""Task dependency graph, readiness and integration order (AGENT-01 §10, §22).

A deterministic, in-memory model - not a workflow engine. It answers exactly
three questions: which tasks may start, which are blocked, and in what order the
Main Agent integrates them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from tools.agent.config import AgentConfig
from tools.agent.contracts import TASK_ID_RE, TaskContract
from tools.agent.errors import (
    DagCycleError,
    DependencyInvalidError,
    TaskInvalidError,
)
from tools.agent.lifecycle import TaskStatus
from tools.agent.paths import patterns_overlap

#: Statuses that satisfy a dependency.
SATISFIED_DEPENDENCY_STATUSES: Final[frozenset[TaskStatus]] = frozenset({TaskStatus.DONE})

#: Statuses that can never satisfy a dependency; the dependent task is blocked
#: until the Main Agent re-plans it (AGENT-01 §38, §94).
UNSATISFIABLE_DEPENDENCY_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.FAILED, TaskStatus.CANCELLED}
)


@dataclass(frozen=True)
class TaskGraph:
    """A validated set of task contracts with their dependencies."""

    tasks: tuple[TaskContract, ...]

    @classmethod
    def build(cls, tasks: Sequence[TaskContract]) -> TaskGraph:
        """Validate ids, dependency references and acyclicity."""
        seen: dict[str, TaskContract] = {}
        for task in tasks:
            if not TASK_ID_RE.match(task.task_id):
                raise TaskInvalidError(
                    f"invalid task id: {task.task_id!r}",
                    details={"field": "task_id", "value": task.task_id},
                )
            if task.task_id in seen:
                raise TaskInvalidError(
                    f"duplicate task id: {task.task_id}",
                    details={"field": "task_id", "value": task.task_id},
                )
            seen[task.task_id] = task
        for task in tasks:
            for dependency in task.dependencies:
                if dependency == task.task_id:
                    raise DependencyInvalidError(
                        f"task {task.task_id} depends on itself",
                        details={"task_id": task.task_id, "dependency": dependency},
                    )
                if dependency not in seen:
                    raise DependencyInvalidError(
                        f"task {task.task_id} depends on unknown task {dependency}",
                        details={"task_id": task.task_id, "dependency": dependency},
                    )
        graph = cls(tasks=tuple(tasks))
        graph.topological_order()  # raises DagCycleError when cyclic
        return graph

    def index(self) -> dict[str, TaskContract]:
        """Task lookup by id."""
        return {task.task_id: task for task in self.tasks}

    def require(self, task_id: str) -> TaskContract:
        """The task with ``task_id``; ``agent.dependency_invalid`` when absent."""
        try:
            return self.index()[task_id]
        except KeyError as exc:
            raise DependencyInvalidError(
                f"unknown task id: {task_id}",
                details={"task_id": task_id},
            ) from exc

    def dependencies_of(self, task_id: str) -> tuple[TaskContract, ...]:
        """The tasks ``task_id`` waits for, in declared order."""
        index = self.index()
        return tuple(index[dep] for dep in self.require(task_id).dependencies)

    def dependents_of(self, task_id: str) -> tuple[TaskContract, ...]:
        """The tasks that wait for ``task_id``, in id order."""
        return tuple(
            task for task in self.tasks if task_id in task.dependencies
        )

    def unsatisfied_dependencies(self, task_id: str) -> tuple[str, ...]:
        """Dependency ids that are not ``DONE``."""
        return tuple(
            dep.task_id
            for dep in self.dependencies_of(task_id)
            if dep.status not in SATISFIED_DEPENDENCY_STATUSES
        )

    def unsatisfiable_dependencies(self, task_id: str) -> tuple[str, ...]:
        """Dependency ids that are ``FAILED`` or ``CANCELLED``."""
        return tuple(
            dep.task_id
            for dep in self.dependencies_of(task_id)
            if dep.status in UNSATISFIABLE_DEPENDENCY_STATUSES
        )

    def readiness(self) -> dict[str, TaskStatus]:
        """Derive the schedulable status of every task.

        Readiness is a fact about the dependency graph, not a label a task
        carries: any task that has not started (``PLANNED``, ``READY`` or
        ``BLOCKED``) is ``READY`` once every dependency is ``DONE`` and
        ``BLOCKED`` otherwise. A task already marked ``READY`` but whose
        dependency is not complete is therefore demoted, not trusted
        (AGENT-01 §10, §38, §74).
        """
        derived: dict[str, TaskStatus] = {}
        for task in self.tasks:
            # Normalise: a contract may carry the status as a plain string (a
            # hand-written JSON fixture, a caller building one in a test), and
            # every downstream comparison is an identity check against the enum.
            status = TaskStatus(task.status)
            if status not in {
                TaskStatus.PLANNED,
                TaskStatus.READY,
                TaskStatus.BLOCKED,
            }:
                derived[task.task_id] = status
                continue
            derived[task.task_id] = (
                TaskStatus.READY if not self.unsatisfied_dependencies(task.task_id)
                else TaskStatus.BLOCKED
            )
        return derived

    def ready_ids(self) -> tuple[str, ...]:
        """Ids that may start now, in deterministic order."""
        derived = self.readiness()
        return tuple(
            task_id for task_id in self._ordered_ids(self._topological_key)
            if derived[task_id] is TaskStatus.READY
        )

    def blocked_ids(self) -> tuple[str, ...]:
        """Ids waiting on a dependency, in deterministic order."""
        derived = self.readiness()
        return tuple(
            task_id for task_id in self._ordered_ids(self._topological_key)
            if derived[task_id] is TaskStatus.BLOCKED
        )

    def with_status(self, task_id: str, status: TaskStatus) -> TaskGraph:
        """A copy of the graph with one task's status changed (no validation)."""
        updated = tuple(
            replace(task, status=status) if task.task_id == task_id else task
            for task in self.tasks
        )
        return TaskGraph(tasks=updated)

    def topological_order(self) -> tuple[str, ...]:
        """Dependency-respecting order; ties broken by task id."""
        return self._ordered_ids(self._topological_key)

    def integration_order(self, config: AgentConfig) -> tuple[str, ...]:
        """Merge order: contract-freezing tasks first, then by task id (§22)."""
        return self._ordered_ids(lambda task: self._integration_key(task, config))

    @staticmethod
    def _topological_key(task: TaskContract) -> tuple[int | str, ...]:
        return (task.task_id,)

    @staticmethod
    def _integration_key(task: TaskContract, config: AgentConfig) -> tuple[int | str, ...]:
        freezes_truth = any(
            patterns_overlap(pattern, truth)
            for pattern in task.allowed_paths
            for truth in config.public_truth_paths
        )
        return (0 if freezes_truth else 1, task.task_id)

    def _ordered_ids(
        self, key: Callable[[TaskContract], tuple[int | str, ...]]
    ) -> tuple[str, ...]:
        """Kahn's algorithm with a deterministic, key-sorted ready set."""
        index = self.index()
        children: dict[str, list[str]] = {task.task_id: [] for task in self.tasks}
        indegree: dict[str, int] = {}
        for task in self.tasks:
            indegree[task.task_id] = len(task.dependencies)
            for dependency in task.dependencies:
                children[dependency].append(task.task_id)
        ready = sorted(
            (task_id for task_id, degree in indegree.items() if degree == 0),
            key=lambda task_id: key(index[task_id]),
        )
        order: list[str] = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            unlocked: list[str] = []
            for child in children[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    unlocked.append(child)
            if unlocked:
                ready.extend(unlocked)
                ready.sort(key=lambda task_id: key(index[task_id]))
        if len(order) != len(self.tasks):
            cycle = sorted(set(index) - set(order))
            raise DagCycleError(
                "task dependency graph contains a cycle",
                details={"in_cycle": cycle},
            )
        return tuple(order)


def blocked_reason(graph: TaskGraph, task_id: str) -> Mapping[str, object] | None:
    """Structured explanation of why ``task_id`` is blocked, if it is."""
    unsatisfied = graph.unsatisfied_dependencies(task_id)
    if not unsatisfied:
        return None
    return {
        "task_id": task_id,
        "unsatisfied_dependencies": list(unsatisfied),
        "unsatisfiable_dependencies": list(graph.unsatisfiable_dependencies(task_id)),
    }
