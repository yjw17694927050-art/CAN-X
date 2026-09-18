"""Conflict classification: C0-C4, the auto-resolvable ceiling and serialisation."""

from __future__ import annotations

import pytest
from agent_tools_support import config, contract_ref, make_task

from tools.agent.conflicts import (
    AUTO_RESOLVABLE_CEILING,
    ConflictLevel,
    blocking_conflicts,
    classify_all,
    classify_pair,
    parallel_safe_pairs,
    requires_serial_review,
)
from tools.agent.contracts import RiskClass


def _pair(left_paths: tuple[str, ...], right_paths: tuple[str, ...], **kwargs: object):
    left = make_task(
        task_id="AGENT-02-A",
        owner="sub-a",
        branch="agent/AGENT-02-A-left",
        allowed_paths=left_paths,
        **kwargs,
    )
    right = make_task(
        task_id="AGENT-02-B",
        owner="sub-b",
        branch="agent/AGENT-02-B-right",
        allowed_paths=right_paths,
    )
    return classify_pair(left, right, config())


def test_disjoint_ownership_is_a_c0() -> None:
    conflict = _pair(("runtime/canx/alpha/**",), ("runtime/canx/beta/**",))
    assert conflict.level is ConflictLevel.C0
    assert conflict.auto_resolvable
    assert conflict.overlapping_paths == ()


def test_an_ordinary_shared_file_is_a_c1() -> None:
    conflict = _pair(("tools/agent/**",), ("tools/agent/validation.py",))
    assert conflict.level is ConflictLevel.C1
    assert conflict.auto_resolvable


def test_a_declared_shared_contract_is_a_c2() -> None:
    left = make_task(
        task_id="AGENT-02-A",
        owner="sub-a",
        branch="agent/AGENT-02-A-left",
        allowed_paths=("docs/engineering/handoff-contract.md",),
        shared_contracts=(contract_ref("docs/engineering/handoff-contract.md"),),
    )
    right = make_task(
        task_id="AGENT-02-B",
        owner="sub-b",
        branch="agent/AGENT-02-B-right",
        allowed_paths=("docs/engineering/handoff-contract.md",),
    )
    conflict = classify_pair(left, right, config())
    assert conflict.level is ConflictLevel.C2
    assert not conflict.auto_resolvable


def test_a_public_truth_overlap_is_a_c3() -> None:
    conflict = _pair(("runtime/canx/domain/**",), ("runtime/canx/domain/frame.py",))
    assert conflict.level is ConflictLevel.C3
    assert not conflict.auto_resolvable
    assert "runtime/canx/domain" in conflict.reason


def test_a_safety_overlap_outranks_everything_else() -> None:
    conflict = _pair(
        ("runtime/canx/safety/**", "runtime/canx/domain/**"),
        ("runtime/canx/safety/approval.py",),
    )
    assert conflict.level is ConflictLevel.C4
    assert not conflict.auto_resolvable


def test_the_auto_resolvable_ceiling_is_c1() -> None:
    assert AUTO_RESOLVABLE_CEILING == ConflictLevel.C1
    assert int(ConflictLevel.C0) < int(ConflictLevel.C1) < int(ConflictLevel.C2)
    assert int(ConflictLevel.C2) < int(ConflictLevel.C3) < int(ConflictLevel.C4)


def test_a_safety_critical_task_always_requires_serial_review() -> None:
    task = make_task(
        allowed_paths=("runtime/canx/alpha/**",), risk_class=RiskClass.SAFETY_CRITICAL
    )
    assert requires_serial_review(task, config())


def test_a_task_touching_a_safety_path_requires_serial_review() -> None:
    task = make_task(allowed_paths=("runtime/canx/safety/approval.py",))
    assert requires_serial_review(task, config())


def test_an_ordinary_task_does_not_require_serial_review() -> None:
    assert not requires_serial_review(make_task(allowed_paths=("docs/x.md",)), config())


def test_classify_all_covers_every_unordered_pair() -> None:
    tasks = (
        make_task(task_id="AGENT-02-A", owner="sub-a", branch="agent/AGENT-02-A-x"),
        make_task(task_id="AGENT-02-B", owner="sub-b", branch="agent/AGENT-02-B-x"),
        make_task(task_id="AGENT-02-C", owner="sub-c", branch="agent/AGENT-02-C-x"),
    )
    conflicts = classify_all(tasks, config())
    assert len(conflicts) == 3
    assert blocking_conflicts(tasks, config()) == ()


def test_parallel_safe_pairs_excludes_a_pair_that_must_be_serialised() -> None:
    alpha = make_task(task_id="AGENT-02-A", owner="sub-a", branch="agent/AGENT-02-A-x")
    beta = make_task(
        task_id="AGENT-02-B",
        owner="sub-b",
        branch="agent/AGENT-02-B-x",
        allowed_paths=("runtime/canx/domain/**",),
        risk_class=RiskClass.SAFETY_CRITICAL,
    )
    assert parallel_safe_pairs((alpha, beta), config()) == ()


def test_a_conflict_records_the_overlapping_patterns() -> None:
    conflict = _pair(("runtime/canx/alpha/**",), ("runtime/canx/alpha/**",))
    assert conflict.overlapping_paths == ("runtime/canx/alpha/** ~ runtime/canx/alpha/**",)
    assert conflict.to_dict()["level"] == "C1"


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (("a/b/**",), ("c/d/**",), ConflictLevel.C0),
        (("a/b/c.py",), ("a/b/**",), ConflictLevel.C1),
        (("runtime/canx/domain/a.py",), ("runtime/canx/domain/a.py",), ConflictLevel.C3),
        (("runtime/canx/safety/a.py",), ("runtime/canx/safety/a.py",), ConflictLevel.C4),
    ],
)
def test_the_classification_table(
    left: tuple[str, ...], right: tuple[str, ...], expected: ConflictLevel
) -> None:
    assert _pair(left, right).level is expected
