"""P1-2 and P1-3: the parallelism cap, and the gates the local verdict must prove.

The plan claims "1 Main Agent + up to 4 Sub-Agents"; the cap has to be enforced by
``plan()``, not merely validated in the config. And the local integration verdict
has to be able to say *no* for dependency, conflict and status reasons - while
never pretending to check the GitHub Quality Gate.
"""

from __future__ import annotations

from pathlib import Path

from agent_tools_support import BASE_SHA, config, make_handoff, make_task

from tools.agent.config import AgentConfig
from tools.agent.contracts import TaskContract
from tools.agent.evidence import SOURCE_WORKTREE, RepositoryEvidence
from tools.agent.lifecycle import TaskStatus
from tools.agent.orchestration import plan
from tools.agent.validation import IntegrationContext, evaluate_integration

#: Full commit id the hand-built evidence will vouch for.
FULL_SHA = "abcdef0123456789abcdef0123456789abcdef01"
PREFIX = FULL_SHA[:7]


def evidence_for(task: TaskContract, handoff, changed: tuple[str, ...]) -> RepositoryEvidence:
    """Consistent Git-backed evidence without touching a repository."""
    return RepositoryEvidence(
        repository_root=Path("."),
        source=SOURCE_WORKTREE,
        worktree_path=Path(".worktrees/agent-02-b"),
        branch=task.branch,
        base_sha=task.base_sha,
        head_sha=handoff.head_sha,
        base_is_ancestor=True,
        clean=True,
        net_changed_paths=changed,
        history_touched_paths=changed,
        commits=(FULL_SHA,),
        diff_records=(("M", changed),),
    )


def _independent(task_id: str, letter: str) -> TaskContract:
    return make_task(
        task_id=task_id,
        owner=f"sub-{letter.lower()}",
        branch=f"agent/{task_id}-{letter.lower()}",
        allowed_paths=(f"runtime/canx/{letter.lower()}/**",),
        status=TaskStatus.READY,
    )


def _five() -> tuple[TaskContract, ...]:
    return tuple(_independent(f"AGENT-02-{letter}", letter) for letter in "ABCDE")


# ------------------------------------------------------------------ P1-2 capacity


def test_five_independent_tasks_cannot_all_be_runnable() -> None:
    """RED -> GREEN: five ready tasks used to come back as five runnable ids."""
    result = plan(_five(), config())
    assert len(result.runnable_ids()) == 4
    assert result.capacity_deferred_ids() == ("AGENT-02-E",)


def test_the_capacity_cap_follows_the_configured_maximum() -> None:
    smaller = AgentConfig.from_dict(
        {
            **config_dict(),
            "max_sub_agents": 2,
        }
    )
    result = plan(_five(), smaller)
    assert result.runnable_ids() == ("AGENT-02-A", "AGENT-02-B")
    assert result.capacity_deferred_ids() == ("AGENT-02-C", "AGENT-02-D", "AGENT-02-E")


def test_the_selected_wave_is_deterministic() -> None:
    first = plan(_five(), config()).runnable_ids()
    for _ in range(5):
        assert plan(_five(), config()).runnable_ids() == first


def test_capacity_deferral_is_reported_as_its_own_kind() -> None:
    result = plan(_five(), config())
    kinds = {entry.task_id: entry.deferral_kind() for entry in result.entries}
    assert kinds["AGENT-02-E"] == "capacity"
    assert kinds["AGENT-02-A"] == "none"
    payload = result.to_dict()
    assert payload["capacity"] == {
        "max_sub_agents": 4,
        "active": [],
        "active_over_capacity": 0,
        "available_slots": 4,
        "selected": ["AGENT-02-A", "AGENT-02-B", "AGENT-02-C", "AGENT-02-D"],
        "capacity_deferred": ["AGENT-02-E"],
    }


def test_a_capacity_deferred_task_moves_into_the_wave_when_one_lands() -> None:
    tasks = list(_five())
    tasks[3] = make_task(
        task_id="AGENT-02-D",
        owner="sub-d",
        branch="agent/AGENT-02-D-d",
        allowed_paths=("runtime/canx/d/**",),
        status=TaskStatus.DONE,
    )
    result = plan(tuple(tasks), config())
    assert "AGENT-02-E" in result.runnable_ids()
    assert result.capacity_deferred_ids() == ()


