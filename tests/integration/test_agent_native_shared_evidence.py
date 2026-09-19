"""Git-backed evidence for a native shared-worktree worker (V0.3-12-FIX-1).

The unit suite proves the *contract* shape. This module proves the **gate** still
holds when the worker executed inside the Main Agent's single worktree, which is
what the host Harness actually does by default: no task branch, no task worktree,
and a delivery that is a real commit range on the integration branch.

Every guarantee AGENT-01 established is re-proved here for that mode, on real
throwaway repositories:

```text
a worker may not claim another task's paths          -> owned surface + Git history
a worker may not reach a protected/public-truth /
  safety path                                        -> owned surface + Git history
Git history remains the authority                    -> history_touched_paths, not the net diff
a stale base still fails closed                      -> agent.base_stale
a self-reported "compliant / ready" handoff does not
  obtain integration readiness                       -> the report is untrusted
the Main Agent owns integration                      -> no caller may supply evidence
```

Like the other AGENT-01 integration tests, this module builds its own config and
its own contract factories rather than importing the unit suite's helpers, so a
focused run of this file is self-contained. The real CAN-X repository is never
mutated: every case builds its own repository under ``tmp_path``.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from agent_git_sandbox import SandboxRepository, git, make_repository

from tools.agent.config import AgentConfig, load_config
from tools.agent.contracts import (
    ExecutionMode,
    HandoffContract,
    RiskClass,
    TaskContract,
    TestResult,
)
from tools.agent.errors import GitStateError
from tools.agent.evidence import collect_repository_evidence
from tools.agent.lifecycle import TaskStatus
from tools.agent.validation import IntegrationContext, evaluate_integration

REPO_ROOT = Path(__file__).resolve().parents[2]
INTEGRATION_BRANCH = "feature/v0.3-12"
OWNED_PATH = "runtime/canx/foo/a.py"
OTHER_PATH = "runtime/canx/bar/b.py"
INTEGRATION_TEST_PATH = "apps/desktop/src/orchestration/project-open.integration.test.ts"


def config() -> AgentConfig:
    """The repository's own ``.agent/config.json``, parsed."""
    return load_config(REPO_ROOT / ".agent" / "config.json")


def unrelated_root_commit(repo: SandboxRepository) -> str:
    """A real commit that shares no history with the branch checked out.

    Built with plumbing so the working tree is never touched.
    """
    tree = git(repo.root, ["rev-parse", "HEAD^{tree}"])
    return git(repo.root, ["commit-tree", tree, "-m", "unrelated root"])


def native_shared_task(repo: SandboxRepository, base_sha: str, **overrides: object) -> TaskContract:
    values: dict[str, object] = {
        "task_id": "V0.3-12-A",
        "title": "native shared delivery",
        "objective": "prove the gate reads Git for a shared-worktree worker",
        "scope": "runtime/canx/foo/**",
        "non_goals": (),
        "base_sha": base_sha,
        "branch": None,
        "worktree": None,
        "owner": "sub-a",
        "allowed_paths": ("runtime/canx/foo/**",),
        "forbidden_paths": (),
        "dependencies": (),
        "shared_contracts": (),
        "acceptance_criteria": ("the gate reads Git",),
        "required_tests": ("unit",),
        "handoff_requirements": ("report the run",),
        "risk_class": RiskClass.LOW,
        "status": TaskStatus.HANDOFF_READY,
        "revision": 1,
        "execution_mode": ExecutionMode.NATIVE_SHARED,
    }
    values.update(overrides)
    return TaskContract(**values)


def handoff_for(
    repo: SandboxRepository,
    base_sha: str,
    head_sha: str,
    changed_files: tuple[str, ...],
    **overrides: object,
) -> HandoffContract:
    values: dict[str, object] = {
        "task_id": "V0.3-12-A",
        "agent": "sub-a",
        "branch": INTEGRATION_BRANCH,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "changed_files": changed_files,
        "ownership_compliance": True,
        "ready_for_integration": True,
        "tests": (TestResult(name="unit", command="pytest -q", result="passed"),),
        "dependencies": (),
    }
    if "commits" not in overrides:
        values["commits"] = repo.short_commits(base_sha, head_sha)
    values.update(overrides)
    return HandoffContract(**values)


