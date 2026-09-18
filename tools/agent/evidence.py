"""Git-backed evidence: what the repository says actually happened.

AGENT-01's trust boundary, restated (FIX-1 §54):

```text
Sub-Agent report        untrusted evidence
Git repository state    authoritative evidence
Task contract           what is permitted
Main Agent verifier     independently compares the two
```

A handoff is a *report format*. Editing its JSON must never be able to turn a
dishonest or stale delivery into ``READY_FOR_INTEGRATION``. Everything the
verifier needs is therefore re-derived here from the real worktree, the real
branch and the real commit range - never from a field the Sub-Agent typed.

This module performs the Git I/O. Comparing the collected evidence against a
handoff is a pure function in :mod:`tools.agent.validation`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.agent.contracts import TaskContract
from tools.agent.errors import WorktreeConflictError
from tools.agent.gitcmd import (
    commit_shas,
    contains,
    diff_name_status,
    git_lines,
    history_touched_paths,
    remote_identity,
    resolve_revision,
    touched_paths,
)
from tools.agent.worktree import (
    WorktreeRecord,
    list_worktrees,
    primary_worktree,
    validate_repository,
)

#: Where the evidence was read from. Surfaced so a fallback is never silent.
SOURCE_WORKTREE = "worktree"
SOURCE_BRANCH_REF = "branch-ref"


@dataclass(frozen=True, kw_only=True)
class RepositoryEvidence:
    """The facts the repository can prove about a task's delivery."""

    repository_root: Path
    source: str
    worktree_path: Path | None
    branch: str
    base_sha: str
    head_sha: str
    base_is_ancestor: bool
    clean: bool
    #: Net tree delta: ``base`` tree versus ``head`` tree. Informational.
    net_changed_paths: tuple[str, ...]
    #: Union of every path touched by every commit in ``base..head``. This is the
    #: authoritative ownership surface: a transient edit that a later commit
    #: reverts is invisible in the net diff but must still be refused.
    history_touched_paths: tuple[str, ...]
    commits: tuple[str, ...]
    diff_records: tuple[tuple[str, tuple[str, ...]], ...]
    #: The canonical ``owner/repo`` identity of the origin the evidence came
    #: from, when one could be read. Diagnostics only - the binding itself is
    #: enforced by ``validate_repository(expected_remote=...)``.
    repository_identity: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "repository_root": str(self.repository_root),
            "repository_identity": self.repository_identity,
            "source": self.source,
            "worktree_path": str(self.worktree_path) if self.worktree_path else None,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "base_is_ancestor": self.base_is_ancestor,
            "clean": self.clean,
            "net_changed_paths": list(self.net_changed_paths),
            "history_touched_paths": list(self.history_touched_paths),
            "commits": list(self.commits),
            "diff_records": [[status, list(paths)] for status, paths in self.diff_records],
        }


