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
from pathlib import Path
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
    BaseNotAncestorError,
    BaseStaleError,
    BranchInvalidError,
    BranchMismatchError,
    ConflictRejectedError,
    DependencyBlockedError,
    GitStateError,
    HandoffEvidenceMismatchError,
    HandoffInvalidError,
    IntegrationContextIncompleteError,
    OwnershipViolationError,
    ProtectedPathConflictError,
    TaskInvalidError,
)
from tools.agent.evidence import RepositoryEvidence, collect_repository_evidence
from tools.agent.graph import TaskGraph
from tools.agent.lifecycle import TaskStatus, requires_conflict_replan
from tools.agent.orchestration import OrchestrationPlan, plan
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
        # Pattern versus pattern: the ownership surface must not be able to
        # *reach* a protected path. `matching_pattern` would answer a different
        # question - whether the literal text of this glob is itself a protected
        # file name - and would let `**`, `**/*.md` or `docs/**` through.
        guard = config.protected_overlap(pattern)
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
    """Validate reported test evidence (AGENT-01 §16, §19, §39, §68).

    ``not_run`` / ``skipped`` / ``failed`` are all legitimate *reports* - a
    handoff that is honest about a test it did not run is more useful than one
    that lies. What they are not is an integration-passing result for a
    *required* test: a required test must be reported as ``passed``.
    """
    if not handoff.tests:
        raise HandoffInvalidError(
            "handoff.tests must report at least one test",
            details={"field": "tests"},
        )
    names = [result.name for result in handoff.tests]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise HandoffInvalidError(
            "handoff.tests contains duplicate test names",
            details={"field": "tests", "duplicates": duplicates},
        )
    if not handoff.ready_for_integration:
        return
    results = {result.name: result.result for result in handoff.tests}
    missing = [name for name in task.required_tests if name not in results]
    if missing:
        raise HandoffInvalidError(
            "handoff is not ready: required tests are not reported",
            details={"field": "tests", "missing": missing},
        )
    not_passed = {
        name: results[name] for name in task.required_tests if results[name] != "passed"
    }
    if not_passed:
        raise HandoffInvalidError(
            "handoff is not ready: every required test must be reported as passed",
            details={"field": "tests", "not_passed": not_passed},
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


def check_base(
    handoff: HandoffContract, task: TaskContract, integration_head_sha: str
) -> None:
    """Refuse a handoff whose base is not the task's base *and* the current head.

    Two distinct facts, both required:

    * ``handoff.base_sha == task.base_sha`` - a handoff may not invent a base.
      A legitimate rebase updates the task contract (``base_sha`` is not one of
      the frozen fields) and re-issues it; a handoff that quietly changes the
      base on its own is reporting a delivery the task never asked for.
    * ``handoff.base_sha == integration_head_sha`` - a PR that was green on a
      stale base is not known to be green once the base moves (AGENT-01 §34,
      §58, §60).
    """
    _validate_sha(handoff.base_sha, field="handoff.base_sha", error=HandoffInvalidError)
    _validate_sha(task.base_sha, field="task.base_sha")
    _validate_sha(integration_head_sha, field="integration_head_sha")
    if handoff.base_sha != task.base_sha:
        raise HandoffInvalidError(
            "handoff.base_sha must equal task.base_sha - a handoff may not invent a base",
            details={
                "task_id": task.task_id,
                "base_sha": handoff.base_sha,
                "task_base_sha": task.base_sha,
            },
        )
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


def verify_repository_evidence(
    handoff: HandoffContract,
    task: TaskContract,
    evidence: RepositoryEvidence,
) -> tuple[AgentToolingError, ...]:
    """Compare a handoff against the facts the repository proves.

    Pure: it takes already-collected evidence, so it is unit-testable without
    Git and cannot be satisfied by anything the handoff says about itself.
    """
    blockers: list[AgentToolingError] = []
    if evidence.branch != task.branch:
        blockers.append(
            BranchMismatchError(
                "the task worktree/branch is not the branch the task declares",
                details={
                    "task_id": task.task_id,
                    "branch": evidence.branch,
                    "expected": task.branch,
                    "source": evidence.source,
                },
            )
        )
    if evidence.base_sha != task.base_sha:
        blockers.append(
            HandoffInvalidError(
                "the delivered history is not based on the task's base commit",
                details={
                    "task_id": task.task_id,
                    "base_sha": evidence.base_sha,
                    "task_base_sha": task.base_sha,
                },
            )
        )
    if evidence.head_sha != handoff.head_sha:
        blockers.append(
            HandoffInvalidError(
                "handoff.head_sha is not the actual HEAD of the task branch",
                details={
                    "task_id": task.task_id,
                    "reported": handoff.head_sha,
                    "actual": evidence.head_sha,
                    "source": evidence.source,
                },
            )
        )
    if not evidence.base_is_ancestor:
        blockers.append(
            BaseNotAncestorError(
                "task.base_sha is not an ancestor of the delivered head",
                details={
                    "task_id": task.task_id,
                    "base_sha": evidence.base_sha,
                    "head_sha": evidence.head_sha,
                },
            )
        )
    if not evidence.clean:
        blockers.append(
            GitStateError(
                "the task worktree has uncommitted work; the committed head is not"
                " the whole delivery",
                details={"task_id": task.task_id, "worktree": str(evidence.worktree_path)},
            )
        )
    # Ownership is decided by Git-derived paths, never by handoff.changed_files.
    # The authoritative set is the *historical* one: every path any commit in
    # base..head touched, not only the net tree delta. Otherwise a task could
    # edit a protected file and restore it in a later commit and escape the gate
    # while both commits still land on the integration branch (FIX-2 §21-§22).
    offending = ownership_violations(task, evidence.history_touched_paths)
    if offending:
        blockers.append(
            OwnershipViolationError(
                "the delivered history touches paths outside the task's ownership surface",
                details={"field": "changed_files", "offending": list(offending)},
            )
        )
    reported = tuple(
        sorted(normalize_repo_path(item, field="changed_files") for item in handoff.changed_files)
    )
    actual = tuple(sorted(evidence.history_touched_paths))
    if reported != actual:
        blockers.append(
            HandoffEvidenceMismatchError(
                "handoff.changed_files does not match every path the task history touched",
                details={
                    "reported": list(reported),
                    "actual": list(actual),
                    "unreported": sorted(set(actual) - set(reported)),
                    "invented": sorted(set(reported) - set(actual)),
                },
            )
        )
    commit_blocker = _commit_evidence_blocker(handoff, evidence)
    if commit_blocker is not None:
        blockers.append(commit_blocker)
    return tuple(blockers)


def _commit_token(entry: str) -> str:
    """The leading sha of a ``<sha> <subject>`` commit line, or the whole line."""
    return entry.split(maxsplit=1)[0] if entry.strip() else ""


def _commit_evidence_blocker(
    handoff: HandoffContract, evidence: RepositoryEvidence
) -> AgentToolingError | None:
    """Compare the reported commit set with the real ``base..head`` range.

    Only enforced for a handoff that claims readiness - a half-finished handoff
    may legitimately describe a range it is still extending. What is never
    allowed is *claiming* integration-ready while the commit set says otherwise.

    The match is one-to-one. Prefix membership plus equal counts is not enough:
    two different tokens that both prefix the *same* commit satisfy a
    membership test while a second real commit is never represented
    (AGENT-01-FIX-2 §30). Every token must therefore resolve to exactly one
    actual commit - not zero (unknown), not two (ambiguous) - and the mapping
    must be a bijection.
    """
    if not handoff.ready_for_integration:
        return None
    reported = [
        token for item in handoff.commits if (token := _commit_token(item).lower())
    ]
    actual = [sha.lower() for sha in evidence.commits]
    reason: str | None = None
    if len(set(reported)) != len(reported):
        reason = "a reported commit token is a duplicate"
    matched: dict[str, str] = {}
    if reason is None:
        for token in reported:
            hits = [sha for sha in actual if sha.startswith(token)]
            if not hits:
                reason = "a reported commit token matches no actual commit"
                break
            if len(hits) > 1:
                reason = "a reported commit token is an ambiguous prefix"
                break
            matched[token] = hits[0]
    if reason is None and len(set(matched.values())) != len(reported):
        reason = "two reported tokens resolve to the same commit"
    if reason is None and set(matched.values()) != set(actual):
        reason = "not every actual commit is represented"
    if reason is None:
        return None
    return HandoffEvidenceMismatchError(
        f"handoff.commits does not match the repository's own commit range: {reason}",
        details={"reported": reported, "actual": actual, "reason": reason},
    )


@dataclass(frozen=True)
class IntegrationContext:
    """Everything the *local* gate needs beyond the handoff and the task.

    The local gate and the GitHub platform gate answer different questions
    (FIX-1 §24-25):

    ```text
    local IntegrationReadiness   handoff schema · Git-backed evidence · ownership
                                 base/current-head · dependency completion
                                 conflict/deferral state · task readiness
    GitHub Ruleset               Quality Gate green · protected PR workflow
    merge eligibility            local READY  AND  platform Quality Gate success
                                 AND the base is still current at merge time
    ```

    Neither half claims to do the other's job. This tooling does not embed a
    GitHub client; it reports what it can prove and says so.
    """

    integration_head_sha: str
    graph: TaskGraph | None = None
    orchestration: OrchestrationPlan | None = None

    @classmethod
    def build(
        cls,
        tasks: tuple[TaskContract, ...],
        config: AgentConfig,
        integration_head_sha: str,
        *,
        include_plan: bool = True,
    ) -> IntegrationContext:
        """Build a full context.

        ``include_plan=False`` exists for lower-level diagnostics only. It omits
        the orchestration plan, which makes ``evaluate_integration`` fail closed
        with ``agent.integration_context_incomplete``: a caller may not disable
        conflict checking and still obtain ``ready: true`` (AGENT-01-FIX-2 §29).
        """
        graph = TaskGraph.build(tasks)
        return cls(
            integration_head_sha=integration_head_sha,
            graph=graph,
            orchestration=plan(tasks, config) if include_plan else None,
        )


@dataclass(frozen=True)
class IntegrationReadiness:
    """The Main Agent's *local* verdict for one handoff (AGENT-01 §56, §69)."""

    task_id: str
    ready: bool
    blockers: tuple[str, ...]
    details: tuple[dict[str, object], ...]
    evidence: RepositoryEvidence | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "ready": self.ready,
            "blockers": list(self.blockers),
            "details": [dict(item) for item in self.details],
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "github_gate": {
                "checked_here": False,
                "note": (
                    "the local gate does not verify the GitHub Quality Gate; merge"
                    " eligibility also requires the platform's required check to be"
                    " green on this head"
                ),
            },
        }