def integration_case(
    tmp_path: Path,
) -> tuple[SandboxRepository, str, str, TaskContract, HandoffContract]:
    """A real native-shared delivery: one owned commit on the integration branch."""

    repo = make_repository(tmp_path)
    base_sha = repo.sha()
    repo.branch(INTEGRATION_BRANCH)
    repo.write(OWNED_PATH, "a = 2\n")
    head_sha = repo.commit("feat: owned work")

    task = native_shared_task(repo, base_sha)
    handoff = handoff_for(repo, base_sha, head_sha, (OWNED_PATH,))
    return repo, base_sha, head_sha, task, handoff


def verdict(repo: SandboxRepository, task: TaskContract, handoff: HandoffContract, head: str):
    context = IntegrationContext.build((task,), config(), head)
    return evaluate_integration(handoff, task, config(), context, repository=repo.root)


# ---------------------------------------------------------------------------
# The mode works at all: the gap this change closes.
# ---------------------------------------------------------------------------


def test_a_native_shared_delivery_is_ready_on_a_real_commit_range(tmp_path) -> None:
    repo, base, head, task, handoff = integration_case(tmp_path)

    result = verdict(repo, task, handoff, head)

    assert result.ready is True, result.blockers
    assert result.evidence is not None
    assert result.evidence.source == "native-shared"
    assert result.evidence.history_touched_paths == (OWNED_PATH,)
    assert result.evidence.branch == INTEGRATION_BRANCH


def test_the_delivery_evidence_is_the_commit_range_not_the_branch_tip(tmp_path) -> None:
    """A later commit on the same branch must not be attributed to this task."""

    repo, base, head, task, handoff = integration_case(tmp_path)
    repo.write(OTHER_PATH, "b = 2\n")
    repo.commit("feat: another task's work")

    collected = collect_repository_evidence(
        repo.root, task, expected_repository=config().repository, delivery_head_sha=head
    )

    assert collected.history_touched_paths == (OWNED_PATH,)
    assert collected.head_sha == head


# ---------------------------------------------------------------------------
# A worker may not claim another task's paths.
# ---------------------------------------------------------------------------


def test_a_delivery_that_touches_another_tasks_path_is_refused(tmp_path) -> None:
    repo, base, head, task, handoff = integration_case(tmp_path)
    repo.write(OTHER_PATH, "b = 2\n")
    head = repo.commit("feat: reach into another surface")
    handoff = handoff_for(repo, base, head, (OWNED_PATH, OTHER_PATH))

    result = verdict(repo, task, handoff, head)

    assert result.ready is False
    assert "agent.ownership_violation" in result.blockers


def test_a_delivery_that_touches_a_protected_path_is_refused(tmp_path) -> None:
    repo, base, head, task, handoff = integration_case(tmp_path)
    repo.write("SPEC.md", "mutated\n")
    head = repo.commit("feat: edit a protected authority")
    handoff = handoff_for(repo, base, head, (OWNED_PATH, "SPEC.md"))

    result = verdict(repo, task, handoff, head)

    assert result.ready is False
    assert "agent.ownership_violation" in result.blockers


def test_a_transient_edit_of_a_protected_path_is_refused_even_when_the_net_diff_is_clean(
    tmp_path,
) -> None:
    """Git history is the authority, not the final tree."""

    repo, base, head, task, handoff = integration_case(tmp_path)
    repo.write("SPEC.md", "mutated\n")
    repo.commit("chore: touch a protected authority")
    repo.write("SPEC.md", "frozen\n")
    head = repo.commit("chore: restore it byte for byte")
    handoff = handoff_for(repo, base, head, (OWNED_PATH,))

    collected = collect_repository_evidence(
        repo.root, task, expected_repository=config().repository, delivery_head_sha=head
    )
    result = verdict(repo, task, handoff, head)

    assert "SPEC.md" not in collected.net_changed_paths, "the net diff really is clean"
    assert "SPEC.md" in collected.history_touched_paths, "the history really is not"
    assert result.ready is False
    assert "agent.ownership_violation" in result.blockers


# ---------------------------------------------------------------------------
# A self-reported handoff is not evidence.
# ---------------------------------------------------------------------------