def test_blocked_and_conflicting_tasks_are_not_capacity_deferred() -> None:
    alpha = _independent("AGENT-02-A", "A")
    dependent = make_task(
        task_id="AGENT-02-C",
        owner="sub-c",
        branch="agent/AGENT-02-C-c",
        allowed_paths=("runtime/canx/c/**",),
        dependencies=("AGENT-02-A",),
        status=TaskStatus.READY,
    )
    result = plan((alpha, dependent), config())
    kinds = {entry.task_id: entry.deferral_kind() for entry in result.entries}
    assert kinds["AGENT-02-C"] == "dependency"


# ------------------------------------------------------- P1-3 local gate responsibility


def _context(tasks: tuple[TaskContract, ...]) -> IntegrationContext:
    return IntegrationContext.build(tasks, config(), BASE_SHA)


def _handoff_for(task: TaskContract, changed: tuple[str, ...]):
    return make_handoff(
        task_id=task.task_id,
        branch=task.branch,
        base_sha=task.base_sha,
        head_sha=FULL_SHA,
        commits=(f"{PREFIX} the commit",),
        changed_files=changed,
        dependencies=task.dependencies,
    )


def _ready_task(**overrides: object) -> TaskContract:
    values: dict[str, object] = {
        "status": TaskStatus.HANDOFF_READY,
        "allowed_paths": ("runtime/canx/foo/**",),
    }
    values.update(overrides)
    return make_task(**values)


def test_a_clean_handoff_with_full_evidence_is_locally_ready() -> None:
    task = _ready_task()
    handoff = _handoff_for(task, ("runtime/canx/foo/a.py",))
    readiness = evaluate_integration(
        handoff, task, config(), _context((task,)),
        evidence=evidence_for(task, handoff, ("runtime/canx/foo/a.py",)),
    )
    assert readiness.ready, readiness.details
    assert readiness.to_dict()["github_gate"]["checked_here"] is False


def test_without_a_repository_the_gate_refuses_to_claim_ready() -> None:
    task = _ready_task()
    handoff = _handoff_for(task, ("runtime/canx/foo/a.py",))
    readiness = evaluate_integration(handoff, task, config(), _context((task,)))
    assert not readiness.ready
    assert "agent.integration_context_incomplete" in readiness.blockers


def test_without_the_task_set_the_gate_refuses_to_claim_ready() -> None:
    task = _ready_task()
    handoff = _handoff_for(task, ("runtime/canx/foo/a.py",))
    context = IntegrationContext(integration_head_sha=BASE_SHA)
    readiness = evaluate_integration(
        handoff, task, config(), context,
        evidence=evidence_for(task, handoff, ("runtime/canx/foo/a.py",)),
    )
    assert not readiness.ready
    assert "agent.integration_context_incomplete" in readiness.blockers


def test_a_dependent_task_cannot_integrate_before_its_dependency_is_done() -> None:
    """RED -> GREEN: this used to come back ready while the dependency was READY."""
    alpha = _independent("AGENT-02-A", "A")
    dependent = _ready_task(
        task_id="AGENT-02-C",
        owner="sub-c",
        branch="agent/AGENT-02-C-c",
        dependencies=("AGENT-02-A",),
    )
    handoff = _handoff_for(dependent, ("runtime/canx/foo/a.py",))
    readiness = evaluate_integration(
        handoff, dependent, config(), _context((alpha, dependent)),
        evidence=evidence_for(dependent, handoff, ("runtime/canx/foo/a.py",)),
    )
    assert not readiness.ready
    assert "agent.dependency_blocked" in readiness.blockers


def test_a_dependent_task_becomes_eligible_once_the_dependency_is_done() -> None:
    alpha = make_task(
        task_id="AGENT-02-A",
        owner="sub-a",
        branch="agent/AGENT-02-A-a",
        allowed_paths=("runtime/canx/a/**",),
        status=TaskStatus.DONE,
    )
    dependent = _ready_task(
        task_id="AGENT-02-C",
        owner="sub-c",
        branch="agent/AGENT-02-C-c",
        dependencies=("AGENT-02-A",),
    )
    handoff = _handoff_for(dependent, ("runtime/canx/foo/a.py",))
    readiness = evaluate_integration(
        handoff, dependent, config(), _context((alpha, dependent)),
        evidence=evidence_for(dependent, handoff, ("runtime/canx/foo/a.py",)),
    )
    assert "agent.dependency_blocked" not in readiness.blockers


