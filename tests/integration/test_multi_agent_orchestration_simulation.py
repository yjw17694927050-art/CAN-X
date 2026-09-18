"""End-to-end multi-agent orchestration simulation (AGENT-01 §41, §74, §75).

The scenario the protocol was designed around:

```text
A  independent
B  independent, and it freezes a public-truth contract
C  depends on A
D  independent, but it races B on that same public-truth path
```

Nothing here spawns an agent. The simulation drives the protocol contracts
themselves, and it reports exactly that - "protocol and tooling verified in
simulation" - never "four agents ran".
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tools.agent.config import AgentConfig, load_config
from tools.agent.conflicts import ConflictLevel
from tools.agent.contracts import (
    HandoffContract,
    TaskContract,
    TestResult,
    load_handoff,
    load_task,
    load_task_plan,
)
from tools.agent.lifecycle import TaskStatus
from tools.agent.orchestration import OrchestrationPlan, plan
from tools.agent.validation import (
    IntegrationReadiness,
    evaluate_integration,
    ownership_violations,
    validate_task,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / ".agent"
EXAMPLE_DIR = AGENT_DIR / "examples"

BASE_SHA = "81f9c4131513c19d859fceac704d9b74449041f2"
OTHER_SHA = "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567"
A_HEAD = "5f3a1c9e4b2d7a6f8e0c1b2a3d4e5f60718293a4"
B_HEAD = "9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a291807"


def _config() -> AgentConfig:
    return load_config(AGENT_DIR / "config.json")


def _tasks() -> tuple[TaskContract, ...]:
    tasks = load_task_plan(EXAMPLE_DIR / "tasks.dependency.example.json")
    for task in tasks:
        validate_task(task, _config())
    return tasks


def _by_id(tasks: tuple[TaskContract, ...], task_id: str) -> TaskContract:
    return next(task for task in tasks if task.task_id == task_id)


def _handoff(
    task: TaskContract,
    *,
    head_sha: str,
    changed_files: tuple[str, ...],
    base_sha: str = BASE_SHA,
    ready: bool = True,
) -> HandoffContract:
    return HandoffContract(
        task_id=task.task_id,
        agent=task.owner,
        branch=task.branch,
        base_sha=base_sha,
        head_sha=head_sha,
        commits=(f"{head_sha[:7]} deliver {task.task_id}",),
        changed_files=changed_files,
        ownership_compliance=not ownership_violations(task, changed_files),
        tests=(TestResult(name="unit", command="python -m pytest -q", result="passed"),),
        dependencies=task.dependencies,
        ready_for_integration=ready,
    )


def _integrate(tasks: tuple[TaskContract, ...], task_id: str) -> tuple[TaskContract, ...]:
    return tuple(
        replace(task, status=TaskStatus.DONE) if task.task_id == task_id else task
        for task in tasks
    )


def _plan(tasks: tuple[TaskContract, ...]) -> OrchestrationPlan:
    return plan(tasks, _config())


def test_the_initial_plan_has_the_agreed_shape() -> None:
    result = _plan(_tasks())
    assert set(result.runnable_ids()) == {"AGENT-02-A", "AGENT-02-B"}
    assert result.blocked_ids() == ("AGENT-02-C",)
    assert result.deferred_ids() == ("AGENT-02-D",)


def test_only_the_public_truth_race_is_a_blocking_conflict() -> None:
    result = _plan(_tasks())
    blocking = result.blocking_conflicts()
    assert len(blocking) == 1
    assert {blocking[0].left, blocking[0].right} == {"AGENT-02-B", "AGENT-02-D"}
    assert blocking[0].level is ConflictLevel.C3
    assert "domain" in blocking[0].reason


def test_the_dependency_between_a_and_c_is_not_a_conflict() -> None:
    result = _plan(_tasks())
    pairs = {(conflict.left, conflict.right) for conflict in result.conflicts}
    a_c = ("AGENT-02-A", "AGENT-02-C")
    assert a_c in pairs
    conflict = next(c for c in result.conflicts if (c.left, c.right) == a_c)
    assert conflict.level is ConflictLevel.C0


def test_the_two_independent_tasks_may_run_in_parallel() -> None:
    result = _plan(_tasks())
    pairs = {(conflict.left, conflict.right) for conflict in result.conflicts}
    expected_pair = ("AGENT-02-A", "AGENT-02-B")
    assert expected_pair in pairs
    a_b = next(
        conflict
        for conflict in result.conflicts
        if (conflict.left, conflict.right) == expected_pair
    )
    assert a_b.auto_resolvable


def test_an_ownership_violating_handoff_never_reaches_integration() -> None:
    tasks = _tasks()
    alpha = _by_id(tasks, "AGENT-02-A")
    handoff = _handoff(alpha, head_sha=A_HEAD, changed_files=("SPEC.md",))

    readiness = evaluate_integration(handoff, alpha, _config(), BASE_SHA)

    assert not readiness.ready
    assert "agent.ownership_violation" in readiness.blockers
    assert handoff.ownership_compliance is False


def test_a_clean_handoff_on_the_current_head_is_integration_ready() -> None:
    tasks = _tasks()
    alpha = _by_id(tasks, "AGENT-02-A")
    handoff = _handoff(
        alpha,
        head_sha=A_HEAD,
        changed_files=("runtime/canx/alpha/source.py", "tests/unit/alpha/test_source.py"),
    )

    readiness = evaluate_integration(handoff, alpha, _config(), BASE_SHA)

    assert readiness.ready
    assert readiness.blockers == ()


def test_a_handoff_built_before_the_head_moved_is_refused() -> None:
    tasks = _tasks()
    beta = _by_id(tasks, "AGENT-02-B")
    handoff = _handoff(
        beta,
        head_sha=B_HEAD,
        changed_files=("runtime/canx/beta/source.py", "runtime/canx/domain/frame.py"),
    )

    assert evaluate_integration(handoff, beta, _config(), BASE_SHA).ready
    stale = evaluate_integration(handoff, beta, _config(), A_HEAD)
    assert not stale.ready
    assert "agent.base_stale" in stale.blockers


def test_integrating_a_makes_c_runnable() -> None:
    tasks = _tasks()
    assert "AGENT-02-C" in _plan(tasks).blocked_ids()

    integrated = _integrate(tasks, "AGENT-02-A")
    result = _plan(integrated)

    assert "AGENT-02-C" in result.runnable_ids()
    assert result.blocked_ids() == ()


def test_integrating_b_releases_the_deferred_task() -> None:
    tasks = _tasks()
    assert _plan(tasks).deferred_ids() == ("AGENT-02-D",)

    integrated = _integrate(tasks, "AGENT-02-B")
    result = _plan(integrated)

    # The two ownership surfaces still overlap, so the pair stays a C3 conflict -
    # that is precisely why integration is serial. What changes is that D is no
    # longer racing an in-flight producer, so nothing defers it any more.
    assert result.deferred_ids() == ()
    assert "AGENT-02-D" in result.runnable_ids()
    assert {conflict.left for conflict in result.blocking_conflicts()} == {"AGENT-02-B"}


def test_the_whole_sequence_lands_without_a_parallel_write_to_the_contract() -> None:
    config = _config()
    tasks = _tasks()
    head = BASE_SHA

    # A integrates first: it only ever touched its own surface.
    alpha = _by_id(tasks, "AGENT-02-A")
    alpha_handoff = _handoff(
        alpha, head_sha=A_HEAD, changed_files=("runtime/canx/alpha/source.py",)
    )
    assert evaluate_integration(alpha_handoff, alpha, config, head).ready
    tasks = _integrate(tasks, "AGENT-02-A")
    head = A_HEAD

    # B now integrates the frozen contract.
    beta = _by_id(tasks, "AGENT-02-B")
    beta_handoff = _handoff(beta, head_sha=B_HEAD, changed_files=("runtime/canx/domain/frame.py",))
    assert not evaluate_integration(beta_handoff, beta, config, head).ready, "stale base"
    rebased = _handoff(
        beta, head_sha=B_HEAD, base_sha=head, changed_files=("runtime/canx/domain/frame.py",)
    )
    assert evaluate_integration(rebased, beta, config, head).ready
    tasks = _integrate(tasks, "AGENT-02-B")
    head = B_HEAD

    # D may now consume the contract B froze; C is already free of A.
    result = _plan(tasks)
    assert set(result.runnable_ids()) == {"AGENT-02-C", "AGENT-02-D"}
    assert result.deferred_ids() == ()
    assert result.integration_order.index("AGENT-02-B") < result.integration_order.index(
        "AGENT-02-D"
    )


def test_the_simulation_is_deterministic() -> None:
    tasks = _tasks()
    assert _plan(tasks).to_dict() == _plan(tasks).to_dict()


def test_all_five_agent_identities_fit_the_protocol() -> None:
    config = _config()
    tasks = _tasks()
    assert {task.owner for task in tasks} == {"sub-a", "sub-b", "sub-c", "sub-d"}
    assert config.is_main_agent("main")
    assert not config.is_main_agent("sub-a")
    assert config.max_sub_agents == 4


def test_the_committed_examples_survive_the_same_gate_the_simulation_applies() -> None:
    """The shipped task/handoff pair must pass the gate, not just the parser."""
    task = load_task(EXAMPLE_DIR / "task.example.json")
    handoff = load_handoff(EXAMPLE_DIR / "handoff.example.json")
    readiness: IntegrationReadiness = evaluate_integration(handoff, task, _config(), BASE_SHA)
    assert readiness.ready
