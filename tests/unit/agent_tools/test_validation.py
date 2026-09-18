"""Task, handoff and integration validators.

The ownership gate and the stale-base gate are the two orchestration contracts
this suite pins: a handoff that changes an unowned file, or one built on a base
that has moved, must never be integrated.
"""

from __future__ import annotations

import pytest
from agent_tools_support import BASE_SHA, HEAD_SHA, OTHER_SHA, config, make_handoff, make_task

from tools.agent.contracts import ContractRef, TaskContract, TestResult
from tools.agent.errors import (
    BaseStaleError,
    BranchInvalidError,
    BranchMismatchError,
    HandoffInvalidError,
    OwnershipViolationError,
    ProtectedPathConflictError,
    TaskInvalidError,
)
from tools.agent.validation import (
    assert_frozen_unchanged,
    check_base,
    evaluate_integration,
    ownership_violations,
    validate_handoff,
    validate_task,
)


def _ready_task(**overrides: object) -> TaskContract:
    return make_task(**overrides)


# --------------------------------------------------------------------------- task


def test_a_well_formed_task_validates() -> None:
    validate_task(_ready_task(), config())


def test_the_example_style_task_validates() -> None:
    validate_task(
        make_task(
            allowed_paths=("tools/agent/validation.py", "tests/unit/agent_tools/**"),
            shared_contracts=(
                ContractRef(path=".agent/schemas/handoff.schema.json", sha=BASE_SHA),
            ),
        ),
        config(),
    )


@pytest.mark.parametrize("task_id", ["has space", "trailing-", "-leading", "", "a" * 100])
def test_an_invalid_task_id_is_refused(task_id: str) -> None:
    with pytest.raises(TaskInvalidError):
        validate_task(_ready_task(task_id=task_id), config())


def test_a_sub_agent_may_not_own_a_protected_path() -> None:
    task = _ready_task(allowed_paths=("SPEC.md",))
    with pytest.raises(ProtectedPathConflictError) as raised:
        validate_task(task, config())
    assert raised.value.code == "agent.protected_path_conflict"
    assert raised.value.details["protected"] == "SPEC.md"


def test_the_main_agent_may_own_a_protected_path() -> None:
    task = make_task(
        task_id="AGENT-02-M",
        owner="main",
        branch="maintenance/agent-02-protocol",
        allowed_paths=("SPEC.md",),
    )
    validate_task(task, config())


def test_a_path_that_is_both_allowed_and_forbidden_is_refused() -> None:
    task = _ready_task(
        allowed_paths=("tools/agent/**",), forbidden_paths=("tools/agent/validation.py",)
    )
    with pytest.raises(TaskInvalidError):
        validate_task(task, config())


def test_a_worktree_outside_the_worktrees_directory_is_refused() -> None:
    with pytest.raises(TaskInvalidError) as raised:
        validate_task(_ready_task(worktree="src/task-a"), config())
    assert raised.value.details["field"] == "worktree"


@pytest.mark.parametrize(
    "branch",
    [
        "feature/whatever",
        "agent/AGENT-99-Z-other-task",
        "agent/AGENT-02-B-Not_Slug",
        "agent/AGENT-02-B-",
        "..",
    ],
)
def test_a_sub_agent_branch_that_breaks_the_convention_is_refused(branch: str) -> None:
    with pytest.raises((BranchInvalidError, TaskInvalidError)):
        validate_task(_ready_task(branch=branch), config())


def test_the_main_agent_may_use_a_maintenance_branch() -> None:
    task = make_task(
        task_id="AGENT-02-M",
        owner="main",
        branch="maintenance/agent-02-protocol",
    )
    validate_task(task, config())


@pytest.mark.parametrize("sha", ["abc", "A" * 40, "a" * 39, "a" * 41, "z" * 40])
def test_a_malformed_base_sha_is_refused(sha: str) -> None:
    with pytest.raises(TaskInvalidError):
        validate_task(_ready_task(base_sha=sha), config())


