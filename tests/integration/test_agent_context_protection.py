"""AGENT-CONTEXT-PROTECTION, git-backed: a transient touch is never laundered.

The static ownership gate refuses a Sub-Agent that *declares* the context truth
surface (see ``tests/unit/agent_tools/test_context_protection.py``). This file
proves the second half: a delivery whose **history** touched that surface cannot
escape by restoring the file byte-for-byte in a later commit, leaving the net
diff clean. ``evidence.history_touched_paths`` is the authoritative set and the
ownership gate reads it, not the net diff (AGENT-01-FIX-2 §21-§23 applied to the
context truth surface).

No real Sub-Agent runs anywhere in this file. Every repository is a throwaway
under ``tmp_path``; the real CAN-X history is never mutated.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_git_sandbox import SandboxRepository, make_repository

from tools.agent.config import AgentConfig, load_config
from tools.agent.contracts import HandoffContract, RiskClass, TaskContract, TestResult
from tools.agent.evidence import collect_repository_evidence
from tools.agent.lifecycle import TaskStatus
from tools.agent.validation import IntegrationContext, evaluate_integration

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / ".agent"

TASK_ID = "AGENT-02-A"
TASK_BRANCH = "agent/AGENT-02-A-context-notes"
OWNED = "docs/engineering/NOTES.md"

CONTEXT_ROUTER = "docs/CONTEXT_INDEX.md"
CONTEXT_GOVERNANCE = "docs/engineering/AGENT_CONTEXT_GOVERNANCE.md"

ROUTER_BODY = "# Context router\n\nTier 1: AGENTS.md, PRD.md, SPEC.md\n"
GOVERNANCE_BODY = "# Agent context governance\n\nL0: AGENTS.md, PROJECT_STATE.md\n"

_BODY = {CONTEXT_ROUTER: ROUTER_BODY, CONTEXT_GOVERNANCE: GOVERNANCE_BODY}


def _config() -> AgentConfig:
    return load_config(AGENT_DIR / "config.json")


def _seed(tmp_path: Path) -> tuple[SandboxRepository, str]:
    """A throwaway repository that really contains the context truth surface."""
    repo = make_repository(tmp_path)
    repo.write(CONTEXT_ROUTER, ROUTER_BODY)
    repo.write(CONTEXT_GOVERNANCE, GOVERNANCE_BODY)
    repo.write(OWNED, "notes v1\n")
    repo.commit("seed the context governance truth surface")
    return repo, repo.sha()


def _task(base: str) -> TaskContract:
    return TaskContract(
        task_id=TASK_ID,
        title="a delivery that stays inside its own surface",
        objective="the context truth surface is never touched",
        scope=OWNED,
        non_goals=(),
        base_sha=base,
        branch=TASK_BRANCH,
        worktree=".",
        owner="sub-a",
        allowed_paths=(OWNED,),
        forbidden_paths=(),
        dependencies=(),
        shared_contracts=(),
        acceptance_criteria=("the history touches only the owned path",),
        required_tests=("unit",),
        handoff_requirements=("report the run",),
        risk_class=RiskClass.LOW,
        status=TaskStatus.HANDOFF_READY,
        revision=1,
    )


def _handoff(
    repo: SandboxRepository, task: TaskContract, head: str, changed: tuple[str, ...]
) -> HandoffContract:
    return HandoffContract(
        task_id=task.task_id,
        agent="sub-a",
        branch=task.branch,
        base_sha=task.base_sha,
        head_sha=head,
        commits=repo.short_commits(task.base_sha, head),
        changed_files=changed,
        ownership_compliance=True,
        tests=(TestResult(name="unit", command="python -m pytest -q", result="passed"),),
        dependencies=(),
        ready_for_integration=True,
    )


def _readiness(repo: SandboxRepository, task: TaskContract, handoff: HandoffContract):
    return evaluate_integration(
        handoff,
        task,
        _config(),
        IntegrationContext.build((task,), _config(), task.base_sha),
        repository=repo.root,
    )


def _transient_touch(tmp_path: Path, truth_file: str):
    """Modify a context truth file, then restore it byte-for-byte."""
    repo, base = _seed(tmp_path)
    repo.branch(TASK_BRANCH)
    repo.write(OWNED, "notes v2\n")
    repo.write(truth_file, "MUTATED BY A SUB-AGENT\n")
    repo.commit("deliver notes, and quietly rewrite the context truth surface")
    repo.write(truth_file, _BODY[truth_file])
    head = repo.commit("restore the context truth surface byte-for-byte")
    task = _task(base)
    return repo, task, head


# ------------------------------------------------------------------ the disguise


@pytest.mark.parametrize("truth_file", [CONTEXT_ROUTER, CONTEXT_GOVERNANCE])
def test_the_net_diff_really_is_clean_of_the_restored_file(
    tmp_path: Path, truth_file: str
) -> None:
    """The precondition the escape depends on: the net diff hides the touch."""
    repo, task, _head = _transient_touch(tmp_path, truth_file)
    evidence = collect_repository_evidence(repo.root, task)
    assert evidence.net_changed_paths == (OWNED,)
    assert truth_file not in evidence.net_changed_paths


# ------------------------------------------------------------ the gate that holds


@pytest.mark.parametrize("truth_file", [CONTEXT_ROUTER, CONTEXT_GOVERNANCE])
def test_an_honest_handoff_reporting_the_real_history_is_refused(
    tmp_path: Path, truth_file: str
) -> None:
    repo, task, head = _transient_touch(tmp_path, truth_file)
    evidence = collect_repository_evidence(repo.root, task)
    assert truth_file in evidence.history_touched_paths

    honest = _handoff(repo, task, head, evidence.history_touched_paths)
    readiness = _readiness(repo, task, honest)

    assert not readiness.ready
    assert "agent.ownership_violation" in readiness.blockers


@pytest.mark.parametrize("truth_file", [CONTEXT_ROUTER, CONTEXT_GOVERNANCE])
def test_a_handoff_hiding_the_touch_is_refused(tmp_path: Path, truth_file: str) -> None:
    """The delivery reports only its owned file, as a dishonest handoff would."""
    repo, task, head = _transient_touch(tmp_path, truth_file)
    disguised = _handoff(repo, task, head, (OWNED,))
    readiness = _readiness(repo, task, disguised)

    assert not readiness.ready
    assert "agent.ownership_violation" in readiness.blockers
    assert "agent.handoff_evidence_mismatch" in readiness.blockers


# --------------------------------------------------------------- the control


def test_a_delivery_that_touches_only_its_owned_surface_is_ready(
    tmp_path: Path,
) -> None:
    """Without this the gate above would be indistinguishable from "always red"."""
    repo, base = _seed(tmp_path)
    repo.branch(TASK_BRANCH)
    repo.write(OWNED, "notes v2\n")
    head = repo.commit("deliver notes only")
    task = _task(base)

    evidence = collect_repository_evidence(repo.root, task)
    assert evidence.history_touched_paths == (OWNED,)
    assert evidence.net_changed_paths == (OWNED,)

    honest = _handoff(repo, task, head, (OWNED,))
    readiness = _readiness(repo, task, honest)
    assert readiness.ready, readiness.details
