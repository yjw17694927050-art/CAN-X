"""Task lifecycle transitions and the dependency graph."""

from __future__ import annotations

import pytest
from agent_tools_support import config, make_task

from tools.agent.contracts import TaskContract
from tools.agent.errors import DagCycleError, DependencyInvalidError, TaskInvalidError
from tools.agent.graph import TaskGraph, blocked_reason
from tools.agent.lifecycle import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    TaskStatus,
    assert_transition,
    can_transition,
)


@pytest.mark.parametrize(
    ("source", "destination"),
    [
        (TaskStatus.PLANNED, TaskStatus.READY),
        (TaskStatus.PLANNED, TaskStatus.BLOCKED),
        (TaskStatus.READY, TaskStatus.IN_PROGRESS),
        (TaskStatus.IN_PROGRESS, TaskStatus.HANDOFF_READY),
        (TaskStatus.HANDOFF_READY, TaskStatus.INTEGRATING),
        (TaskStatus.INTEGRATING, TaskStatus.DONE),
        (TaskStatus.BLOCKED, TaskStatus.READY),
        (TaskStatus.IN_PROGRESS, TaskStatus.FAILED),
        (TaskStatus.HANDOFF_READY, TaskStatus.BLOCKED),
    ],
)
def test_the_happy_path_and_its_recovery_edges_are_allowed(
    source: TaskStatus, destination: TaskStatus
) -> None:
    assert can_transition(source, destination)
    assert_transition(source, destination)


@pytest.mark.parametrize(
    ("source", "destination"),
    [
        (TaskStatus.PLANNED, TaskStatus.IN_PROGRESS),
        (TaskStatus.PLANNED, TaskStatus.DONE),
        (TaskStatus.IN_PROGRESS, TaskStatus.DONE),
        (TaskStatus.DONE, TaskStatus.READY),
        (TaskStatus.FAILED, TaskStatus.IN_PROGRESS),
        (TaskStatus.CANCELLED, TaskStatus.READY),
    ],
)
def test_skipping_a_step_is_refused(source: TaskStatus, destination: TaskStatus) -> None:
    assert not can_transition(source, destination)
    with pytest.raises(TaskInvalidError) as raised:
        assert_transition(source, destination)
    assert raised.value.code == "agent.task_invalid"
    assert raised.value.details["from"] == str(source)


def test_terminal_statuses_have_no_successors() -> None:
    for status in TERMINAL_STATUSES:
        assert ALLOWED_TRANSITIONS[status] == frozenset()


def _chain() -> tuple[TaskContract, TaskContract, TaskContract]:
    alpha = make_task(task_id="AGENT-02-A", owner="sub-a", branch="agent/AGENT-02-A-alpha")
    beta = make_task(task_id="AGENT-02-B", owner="sub-b", branch="agent/AGENT-02-B-beta")
    gamma = make_task(
        task_id="AGENT-02-C",
        owner="sub-c",
        branch="agent/AGENT-02-C-gamma",
        dependencies=("AGENT-02-A",),
    )
    return alpha, beta, gamma


def test_an_independent_task_is_ready_and_a_dependent_task_is_blocked() -> None:
    alpha, beta, gamma = _chain()
    graph = TaskGraph.build((alpha, beta, gamma))
    assert set(graph.ready_ids()) == {"AGENT-02-A", "AGENT-02-B"}
    assert graph.blocked_ids() == ("AGENT-02-C",)
    assert graph.unsatisfied_dependencies("AGENT-02-C") == ("AGENT-02-A",)


def test_a_dependent_task_becomes_ready_once_its_dependency_is_done() -> None:
    alpha, beta, gamma = _chain()
    graph = TaskGraph.build((alpha, beta, gamma))
    advanced = graph.with_status("AGENT-02-A", TaskStatus.DONE)
    rebuilt = TaskGraph.build(advanced.tasks)
    assert "AGENT-02-C" in rebuilt.ready_ids()
    assert rebuilt.blocked_ids() == ()


def test_a_failed_dependency_is_reported_as_unsatisfiable() -> None:
    alpha, beta, gamma = _chain()
    graph = TaskGraph.build((alpha, beta, gamma))
    broken = TaskGraph.build(graph.with_status("AGENT-02-A", TaskStatus.FAILED).tasks)
    assert broken.unsatisfiable_dependencies("AGENT-02-C") == ("AGENT-02-A",)
    reason = blocked_reason(broken, "AGENT-02-C")
    assert reason is not None
    assert reason["unsatisfiable_dependencies"] == ["AGENT-02-A"]


def test_a_duplicate_task_id_is_refused() -> None:
    with pytest.raises(TaskInvalidError):
        TaskGraph.build((make_task(task_id="AGENT-02-A"), make_task(task_id="AGENT-02-A")))


def test_an_unknown_dependency_is_refused() -> None:
    with pytest.raises(DependencyInvalidError):
        TaskGraph.build((make_task(task_id="AGENT-02-B", dependencies=("AGENT-02-Z",)),))


def test_a_self_dependency_is_refused() -> None:
    with pytest.raises(DependencyInvalidError):
        TaskGraph.build((make_task(task_id="AGENT-02-B", dependencies=("AGENT-02-B",)),))


def test_a_dependency_cycle_is_refused() -> None:
    left = make_task(task_id="AGENT-02-A", dependencies=("AGENT-02-B",))
    right = make_task(task_id="AGENT-02-B", dependencies=("AGENT-02-A",))
    with pytest.raises(DagCycleError) as raised:
        TaskGraph.build((left, right))
    assert raised.value.code == "agent.dag_cycle"
    assert set(raised.value.details["in_cycle"]) == {"AGENT-02-A", "AGENT-02-B"}


def test_the_topological_order_is_deterministic_and_dependency_respecting() -> None:
    alpha, beta, gamma = _chain()
    graph = TaskGraph.build((gamma, beta, alpha))
    order = graph.topological_order()
    assert order.index("AGENT-02-A") < order.index("AGENT-02-C")
    assert order == graph.topological_order()


def test_the_integration_order_puts_a_contract_freezing_task_first() -> None:
    producer = make_task(
        task_id="AGENT-02-Z",
        owner="sub-b",
        branch="agent/AGENT-02-Z-contract",
        allowed_paths=("runtime/canx/domain/**",),
    )
    consumer = make_task(
        task_id="AGENT-02-A",
        owner="sub-a",
        branch="agent/AGENT-02-A-consumer",
        allowed_paths=("runtime/canx/alpha/**",),
    )
    graph = TaskGraph.build((consumer, producer))
    assert graph.integration_order(config()) == ("AGENT-02-Z", "AGENT-02-A")


def test_require_refuses_an_unknown_task() -> None:
    graph = TaskGraph.build((make_task(task_id="AGENT-02-A"),))
    with pytest.raises(DependencyInvalidError):
        graph.require("AGENT-02-Z")
