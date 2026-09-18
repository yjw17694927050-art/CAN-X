"""Structured, machine-actionable errors for the CAN-X agent orchestration tooling.

AGENT-01 requires every tooling failure to carry a stable code so automation can
branch on it without parsing prose (AGENT-01 §54). The code set is the contract;
the human-readable message may change freely.

Exit-code mapping follows AGENT-01 §55:

```text
0  success
2  validation error
3  ownership / conflict / contract-state error
4  git state error
5  internal failure
```
"""

from __future__ import annotations

from typing import Final

#: Exit code returned by the CLI for each error code.
EXIT_CODES: Final[dict[str, int]] = {
    "agent.internal": 5,
    "agent.task_invalid": 2,
    "agent.handoff_invalid": 2,
    "agent.dependency_invalid": 2,
    "agent.dag_cycle": 2,
    "agent.path_invalid": 2,
    "agent.branch_invalid": 2,
    "agent.branch_mismatch": 3,
    "agent.base_stale": 3,
    "agent.base_not_ancestor": 3,
    "agent.handoff_evidence_mismatch": 3,
    "agent.integration_context_incomplete": 3,
    "agent.ownership_violation": 3,
    "agent.protected_path_conflict": 3,
    "agent.conflict_rejected": 3,
    "agent.dependency_blocked": 3,
    "agent.worktree_conflict": 4,
    "agent.worktree_dirty": 4,
    "agent.branch_unmerged": 4,
    "agent.git_state_error": 4,
}

DEFAULT_EXIT_CODE: Final[int] = 5


class AgentToolingError(Exception):
    """Base class for every structured tooling failure.

    ``code`` is stable and machine-readable; ``message`` is for humans;
    ``details`` carries the machine-checkable coordinates (field names, paths,
    shas) so a caller never has to re-parse the message.
    """

    code: str = "agent.internal"

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, object] = dict(details or {})

    @property
    def exit_code(self) -> int:
        """The process exit code this failure maps to (AGENT-01 §55)."""
        return EXIT_CODES.get(self.code, DEFAULT_EXIT_CODE)

    def as_dict(self) -> dict[str, object]:
        """Render the structured error envelope."""
        return {"code": self.code, "message": self.message, "details": self.details}


class TaskInvalidError(AgentToolingError):
    """A task contract is structurally invalid or internally inconsistent."""

    code = "agent.task_invalid"


class HandoffInvalidError(AgentToolingError):
    """A handoff is structurally invalid or inconsistent with its task."""

    code = "agent.handoff_invalid"


class DependencyInvalidError(AgentToolingError):
    """A dependency reference is malformed, unknown or self-referential."""

    code = "agent.dependency_invalid"


class DagCycleError(AgentToolingError):
    """The task dependency graph contains a cycle."""

    code = "agent.dag_cycle"


class PathInvalidError(AgentToolingError):
    """A repository-relative path or pattern violates the path contract."""

    code = "agent.path_invalid"


class BranchInvalidError(AgentToolingError):
    """A branch name is not a valid git ref or not a valid task branch."""

    code = "agent.branch_invalid"


class BranchMismatchError(AgentToolingError):
    """The handoff was produced on a branch other than the task's branch."""

    code = "agent.branch_mismatch"


class BaseStaleError(AgentToolingError):
    """The handoff is based on a commit that is not the current integration head."""

    code = "agent.base_stale"


class BaseNotAncestorError(AgentToolingError):
    """The task base is not an ancestor of the delivered head.

    A handoff may not claim a base its history does not actually descend from:
    the relationship is proved with ``git merge-base --is-ancestor``, never
    inferred from two values merely looking like commit ids.
    """

    code = "agent.base_not_ancestor"


class HandoffEvidenceMismatchError(AgentToolingError):
    """The handoff's reported facts disagree with the repository's own facts.

    ``changed_files`` and ``commits`` are claims; the Git-derived lists are the
    evidence. A disagreement is not a formatting nit - it means the handoff is
    not a faithful report of what happened on the branch.
    """

    code = "agent.handoff_evidence_mismatch"


class IntegrationContextIncompleteError(AgentToolingError):
    """The local integration gate was asked to decide without enough evidence.

    The gate reports ready only when it can actually prove every requirement.
    "No repository supplied" is therefore a blocker, not a pass.
    """

    code = "agent.integration_context_incomplete"


class OwnershipViolationError(AgentToolingError):
    """A changed file falls outside the task's ownership surface."""

    code = "agent.ownership_violation"


class ProtectedPathConflictError(AgentToolingError):
    """A task claims ownership of a protected / high-contention path."""

    code = "agent.protected_path_conflict"


class ConflictRejectedError(AgentToolingError):
    """Two tasks conflict above the level that may be auto-integrated."""

    code = "agent.conflict_rejected"


class DependencyBlockedError(AgentToolingError):
    """A task cannot proceed because a dependency is not complete."""

    code = "agent.dependency_blocked"


class WorktreeConflictError(AgentToolingError):
    """A worktree or branch is already associated with another task."""

    code = "agent.worktree_conflict"


class WorktreeDirtyError(AgentToolingError):
    """A worktree has uncommitted work and may not be cleaned up."""

    code = "agent.worktree_dirty"


class BranchUnmergedError(AgentToolingError):
    """A task branch has commits that are not contained in the integration branch."""

    code = "agent.branch_unmerged"


class GitStateError(AgentToolingError):
    """The repository is not in the state the operation requires."""

    code = "agent.git_state_error"
