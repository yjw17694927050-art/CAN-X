"""P2: the repository-identity check against a real git remote.

RED -> GREEN: every origin shape in the "substring lookalike" cases used to pass
``validate_repository`` because the old check asked whether the expected identity
appeared *anywhere* in the URL.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_git_sandbox import GIT_IDENTITY, SandboxRepository, git, make_repository

from tools.agent.config import AgentConfig, load_config
from tools.agent.contracts import RiskClass, TaskContract, TestResult
from tools.agent.errors import GitStateError
from tools.agent.evidence import collect_repository_evidence
from tools.agent.handoff import build_handoff
from tools.agent.lifecycle import TaskStatus
from tools.agent.validation import IntegrationContext, evaluate_integration
from tools.agent.worktree import create_worktree, validate_repository

CANONICAL = "yjw17694927050-art/CAN-X"
REPO_ROOT = Path(__file__).resolve().parents[2]


def config() -> AgentConfig:
    return load_config(REPO_ROOT / ".agent" / "config.json")


def _with_remote(tmp_path: Path, origin: str | None) -> Path:
    repo = make_repository(tmp_path, origin=origin)
    return repo.root


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/evil/yjw17694927050-art/CAN-X-copy.git",
        "https://example.com/yjw17694927050-art/CAN-X.git",
        "git@github.com:yjw17694927050-art/CAN-X-evil.git",
    ],
)
def test_a_substring_lookalike_origin_is_refused(tmp_path: Path, origin: str) -> None:
    root = _with_remote(tmp_path, origin)
    with pytest.raises(GitStateError) as raised:
        validate_repository(root, expected_remote=CANONICAL)
    assert raised.value.code == "agent.git_state_error"


@pytest.mark.parametrize(
    "origin",
    [
        f"https://github.com/{CANONICAL}.git",
        f"git@github.com:{CANONICAL}.git",
    ],
)
def test_the_real_origin_shapes_are_accepted(tmp_path: Path, origin: str) -> None:
    root = _with_remote(tmp_path, origin)
    assert validate_repository(root, expected_remote=CANONICAL) == root


def test_a_repository_without_origin_fails_closed(tmp_path: Path) -> None:
    repo = make_repository(tmp_path, origin=None)
    with pytest.raises(GitStateError):
        validate_repository(repo.root, expected_remote=CANONICAL)


def test_an_unrecognised_remote_host_fails_closed(tmp_path: Path) -> None:
    root = _with_remote(tmp_path, f"https://gitlab.example.com/{CANONICAL}.git")
    with pytest.raises(GitStateError) as raised:
        validate_repository(root, expected_remote=CANONICAL)
    assert "supported GitHub remote shape" in raised.value.message


def test_no_expected_remote_still_validates_the_repository(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    assert validate_repository(repo.root) == repo.root


# --------------------------------- FIX-3: final integration evidence is repository-bound
#
# `validate_repository()` already canonicalised and compared exactly, but the
# integration path never passed the configured identity, so any valid Git repo
# could produce `ready: true`. These tests close that trust boundary
# (AGENT-01-FIX-3 §16-§20, §25-§28).

TASK_BRANCH = "agent/AGENT-02-A-fix3"
WORKTREE_REL = ".worktrees/agent-02-a"


def _task(base: str) -> TaskContract:
    return TaskContract(
        task_id="AGENT-02-A",
        title="fix3 repository identity",
        objective="integration evidence is bound to the configured repository",
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
        acceptance_criteria=("the gate reads the configured repository",),
        required_tests=("unit",),
        handoff_requirements=("report the run",),
        risk_class=RiskClass.LOW,
        status=TaskStatus.HANDOFF_READY,
        revision=1,
    )


def _delivered(
    tmp_path: Path, *, origin: str | None
) -> tuple[SandboxRepository, Path, TaskContract]:
    """A real linked task worktree with one commit, on an origin of choice."""
    repo = make_repository(tmp_path, origin=origin)
    base = repo.sha()
    worktree = repo.root / WORKTREE_REL
    create_worktree(
        repo.root, task_id="AGENT-02-A", branch=TASK_BRANCH, path=worktree, base_sha=base
    )
    task = _task(base)
    (worktree / "runtime" / "canx" / "foo").mkdir(parents=True, exist_ok=True)
    (worktree / "runtime" / "canx" / "foo" / "a.py").write_text("a = 2\n", encoding="utf-8")
    git(worktree, ["add", "-A"])
    git(worktree, [*GIT_IDENTITY, "commit", "-m", "deliver the owned change"])
    return repo, worktree, task


def _readiness(repo: SandboxRepository, worktree: Path, task: TaskContract):
    handoff = build_handoff(
        worktree,
        task,
        agent="sub-a",
        tests=(TestResult(name="unit", command="python -m pytest -q", result="passed"),),
        ready_for_integration=True,
    )
    return evaluate_integration(
        handoff,
        task,
        config(),
        IntegrationContext.build((task,), config(), task.base_sha),
        repository=repo.root,
    )


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/other-owner/other-repo.git",
        "https://github.com/yjw17694927050-art/CAN-X-copy.git",
        "git@github.com:yjw17694927050-art/CAN-X-evil.git",
        "https://gitlab.example.com/yjw17694927050-art/CAN-X.git",
        None,
    ],
)
def test_evidence_from_the_wrong_repository_can_never_be_ready(
    tmp_path: Path, origin: str | None
) -> None:
    """RED -> GREEN: every one of these used to come back `ready: true`."""
    repo, worktree, task = _delivered(tmp_path, origin=origin)
    readiness = _readiness(repo, worktree, task)
    assert not readiness.ready
    assert "agent.git_state_error" in readiness.blockers


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/yjw17694927050-art/CAN-X.git",
        "git@github.com:yjw17694927050-art/CAN-X.git",
        "ssh://git@github.com/yjw17694927050-art/CAN-X.git",
    ],
)
def test_evidence_from_the_configured_repository_is_accepted(
    tmp_path: Path, origin: str
) -> None:
    repo, worktree, task = _delivered(tmp_path, origin=origin)
    readiness = _readiness(repo, worktree, task)
    assert readiness.ready, readiness.details
    assert readiness.evidence is not None
    assert readiness.evidence.repository_identity == CANONICAL


def test_the_identity_is_checked_on_the_same_path_check_integration_uses(
    tmp_path: Path,
) -> None:
    """The binding lives in `evaluate_integration`, not in a side helper."""
    repo, _worktree, task = _delivered(tmp_path, origin="https://github.com/x/y.git")
    with pytest.raises(GitStateError):
        collect_repository_evidence(
            repo.root, task, expected_repository=CANONICAL
        )
    # ...and a bare identity-less collection still works, for low-level callers.
    assert collect_repository_evidence(repo.root, task).repository_identity == "x/y"