def _gate(blockers: list[str], details: list[dict[str, object]], check: Callable[[], None]) -> None:
    """Run one check, recording its structured failure instead of raising."""
    try:
        check()
    except AgentToolingError as exc:
        _record(blockers, details, exc)


def _record(
    blockers: list[str], details: list[dict[str, object]], error: AgentToolingError
) -> None:
    """Record one structured blocker on the verdict being built."""
    blockers.append(error.code)
    details.append(error.as_dict())


def _dependency_blocker(
    task: TaskContract, context: IntegrationContext
) -> AgentToolingError | None:
    if context.graph is None:
        return None
    if task.task_id not in context.graph.index():
        return IntegrationContextIncompleteError(
            "the task being integrated is not in the supplied task set",
            details={"task_id": task.task_id},
        )
    unsatisfied = context.graph.unsatisfied_dependencies(task.task_id)
    if not unsatisfied:
        return None
    unsatisfiable = context.graph.unsatisfiable_dependencies(task.task_id)
    return DependencyBlockedError(
        "the task's dependencies are not complete",
        details={
            "task_id": task.task_id,
            "unsatisfied_dependencies": list(unsatisfied),
            "unsatisfiable_dependencies": list(unsatisfiable),
            "next": "re-plan the DAG" if unsatisfiable else "wait for the dependency to reach DONE",
        },
    )


