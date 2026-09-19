"""The native shared-worktree execution mode (ADR-0003, V0.3-12-FIX-1).

AGENT-01 modelled a Sub-Agent as a *branch plus a worktree*: the contract froze
``branch`` and ``worktree``, and the final gate proved the delivery by resolving
that worktree back through Git. The first real AGENT-02 pilot ran the host
Harness in its default mode, where workers execute inside the **Main Agent's**
single worktree — no task branch is created and no task worktree exists. The
contract was therefore free to declare a branch that Git could never resolve,
and every handoff failed closed with ``agent.git_state_error``. That is a real
architectural gap, not an operator mistake.

``execution_mode: "native-shared"`` closes it without weakening anything:

* the task declares **no** ``branch`` and **no** ``worktree``, so there is
  nothing to be wrong — the integration coordinates are read from the repository;
* the delivery is a real commit range on the integration branch, and
  ``history_touched_paths`` over that range stays the ownership authority;
* ``integration_paths`` names the repository-relative files (exact paths, never
  globs) that the **Main Agent** reviewed and permitted to coexist inside this
  task's delivery *range* (for example a cross-boundary test), and it can never
  reach a protected, public-truth or safety path, nor another task's surface. It
  widens what the range may contain; it is not evidence of who wrote each file,
  which a shared worktree cannot record (V0.3-12-FIX-2).

Every refusal below is asserted on the **error code**, so a test cannot pass on
"some error happened to be raised".
"""

from __future__ import annotations

import json

import pytest
from agent_tools_support import (
    BASE_SHA,
    EXAMPLE_DIR,
    HEAD_SHA,
    REPO_ROOT,
    config,
    make_handoff,
    make_task,
)

from tools.agent.conflicts import classify_pair
from tools.agent.contracts import ExecutionMode, TaskContract, load_task
from tools.agent.errors import (
    OwnershipViolationError,
    PathInvalidError,
    ProtectedPathConflictError,
    TaskInvalidError,
)
from tools.agent.paths import patterns_overlap
from tools.agent.validation import ownership_surface, validate_handoff, validate_task

#: Repo-relative paths that sit in each protected class, per `.agent/config.json`.
PROTECTED_PATH = "SPEC.md"
PUBLIC_TRUTH_PATH = "runtime/canx/api/project.py"
SAFETY_PATH = "runtime/canx/safety/policy.py"


def native_shared(**overrides: object) -> TaskContract:
    """A native-shared task with a valid, disjoint ownership surface."""
    base: dict[str, object] = {
        "task_id": "V0.3-12-A",
        "owner": "sub-a",
        "execution_mode": ExecutionMode.NATIVE_SHARED,
        "branch": None,
        "worktree": None,
        "allowed_paths": ("runtime/canx/foo/**",),
    }
    base.update(overrides)
    return make_task(**base)


# ---------------------------------------------------------------------------
# Backwards compatibility: the AGENT-01 mode is untouched and still the default.
# ---------------------------------------------------------------------------


def test_the_default_execution_mode_is_the_isolated_worktree() -> None:
    task = make_task()

    assert task.execution_mode is ExecutionMode.ISOLATED_WORKTREE
    assert task.integration_paths == ()
    validate_task(task, config())


def test_an_isolated_task_still_requires_a_branch_and_a_worktree() -> None:
    with pytest.raises(TaskInvalidError) as raised:
        validate_task(make_task(branch=None, worktree=None), config())

    assert raised.value.code == "agent.task_invalid"


# ---------------------------------------------------------------------------
# The native-shared contract shape.
# ---------------------------------------------------------------------------


def test_a_native_shared_task_declares_no_branch_and_no_worktree() -> None:
    task = native_shared()

    assert task.branch is None
    assert task.worktree is None
    validate_task(task, config())


def test_a_native_shared_task_may_not_declare_a_branch() -> None:
    """A branch Git will never resolve is exactly the defect this mode removes."""

    with pytest.raises(TaskInvalidError) as raised:
        validate_task(native_shared(branch="agent/V0.3-12-A-something"), config())

    assert raised.value.code == "agent.task_invalid"


