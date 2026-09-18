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
    resolve_revision,
    touched_paths,
)
from tools.agent.worktree import find_worktree, validate_repository

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
    changed_paths: tuple[str, ...]
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
            "changed_paths": list(self.changed_paths),
            "commits": list(self.commits),
            "diff_records": [[status, list(paths)] for status, paths in self.diff_records],
        }


def collect_repository_evidence(repo: Path, task: TaskContract) -> RepositoryEvidence:
    """Read the authoritative facts for ``task`` out of the repository.

    The task worktree is the strongest local source of truth, and handoff
    validation is meant to happen before any cleanup, so it is preferred. When
    the worktree is gone the task *branch ref* is used instead - still Git, and
    still required to resolve to the head the handoff claims - and the fallback
    is recorded in ``source`` rather than hidden (FIX-1 §9).
    """
    root = validate_repository(repo)
    target = root / task.worktree
    record = find_worktree(root, target)
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
        head_sha = resolve_revision(task.branch, cwd=root)
        # The worktree is gone, so there is no uncommitted work left to lose;
        # the committed head is the whole delivery.
        clean = True
        probe = root
    base_sha = task.base_sha
    records = diff_name_status(probe, base_sha, head_sha)
    return RepositoryEvidence(
        repository_root=root,
        source=source,
        worktree_path=worktree_path,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        base_is_ancestor=contains(probe, base_sha, head_sha),
        clean=clean,
        changed_paths=touched_paths(probe, base_sha, head_sha),
        commits=commit_shas(probe, base_sha, head_sha),
        diff_records=records,
    )
