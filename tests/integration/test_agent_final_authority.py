"""AGENT-01-FIX-4: the authority of the final verdict, and the secrets it must not echo.

Two invariants are pinned here.

**INVARIANT A.** A final ``ready: true`` verdict is derived from a real
repository and real Git evidence - never from a caller-supplied evidence object.
There is no ``evidence=`` parameter to hand one in through, and a verdict asked
for without a repository is ``agent.integration_context_incomplete``.

**INVARIANT B.** Repository identity validation may reject an unsafe remote, but
the rejection itself must never disclose it. ``canonical_repository`` refusing a
credential-bearing origin is correct; echoing that origin into
``exception.details`` - and from there into CLI output, CI logs and captured
error artifacts - is not.

The fixtures are real temporary Git repositories, because that is the only thing
the final gate now accepts as evidence.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest
from agent_git_sandbox import (
    CANONICAL_ORIGIN,
    GIT_IDENTITY,
    SandboxRepository,
    git,
    make_repository,
)

from tools.agent.config import AgentConfig, load_config
from tools.agent.contracts import RiskClass, TaskContract, TestResult
from tools.agent.errors import GitStateError
from tools.agent.evidence import collect_repository_evidence
from tools.agent.handoff import build_handoff
from tools.agent.lifecycle import TaskStatus
from tools.agent.validation import IntegrationContext, evaluate_integration
from tools.agent.worktree import create_worktree

CANONICAL = "yjw17694927050-art/CAN-X"
REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_BRANCH = "agent/AGENT-02-A-fix4"
WORKTREE_REL = ".worktrees/agent-02-a"
SECRET = "TOP_SECRET_TOKEN"
CREDENTIAL_ORIGIN = f"https://alice:{SECRET}@github.com/{CANONICAL}.git"


def config() -> AgentConfig:
    return load_config(REPO_ROOT / ".agent" / "config.json")


def _task(base: str) -> TaskContract:
    return TaskContract(
        task_id="AGENT-02-A",
        title="fix4 final authority",
        objective="the final verdict is read from a real repository",
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
        acceptance_criteria=("the gate reads the real repository",),
        required_tests=("unit",),
        handoff_requirements=("report the run",),
        risk_class=RiskClass.LOW,
        status=TaskStatus.HANDOFF_READY,
        revision=1,
    )


def _delivered(
    tmp_path: Path, *, origin: str | None = CANONICAL_ORIGIN
) -> tuple[SandboxRepository, Path, TaskContract]:
    """A real linked task worktree with one real commit, on an origin of choice."""
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


def _handoff(worktree: Path, task: TaskContract):
    return build_handoff(
        worktree,
        task,
        agent="sub-a",
        tests=(TestResult(name="unit", command="python -m pytest -q", result="passed"),),
        ready_for_integration=True,
    )


def _context(task: TaskContract) -> IntegrationContext:
    return IntegrationContext.build((task,), config(), task.base_sha)


def _assert_no_secret(payload: object) -> None:
    blob = json.dumps(payload, default=str)
    assert SECRET not in blob
    assert f"alice:{SECRET}" not in blob


# ------------------------------------------- INVARIANT A: real Git is the authority


def test_the_final_gate_has_no_caller_supplied_evidence_parameter() -> None:
    """There is no second authority path to hand a fabricated dataclass into."""
    parameters = inspect.signature(evaluate_integration).parameters
    assert "evidence" not in parameters
    assert "repository" in parameters


def test_a_caller_created_evidence_object_cannot_reach_the_final_verdict(
    tmp_path: Path,
) -> None:
    """RED -> GREEN: this exact call shape used to return ``ready: true``.

    Before FIX-4, a hand-built ``RepositoryEvidence`` with
    ``repository_identity=None`` - no Git ever read, no proof the repository was
    ``config.repository`` - was accepted as the authority and produced a final
    READY. The parameter is gone, so the shape is now impossible rather than
    merely discouraged.
    """
    repo, worktree, task = _delivered(tmp_path)
    handoff = _handoff(worktree, task)
    fabricated = collect_repository_evidence(repo.root, task)

    with pytest.raises(TypeError):
        evaluate_integration(  # type: ignore[call-arg]
            handoff, task, config(), _context(task), evidence=fabricated
        )


def test_without_a_repository_the_gate_can_never_report_ready(tmp_path: Path) -> None:
    """No repository is not a pass; it is "cannot tell", and that is a blocker."""
    _repo, worktree, task = _delivered(tmp_path)
    readiness = evaluate_integration(_handoff(worktree, task), task, config(), _context(task))
    assert not readiness.ready
    assert "agent.integration_context_incomplete" in readiness.blockers
    assert readiness.evidence is None


def test_a_real_repository_delivery_is_locally_ready(tmp_path: Path) -> None:
    """The valid route still works end to end (FIX-4 §10)."""
    repo, worktree, task = _delivered(tmp_path)
    readiness = evaluate_integration(
        _handoff(worktree, task), task, config(), _context(task), repository=repo.root
    )
    assert readiness.ready, readiness.details
    assert readiness.blockers == ()
    assert readiness.evidence is not None
    assert readiness.evidence.repository_identity == CANONICAL
    # ...and the verdict still refuses to speak for the platform gate. The context
    # here is a full one (orchestration plan included), which is what makes the
    # positive verdict reachable at all (FIX-2 §29).
    payload = readiness.to_dict()
    assert payload["github_gate"]["checked_here"] is False
    assert "Quality Gate" in str(payload["github_gate"]["note"])


# ------------------------------------- INVARIANT B: a rejected remote is not echoed


def test_a_credential_bearing_origin_is_refused_without_echoing_it(tmp_path: Path) -> None:
    """RED -> GREEN: the raw URL used to land in ``details["origin"]``."""
    repo, _worktree, task = _delivered(tmp_path, origin=CREDENTIAL_ORIGIN)

    with pytest.raises(GitStateError) as raised:
        collect_repository_evidence(repo.root, task, expected_repository=CANONICAL)

    error = raised.value
    assert error.code == "agent.git_state_error"
    assert error.details["origin_supported"] is False
    for rendered in (
        str(error),
        error.message,
        error.details,
        error.as_dict(),
        json.dumps(error.as_dict()),
    ):
        _assert_no_secret(rendered)


def test_the_credential_never_reaches_the_verdict_the_gate_returns(tmp_path: Path) -> None:
    """The same rejection, through the path ``evaluate_integration`` actually uses."""
    repo, worktree, task = _delivered(tmp_path, origin=CREDENTIAL_ORIGIN)
    readiness = evaluate_integration(
        _handoff(worktree, task), task, config(), _context(task), repository=repo.root
    )
    assert not readiness.ready
    assert "agent.git_state_error" in readiness.blockers
    _assert_no_secret(readiness.to_dict())


def test_a_valid_wrong_repository_reports_only_the_safe_canonical_identity(
    tmp_path: Path,
) -> None:
    """A canonical ``owner/repo`` carries no credentials, so it stays reportable."""
    repo, worktree, task = _delivered(
        tmp_path, origin="https://github.com/other-owner/other-repo.git"
    )
    readiness = evaluate_integration(
        _handoff(worktree, task), task, config(), _context(task), repository=repo.root
    )
    assert not readiness.ready
    entry = next(item for item in readiness.details if item["code"] == "agent.git_state_error")
    assert entry["details"]["origin"] == "other-owner/other-repo"
    assert "https://" not in json.dumps(entry)


def test_no_origin_at_all_is_refused_without_inventing_one(tmp_path: Path) -> None:
    repo, worktree, task = _delivered(tmp_path, origin=None)
    readiness = evaluate_integration(
        _handoff(worktree, task), task, config(), _context(task), repository=repo.root
    )
    assert not readiness.ready
    assert "agent.git_state_error" in readiness.blockers


def test_the_cli_never_prints_a_credential_bearing_origin(tmp_path: Path) -> None:
    """The structured error is rendered by the real CLI, not just serialized.

    A leak that only survives ``json.dumps`` would still reach CI logs and agent
    logs; this runs the actual entry point and inspects both streams (FIX-4 §17).
    """
    repo, worktree, task = _delivered(tmp_path, origin=CREDENTIAL_ORIGIN)
    task_file = tmp_path / "task.json"
    handoff_file = tmp_path / "handoff.json"
    task_file.write_text(json.dumps(task.to_dict()), encoding="utf-8")
    handoff_file.write_text(
        json.dumps(_handoff(worktree, task).to_dict()), encoding="utf-8"
    )

    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.agent.cli",
            "--config",
            str(REPO_ROOT / ".agent" / "config.json"),
            "check-integration",
            "--task",
            str(task_file),
            "--handoff",
            str(handoff_file),
            "--integration-head",
            task.base_sha,
            "--repo",
            str(repo.root),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert done.returncode != 0
    assert SECRET not in done.stdout
    assert SECRET not in done.stderr
    assert "agent.git_state_error" in done.stdout
