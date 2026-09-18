"""End-to-end multi-agent orchestration simulation (AGENT-01 §41, §74, §75; FIX-1 §30).

The scenario the protocol was designed around, plus the FIX-1 hardening:

```text
A  independent
B  independent, and it freezes a public-truth contract
C  depends on A
D  independent, but it races B on that same public-truth path (C3)
E, F, G  independent extras, to push past the parallelism cap
```

Nothing here spawns an agent. The simulation drives the protocol contracts
themselves, and it reports exactly that - "protocol and tooling verified in
simulation" - never "four agents ran".
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from agent_git_sandbox import SandboxRepository, git, make_repository

from tools.agent.config import AgentConfig, load_config
from tools.agent.conflicts import ConflictLevel
from tools.agent.contracts import (
    HandoffContract,
    RiskClass,
    TaskContract,
    TestResult,
    load_handoff,
    load_task,
    load_task_plan,
)
from tools.agent.evidence import SOURCE_WORKTREE, collect_repository_evidence
from tools.agent.lifecycle import TaskStatus
from tools.agent.orchestration import OrchestrationPlan, plan
from tools.agent.validation import (
    IntegrationContext,
    IntegrationReadiness,
    evaluate_integration,
    ownership_violations,
    validate_task,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / ".agent"
EXAMPLE_DIR = AGENT_DIR / "examples"

A_HEAD = "5f3a1c9e4b2d7a6f8e0c1b2a3d4e5f60718293a4"
B_HEAD = "9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a291807"
OTHER_SHA = "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567"


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
    base_sha: str | None = None,
    ready: bool = True,
) -> HandoffContract:
    base = task.base_sha if base_sha is None else base_sha
    return HandoffContract(
        task_id=task.task_id,
        agent=task.owner,
        branch=task.branch,
        base_sha=base,
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


def _context(tasks: tuple[TaskContract, ...], head: str) -> IntegrationContext:
    return IntegrationContext.build(tasks, _config(), head)


# --------------------------------------------------------------- the shipped plan


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


def test_the_dependency_between_a_and_c_is_not_a_conflict() -> None:
    result = _plan(_tasks())
    a_c = next(
        conflict
        for conflict in result.conflicts
        if {conflict.left, conflict.right} == {"AGENT-02-A", "AGENT-02-C"}
    )
    assert a_c.level is ConflictLevel.C0


def test_the_two_independent_tasks_may_run_in_parallel() -> None:
    result = _plan(_tasks())
    a_b = next(
        conflict
        for conflict in result.conflicts
        if {conflict.left, conflict.right} == {"AGENT-02-A", "AGENT-02-B"}
    )
    assert a_b.auto_resolvable


def test_integrating_a_makes_c_runnable() -> None:
    tasks = _tasks()
    assert "AGENT-02-C" in _plan(tasks).blocked_ids()

    result = _plan(_integrate(tasks, "AGENT-02-A"))

    assert "AGENT-02-C" in result.runnable_ids()
    assert result.blocked_ids() == ()


def test_integrating_b_releases_the_deferred_task() -> None:
    tasks = _tasks()
    assert _plan(tasks).deferred_ids() == ("AGENT-02-D",)

    result = _plan(_integrate(tasks, "AGENT-02-B"))

    # The two ownership surfaces still overlap, so the pair stays a C3 conflict -
    # that is precisely why integration is serial. What changes is that D is no
    # longer racing an in-flight producer, so nothing defers it any more.
    assert result.deferred_ids() == ()
    assert "AGENT-02-D" in result.runnable_ids()
    assert {conflict.left for conflict in result.blocking_conflicts()} == {"AGENT-02-B"}


# ------------------------------------------------------- widened plan (FIX-1 §30)


def _widened() -> tuple[TaskContract, ...]:
    """A, B, C(dep A), D(races B), E, F, G - all owned legitimately."""
    tasks = list(_tasks())
    for letter in ("E", "F", "G"):
        tasks.append(
            TaskContract(
                task_id=f"AGENT-02-{letter}",
                title=f"independent {letter}",
                objective=f"{letter} is independent",
                scope=f"runtime/canx/{letter.lower()}/**",
                non_goals=(),
                base_sha=_by_id(_tasks(), "AGENT-02-A").base_sha,
                branch=f"agent/AGENT-02-{letter}-{letter.lower()}",
                worktree=f".worktrees/agent-02-{letter.lower()}",
                owner=f"sub-{letter.lower()}",
                allowed_paths=(f"runtime/canx/{letter.lower()}/**",),
                forbidden_paths=(),
                dependencies=(),
                shared_contracts=(),
                acceptance_criteria=("it works",),
                required_tests=("unit",),
                handoff_requirements=("report the run",),
                risk_class=RiskClass.LOW,
                status=TaskStatus.PLANNED,
                revision=1,
            )
        )
    for task in tasks:
        validate_task(task, _config())
    return tuple(tasks)


def test_the_initial_wave_never_exceeds_the_parallelism_cap() -> None:
    result = _plan(_widened())
    assert len(result.runnable_ids()) == _config().max_sub_agents
    assert result.capacity_deferred_ids(), "an extra independent task must be held back"


def test_the_cap_holds_repeatedly() -> None:
    for _ in range(5):
        assert len(_plan(_widened()).runnable_ids()) <= _config().max_sub_agents


def test_a_capacity_deferred_task_is_not_reported_as_blocked_or_conflicted() -> None:
    result = _plan(_widened())
    kinds = {task_id: "deferred" for task_id in result.capacity_deferred_ids()}
    assert kinds, "the widened plan must exercise capacity"
    for entry in result.entries:
        if entry.task_id in kinds:
            assert entry.deferral_kind() == "capacity"


def test_c_cannot_reach_integration_before_a_is_done() -> None:
    tasks = _widened()
    task_c = _by_id(tasks, "AGENT-02-C").__class__(
        **{**_by_id(tasks, "AGENT-02-C").to_dict(), "status": TaskStatus.HANDOFF_READY}
    )
    tasks = tuple(task_c if item.task_id == "AGENT-02-C" else item for item in tasks)
    handoff = _handoff(task_c, head_sha=A_HEAD, changed_files=("runtime/canx/gamma/x.py",))

    readiness = evaluate_integration(
        handoff,
        task_c,
        _config(),
        _context(tasks, task_c.base_sha),
        evidence=_synthetic_evidence(task_c, handoff, ("runtime/canx/gamma/x.py",)),
    )

    assert not readiness.ready
    assert "agent.dependency_blocked" in readiness.blockers


def test_d_cannot_reach_integration_while_deferred_by_b() -> None:
    tasks = _widened()
    task_d = _by_id(tasks, "AGENT-02-D")
    task_d = replace(task_d, status=TaskStatus.HANDOFF_READY)
    tasks = tuple(task_d if item.task_id == "AGENT-02-D" else item for item in tasks)
    handoff = _handoff(
        task_d, head_sha=A_HEAD, changed_files=("runtime/canx/domain/frame.py",)
    )

    readiness = evaluate_integration(
        handoff,
        task_d,
        _config(),
        _context(tasks, task_d.base_sha),
        evidence=_synthetic_evidence(task_d, handoff, ("runtime/canx/domain/frame.py",)),
    )

    assert not readiness.ready
    assert "agent.conflict_rejected" in readiness.blockers


def test_d_becomes_eligible_once_b_is_done() -> None:
    tasks = _integrate(_widened(), "AGENT-02-B")
    task_d = replace(_by_id(tasks, "AGENT-02-D"), status=TaskStatus.HANDOFF_READY)
    tasks = tuple(task_d if item.task_id == "AGENT-02-D" else item for item in tasks)
    handoff = _handoff(
        task_d, head_sha=A_HEAD, changed_files=("runtime/canx/domain/frame.py",)
    )

    readiness = evaluate_integration(
        handoff,
        task_d,
        _config(),
        _context(tasks, task_d.base_sha),
        evidence=_synthetic_evidence(task_d, handoff, ("runtime/canx/domain/frame.py",)),
    )

    assert "agent.conflict_rejected" not in readiness.blockers


def test_a_moved_integration_head_makes_the_old_handoff_stale() -> None:
    tasks = _widened()
    task_a = replace(_by_id(tasks, "AGENT-02-A"), status=TaskStatus.HANDOFF_READY)
    tasks = tuple(task_a if item.task_id == "AGENT-02-A" else item for item in tasks)
    handoff = _handoff(task_a, head_sha=A_HEAD, changed_files=("runtime/canx/alpha/x.py",))
    evidence = _synthetic_evidence(task_a, handoff, ("runtime/canx/alpha/x.py",))

    fresh = evaluate_integration(
        handoff, task_a, _config(), _context(tasks, task_a.base_sha), evidence=evidence
    )
    stale = evaluate_integration(
        handoff, task_a, _config(), _context(tasks, OTHER_SHA), evidence=evidence
    )

    assert fresh.ready, fresh.details
    assert not stale.ready
    assert "agent.base_stale" in stale.blockers


def test_capacity_and_conflict_and_dependency_are_three_distinct_kinds() -> None:
    result = _plan(_widened())
    kinds = {entry.task_id: entry.deferral_kind() for entry in result.entries}
    assert kinds["AGENT-02-C"] == "dependency"
    assert kinds["AGENT-02-D"] == "conflict"
    assert "capacity" in set(kinds.values())


# ------------------------------------------------------------------- git-backed


def _synthetic_evidence(task: TaskContract, handoff: HandoffContract, changed: tuple[str, ...]):
    from tools.agent.evidence import RepositoryEvidence

    return RepositoryEvidence(
        repository_root=Path("."),
        source=SOURCE_WORKTREE,
        worktree_path=Path("."),
        branch=task.branch,
        base_sha=task.base_sha,
        head_sha=handoff.head_sha,
        base_is_ancestor=True,
        clean=True,
        net_changed_paths=changed,
        history_touched_paths=changed,
        commits=(handoff.commits[0].split()[0] + "0" * 33,),
        diff_records=(("M", changed),),
    )


@pytest.fixture
def delivered_repo(tmp_path: Path):
    """A real branch whose real diff contains a path the handoff will not report."""
    repo = make_repository(tmp_path)
    base = repo.sha()
    repo.branch("agent/AGENT-02-A-alpha-source")
    repo.write("runtime/canx/alpha/source.py", "alpha = 1\n")
    repo.write("SPEC.md", "MUTATED\n")
    head = repo.commit("deliver alpha, and quietly touch SPEC.md")
    task = TaskContract(
        task_id="AGENT-02-A",
        title="alpha",
        objective="alpha exists",
        scope="runtime/canx/alpha/**",
        non_goals=(),
        base_sha=base,
        branch="agent/AGENT-02-A-alpha-source",
        worktree=".",
        owner="sub-a",
        allowed_paths=("runtime/canx/alpha/**",),
        forbidden_paths=(),
        dependencies=(),
        shared_contracts=(),
        acceptance_criteria=("it works",),
        required_tests=("unit",),
        handoff_requirements=("report the run",),
        risk_class=RiskClass.LOW,
        status=TaskStatus.HANDOFF_READY,
        revision=1,
    )
    return repo, task, base, head


def test_a_real_git_diff_overrides_a_handoff_that_hides_a_path(delivered_repo) -> None:
    repo, task, base, head = delivered_repo
    lying = _handoff(
        task, head_sha=head, changed_files=("runtime/canx/alpha/source.py",)
    )

    readiness = evaluate_integration(
        lying, task, _config(), _context((task,), base), repository=repo.root
    )

    assert not readiness.ready
    assert "agent.ownership_violation" in readiness.blockers
    assert "agent.handoff_evidence_mismatch" in readiness.blockers
    assert readiness.evidence is not None
    assert "SPEC.md" in readiness.evidence.history_touched_paths


def test_the_same_delivery_without_the_extra_path_is_ready(tmp_path: Path) -> None:
    """A delivery that really only touched its own surface is integration-ready."""
    repo = make_repository(tmp_path)
    base = repo.sha()
    repo.branch("agent/AGENT-02-A-alpha-source")
    repo.write("runtime/canx/alpha/source.py", "alpha = 1\n")
    head = repo.commit("deliver alpha only")
    task = replace(_by_id(_tasks(), "AGENT-02-A"), worktree=".", base_sha=base,
                   status=TaskStatus.HANDOFF_READY)

    evidence = collect_repository_evidence(repo.root, task)
    honest = _handoff(task, head_sha=head, changed_files=tuple(evidence.history_touched_paths))

    readiness = evaluate_integration(
        honest, task, _config(), _context((task,), base), repository=repo.root
    )

    assert readiness.ready, readiness.details
    assert evidence.history_touched_paths == ("runtime/canx/alpha/source.py",)


def _git_oneline(repo: SandboxRepository, base: str, head: str) -> tuple[str, ...]:
    output = git(repo.root, ["log", "--format=%h %s", f"{base}..{head}"])
    return tuple(line for line in output.splitlines() if line.strip())


def test_the_committed_examples_survive_the_same_gate_the_simulation_applies() -> None:
    """The shipped task/handoff pair must pass the contract validators."""
    task = load_task(EXAMPLE_DIR / "task.example.json")
    handoff = load_handoff(EXAMPLE_DIR / "handoff.example.json")
    from tools.agent.validation import validate_handoff

    validate_task(task, _config())
    validate_handoff(handoff, task, _config())


def test_the_simulation_is_deterministic() -> None:
    tasks = _tasks()
    assert _plan(tasks).to_dict() == _plan(tasks).to_dict()


def test_all_four_sub_agent_identities_fit_the_protocol() -> None:
    config = _config()
    tasks = _tasks()
    assert {task.owner for task in tasks} == {"sub-a", "sub-b", "sub-c", "sub-d"}
    assert config.is_main_agent("main")
    assert not config.is_main_agent("sub-a")
    assert config.max_sub_agents == 4


def test_the_verdict_never_claims_to_have_checked_the_github_gate() -> None:
    tasks = _widened()
    task_a = replace(_by_id(tasks, "AGENT-02-A"), status=TaskStatus.HANDOFF_READY)
    tasks = tuple(task_a if item.task_id == "AGENT-02-A" else item for item in tasks)
    handoff = _handoff(task_a, head_sha=A_HEAD, changed_files=("runtime/canx/alpha/x.py",))
    readiness: IntegrationReadiness = evaluate_integration(
        handoff,
        task_a,
        _config(),
        _context(tasks, task_a.base_sha),
        evidence=_synthetic_evidence(task_a, handoff, ("runtime/canx/alpha/x.py",)),
    )
    payload = readiness.to_dict()
    assert payload["github_gate"]["checked_here"] is False
    assert "Quality Gate" in payload["github_gate"]["note"]


# ------------------------------------------- the multi-cycle serial-integration life


def _set_status(
    tasks: tuple[TaskContract, ...], task_id: str, status: TaskStatus
) -> tuple[TaskContract, ...]:
    return tuple(
        replace(task, status=status) if task.task_id == task_id else task for task in tasks
    )


def _entry(result: OrchestrationPlan, task_id: str):
    return next(entry for entry in result.entries if entry.task_id == task_id)


def test_the_serial_integration_lifecycle_survives_every_status_transition() -> None:
    """A multi-cycle life, not a snapshot (AGENT-01-FIX-2 §36).

    ``B`` owns a C3 public-truth surface; ``D`` races it. ``B`` moving through
    READY -> IN_PROGRESS -> HANDOFF_READY -> INTEGRATING -> DONE must keep ``D``
    deferred until ``B`` actually lands - and release it only then, with the
    moved integration head making ``D``'s old handoff stale.
    """
    config = _config()
    tasks = _widened()
    peak = 0

    def observe(state: tuple[TaskContract, ...]) -> OrchestrationPlan:
        nonlocal peak
        result = _plan(state)
        peak = max(peak, len(result.active_ids) + len(result.runnable_ids()))
        return result

    # Cycle 1: B and D are both READY. B holds the surface, D waits.
    cycle1 = observe(tasks)
    assert "AGENT-02-B" in cycle1.runnable_ids()
    assert "AGENT-02-D" in cycle1.deferred_ids()
    assert "AGENT-02-D" not in cycle1.runnable_ids()

    # Dispatch B plus three independents: the four slots are genuinely full.
    state = tasks
    for task_id in ("AGENT-02-B", "AGENT-02-A", "AGENT-02-E", "AGENT-02-F"):
        state = _set_status(state, task_id, TaskStatus.IN_PROGRESS)

    # Cycle 2: B is no longer READY, but D must stay deferred, and no slot leaks.
    cycle2 = observe(state)
    assert cycle2.active_ids == (
        "AGENT-02-B",
        "AGENT-02-A",
        "AGENT-02-E",
        "AGENT-02-F",
    )
    assert cycle2.available_slots == 0
    assert cycle2.runnable_ids() == ()
    assert "AGENT-02-D" in cycle2.deferred_ids()

    # B hands off: it stops consuming a slot but keeps the lease.
    state = _set_status(state, "AGENT-02-B", TaskStatus.HANDOFF_READY)
    cycle3 = observe(state)
    assert cycle3.available_slots == 1
    assert "AGENT-02-D" not in cycle3.runnable_ids()
    assert "AGENT-02-D" in cycle3.deferred_ids()
    assert "AGENT-02-B" not in cycle3.active_ids

    # B integrates: still no release.
    state = _set_status(state, "AGENT-02-B", TaskStatus.INTEGRATING)
    cycle4 = observe(state)
    assert "AGENT-02-D" not in cycle4.runnable_ids()
    assert "AGENT-02-D" in cycle4.deferred_ids()

    # B lands. The surface is released - and only now.
    state = _set_status(state, "AGENT-02-B", TaskStatus.DONE)
    cycle5 = observe(state)
    assert "AGENT-02-D" not in cycle5.deferred_ids()
    assert "AGENT-02-D" in cycle5.runnable_ids()

    # ...but the integration head moved with B, so D's old delivery is stale.
    stale_head = "1234567890abcdef1234567890abcdef12345678"
    task_d = replace(_by_id(state, "AGENT-02-D"), status=TaskStatus.HANDOFF_READY)
    state_with_d = tuple(
        task_d if task.task_id == "AGENT-02-D" else task for task in state
    )
    handoff = _handoff(
        task_d, head_sha=A_HEAD, changed_files=("runtime/canx/domain/frame.py",)
    )
    readiness = evaluate_integration(
        handoff,
        task_d,
        config,
        _context(state_with_d, stale_head),
        evidence=_synthetic_evidence(task_d, handoff, ("runtime/canx/domain/frame.py",)),
    )
    assert not readiness.ready
    assert "agent.base_stale" in readiness.blockers

    assert peak <= config.max_sub_agents, "a conflict leak would have exceeded the cap"