def test_a_native_shared_task_may_not_declare_a_worktree() -> None:
    with pytest.raises(TaskInvalidError) as raised:
        validate_task(native_shared(worktree=".worktrees/v0.3-12-a"), config())

    assert raised.value.code == "agent.task_invalid"


def test_an_isolated_task_may_not_declare_integration_paths() -> None:
    """Integration paths exist only where the Main Agent shares the worktree."""

    with pytest.raises(TaskInvalidError) as raised:
        validate_task(
            make_task(integration_paths=("apps/desktop/src/orchestration/x.test.ts",)),
            config(),
        )

    assert raised.value.code == "agent.task_invalid"


# ---------------------------------------------------------------------------
# A worker may not reach a protected / public-truth / safety path through the
# back door of `integration_paths`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected_code"),
    [
        (PROTECTED_PATH, "agent.protected_path_conflict"),
        ("docs/PROJECT_STATE.md", "agent.protected_path_conflict"),
        (PUBLIC_TRUTH_PATH, "agent.protected_path_conflict"),
        (SAFETY_PATH, "agent.protected_path_conflict"),
    ],
)
def test_integration_paths_may_not_reach_a_governed_path(path: str, expected_code: str) -> None:
    with pytest.raises(ProtectedPathConflictError) as raised:
        validate_task(native_shared(integration_paths=(path,)), config())

    assert raised.value.code == expected_code


def test_integration_paths_may_not_reach_a_governed_path_through_a_glob() -> None:
    """Pattern versus pattern: `**` matches no literal guard but reaches all of them."""

    with pytest.raises(ProtectedPathConflictError) as raised:
        validate_task(native_shared(integration_paths=("**",)), config())

    assert raised.value.code == "agent.protected_path_conflict"


def test_integration_paths_may_not_overlap_the_tasks_own_forbidden_surface() -> None:
    with pytest.raises(TaskInvalidError) as raised:
        validate_task(
            native_shared(
                allowed_paths=("runtime/canx/foo/**",),
                forbidden_paths=("runtime/canx/foo/private/**",),
                integration_paths=("runtime/canx/foo/private/notes.md",),
            ),
            config(),
        )

    assert raised.value.code == "agent.task_invalid"


def test_integration_paths_must_be_repository_relative() -> None:
    with pytest.raises(PathInvalidError):
        validate_task(native_shared(integration_paths=("../outside.md",)), config())


@pytest.mark.parametrize(
    "glob",
    [
        "apps/desktop/src/orchestration/*.test.ts",
        "apps/desktop/src/orchestration/**",
        "apps/desktop/src/orchestration/project-open?.integration.test.ts",
    ],
)
def test_integration_paths_must_be_exact_file_paths_not_globs(glob: str) -> None:
    """A glob would let a delivery range widen past the one file it named.

    The field says "these named Main-Agent paths may coexist in this delivery
    range"; `apps/desktop/src/**` says something much larger. It is refused even
    when the glob happens to reach nothing governed, because the point is what
    the declaration *permits*, not what it touched this time.
    """

    with pytest.raises(TaskInvalidError) as raised:
        validate_task(native_shared(integration_paths=(glob,)), config())

    assert raised.value.code == "agent.task_invalid"


def test_a_valid_integration_path_is_accepted() -> None:
    task = native_shared(
        integration_paths=("apps/desktop/src/orchestration/project-open.integration.test.ts",)
    )

    validate_task(task, config())

    assert task.integration_paths == (
        "apps/desktop/src/orchestration/project-open.integration.test.ts",
    )


# ---------------------------------------------------------------------------
# The ownership surface the worker is judged against.
# ---------------------------------------------------------------------------


def test_the_ownership_surface_is_the_allowed_surface_for_an_isolated_task() -> None:
    task = make_task(allowed_paths=("runtime/canx/foo/**",))

    assert ownership_surface(task) == ("runtime/canx/foo/**",)


