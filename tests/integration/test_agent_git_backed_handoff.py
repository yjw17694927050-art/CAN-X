"""P0-2: the handoff is a report; Git is the authority.

Every test here edits or invents handoff JSON and checks that the repository's
own facts win. None of them pass the "true" changed-file list into the
validator - the whole point is that Git discovers the lie.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_git_sandbox import SandboxRepository, make_repository

from tools.agent.config import AgentConfig, load_config
from tools.agent.contracts import HandoffContract, RiskClass, TaskContract, TestResult
from tools.agent.errors import AgentToolingError
from tools.agent.evidence import SOURCE_BRANCH_REF, SOURCE_WORKTREE, collect_repository_evidence
from tools.agent.handoff import build_handoff
from tools.agent.lifecycle import TaskStatus
from tools.agent.validation import (
    IntegrationContext,
    evaluate_integration,
    verify_repository_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_BRANCH = "agent/AGENT-02-A-fix1"


def config() -> AgentConfig:
    return load_config(REPO_ROOT / ".agent" / "config.json")


def task_for(
    repo: SandboxRepository, base: str, *, worktree: str = ".", **overrides: object
) -> TaskContract:
    values: dict[str, object] = {
        "task_id": "AGENT-02-A",
        "title": "fix1 evidence",
        "objective": "prove the gate reads Git",
        "scope": "runtime/canx/foo/**",
        "non_goals": (),
        "base_sha": base,
        "branch": TASK_BRANCH,
        "worktree": worktree,
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
    }
    values.update(overrides)
    return TaskContract(**values)


def handoff_for(task: TaskContract, **overrides: object) -> HandoffContract:
    values: dict[str, object] = {
        "task_id": task.task_id,
        "agent": task.owner,
        "branch": task.branch,
        "base_sha": task.base_sha,
        "head_sha": task.base_sha,
        "commits": (),
        "changed_files": ("runtime/canx/foo/a.py",),
        "ownership_compliance": True,
        "tests": (TestResult(name="unit", command="pytest", result="passed"),),
        "dependencies": (),
        "ready_for_integration": True,
    }
    values.update(overrides)
    return HandoffContract(**values)


def reading_repo(tmp_path: Path) -> tuple[SandboxRepository, str, str, tuple[str, ...]]:
    """A repo on the task branch with one real commit that also edits SPEC.md."""
    repo = make_repository(tmp_path)
    base = repo.sha()
    repo.branch(TASK_BRANCH)
    repo.write("runtime/canx/foo/a.py", "a = 2\n")
    repo.write("SPEC.md", "MUTATED\n")
    head = repo.commit("real work")
    return repo, base, head, repo.short_commits(base, head)


def test_evidence_is_read_from_the_worktree(repo_and_task) -> None:
    repo, base, head = repo_and_task
    task = task_for(repo, base)
    evidence = collect_repository_evidence(repo.root, task)
    assert evidence.source == SOURCE_WORKTREE
    assert evidence.branch == TASK_BRANCH
    assert evidence.head_sha == head
    assert evidence.base_is_ancestor
    assert evidence.clean


@pytest.fixture
def repo_and_task(tmp_path: Path) -> tuple[SandboxRepository, str, str]:
    repo, base, head, _commits = reading_repo(tmp_path)
    return repo, base, head


def test_a_handoff_that_omits_a_really_changed_file_is_rejected(tmp_path: Path) -> None:
    """RED -> GREEN: the omitted-file attack.

    The branch really changed ``runtime/canx/foo/a.py`` **and** ``SPEC.md``. The
    handoff reports only the first - which the old gate accepted, because it
    validated the reported list against itself.
    """
    repo, base, head, commits = reading_repo(tmp_path)
    task = task_for(repo, base)
    lying = handoff_for(
        task,
        head_sha=head,
        commits=commits,
        changed_files=("runtime/canx/foo/a.py",),
        ownership_compliance=True,
    )

    readiness = evaluate_integration(
        lying,
        task,
        config(),
        IntegrationContext.build((task,), config(), base),
        repository=repo.root,
    )

    assert not readiness.ready
    assert "agent.ownership_violation" in readiness.blockers
    assert "agent.handoff_evidence_mismatch" in readiness.blockers
    mismatch = next(
        item for item in readiness.details if item["code"] == "agent.handoff_evidence_mismatch"
    )
    assert mismatch["details"]["unreported"] == ["SPEC.md"]


def test_an_honest_handoff_naming_the_real_change_set_is_accepted(tmp_path: Path) -> None:
    repo, base, head, commits = reading_repo(tmp_path)
    task = task_for(repo, base, allowed_paths=("runtime/canx/foo/**", "SPEC.md"), owner="main")
    honest = handoff_for(
        task,
        head_sha=head,
        commits=commits,
        changed_files=("runtime/canx/foo/a.py", "SPEC.md"),
    )

    readiness = evaluate_integration(
        honest,
        task,
        config(),
        IntegrationContext.build((task,), config(), base),
        repository=repo.root,
    )

    assert readiness.ready, readiness.details
    assert readiness.evidence is not None
    assert readiness.evidence.source == SOURCE_WORKTREE


def test_a_handoff_that_invents_the_current_main_as_its_base_is_rejected(
    tmp_path: Path,
) -> None:
    """RED -> GREEN: the fake-base attack."""
    repo, base, head, commits = reading_repo(tmp_path)
    repo.checkout("main")
    repo.write("README.md", "main advances\n")
    advanced = repo.commit("main advances")
    repo.checkout(TASK_BRANCH)

    task = task_for(repo, base, allowed_paths=("runtime/canx/foo/**", "SPEC.md"), owner="main")
    lying = handoff_for(
        task,
        base_sha=advanced,
        head_sha=head,
        commits=commits,
        changed_files=("runtime/canx/foo/a.py", "SPEC.md"),
    )

    readiness = evaluate_integration(
        lying,
        task,
        config(),
        IntegrationContext.build((task,), config(), advanced),
        repository=repo.root,
    )

    assert not readiness.ready
    assert "agent.handoff_invalid" in readiness.blockers
    assert any("base" in str(item["message"]).lower() for item in readiness.details)


def test_a_handoff_that_invents_a_head_is_rejected(tmp_path: Path) -> None:
    """RED -> GREEN: the fake-head attack."""
    repo, base, head, commits = reading_repo(tmp_path)
    task = task_for(repo, base, allowed_paths=("runtime/canx/foo/**", "SPEC.md"), owner="main")
    lying = handoff_for(
        task,
        head_sha="9" * 40,
        commits=commits,
        changed_files=("runtime/canx/foo/a.py", "SPEC.md"),
    )

    readiness = evaluate_integration(
        lying,
        task,
        config(),
        IntegrationContext.build((task,), config(), base),
        repository=repo.root,
    )

    assert not readiness.ready
    fake = next(
        item for item in readiness.details if "actual HEAD" in str(item["message"])
    )
    assert fake["details"]["actual"] == head
    assert fake["details"]["reported"] == "9" * 40


def test_a_dirty_worktree_cannot_be_integration_ready(tmp_path: Path) -> None:
    repo, base, head, commits = reading_repo(tmp_path)
    task = task_for(repo, base, allowed_paths=("runtime/canx/foo/**", "SPEC.md"), owner="main")
    honest = handoff_for(
        task,
        head_sha=head,
        commits=commits,
        changed_files=("runtime/canx/foo/a.py", "SPEC.md"),
    )
    repo.write("runtime/canx/foo/a.py", "uncommitted work\n")

    readiness = evaluate_integration(
        honest,
        task,
        config(),
        IntegrationContext.build((task,), config(), base),
        repository=repo.root,
    )

    assert not readiness.ready
    assert "agent.git_state_error" in readiness.blockers


def test_a_handoff_that_misreports_the_commit_set_is_rejected(tmp_path: Path) -> None:
    repo, base, head, _commits = reading_repo(tmp_path)
    task = task_for(repo, base, allowed_paths=("runtime/canx/foo/**", "SPEC.md"), owner="main")
    lying = handoff_for(
        task,
        head_sha=head,
        commits=("abc1234 a commit that never happened",),
        changed_files=("runtime/canx/foo/a.py", "SPEC.md"),
    )

    readiness = evaluate_integration(
        lying,
        task,
        config(),
        IntegrationContext.build((task,), config(), base),
        repository=repo.root,
    )

    assert not readiness.ready
    assert "agent.handoff_evidence_mismatch" in readiness.blockers


def test_both_sides_of_a_rename_are_in_the_ownership_surface(tmp_path: Path) -> None:
    """A protected file renamed into an owned subtree must still be visible."""
    repo = make_repository(tmp_path)
    base = repo.sha()
    repo.branch(TASK_BRANCH)
    repo.rename("SPEC.md", "runtime/canx/foo/spec-copy.md")
    head = repo.commit("hide SPEC.md behind a rename")

    task = task_for(repo, base)
    evidence = collect_repository_evidence(repo.root, task)

    assert "SPEC.md" in evidence.history_touched_paths
    assert "runtime/canx/foo/spec-copy.md" in evidence.history_touched_paths
    blockers = verify_repository_evidence(
        handoff_for(task, head_sha=head, commits=repo.short_commits(base, head),
                    changed_files=tuple(evidence.history_touched_paths)),
        task,
        evidence,
    )
    codes = {blocker.code for blocker in blockers}
    assert "agent.ownership_violation" in codes, blockers


def test_a_deleted_protected_file_is_still_a_violation(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    base = repo.sha()
    repo.branch(TASK_BRANCH)
    (repo.root / "SPEC.md").unlink()
    repo.write("runtime/canx/foo/a.py", "a = 2\n")
    head = repo.commit("delete SPEC.md")

    task = task_for(repo, base)
    evidence = collect_repository_evidence(repo.root, task)
    assert "SPEC.md" in evidence.history_touched_paths
    blockers = verify_repository_evidence(
        handoff_for(
            task,
            head_sha=head,
            commits=repo.short_commits(base, head),
            changed_files=tuple(evidence.history_touched_paths),
        ),
        task,
        evidence,
    )
    assert "agent.ownership_violation" in {blocker.code for blocker in blockers}


def test_evidence_falls_back_to_the_branch_ref_and_says_so(tmp_path: Path) -> None:
    """A cleaned-up worktree may still be verified, but never silently."""
    repo, base, head, _commits = reading_repo(tmp_path)
    task = task_for(repo, base, worktree=".worktrees/agent-02-a")

    evidence = collect_repository_evidence(repo.root, task)

    assert evidence.source == SOURCE_BRANCH_REF
    assert evidence.worktree_path is None
    assert evidence.branch == TASK_BRANCH
    assert evidence.head_sha == head


def test_a_base_that_is_not_an_ancestor_is_reported(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    repo.branch(TASK_BRANCH)
    repo.write("runtime/canx/foo/a.py", "branch work\n")
    head = repo.commit("branch work")
    repo.checkout("main")
    repo.write("README.md", "unrelated\n")
    unrelated = repo.commit("unrelated main work")
    repo.checkout(TASK_BRANCH)

    task = task_for(repo, unrelated)
    evidence = collect_repository_evidence(repo.root, task)

    assert not evidence.base_is_ancestor
    blockers = verify_repository_evidence(
        handoff_for(task, head_sha=head, commits=()), task, evidence
    )
    assert "agent.base_not_ancestor" in {blocker.code for blocker in blockers}


def test_the_evidence_carries_no_handoff_input(tmp_path: Path) -> None:
    """The collector takes only the repository and the task contract."""
    repo, base, _head, _commits = reading_repo(tmp_path)
    task = task_for(repo, base)
    evidence = collect_repository_evidence(repo.root, task)
    payload = evidence.to_dict()
    assert payload["history_touched_paths"] == sorted(payload["history_touched_paths"]) or set(
        payload["history_touched_paths"]
    ) == {"runtime/canx/foo/a.py", "SPEC.md"}
    assert "handoff" not in payload


@pytest.mark.parametrize("error_type", [AgentToolingError])
def test_verification_never_raises_on_a_dishonest_handoff(
    tmp_path: Path, error_type: type[Exception]
) -> None:
    repo, base, head, commits = reading_repo(tmp_path)
    task = task_for(repo, base)
    lying = handoff_for(
        task,
        head_sha=head,
        commits=commits,
        changed_files=("runtime/canx/foo/a.py",),
    )
    evidence = collect_repository_evidence(repo.root, task)
    blockers = verify_repository_evidence(lying, task, evidence)
    assert blockers, "a dishonest handoff must produce at least one blocker"
    assert all(isinstance(item, error_type) for item in blockers)


# ------------------------------- P1-2: ownership is what the history ever touched


def _transient_history(
    tmp_path: Path, *, shape: str
) -> tuple[SandboxRepository, str, str, tuple[str, ...]]:
    """A real branch whose ``SPEC.md`` edit is undone before head.

    ``net_changed_paths`` is only the owned delivery; ``SPEC.md`` reappears in
    ``history_touched_paths`` because a commit in the range really touched it.
    """
    repo = make_repository(tmp_path)
    base = repo.sha()
    repo.branch(TASK_BRANCH)
    if shape == "modify":
        repo.write("SPEC.md", "MUTATED\n")
    elif shape == "rename":
        repo.rename("SPEC.md", "SPEC.md.bak")
    elif shape == "delete":
        (repo.root / "SPEC.md").unlink()
    else:  # pragma: no cover - guarded by the parametrisation
        raise AssertionError(shape)
    repo.commit(f"{shape} SPEC.md")
    repo.write("SPEC.md", "frozen\n")
    leftover = repo.root / "SPEC.md.bak"
    if leftover.exists():
        leftover.unlink()
    repo.write("runtime/canx/foo/a.py", "a = 2\n")
    head = repo.commit("restore SPEC.md and deliver the owned change")
    return repo, base, head, repo.short_commits(base, head)


@pytest.mark.parametrize("shape", ["modify", "rename", "delete"])
def test_a_transient_protected_edit_escapes_the_net_diff_but_not_ownership(
    tmp_path: Path, shape: str
) -> None:
    """RED -> GREEN: modify/rename/delete SPEC.md, then restore it before head.

    The net tree diff no longer mentions ``SPEC.md``, so a net-diff-only
    ownership gate passed. Both commits still land on the integration branch
    under a merge, so ownership must inspect the history (FIX-2 §21-§25). The
    assertion is on the *evidence* layer, so it does not ride on the handoff's
    own reported list: even a handoff that reports only the net paths is caught.
    """
    repo, base, head, commits = _transient_history(tmp_path, shape=shape)
    task = task_for(repo, base)

    evidence = collect_repository_evidence(repo.root, task)
    assert evidence.net_changed_paths == ("runtime/canx/foo/a.py",)
    assert "SPEC.md" in evidence.history_touched_paths

    net_only = handoff_for(
        task,
        head_sha=head,
        commits=commits,
        changed_files=tuple(evidence.net_changed_paths),
    )
    blockers = verify_repository_evidence(net_only, task, evidence)
    codes = {blocker.code for blocker in blockers}
    assert "agent.ownership_violation" in codes, blockers
    ownership = next(blocker for blocker in blockers if blocker.code == "agent.ownership_violation")
    assert "SPEC.md" in ownership.details["offending"]

    readiness = evaluate_integration(
        net_only,
        task,
        config(),
        IntegrationContext.build((task,), config(), base),
        repository=repo.root,
    )
    assert not readiness.ready
    assert "agent.ownership_violation" in readiness.blockers


def test_a_net_diff_only_handoff_is_rejected_as_an_evidence_mismatch(tmp_path: Path) -> None:
    """A handoff describing only the final tree is not a faithful history report."""
    repo, base, head, commits = _transient_history(tmp_path, shape="modify")
    task = task_for(repo, base)
    net_only = handoff_for(
        task,
        head_sha=head,
        commits=commits,
        changed_files=("runtime/canx/foo/a.py",),
    )
    readiness = evaluate_integration(
        net_only,
        task,
        config(),
        IntegrationContext.build((task,), config(), base),
        repository=repo.root,
    )
    assert "agent.handoff_evidence_mismatch" in readiness.blockers


def test_a_rename_away_and_back_is_recorded_on_both_sides(tmp_path: Path) -> None:
    repo, base, _head, _commits = _transient_history(tmp_path, shape="rename")
    task = task_for(repo, base)
    evidence = collect_repository_evidence(repo.root, task)
    assert {"SPEC.md", "SPEC.md.bak"} <= set(evidence.history_touched_paths)


def test_build_handoff_uses_the_same_touched_path_definition_as_the_gate(
    tmp_path: Path,
) -> None:
    """Producer and consumer must not disagree about ``changed_files`` (§26)."""
    repo, base, _head, _commits = _transient_history(tmp_path, shape="modify")
    task = task_for(repo, base, allowed_paths=("runtime/canx/foo/**", "SPEC.md"), owner="main")

    handoff = build_handoff(
        repo.root,
        task,
        agent="main",
        tests=(TestResult(name="unit", command="python -m pytest -q", result="passed"),),
        ready_for_integration=True,
    )
    evidence = collect_repository_evidence(repo.root, task)
    assert tuple(sorted(handoff.changed_files)) == tuple(
        sorted(evidence.history_touched_paths)
    )

    readiness = evaluate_integration(
        handoff,
        task,
        config(),
        IntegrationContext.build((task,), config(), base),
        repository=repo.root,
    )
    assert "agent.handoff_evidence_mismatch" not in readiness.blockers
    assert readiness.ready, readiness.details
