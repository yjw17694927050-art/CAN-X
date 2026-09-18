"""P1-1: the task worktree is found identically from either documented entry point.

``--repo`` may be the main repository worktree or the actual task worktree; both
must produce the same evidence, and a dirty task worktree must block readiness
from either one (AGENT-01-FIX-2 §15-§20). The fixture uses the real
``.worktrees/<task>`` shape and a contract that passes ``validate_task``, so it
cannot accidentally bypass the production contract (§37).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_git_sandbox import GIT_IDENTITY, SandboxRepository, git, make_repository

from tools.agent.config import AgentConfig, load_config
from tools.agent.contracts import RiskClass, TaskContract, TestResult
from tools.agent.evidence import (
    SOURCE_BRANCH_REF,
    SOURCE_WORKTREE,
    collect_repository_evidence,
)
from tools.agent.handoff import build_handoff
from tools.agent.lifecycle import TaskStatus
from tools.agent.validation import (
    IntegrationContext,
    evaluate_integration,
    validate_task,
)
from tools.agent.worktree import create_worktree, remove_worktree

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_BRANCH = "agent/AGENT-02-A-fix2"
WORKTREE_REL = ".worktrees/agent-02-a"


def config() -> AgentConfig:
    return load_config(REPO_ROOT / ".agent" / "config.json")


def _task(base: str) -> TaskContract:
    return TaskContract(
        task_id="AGENT-02-A",
        title="fix2 worktree evidence",
        objective="the task worktree is found from either entry point",
        scope="runtime/canx/foo/**",
        non_goals=(),
        base_sha=base,
        branch=TASK_BRANCH,
        worktree=WORKTREE_REL,
        owner="sub-a",
        allowed_paths=("runtime/canx/foo/**",),
        forbidden_paths=(),
        dependencies=(),
        shared_contracts=(),
        acceptance_criteria=("the gate reads the real task worktree",),
        required_tests=("unit",),
        handoff_requirements=("report the run",),
        risk_class=RiskClass.LOW,
        status=TaskStatus.HANDOFF_READY,
        revision=1,
    )


def _delivered(tmp_path: Path) -> tuple[SandboxRepository, Path, TaskContract]:
    """A real linked worktree at the production path, with one real commit."""
    repo = make_repository(tmp_path)
    base = repo.sha()
    worktree = repo.root / WORKTREE_REL
    create_worktree(
        repo.root,
        task_id="AGENT-02-A",
        branch=TASK_BRANCH,
        path=worktree,
        base_sha=base,
    )
    task = _task(base)
    validate_task(task, config())

    (worktree / "runtime" / "canx" / "foo").mkdir(parents=True, exist_ok=True)
    (worktree / "runtime" / "canx" / "foo" / "a.py").write_text("a = 2\n", encoding="utf-8")
    git(worktree, ["add", "-A"])
    git(worktree, [*GIT_IDENTITY, "commit", "-m", "deliver the owned change"])
    return repo, worktree, task


def _handoff(worktree: Path, task: TaskContract):
    return build_handoff(
        worktree,
        task,
        agent="sub-a",
        tests=(TestResult(name="unit", command="python -m pytest -q", result="passed"),),
        ready_for_integration=True,
    )


def test_both_entry_points_produce_equivalent_evidence(tmp_path: Path) -> None:
    """RED -> GREEN: from the task worktree this used to fall back to branch-ref."""
    repo, worktree, task = _delivered(tmp_path)
    from_main = collect_repository_evidence(repo.root, task)
    from_worktree = collect_repository_evidence(worktree, task)

    assert from_main.source == SOURCE_WORKTREE
    assert from_worktree.source == SOURCE_WORKTREE
    assert from_main.branch == from_worktree.branch == TASK_BRANCH
    assert from_main.head_sha == from_worktree.head_sha
    assert from_main.base_sha == from_worktree.base_sha == task.base_sha
    assert from_main.base_is_ancestor and from_worktree.base_is_ancestor
    assert from_main.clean and from_worktree.clean
    assert from_main.net_changed_paths == from_worktree.net_changed_paths
    assert from_main.history_touched_paths == from_worktree.history_touched_paths
    assert from_main.commits == from_worktree.commits
    assert from_main.worktree_path == from_worktree.worktree_path


def test_a_clean_delivery_is_ready_from_either_entry_point(tmp_path: Path) -> None:
    repo, worktree, task = _delivered(tmp_path)
    handoff = _handoff(worktree, task)
    context = IntegrationContext.build((task,), config(), task.base_sha)
    for repository in (repo.root, worktree):
        readiness = evaluate_integration(
            handoff, task, config(), context, repository=repository
        )
        assert readiness.ready, (repository, readiness.details)


@pytest.mark.parametrize("entry", ["main", "worktree"])
def test_a_dirty_task_worktree_blocks_readiness_from_either_entry_point(
    tmp_path: Path, entry: str
) -> None:
    """RED -> GREEN: the dirty state used to be missed from the worktree path."""
    repo, worktree, task = _delivered(tmp_path)
    handoff = _handoff(worktree, task)
    (worktree / "runtime" / "canx" / "foo" / "uncommitted.py").write_text(
        "x = 1\n", encoding="utf-8"
    )

    repository = repo.root if entry == "main" else worktree
    readiness = evaluate_integration(
        handoff,
        task,
        config(),
        IntegrationContext.build((task,), config(), task.base_sha),
        repository=repository,
    )
    assert not readiness.ready
    assert "agent.git_state_error" in readiness.blockers


def test_the_fallback_is_used_only_when_no_task_worktree_is_registered(
    tmp_path: Path,
) -> None:
    repo, worktree, task = _delivered(tmp_path)
    remove_worktree(repo.root, worktree, allow_unmerged=True)

    evidence = collect_repository_evidence(repo.root, task)
    assert evidence.source == SOURCE_BRANCH_REF
    assert evidence.worktree_path is None
    assert evidence.branch == TASK_BRANCH
