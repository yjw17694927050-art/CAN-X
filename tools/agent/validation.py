"""Validators for task contracts, handoffs and integration readiness.

Three gates, in the order the protocol applies them:

```text
validate_task       the assignment is well formed and owns something real
validate_handoff    the delivered work stayed inside that ownership surface
check_base          the delivery is still based on the current integration head
evaluate_integration  all of the above, collected into a single verdict
```

``validate_handoff`` re-derives ownership compliance from ``changed_files``
rather than trusting the ``ownership_compliance`` flag a handoff reports about
itself - a self-assessment is not evidence (AGENT-01 §7, §20, §56).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from tools.agent.config import AgentConfig
from tools.agent.contracts import (
    BRANCH_RE,
    BRANCH_SLUG_RE,
    FROZEN_FIELDS,
    SHA_RE,
    TASK_ID_RE,
    HandoffContract,
    TaskContract,
)
from tools.agent.errors import (
    AgentToolingError,
    BaseStaleError,
    BranchInvalidError,
    BranchMismatchError,
    HandoffInvalidError,
    OwnershipViolationError,
    ProtectedPathConflictError,
    TaskInvalidError,
)
from tools.agent.paths import (
    is_within,
    matching_pattern,
    normalize_repo_path,
    normalize_repo_pattern,
    patterns_overlap,
)

_OWNER_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def _validate_sha(
    value: str, *, field: str, error: type[AgentToolingError] = TaskInvalidError
) -> None:
    if not SHA_RE.match(value):
        raise error(
            f"{field} must be a full 40-character lowercase hex commit id",
            details={"field": field, "value": value},
        )


def _validate_task_id(task_id: str) -> None:
    if not TASK_ID_RE.match(task_id):
        raise TaskInvalidError(
            f"invalid task id: {task_id!r}",
            details={"field": "task_id", "value": task_id},
        )


def _validate_owner(owner: str) -> None:
    if not _OWNER_RE.match(owner):
        raise TaskInvalidError(
            f"invalid owner identity: {owner!r}",
            details={"field": "owner", "value": owner},
        )


def _validate_branch_name(
    branch: str, *, task_id: str, owner: str, config: AgentConfig
) -> None:
    """A sub-agent branch must be ``<prefix><task-id>-<slug>`` (AGENT-01 §11, §33)."""
    if (
        not BRANCH_RE.match(branch)
        or ".." in branch
        or branch.startswith("/")
        or branch.endswith("/")
        or branch.endswith(".lock")
        or "//" in branch
    ):
        raise BranchInvalidError(
            f"branch is not a valid git branch name: {branch!r}",
            details={"field": "branch", "value": branch},
        )
    if config.is_main_agent(owner):
        return
    prefix = config.branch_prefix
    if not branch.startswith(prefix):
        raise BranchInvalidError(
            f"sub-agent branch must start with {prefix!r}: {branch!r}",
            details={"field": "branch", "value": branch, "prefix": prefix},
        )
    remainder = branch[len(prefix) :]
    if not remainder.startswith(f"{task_id}-"):
        raise BranchInvalidError(
            f"branch must encode its task id ({task_id!r}): {branch!r}",
            details={"field": "branch", "value": branch, "task_id": task_id},
        )
    slug = remainder[len(task_id) + 1 :]
    if not BRANCH_SLUG_RE.match(slug):
        raise BranchInvalidError(
            f"branch slug must be lower-case kebab-case: {slug!r}",
            details={"field": "branch", "value": branch, "slug": slug},
        )


def _normalized_paths(raw: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    return tuple(normalize_repo_path(item, field=field) for item in raw)


def _normalized_patterns(raw: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    return tuple(normalize_repo_pattern(item, field=field) for item in raw)


def validate_task(task: TaskContract, config: AgentConfig) -> None:
    """Validate a task contract in isolation (AGENT-01 §15, §19, §36)."""
    _validate_task_id(task.task_id)
    if task.schema_version != 1:
        raise TaskInvalidError(
            "task.schema_version must be 1",
            details={"field": "schema_version", "value": repr(task.schema_version)},
        )
    _validate_owner(task.owner)
    _validate_sha(task.base_sha, field="base_sha")
    _validate_branch_name(
        task.branch, task_id=task.task_id, owner=task.owner, config=config
    )
    worktree = normalize_repo_path(task.worktree, field="worktree")
    if not is_within(worktree, config.worktrees_dir):
        raise TaskInvalidError(
            f"worktree must live under {config.worktrees_dir!r}",
            details={"field": "worktree", "value": task.worktree},
        )
    if task.revision < 1:
        raise TaskInvalidError(
            "task.revision must be >= 1",
            details={"field": "revision", "value": task.revision},
        )
    if not task.allowed_paths:
        raise TaskInvalidError(
            "task.allowed_paths must not be empty - an unowned task owns nothing",
            details={"field": "allowed_paths"},
        )
    allowed = _normalized_patterns(task.allowed_paths, field="allowed_paths")
    forbidden = _normalized_patterns(task.forbidden_paths, field="forbidden_paths")
    for pattern in allowed:
        for blocked in forbidden:
            if patterns_overlap(pattern, blocked):
                raise TaskInvalidError(
                    f"path {pattern!r} is both allowed and forbidden",
                    details={"field": "allowed_paths", "value": pattern, "forbidden": blocked},
                )
    for pattern in allowed:
        guard = config.protected_match(pattern)
        if guard is not None and not config.is_main_agent(task.owner):
            raise ProtectedPathConflictError(
                f"task {task.task_id} may not own protected path {pattern!r}",
                details={
                    "task_id": task.task_id,
                    "owner": task.owner,
                    "path": pattern,
                    "protected": guard,
                },
            )
    for ref in task.shared_contracts:
        normalize_repo_path(ref.path, field="shared_contracts[].path")
        if ref.sha is not None:
            _validate_sha(ref.sha, field="shared_contracts[].sha")
    for dependency in task.dependencies:
        _validate_task_id(dependency)
    if not task.required_tests:
        raise TaskInvalidError(
            "task.required_tests must name at least one test",
            details={"field": "required_tests"},
        )
    if not task.acceptance_criteria:
        raise TaskInvalidError(
            "task.acceptance_criteria must not be empty",
            details={"field": "acceptance_criteria"},
        )
    if not task.handoff_requirements:
        raise TaskInvalidError(
            "task.handoff_requirements must not be empty",
            details={"field": "handoff_requirements"},
        )


def ownership_violations(task: TaskContract, changed_files: tuple[str, ...]) -> tuple[str, ...]:
    """Changed paths that fall outside the task's ownership surface."""
    allowed = _normalized_patterns(task.allowed_paths, field="allowed_paths")
    forbidden = _normalized_patterns(task.forbidden_paths, field="forbidden_paths")
    offending: list[str] = []
    for raw in changed_files:
        path = normalize_repo_path(raw, field="changed_files")
        unowned = matching_pattern(path, allowed) is None
        forbidden_hit = matching_pattern(path, forbidden) is not None
        if unowned or forbidden_hit:
            offending.append(path)
    return tuple(offending)