def _conflict_blocker(task: TaskContract, context: IntegrationContext) -> AgentToolingError | None:
    """Refuse a task that must wait for an in-flight producer it conflicts with.

    Deferral is not only a dispatch-time question. A task that has *already*
    handed off still may not integrate while the earlier producer it shares a
    C2/C3/C4 surface with has not landed - otherwise a conflicted pair could
    both reach the integration branch by taking turns being "ready"
    (FIX-1 §28).
    """
    orchestration = context.orchestration
    if orchestration is None:
        return None
    positions = {
        task_id: index for index, task_id in enumerate(orchestration.integration_order)
    }
    statuses = {entry.task_id: entry.status for entry in orchestration.entries}
    waiting_on: list[str] = []
    for conflict in orchestration.blocking_conflicts():
        if task.task_id not in (conflict.left, conflict.right):
            continue
        other = conflict.right if conflict.left == task.task_id else conflict.left
        if statuses.get(other) is TaskStatus.DONE:
            continue
        if positions.get(other, 0) < positions.get(task.task_id, 0):
            waiting_on.append(other)
    if not waiting_on:
        return None
    # A conflicting owner that terminated without integrating is not a silent
    # release: the later task still may not integrate, and the verdict names the
    # dead owner so the Main Agent knows to re-plan rather than wait
    # (AGENT-01-FIX-2 §11-§12).
    replan_required_by = sorted(
        other for other in waiting_on if requires_conflict_replan(statuses[other])
    )
    return ConflictRejectedError(
        "a blocking conflict with an in-flight task is not resolved yet",
        details={
            "task_id": task.task_id,
            "waiting_on": sorted(waiting_on),
            "replan_required": bool(replan_required_by),
            "replan_required_by": replan_required_by,
            "conflicts": [
                conflict.to_dict()
                for conflict in orchestration.blocking_conflicts()
                if task.task_id in (conflict.left, conflict.right)
            ],
        },
    )