def test_a_handoff_that_claims_compliance_does_not_obtain_readiness(tmp_path) -> None:
    repo, base, head, task, _ = integration_case(tmp_path)
    repo.write("SPEC.md", "mutated\n")
    head = repo.commit("feat: reach a protected path")
    lying = handoff_for(
        repo,
        base,
        head,
        (OWNED_PATH, "SPEC.md"),
        ownership_compliance=True,
        ready_for_integration=True,
    )

    result = verdict(repo, task, lying, head)

    assert lying.ownership_compliance is True, "the example must claim compliance"
    assert result.ready is False
    assert "agent.ownership_violation" in result.blockers


def test_ready_for_integration_is_not_readiness(tmp_path) -> None:
    repo, base, head, task, handoff = integration_case(tmp_path)
    task = native_shared_task(repo, base, status=TaskStatus.IN_PROGRESS)

    result = verdict(repo, task, handoff, head)

    assert result.ready is False
    assert "agent.task_invalid" in result.blockers


def test_an_unreported_required_test_blocks_readiness(tmp_path) -> None:
    repo, base, head, task, _ = integration_case(tmp_path)
    silent = handoff_for(
        repo,
        base,
        head,
        (OWNED_PATH,),
        tests=(TestResult(name="unit", command="pytest -q", result="not_run"),),
    )

    result = verdict(repo, task, silent, head)

    assert result.ready is False
    assert "agent.handoff_invalid" in result.blockers


# ---------------------------------------------------------------------------
# A stale base still fails closed.
# ---------------------------------------------------------------------------


def test_a_base_that_is_not_on_the_integration_branch_fails_closed(tmp_path) -> None:
    repo, _base, _head, task, handoff = integration_case(tmp_path)

    foreign = unrelated_root_commit(repo)
    result = verdict(repo, task, handoff, foreign)

    assert result.ready is False
    assert "agent.base_stale" in result.blockers


def test_a_delivery_head_that_is_not_on_the_integration_branch_fails_closed(tmp_path) -> None:
    repo, base, _head, task, _ = integration_case(tmp_path)
    foreign = unrelated_root_commit(repo)
    handoff = handoff_for(repo, base, foreign, (OWNED_PATH,), commits=())

    result = verdict(repo, task, handoff, repo.sha())

    assert result.ready is False
    assert "agent.base_stale" in result.blockers


def test_a_handoff_that_invents_a_base_is_refused(tmp_path) -> None:
    repo, _base, head, task, _ = integration_case(tmp_path)
    other = unrelated_root_commit(repo)
    invented = handoff_for(repo, other, head, (OWNED_PATH,), commits=())

    result = verdict(repo, task, invented, head)

    assert result.ready is False
    assert "agent.handoff_invalid" in result.blockers


# ---------------------------------------------------------------------------
# The Main Agent owns integration; the Harness only executes.
# ---------------------------------------------------------------------------


def test_no_caller_may_supply_evidence_to_the_integration_verdict() -> None:
    """There is no `evidence=` parameter: a consistent dataclass proves nothing."""

    assert "evidence" not in inspect.signature(evaluate_integration).parameters


def test_readiness_without_a_repository_fails_closed(tmp_path) -> None:
    repo, base, head, task, handoff = integration_case(tmp_path)
    context = IntegrationContext.build((task,), config(), head)

    result = evaluate_integration(handoff, task, config(), context, repository=None)

    assert result.ready is False
    assert "agent.integration_context_incomplete" in result.blockers


def test_readiness_without_a_plan_fails_closed(tmp_path) -> None:
    repo, base, head, task, handoff = integration_case(tmp_path)
    context = IntegrationContext.build((task,), config(), head, include_plan=False)

    result = evaluate_integration(handoff, task, config(), context, repository=repo.root)

    assert result.ready is False
    assert "agent.integration_context_incomplete" in result.blockers


def test_the_evidence_collector_refuses_a_repository_that_is_not_canx(tmp_path) -> None:
    repo, base, single, task, _ = integration_case(tmp_path)
    repo.write("AGENTS.md", "different rules\n")
    repo.commit("chore: make the sandbox look like another repository")
    git(repo.root, ["remote", "set-url", "origin", "https://github.com/other/other.git"])

    with pytest.raises(GitStateError) as raised:
        collect_repository_evidence(
            repo.root,
            task,
            expected_repository=config().repository,
            delivery_head_sha=single,
        )

    assert raised.value.code == "agent.git_state_error"