def test_an_unsatisfiable_dependency_says_replan() -> None:
    alpha = make_task(
        task_id="AGENT-02-A",
        owner="sub-a",
        branch="agent/AGENT-02-A-a",
        allowed_paths=("runtime/canx/a/**",),
        status=TaskStatus.FAILED,
    )
    dependent = _ready_task(
        task_id="AGENT-02-C",
        owner="sub-c",
        branch="agent/AGENT-02-C-c",
        dependencies=("AGENT-02-A",),
    )
    handoff = _handoff_for(dependent, ("runtime/canx/foo/a.py",))
    readiness = evaluate_integration(
        handoff, dependent, config(), _context((alpha, dependent)),
        evidence=evidence_for(dependent, handoff, ("runtime/canx/foo/a.py",)),
    )
    blocked = next(item for item in readiness.details if item["code"] == "agent.dependency_blocked")
    assert blocked["details"]["next"] == "re-plan the DAG"


def test_a_task_deferred_by_a_blocking_conflict_cannot_integrate() -> None:
    """RED -> GREEN: D used to come back ready while it was deferred behind B."""
    beta = make_task(
        task_id="AGENT-02-B",
        owner="sub-b",
        branch="agent/AGENT-02-B-b",
        allowed_paths=("runtime/canx/domain/**",),
        status=TaskStatus.READY,
    )
    delta = _ready_task(
        task_id="AGENT-02-D",
        owner="sub-d",
        branch="agent/AGENT-02-D-d",
        allowed_paths=("runtime/canx/domain/**",),
    )
    handoff = _handoff_for(delta, ("runtime/canx/domain/frame.py",))
    readiness = evaluate_integration(
        handoff, delta, config(), _context((beta, delta)),
        evidence=evidence_for(delta, handoff, ("runtime/canx/domain/frame.py",)),
    )
    assert not readiness.ready
    assert "agent.conflict_rejected" in readiness.blockers


def test_the_conflict_is_released_once_the_producer_lands() -> None:
    beta = make_task(
        task_id="AGENT-02-B",
        owner="sub-b",
        branch="agent/AGENT-02-B-b",
        allowed_paths=("runtime/canx/domain/**",),
        status=TaskStatus.DONE,
    )
    delta = _ready_task(
        task_id="AGENT-02-D",
        owner="sub-d",
        branch="agent/AGENT-02-D-d",
        allowed_paths=("runtime/canx/domain/**",),
    )
    handoff = _handoff_for(delta, ("runtime/canx/domain/frame.py",))
    readiness = evaluate_integration(
        handoff, delta, config(), _context((beta, delta)),
        evidence=evidence_for(delta, handoff, ("runtime/canx/domain/frame.py",)),
    )
    assert "agent.conflict_rejected" not in readiness.blockers


def test_a_task_that_is_not_handoff_ready_cannot_integrate() -> None:
    task = _ready_task(status=TaskStatus.IN_PROGRESS)
    handoff = _handoff_for(task, ("runtime/canx/foo/a.py",))
    readiness = evaluate_integration(
        handoff, task, config(), _context((task,)),
        evidence=evidence_for(task, handoff, ("runtime/canx/foo/a.py",)),
    )
    assert not readiness.ready
    assert "agent.task_invalid" in readiness.blockers


def test_the_task_being_integrated_must_be_in_the_supplied_task_set() -> None:
    task = _ready_task()
    other = _independent("AGENT-02-A", "A")
    handoff = _handoff_for(task, ("runtime/canx/foo/a.py",))
    readiness = evaluate_integration(
        handoff, task, config(), _context((other,)),
        evidence=evidence_for(task, handoff, ("runtime/canx/foo/a.py",)),
    )
    assert "agent.integration_context_incomplete" in readiness.blockers


def config_dict() -> dict[str, object]:
    """The shipped config as a plain dict, for deriving variants."""
    import json

    path = Path(__file__).resolve().parents[3] / ".agent" / "config.json"
    return dict(json.loads(path.read_text(encoding="utf-8")))