def resolve_task_worktree(
    root: Path, task: TaskContract
) -> tuple[Path, WorktreeRecord | None]:
    """Locate ``task``'s registered worktree, or refuse a contract mismatch.

    ``root`` may be the main worktree or a linked task worktree - both are
    supported inputs. ``task.worktree`` is repository-relative, so it is resolved
    against the **primary** worktree root, never against whichever worktree
    happened to invoke the tool (AGENT-01-FIX-2 §15-§18).

    The contract freezes *both* identities - the branch and the worktree path -
    and Git must agree with both. Every registered worktree is examined and the
    state is classified (AGENT-01-FIX-3 §5-§11):

    ```text
    A  declared path + branch both match          -> use that worktree
    B  declared path exists, branch differs       -> agent.worktree_conflict
    C  branch is registered at another path       -> agent.worktree_conflict
    D  ambiguous metadata (same path/branch x2)   -> agent.worktree_conflict
    E  neither the path nor the branch registered -> branch-ref fallback
    ```

    A mismatch is never silently repaired: following the branch to a different
    worktree would make ``task.worktree`` non-authoritative, and falling back to
    the branch ref would ignore a live worktree that may hold dirty work.
    """
    primary_root = primary_worktree(root)
    declared = (primary_root / task.worktree).resolve()
    records = list_worktrees(primary_root)
    path_matches = [record for record in records if record.path.resolve() == declared]
    branch_matches = [record for record in records if (record.branch or "") == task.branch]
    exact = [
        record for record in path_matches if (record.branch or "") == task.branch
    ]

    def conflict(reason: str, actual: WorktreeRecord | None) -> WorktreeConflictError:
        return WorktreeConflictError(
            reason,
            details={
                "task_id": task.task_id,
                "expected_branch": task.branch,
                "expected_worktree": task.worktree,
                "actual_worktree": str(actual.path) if actual is not None else None,
                "actual_branch": actual.branch if actual is not None else None,
                "declared_path": str(declared),
            },
        )

    if len(exact) > 1 or len(path_matches) > 1 or len(branch_matches) > 1:
        ambiguous = (exact or path_matches or branch_matches)[0]
        raise conflict(
            "worktree metadata is ambiguous for this task's branch or path", ambiguous
        )
    if exact:
        return primary_root, exact[0]
    if path_matches:
        raise conflict(
            "the declared task worktree path is checked out on a different branch",
            path_matches[0],
        )
    if branch_matches:
        raise conflict(
            "the task branch is registered at a different worktree path than the"
            " contract declares",
            branch_matches[0],
        )
    return primary_root, None


def collect_repository_evidence(
    repo: Path, task: TaskContract, *, expected_repository: str | None = None
) -> RepositoryEvidence:
    """Read the authoritative facts for ``task`` out of the repository.

    ``expected_repository`` is a canonical ``owner/repo`` identity. When it is
    given, the repository must be *that* repository - a wrong owner, a lookalike
    name, an unsupported host or a missing origin raises ``agent.git_state_error``
    instead of yielding evidence. The final integration gate always passes
    ``config.repository``, so "some valid Git repository" is never enough
    (AGENT-01-FIX-3 §16-§20).

    The task worktree is the strongest local source of truth, and handoff
    validation is meant to happen before any cleanup, so it is preferred. When
    no task worktree is registered the task *branch ref* is used instead - still
    Git, and still required to resolve to the head the handoff claims - and the
    fallback is recorded in ``source`` rather than hidden (FIX-1 §9). In that
    case ``clean`` means "no live task worktree exists **and** nothing is left at
    the declared path to contain uncommitted changes" - **not** "a worktree was
    inspected and found clean" (FIX-2 §20).
    """
    root = validate_repository(repo, expected_remote=expected_repository)
    primary_root, record = resolve_task_worktree(root, task)
    if record is not None:
        source = SOURCE_WORKTREE
        worktree_path: Path | None = record.path
        branch = record.branch or ""
        head_sha = record.head or resolve_revision("HEAD", cwd=record.path)
        clean = not git_lines(["status", "--porcelain"], cwd=record.path)
        probe = record.path
    else:
        source = SOURCE_BRANCH_REF
        worktree_path = None
        branch = task.branch
        head_sha = resolve_revision(task.branch, cwd=primary_root)
        # No task worktree is registered. ``clean`` then means "nothing is left
        # at the declared path that could hold uncommitted work": a leftover,
        # de-registered directory is treated as dirty rather than assumed clean,
        # so removing a worktree's registration cannot launder its dirty state
        # (FIX-2 §20). The declared path is never the primary root here - if it
        # were, the main worktree would have matched above.
        declared = (primary_root / task.worktree).resolve()
        clean = declared == primary_root.resolve() or not declared.exists()
        probe = primary_root
    base_sha = task.base_sha
    records = diff_name_status(probe, base_sha, head_sha)
    return RepositoryEvidence(
        repository_root=primary_root,
        source=source,
        worktree_path=worktree_path,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        base_is_ancestor=contains(probe, base_sha, head_sha),
        clean=clean,
        net_changed_paths=touched_paths(probe, base_sha, head_sha),
        history_touched_paths=history_touched_paths(probe, base_sha, head_sha),
        commits=commit_shas(probe, base_sha, head_sha),
        diff_records=records,
        repository_identity=remote_identity(primary_root),
    )