def evaluate_integration(
    handoff: HandoffContract,
    task: TaskContract,
    config: AgentConfig,
    context: IntegrationContext,
    *,
    repository: Path | None = None,
    evidence: RepositoryEvidence | None = None,
) -> IntegrationReadiness:
    """Collect every reason a handoff may not be integrated, rather than the first.

    ``ready`` is reported only when the gate could actually prove every local
    requirement. Git-backed evidence is required: with neither ``repository`` nor
    ``evidence`` the verdict is not "ready", it is "cannot tell", and that is a
    blocker (FIX-1 §8-§10, §26).
    """
    blockers: list[str] = []
    details: list[dict[str, object]] = []
    _gate(blockers, details, lambda: validate_handoff(handoff, task, config))
    _gate(blockers, details, lambda: check_base(handoff, task, context.integration_head_sha))

    if TaskStatus(task.status) is not TaskStatus.HANDOFF_READY:
        _record(
            blockers,
            details,
            TaskInvalidError(
                "task status is not HANDOFF_READY",
                details={"task_id": task.task_id, "status": str(task.status)},
            ),
        )

    collected = evidence
    if collected is None and repository is not None:
        try:
            collected = collect_repository_evidence(repository, task)
        except AgentToolingError as exc:
            _record(blockers, details, exc)
    if collected is None:
        _record(
            blockers,
            details,
            IntegrationContextIncompleteError(
                "Git-backed evidence is required and was not supplied",
                details={"task_id": task.task_id},
            ),
        )
    else:
        for blocker in verify_repository_evidence(handoff, task, collected):
            _record(blockers, details, blocker)

    if context.graph is None:
        _record(
            blockers,
            details,
            IntegrationContextIncompleteError(
                "the task set is required and was not supplied",
                details={"task_id": task.task_id},
            ),
        )
        dependency_blocker = None
    else:
        dependency_blocker = _dependency_blocker(task, context)
    if dependency_blocker is not None:
        _record(blockers, details, dependency_blocker)

    # The conflict gate cannot be proven without the orchestration plan. A
    # context built with ``include_plan=False`` is a diagnostics convenience and
    # must never yield ``ready: true`` (AGENT-01-FIX-2 §29).
    if context.orchestration is None:
        _record(
            blockers,
            details,
            IntegrationContextIncompleteError(
                "the orchestration plan is required to prove the conflict gate",
                details={"task_id": task.task_id},
            ),
        )

    conflict_blocker = _conflict_blocker(task, context)
    if conflict_blocker is not None:
        _record(blockers, details, conflict_blocker)

    if not handoff.ready_for_integration:
        _record(
            blockers,
            details,
            HandoffInvalidError(
                "handoff does not report ready_for_integration",
                details={"field": "ready_for_integration"},
            ),
        )
    return IntegrationReadiness(
        task_id=task.task_id,
        ready=not blockers,
        blockers=tuple(blockers),
        details=tuple(details),
        evidence=collected,
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