def test_a_task_that_owns_nothing_is_refused() -> None:
    with pytest.raises(TaskInvalidError) as raised:
        validate_task(_ready_task(allowed_paths=()), config())
    assert raised.value.details["field"] == "allowed_paths"


def test_a_task_without_required_tests_is_refused() -> None:
    with pytest.raises(TaskInvalidError):
        validate_task(_ready_task(required_tests=()), config())


def test_an_invalid_owner_identity_is_refused() -> None:
    with pytest.raises(TaskInvalidError):
        validate_task(_ready_task(owner="sub agent!"), config())


# ------------------------------------------------------------------------ handoff


def test_a_handoff_that_stays_inside_its_ownership_surface_validates() -> None:
    validate_handoff(make_handoff(), _ready_task(), config())


def test_a_handoff_that_changes_a_file_outside_allowed_paths_is_refused() -> None:
    """RED -> GREEN #1.

    Before the ownership gate existed, a handoff could report a self-assessed
    ``ownership_compliance: true`` and change ``SPEC.md`` while its task owned
    only ``runtime/canx/foo/**``; the validator accepted it. The change is
    re-derived from ``changed_files`` now, so the claim cannot buy compliance.
    """
    task = _ready_task(allowed_paths=("runtime/canx/foo/**",))
    handoff = make_handoff(
        changed_files=("runtime/canx/foo/a.py", "SPEC.md"),
        ownership_compliance=True,
    )
    with pytest.raises(OwnershipViolationError) as raised:
        validate_handoff(handoff, task, config())
    assert raised.value.code == "agent.ownership_violation"
    assert raised.value.details["offending"] == ["SPEC.md"]


def test_a_handoff_that_changes_a_forbidden_path_is_refused() -> None:
    task = _ready_task(
        allowed_paths=("tools/agent/**",), forbidden_paths=("tools/agent/worktree.py",)
    )
    handoff = make_handoff(changed_files=("tools/agent/worktree.py",))
    with pytest.raises(OwnershipViolationError):
        validate_handoff(handoff, task, config())


def test_the_ownership_helper_reports_every_offending_path() -> None:
    task = _ready_task(allowed_paths=("runtime/canx/foo/**",))
    assert ownership_violations(task, ("runtime/canx/foo/a.py", "b.py", "c.py")) == ("b.py", "c.py")


def test_a_handoff_on_a_different_branch_is_refused() -> None:
    handoff = make_handoff(branch="agent/AGENT-02-B-other")
    with pytest.raises(BranchMismatchError) as raised:
        validate_handoff(handoff, _ready_task(), config())
    assert raised.value.code == "agent.branch_mismatch"


def test_a_handoff_for_another_task_is_refused() -> None:
    with pytest.raises(HandoffInvalidError):
        validate_handoff(make_handoff(task_id="AGENT-02-Z"), _ready_task(), config())


def test_a_handoff_with_no_commit_is_refused() -> None:
    with pytest.raises(HandoffInvalidError):
        validate_handoff(make_handoff(head_sha=BASE_SHA), _ready_task(), config())


def test_a_handoff_reporting_no_tests_is_refused() -> None:
    with pytest.raises(HandoffInvalidError) as raised:
        validate_handoff(make_handoff(tests=()), _ready_task(), config())
    assert raised.value.details["field"] == "tests"


def test_a_handoff_ready_without_its_required_tests_is_refused() -> None:
    handoff = make_handoff(tests=(TestResult(name="lint", command="ruff", result="passed"),))
    with pytest.raises(HandoffInvalidError) as raised:
        validate_handoff(handoff, _ready_task(required_tests=("unit",)), config())
    assert raised.value.details["missing"] == ["unit"]


def test_a_handoff_ready_on_a_failed_test_is_refused() -> None:
    handoff = make_handoff(
        tests=(TestResult(name="unit", command="pytest", result="failed"),)
    )
    with pytest.raises(HandoffInvalidError):
        validate_handoff(handoff, _ready_task(), config())


