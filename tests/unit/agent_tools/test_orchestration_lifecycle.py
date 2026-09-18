"""P0-1 and P0-2: persistent conflict leases and active-agent capacity.

A planning cycle must reason about the work already in flight, not only the
tasks that happen to be ``READY`` at this instant (AGENT-01-FIX-2 §4-§10,
§32-§33):

```text
execution capacity     who is currently consuming one of max_sub_agents?
conflict serialisation which earlier task still owns a shared surface?
```

The two are deliberately separate predicates, so these tests pin them apart.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from agent_tools_support import AGENT_DIR, config, contract_ref, make_task

from tools.agent.config import AgentConfig
from tools.agent.conflicts import ConflictLevel
from tools.agent.contracts import TaskContract
from tools.agent.lifecycle import (
    CONFLICT_LEASE_STATUSES,
    CONFLICT_REPLAN_STATUSES,
    EXECUTION_SLOT_STATUSES,
    TaskStatus,
    holds_conflict_lease,
    occupies_sub_agent_slot,
    requires_conflict_replan,
)
from tools.agent.orchestration import plan

DOMAIN = ("runtime/canx/domain/**",)
SAFETY = ("runtime/canx/safety/**",)
LETTERS = "abcdefghij"


def _config_dict() -> dict[str, object]:
    return dict(json.loads((AGENT_DIR / "config.json").read_text(encoding="utf-8")))


def _with_max(max_sub_agents: int) -> AgentConfig:
    return AgentConfig.from_dict({**_config_dict(), "max_sub_agents": max_sub_agents})


def _pair(
    left_status: TaskStatus,
    right_status: TaskStatus,
    *,
    paths: tuple[str, ...] = DOMAIN,
) -> tuple[TaskContract, TaskContract]:
    """Two tasks whose ownership surfaces overlap on ``paths``."""
    left = make_task(
        task_id="AGENT-02-B",
        owner="sub-b",
        branch="agent/AGENT-02-B-owner",
        allowed_paths=paths,
        status=left_status,
    )
    right = make_task(
        task_id="AGENT-02-D",
        owner="sub-d",
        branch="agent/AGENT-02-D-waiter",
        allowed_paths=paths,
        status=right_status,
    )
    return left, right


def _entry(result, task_id: str):
    return next(entry for entry in result.entries if entry.task_id == task_id)


def _slot_task(task_id: str, status: TaskStatus, letter: str) -> TaskContract:
    """An independent task whose ownership cannot overlap any sibling."""
    return make_task(
        task_id=task_id,
        owner=f"sub-{letter}",
        branch=f"agent/{task_id}-{letter}",
        allowed_paths=(f"runtime/canx/{letter}/**",),
        status=status,
    )


def _scenario(
    active: int, ready: int, max_sub_agents: int
) -> tuple[tuple[TaskContract, ...], AgentConfig]:
    tasks = [
        _slot_task(f"AGENT-02-A{index}", TaskStatus.IN_PROGRESS, LETTERS[index])
        for index in range(active)
    ]
    tasks += [
        _slot_task(f"AGENT-02-R{index}", TaskStatus.READY, LETTERS[active + index])
        for index in range(ready)
    ]
    return tuple(tasks), _with_max(max_sub_agents)


# --------------------------------------------------------- slot / lease predicates


def test_only_a_running_implementation_consumes_an_execution_slot() -> None:
    assert frozenset({TaskStatus.IN_PROGRESS}) == EXECUTION_SLOT_STATUSES
    assert occupies_sub_agent_slot(TaskStatus.IN_PROGRESS)
    for status in TaskStatus:
        if status is not TaskStatus.IN_PROGRESS:
            assert not occupies_sub_agent_slot(status), status


def test_the_conflict_lease_predicate_covers_every_unresolved_status() -> None:
    assert frozenset(
        {
            TaskStatus.PLANNED,
            TaskStatus.READY,
            TaskStatus.IN_PROGRESS,
            TaskStatus.HANDOFF_READY,
            TaskStatus.BLOCKED,
            TaskStatus.INTEGRATING,
        }
    ) == CONFLICT_LEASE_STATUSES
    for status in CONFLICT_LEASE_STATUSES:
        assert holds_conflict_lease(status), status
    for status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
        assert not holds_conflict_lease(status), status


def test_a_terminated_conflict_owner_is_not_a_release() -> None:
    assert frozenset({TaskStatus.FAILED, TaskStatus.CANCELLED}) == CONFLICT_REPLAN_STATUSES
    for status in CONFLICT_REPLAN_STATUSES:
        assert requires_conflict_replan(status), status
        assert not holds_conflict_lease(status), status


def test_the_two_policies_are_not_the_same_predicate() -> None:
    """Capacity and conflict serialisation must stay independent (FIX-2 §4)."""
    # A lease holder that consumes no slot: exactly the HANDOFF_READY case.
    assert holds_conflict_lease(TaskStatus.HANDOFF_READY)
    assert not occupies_sub_agent_slot(TaskStatus.HANDOFF_READY)
    assert holds_conflict_lease(TaskStatus.INTEGRATING)
    assert not occupies_sub_agent_slot(TaskStatus.INTEGRATING)
    # A slot consumer that need not hold a lease.
    assert occupies_sub_agent_slot(TaskStatus.IN_PROGRESS)


# --------------------------------------------------- P0-1 conflict lease lifecycle


def test_a_c3_conflict_is_classified_as_the_blocking_pair() -> None:
    left, right = _pair(TaskStatus.READY, TaskStatus.READY)
    result = plan((left, right), config())
    pair = next(
        conflict
        for conflict in result.conflicts
        if {conflict.left, conflict.right} == {"AGENT-02-B", "AGENT-02-D"}
    )
    assert pair.level is ConflictLevel.C3
    assert not pair.auto_resolvable


@pytest.mark.parametrize(
    "owner_status",
    [
        TaskStatus.READY,
        TaskStatus.IN_PROGRESS,
        TaskStatus.HANDOFF_READY,
        TaskStatus.INTEGRATING,
    ],
)
def test_the_conflict_lease_survives_a_status_transition(owner_status: TaskStatus) -> None:
    """RED -> GREEN: D used to become runnable the moment B left READY."""
    left, right = _pair(owner_status, TaskStatus.READY)
    result = plan((left, right), config())
    assert "AGENT-02-D" not in result.runnable_ids()
    entry = _entry(result, "AGENT-02-D")
    assert entry.deferral_kind() == "conflict"
    assert entry.deferred_by == ("AGENT-02-B",)


def test_a_blocked_owner_still_holds_the_lease() -> None:
    """A PLANNED / BLOCKED owner holds the lease too (FIX-2 §10).

    A dependency-gated owner is emitted after its gate in integration order, so
    the pair here is a C2 shared-contract pair on a non-public-truth surface,
    with the gate's id sorting ahead of the waiter's. The point under test is the
    lease, not the ordering trick: an owner that is ``BLOCKED`` is *not* a
    release.
    """
    gate = _slot_task("AGENT-02-A", TaskStatus.IN_PROGRESS, "a")
    owner = make_task(
        task_id="AGENT-02-B",
        owner="sub-b",
        branch="agent/AGENT-02-B-owner",
        allowed_paths=("runtime/canx/widgets/**",),
        shared_contracts=(contract_ref("runtime/canx/widgets/w.py"),),
        dependencies=("AGENT-02-A",),
        status=TaskStatus.READY,
    )
    waiter = make_task(
        task_id="AGENT-02-D",
        owner="sub-d",
        branch="agent/AGENT-02-D-waiter",
        allowed_paths=("runtime/canx/widgets/w.py",),
        status=TaskStatus.READY,
    )
    result = plan((gate, owner, waiter), config())
    pair = next(
        conflict
        for conflict in result.conflicts
        if {conflict.left, conflict.right} == {"AGENT-02-B", "AGENT-02-D"}
    )
    assert pair.level is ConflictLevel.C2
    assert _entry(result, "AGENT-02-B").status is TaskStatus.BLOCKED
    assert "AGENT-02-D" not in result.runnable_ids()
    assert _entry(result, "AGENT-02-D").deferral_kind() == "conflict"


def test_done_releases_the_lease() -> None:
    left, right = _pair(TaskStatus.DONE, TaskStatus.READY)
    result = plan((left, right), config())
    assert result.deferred_ids() == ()
    assert "AGENT-02-D" in result.runnable_ids()


@pytest.mark.parametrize("terminal", [TaskStatus.FAILED, TaskStatus.CANCELLED])
def test_a_terminated_owner_does_not_silently_release_the_later_task(
    terminal: TaskStatus,
) -> None:
    """BLOCKED/FAILED is not "the conflict is resolved" (FIX-2 §11-§12)."""
    left, right = _pair(terminal, TaskStatus.READY)
    result = plan((left, right), config())
    assert "AGENT-02-D" not in result.runnable_ids()
    entry = _entry(result, "AGENT-02-D")
    assert entry.deferral_kind() == "replan"
    assert entry.replan_required_by == ("AGENT-02-B",)
    assert result.replan_required_ids() == ("AGENT-02-D",)


def test_a_c4_conflict_remains_fail_closed() -> None:
    left, right = _pair(TaskStatus.IN_PROGRESS, TaskStatus.READY, paths=SAFETY)
    result = plan((left, right), config())
    pair = next(
        conflict
        for conflict in result.blocking_conflicts()
        if {conflict.left, conflict.right} == {"AGENT-02-B", "AGENT-02-D"}
    )
    assert pair.level is ConflictLevel.C4
    assert "AGENT-02-D" not in result.runnable_ids()


def test_replan_is_distinct_from_blocked_and_conflict_in_the_machine_output() -> None:
    gate = _slot_task("AGENT-02-Z", TaskStatus.IN_PROGRESS, "z")
    blocked = make_task(
        task_id="AGENT-02-C",
        owner="sub-c",
        branch="agent/AGENT-02-C-consumer",
        allowed_paths=("runtime/canx/gamma/**",),
        dependencies=("AGENT-02-Z",),
        status=TaskStatus.READY,
    )
    dead_owner, dead_waiter = _pair(TaskStatus.FAILED, TaskStatus.READY)
    result = plan((gate, blocked, dead_owner, dead_waiter), config())
    kinds = {entry.task_id: entry.deferral_kind() for entry in result.entries}
    assert kinds["AGENT-02-C"] == "dependency"
    assert kinds["AGENT-02-D"] == "replan"
    assert result.to_dict()["replan_required"] == ["AGENT-02-D"]


def test_plan_never_mutates_a_task_status() -> None:
    """plan() is a pure function; the Main Agent performs the transition (§42)."""
    left, right = _pair(TaskStatus.READY, TaskStatus.READY)
    before = (left.status, right.status)
    plan((left, right), config())
    assert (left.status, right.status) == before


# ------------------------------------------------------------- P0-2 capacity matrix


@pytest.mark.parametrize(
    ("active", "ready", "max_sub", "expected_runnable"),
    [
        (0, 5, 4, 4),
        (1, 5, 4, 3),
        (3, 4, 4, 1),
        (4, 4, 4, 0),
        (1, 3, 2, 1),
        (0, 4, 4, 4),
    ],
)
def test_the_capacity_matrix(
    active: int, ready: int, max_sub: int, expected_runnable: int
) -> None:
    """RED -> GREEN: 3 IN_PROGRESS + 4 READY used to yield 4 new runnable tasks."""
    tasks, cfg = _scenario(active, ready, max_sub)
    result = plan(tasks, cfg)
    assert len(result.active_ids) == active
    assert result.available_slots == max(0, max_sub - active)
    assert len(result.runnable_ids()) == expected_runnable
    assert len(result.capacity_deferred_ids()) == ready - expected_runnable


@pytest.mark.parametrize(
    ("active", "ready", "max_sub"),
    [(0, 5, 4), (1, 5, 4), (3, 4, 4), (4, 4, 4), (1, 3, 2), (2, 6, 4), (0, 7, 3)],
)
def test_active_plus_dispatched_never_exceeds_the_cap(
    active: int, ready: int, max_sub: int
) -> None:
    tasks, cfg = _scenario(active, ready, max_sub)
    result = plan(tasks, cfg)
    assert len(result.active_ids) + len(result.runnable_ids()) <= max_sub


def test_capacity_is_deterministic_with_active_slots() -> None:
    tasks, cfg = _scenario(3, 4, 4)
    first = plan(tasks, cfg).to_dict()
    for _ in range(5):
        assert plan(tasks, cfg).to_dict() == first


def test_a_freed_execution_slot_admits_one_capacity_deferred_task() -> None:
    tasks, cfg = _scenario(3, 4, 4)
    assert len(plan(tasks, cfg).runnable_ids()) == 1

    landed = tuple(
        replace(task, status=TaskStatus.DONE) if task.task_id == "AGENT-02-A0" else task
        for task in tasks
    )
    result = plan(landed, cfg)
    assert len(result.runnable_ids()) == 2
    assert len(result.active_ids) == 2


def test_the_capacity_block_names_the_occupants() -> None:
    tasks, cfg = _scenario(3, 4, 4)
    payload = plan(tasks, cfg).to_dict()
    assert payload["capacity"]["active"] == ["AGENT-02-A0", "AGENT-02-A1", "AGENT-02-A2"]
    assert payload["capacity"]["available_slots"] == 1
    assert len(payload["capacity"]["selected"]) == 1