def test_the_ownership_surface_includes_integration_paths_for_a_native_shared_task() -> None:
    task = native_shared(integration_paths=("apps/desktop/src/orchestration/p.test.ts",))

    assert ownership_surface(task) == (
        "runtime/canx/foo/**",
        "apps/desktop/src/orchestration/p.test.ts",
    )


# ---------------------------------------------------------------------------
# A handoff in native-shared mode is not compared against a declared branch.
# ---------------------------------------------------------------------------


def test_a_native_shared_handoff_is_not_compared_to_a_task_branch() -> None:
    """The branch relation is proved against the repository, not against a claim."""

    task = native_shared(required_tests=("unit",))
    handoff = make_handoff(
        task_id="V0.3-12-A",
        agent="sub-a",
        branch="feature/v0.3-12",
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        changed_files=("runtime/canx/foo/a.py",),
    )

    validate_handoff(handoff, task, config())


def test_a_native_shared_handoff_still_obeys_the_ownership_surface() -> None:
    task = native_shared(required_tests=("unit",))
    handoff = make_handoff(
        task_id="V0.3-12-A",
        agent="sub-a",
        branch="feature/v0.3-12",
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        changed_files=("runtime/canx/bar/b.py",),
    )

    with pytest.raises(OwnershipViolationError) as raised:
        validate_handoff(handoff, task, config())

    assert raised.value.code == "agent.ownership_violation"


def test_a_native_shared_handoff_is_accepted_for_a_main_agent_integration_path() -> None:
    task = native_shared(
        required_tests=("unit",),
        integration_paths=("apps/desktop/src/orchestration/p.test.ts",),
    )
    handoff = make_handoff(
        task_id="V0.3-12-A",
        agent="sub-a",
        branch="feature/v0.3-12",
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        changed_files=(
            "apps/desktop/src/orchestration/p.test.ts",
            "runtime/canx/foo/a.py",
        ),
    )

    validate_handoff(handoff, task, config())


# ---------------------------------------------------------------------------
# `integration_paths` is part of the conflict surface.
# ---------------------------------------------------------------------------


def test_an_integration_path_overlapping_another_task_is_no_longer_c0() -> None:
    """`integration_paths` is not a conflict-free hole: a delivery that reaches
    another task's surface must be *visible* to the classifier, not read as C0."""

    left = native_shared(task_id="V0.3-12-A", allowed_paths=("runtime/canx/foo/**",))
    right = make_task(
        task_id="V0.3-12-C",
        owner="sub-c",
        branch="agent/V0.3-12-C-flow",
        worktree=".worktrees/v0.3-12-c",
        allowed_paths=("apps/desktop/src/orchestration/**",),
    )
    overlapping = native_shared(
        task_id="V0.3-12-A",
        allowed_paths=("runtime/canx/foo/**",),
        integration_paths=("apps/desktop/src/orchestration/p.test.ts",),
    )

    assert classify_pair(left, right, config()).level.name == "C0"
    conflict = classify_pair(overlapping, right, config())
    assert conflict.level.name != "C0", "the overlap must not be invisible"
    assert conflict.overlapping_paths, "the overlapping patterns must be named"


def test_the_shipped_task_schema_declares_the_execution_mode() -> None:
    schema = json.loads(
        (REPO_ROOT / ".agent/schemas/task.schema.json").read_text(encoding="utf-8")
    )

    assert "execution_mode" in schema["properties"]
    assert "integration_paths" in schema["properties"]
    assert "branch" not in schema["required"]
    assert "worktree" not in schema["required"]
    assert set(schema["properties"]["execution_mode"]["enum"]) == {
        "isolated-worktree",
        "native-shared",
    }


def test_every_shipped_example_still_parses_under_the_new_contract() -> None:
    example = load_task(EXAMPLE_DIR / "task.example.json")

    assert example.execution_mode is ExecutionMode.ISOLATED_WORKTREE
    validate_task(example, config())


def test_patterns_overlap_is_used_for_the_guard_not_a_literal_match() -> None:
    """A guard that fell back to literal matching would let `**` straight through."""

    assert patterns_overlap("**", PROTECTED_PATH) is True
    assert patterns_overlap("docs/**", "docs/PROJECT_STATE.md") is True