def test_a_handoff_declaring_non_compliance_may_not_be_ready() -> None:
    handoff = make_handoff(ownership_compliance=False)
    with pytest.raises(HandoffInvalidError):
        validate_handoff(handoff, _ready_task(), config())


def test_a_not_ready_handoff_may_still_report_a_failure_honestly() -> None:
    handoff = make_handoff(
        tests=(TestResult(name="unit", command="pytest", result="not_run"),),
        ownership_compliance=False,
        ready_for_integration=False,
    )
    validate_handoff(handoff, _ready_task(required_tests=("unit",)), config())


# --------------------------------------------------------------------- base / gate


def test_a_handoff_based_on_the_current_integration_head_is_accepted() -> None:
    check_base(make_handoff(base_sha=BASE_SHA), BASE_SHA)


def test_a_handoff_based_on_a_stale_commit_is_refused() -> None:
    """RED -> GREEN #2.

    Before the stale-base gate existed, ``check_base`` validated the shape of the
    two shas and then returned; a handoff based on an obsolete ``main`` was
    integrated as if it had been built on the current one. The comparison is the
    whole point, so it is what this pins.
    """
    with pytest.raises(BaseStaleError) as raised:
        check_base(make_handoff(base_sha=BASE_SHA), OTHER_SHA)
    assert raised.value.code == "agent.base_stale"
    assert raised.value.details["integration_head"] == OTHER_SHA


def test_check_base_still_validates_the_shape_of_its_inputs() -> None:
    with pytest.raises(HandoffInvalidError):
        check_base(make_handoff(base_sha="not-a-sha"), BASE_SHA)
    with pytest.raises(TaskInvalidError):
        check_base(make_handoff(base_sha=BASE_SHA), "not-a-sha")


def test_integration_readiness_collects_every_blocker_not_just_the_first() -> None:
    task = _ready_task(allowed_paths=("runtime/canx/foo/**",))
    handoff = make_handoff(changed_files=("SPEC.md",), base_sha=BASE_SHA)
    readiness = evaluate_integration(handoff, task, config(), OTHER_SHA)
    assert not readiness.ready
    assert set(readiness.blockers) == {"agent.ownership_violation", "agent.base_stale"}


def test_integration_readiness_is_green_for_a_clean_handoff_on_the_current_head() -> None:
    readiness = evaluate_integration(make_handoff(), _ready_task(), config(), BASE_SHA)
    assert readiness.ready
    assert readiness.blockers == ()
    assert readiness.to_dict()["task_id"] == "AGENT-02-B"


def test_integration_readiness_reports_a_handoff_that_does_not_claim_readiness() -> None:
    handoff = make_handoff(ready_for_integration=False)
    readiness = evaluate_integration(handoff, _ready_task(), config(), BASE_SHA)
    assert not readiness.ready
    assert "agent.handoff_invalid" in readiness.blockers


def test_a_clean_handoff_on_the_previous_head_is_not_stale_when_the_head_matched() -> None:
    handoff = make_handoff(base_sha=OTHER_SHA)
    check_base(handoff, OTHER_SHA)


# ----------------------------------------------------------------------- revision


def test_changing_a_frozen_field_without_a_revision_bump_is_refused() -> None:
    before = _ready_task()
    after = _ready_task(objective="a different objective", revision=1)
    with pytest.raises(TaskInvalidError) as raised:
        assert_frozen_unchanged(before, after)
    assert raised.value.details["changed"] == ["objective"]


def test_changing_a_frozen_field_with_a_revision_bump_is_allowed() -> None:
    before = _ready_task()
    after = _ready_task(objective="a different objective", revision=2)
    assert_frozen_unchanged(before, after)


def test_changing_only_status_never_needs_a_revision_bump() -> None:
    before = _ready_task()
    after = _ready_task(status="IN_PROGRESS")
    assert_frozen_unchanged(before, after)


def test_the_head_sha_fixture_differs_from_the_base_sha() -> None:
    assert HEAD_SHA != BASE_SHA
    assert len(HEAD_SHA) == 40
