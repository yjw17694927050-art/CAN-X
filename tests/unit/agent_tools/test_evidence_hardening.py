"""FIX-2 hardening: fail closed without a plan, and one-to-one commit evidence.

Two holes are pinned here (AGENT-01-FIX-2 §29-§30):

* a context without an orchestration plan could skip the conflict gate and still
  report ``ready: true``;
* prefix membership plus equal counts let two tokens that both prefix the same
  commit hide a second, unrepresented commit.

The evidence comparison is a pure function, so it is exercised through
``verify_repository_evidence`` with a hand-built evidence value. The *final*
verdict is deliberately not reachable that way any more: since AGENT-01-FIX-4
only a real repository can produce ``ready: true``, and that path lives in
``tests/integration/test_agent_final_authority.py``.
"""

from __future__ import annotations

from pathlib import Path

from agent_tools_support import BASE_SHA, HEAD_SHA, config, make_handoff, make_task

from tools.agent.errors import AgentToolingError
from tools.agent.evidence import SOURCE_WORKTREE, RepositoryEvidence
from tools.agent.lifecycle import TaskStatus
from tools.agent.validation import (
    IntegrationContext,
    evaluate_integration,
    verify_repository_evidence,
)

CHANGED = ("runtime/canx/foo/a.py",)
DOMAIN = ("runtime/canx/domain/**",)
DOMAIN_CHANGED = ("runtime/canx/domain/x.py",)


def _task(**overrides: object):
    values: dict[str, object] = {
        "status": TaskStatus.HANDOFF_READY,
        "allowed_paths": ("runtime/canx/foo/**",),
    }
    values.update(overrides)
    return make_task(**values)


def _handoff(task, *, commits: tuple[str, ...], changed: tuple[str, ...] = CHANGED):
    return make_handoff(
        task_id=task.task_id,
        branch=task.branch,
        base_sha=task.base_sha,
        head_sha=HEAD_SHA,
        commits=commits,
        changed_files=changed,
        dependencies=task.dependencies,
    )


def _evidence(task, *, commits: tuple[str, ...], changed: tuple[str, ...] = CHANGED):
    return RepositoryEvidence(
        repository_root=Path("."),
        source=SOURCE_WORKTREE,
        worktree_path=Path("."),
        branch=task.branch,
        base_sha=task.base_sha,
        head_sha=HEAD_SHA,
        base_is_ancestor=True,
        clean=True,
        net_changed_paths=changed,
        history_touched_paths=changed,
        commits=commits,
        diff_records=(("M", changed),),
    )


def _compare(
    task,
    *,
    repository_evidence: RepositoryEvidence,
    commits: tuple[str, ...] = (f"{HEAD_SHA[:7]} work",),
    changed: tuple[str, ...] = CHANGED,
) -> tuple[AgentToolingError, ...]:
    """The pure handoff-versus-evidence comparison, with no Git involved."""
    return verify_repository_evidence(
        _handoff(task, commits=commits, changed=changed), task, repository_evidence
    )


def _commit_reason(blockers: tuple[AgentToolingError, ...]) -> str:
    return next(
        str(blocker.details["reason"])
        for blocker in blockers
        if blocker.code == "agent.handoff_evidence_mismatch"
    )


# ------------------------------------------- §29 the plan is required to be ready

def test_a_context_without_an_orchestration_plan_can_never_be_ready() -> None:
    """RED -> GREEN: ``include_plan=False`` used to skip the conflict gate."""
    task = _task()
    readiness = evaluate_integration(
        _handoff(task, commits=(f"{HEAD_SHA[:7]} work",)),
        task,
        config(),
        IntegrationContext.build((task,), config(), BASE_SHA, include_plan=False),
    )
    assert not readiness.ready
    assert "agent.integration_context_incomplete" in readiness.blockers


# ------------------------------------------------- §30 one-to-one commit evidence


def test_a_duplicate_commit_token_is_rejected() -> None:
    task = _task()
    blockers = _compare(
        task,
        repository_evidence=_evidence(task, commits=(HEAD_SHA,)),
        commits=(f"{HEAD_SHA[:7]} first", f"{HEAD_SHA[:7]} again"),
    )
    assert "duplicate" in _commit_reason(blockers)


def test_two_tokens_mapping_to_one_commit_cannot_hide_another() -> None:
    """RED -> GREEN: prefix membership + equal counts accepted this mapping."""
    first = "abc111" + "0" * 34
    second = "def222" + "0" * 34
    task = _task()
    blockers = _compare(
        task,
        repository_evidence=_evidence(task, commits=(first, second)),
        commits=(f"{first[:4]} first", f"{first[:6]} second"),
    )
    assert "resolve to the same commit" in _commit_reason(blockers)


def test_an_ambiguous_commit_prefix_is_rejected() -> None:
    first = "abc111" + "0" * 34
    second = "abc112" + "0" * 34
    task = _task()
    blockers = _compare(
        task,
        repository_evidence=_evidence(task, commits=(first, second)),
        commits=("abc11 work",),
    )
    assert "ambiguous" in _commit_reason(blockers)


def test_an_unknown_commit_token_is_rejected() -> None:
    task = _task()
    blockers = _compare(
        task,
        repository_evidence=_evidence(task, commits=("abc111" + "0" * 34,)),
        commits=("deadbee no such commit",),
    )
    assert "matches no actual commit" in _commit_reason(blockers)


def test_an_unrepresented_commit_is_rejected() -> None:
    first = "abc111" + "0" * 34
    second = "def222" + "0" * 34
    task = _task()
    blockers = _compare(
        task,
        repository_evidence=_evidence(task, commits=(first, second)),
        commits=(f"{first[:7]} only",),
    )
    assert "not every actual commit is represented" in _commit_reason(blockers)


def test_a_bijective_commit_set_is_accepted() -> None:
    first = "abc111" + "0" * 34
    second = "def222" + "0" * 34
    task = _task()
    blockers = _compare(
        task,
        repository_evidence=_evidence(task, commits=(first, second)),
        commits=(f"{first[:7]} one", f"{second[:7]} two"),
    )
    assert blockers == ()


# --------------------------------- §12 a dead conflict owner is a visible re-plan


def test_a_terminated_conflict_owner_is_visible_as_a_replan_requirement() -> None:
    owner = make_task(
        task_id="AGENT-02-B",
        owner="sub-b",
        branch="agent/AGENT-02-B-owner",
        allowed_paths=DOMAIN,
        status=TaskStatus.FAILED,
    )
    waiter = _task(
        task_id="AGENT-02-D",
        owner="sub-d",
        branch="agent/AGENT-02-D-waiter",
        allowed_paths=DOMAIN,
    )
    readiness = evaluate_integration(
        _handoff(waiter, commits=(f"{HEAD_SHA[:7]} work",), changed=DOMAIN_CHANGED),
        waiter,
        config(),
        IntegrationContext.build((owner, waiter), config(), BASE_SHA),
    )
    assert not readiness.ready
    conflict = next(
        item for item in readiness.details if item["code"] == "agent.conflict_rejected"
    )
    assert conflict["details"]["replan_required"] is True
    assert conflict["details"]["replan_required_by"] == ["AGENT-02-B"]
