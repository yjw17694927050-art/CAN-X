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
from tools.agent.gitcmd import (
    commit_shas,
    contains,
    diff_name_status,
    git_lines,
    history_touched_paths,
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

    def to_dict(self) -> dict[str, object]:
        return {
            "repository_root": str(self.repository_root),
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
    """Locate ``task``'s registered worktree from any documented entry point.

    ``root`` may be the main worktree or a linked task worktree - both are
    supported inputs. ``task.worktree`` is repository-relative, so it is resolved
    against the **primary** worktree root, never against whichever worktree
    happened to invoke the tool. Resolving it against a linked worktree is what
    made ``<task-worktree>/.worktrees/<task>`` look like a path that does not
    exist, silently demoting the evidence to the branch-ref fallback and losing
    the real worktree's dirty state (AGENT-01-FIX-2 §15-§18).

    Identification is by the declared path (preferring the record whose branch
    also matches); the branch is then verified against the record, so a wrong
    checkout is reported as a mismatch rather than silently accepted.
    """
    primary_root = primary_worktree(root)
    declared = (primary_root / task.worktree).resolve()
    on_path = [
        record for record in list_worktrees(primary_root) if record.path.resolve() == declared
    ]
    if not on_path:
        return primary_root, None
    for record in on_path:
        if (record.branch or "") == task.branch:
            return primary_root, record
    return primary_root, on_path[0]


def collect_repository_evidence(repo: Path, task: TaskContract) -> RepositoryEvidence:
    """Read the authoritative facts for ``task`` out of the repository.

    The task worktree is the strongest local source of truth, and handoff
    validation is meant to happen before any cleanup, so it is preferred. When
    no task worktree is registered the task *branch ref* is used instead - still
    Git, and still required to resolve to the head the handoff claims - and the
    fallback is recorded in ``source`` rather than hidden (FIX-1 §9). In that
    case ``clean`` means "no live task worktree exists to contain uncommitted
    changes", **not** "a worktree was inspected and found clean" (FIX-2 §20).
    """
    root = validate_repository(repo)
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
        # No task worktree is registered, so there is no uncommitted work left to
        # lose; the committed head is the whole delivery.
        clean = True
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
    )