def _validate_tests(handoff: HandoffContract, task: TaskContract) -> None:
    if not handoff.tests:
        raise HandoffInvalidError(
            "handoff.tests must report at least one test",
            details={"field": "tests"},
        )
    missing = tuple(
        name for name in task.required_tests if handoff.test_named(name) is None
    )
    if handoff.ready_for_integration and missing:
        raise HandoffInvalidError(
            "handoff is not ready: required tests are not reported",
            details={"field": "tests", "missing": list(missing)},
        )
    if not handoff.ready_for_integration:
        return
    failed = tuple(result.name for result in handoff.tests if result.result == "failed")
    if failed:
        raise HandoffInvalidError(
            "handoff is not ready: reported tests failed",
            details={"field": "tests", "failed": list(failed)},
        )
    if not any(result.result == "passed" for result in handoff.tests):
        raise HandoffInvalidError(
            "handoff is not ready: no test reported as passed",
            details={"field": "tests"},
        )
    if not handoff.ownership_compliance:
        raise HandoffInvalidError(
            "handoff is not ready: it reports an ownership violation",
            details={"field": "ownership_compliance"},
        )


def validate_handoff(handoff: HandoffContract, task: TaskContract, config: AgentConfig) -> None:
    """Validate a handoff against its task (AGENT-01 §16, §19, §39, §68)."""
    if handoff.schema_version != 1:
        raise HandoffInvalidError(
            "handoff.schema_version must be 1",
            details={"field": "schema_version", "value": repr(handoff.schema_version)},
        )
    if handoff.task_id != task.task_id:
        raise HandoffInvalidError(
            f"handoff.task_id {handoff.task_id!r} does not match task {task.task_id!r}",
            details={"field": "task_id", "value": handoff.task_id},
        )
    if handoff.branch != task.branch:
        raise BranchMismatchError(
            f"handoff branch {handoff.branch!r} is not the task branch {task.branch!r}",
            details={"field": "branch", "value": handoff.branch, "expected": task.branch},
        )
    _validate_sha(handoff.base_sha, field="handoff.base_sha", error=HandoffInvalidError)
    _validate_sha(handoff.head_sha, field="handoff.head_sha", error=HandoffInvalidError)
    if handoff.head_sha == handoff.base_sha:
        raise HandoffInvalidError(
            "handoff.head_sha must differ from handoff.base_sha - no commit was made",
            details={"field": "head_sha", "value": handoff.head_sha},
        )
    for raw in handoff.changed_files:
        normalize_repo_path(raw, field="changed_files")
    validate_ownership_or_raise(handoff.changed_files, task)
    _validate_tests(handoff, task)


def check_base(handoff: HandoffContract, integration_head_sha: str) -> None:
    """Refuse a handoff that is not based on the current integration head.

    A PR that is green on a stale base is not known to be green once the base
    moves; the Main Agent integrates onto the current head or not at all
    (AGENT-01 §34, §58, §60).
    """
    _validate_sha(handoff.base_sha, field="handoff.base_sha", error=HandoffInvalidError)
    _validate_sha(integration_head_sha, field="integration_head_sha")
    if handoff.base_sha != integration_head_sha:
        raise BaseStaleError(
            "handoff is based on a commit that is not the current integration head",
            details={
                "task_id": handoff.task_id,
                "base_sha": handoff.base_sha,
                "integration_head": integration_head_sha,
            },
        )


def assert_frozen_unchanged(before: TaskContract, after: TaskContract) -> None:
    """Refuse a silent change to a task's frozen fields (AGENT-01 §36)."""
    if before.frozen_snapshot() == after.frozen_snapshot():
        return
    if after.revision > before.revision:
        return
    changed = tuple(
        field
        for field in FROZEN_FIELDS
        if before.frozen_snapshot()[field] != after.frozen_snapshot()[field]
    )
    raise TaskInvalidError(
        "frozen task fields changed without a revision bump",
        details={
            "task_id": before.task_id,
            "changed": list(changed),
            "revision": after.revision,
        },
    )


@dataclass(frozen=True)
class IntegrationReadiness:
    """The Main Agent's verdict for one handoff (AGENT-01 §56, §69)."""

    task_id: str
    ready: bool
    blockers: tuple[str, ...]
    details: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "ready": self.ready,
            "blockers": list(self.blockers),
            "details": [dict(item) for item in self.details],
        }


def evaluate_integration(
    handoff: HandoffContract,
    task: TaskContract,
    config: AgentConfig,
    integration_head_sha: str,
) -> IntegrationReadiness:
    """Collect every reason a handoff may not be integrated, rather than the first.

    Reporting one blocker at a time makes an integration loop slow and makes the
    checklist (§56) unfalsifiable; this returns the whole set.
    """
    blockers: list[str] = []
    details: list[dict[str, object]] = []
    checks: tuple[Callable[[], None], ...] = (
        (lambda: validate_handoff(handoff, task, config)),
        (lambda: check_base(handoff, integration_head_sha)),
    )
    for check in checks:
        try:
            check()
        except AgentToolingError as exc:
            blockers.append(exc.code)
            details.append(exc.as_dict())
    if not handoff.ready_for_integration:
        blockers.append("agent.handoff_invalid")
        details.append(
            {
                "code": "agent.handoff_invalid",
                "message": "handoff does not report ready_for_integration",
                "details": {"field": "ready_for_integration"},
            }
        )
    return IntegrationReadiness(
        task_id=task.task_id,
        ready=not blockers,
        blockers=tuple(blockers),
        details=tuple(details),
    )


def validate_ownership_or_raise(
    changed_files: tuple[str, ...], task: TaskContract
) -> None:
    """Raise ``agent.ownership_violation`` when any changed file is unowned."""
    offending = ownership_violations(task, changed_files)
    if offending:
        raise OwnershipViolationError(
            "changed files fall outside the task's ownership surface",
            details={"field": "changed_files", "offending": list(offending)},
        )
