"""Isolated worktree lifecycle for parallel tasks (AGENT-01 §12-§14, §40, §46, §48).

One branch, one agent, one task, one worktree. The helper creates, validates,
lists and removes task worktrees, and it fails closed: it never deletes an
arbitrary branch, never force-removes a dirty worktree and never removes a
directory that is not a registered worktree of the repository. There is no
``rm -rf`` path in this module at all - removal goes through
``git worktree remove``, which refuses a dirty tree itself.

Precondition: the worktrees directory must be ignored by git (CAN-X ignores
``.worktrees/``). ``create_worktree`` refuses a repository with uncommitted
work, and an untracked worktrees directory would make the repository dirty the
moment the first task worktree exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tools.agent.contracts import SHA_RE
from tools.agent.errors import (
    BranchUnmergedError,
    GitStateError,
    WorktreeConflictError,
    WorktreeDirtyError,
)
from tools.agent.gitcmd import (
    canonical_repository,
    contains,
    current_branch,
    git,
    git_lines,
    is_git_repository,
    remote_url,
    repository_root,
    resolve_revision,
)

_BRANCH_PREFIX: Final[str] = "refs/heads/"
_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")


@dataclass(frozen=True, kw_only=True)
class WorktreeRecord:
    """One entry of ``git worktree list --porcelain``."""

    path: Path
    head: str | None
    branch: str | None
    detached: bool
    bare: bool
    locked: bool
    prunable: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "head": self.head,
            "branch": self.branch,
            "detached": self.detached,
            "bare": self.bare,
            "locked": self.locked,
            "prunable": self.prunable,
        }


def _parse_porcelain(text: str) -> tuple[WorktreeRecord, ...]:
    records: list[WorktreeRecord] = []
    fields: dict[str, object] = {}

    def flush() -> None:
        if not fields:
            return
        head = fields.get("head")
        branch = fields.get("branch")
        records.append(
            WorktreeRecord(
                path=Path(str(fields["path"])),
                head=head if isinstance(head, str) else None,
                branch=branch if isinstance(branch, str) else None,
                detached=bool(fields.get("detached", False)),
                bare=bool(fields.get("bare", False)),
                locked=bool(fields.get("locked", False)),
                prunable=bool(fields.get("prunable", False)),
            )
        )
        fields.clear()

    for line in text.splitlines():
        if not line.strip():
            flush()
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            flush()
            fields["path"] = value
        elif key == "HEAD":
            fields["head"] = value
        elif key == "branch":
            fields["branch"] = value.removeprefix(_BRANCH_PREFIX)
        elif key in {"detached", "bare", "locked", "prunable"}:
            fields[key] = True
    flush()
    return tuple(records)


def list_worktrees(repo: Path) -> tuple[WorktreeRecord, ...]:
    """Every worktree registered for the repository at ``repo``."""
    result = git(["worktree", "list", "--porcelain"], cwd=repo)
    return _parse_porcelain(result.stdout)


def validate_repository(repo: Path, *, expected_remote: str | None = None) -> Path:
    """Confirm ``repo`` is the repository the tooling is allowed to touch.

    ``expected_remote`` is a canonical ``owner/repo`` identity. The origin URL is
    canonicalised and compared exactly: a substring test would accept
    ``github.com/evil/owner-repo-copy.git`` as ``owner/repo`` (FIX-1 §31-33).
    Any origin shape the canonicaliser does not recognise fails closed.
    """
    if not repo.exists():
        raise GitStateError("repository path does not exist", details={"repo": str(repo)})
    if not is_git_repository(repo):
        raise GitStateError("not a git work tree", details={"repo": str(repo)})
    root = repository_root(repo)
    if expected_remote:
        url = remote_url(root)
        if url is None:
            raise GitStateError(
                "repository has no 'origin' remote",
                details={"repo": str(root), "expected_remote": expected_remote},
            )
        expected = canonical_repository(expected_remote) or expected_remote
        actual = canonical_repository(url)
        if actual is None:
            raise GitStateError(
                "repository origin is not a supported GitHub remote shape",
                details={"repo": str(root), "origin": url, "expected_remote": expected},
            )
        if actual != expected:
            raise GitStateError(
                "repository origin does not match the configured repository",
                details={"repo": str(root), "origin": actual, "expected_remote": expected},
            )
    return root


def find_worktree(repo: Path, path: Path) -> WorktreeRecord | None:
    """The registered worktree at ``path``, or ``None``."""
    target = path.resolve()
    for record in list_worktrees(repo):
        if record.path.resolve() == target:
            return record
    return None


def _require_inside_repository(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    target = path.resolve() if path.is_absolute() else (resolved_root / path).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:
        raise WorktreeConflictError(
            "worktree path escapes the repository",
            details={"repo": str(resolved_root), "path": str(target)},
        ) from exc
    if target == resolved_root:
        raise WorktreeConflictError(
            "worktree path must not be the repository root",
            details={"repo": str(resolved_root)},
        )
    return target


def create_worktree(
    repo: Path,
    *,
    task_id: str,
    branch: str,
    path: Path,
    base_sha: str,
    expected_remote: str | None = None,
    require_clean: bool = True,
) -> WorktreeRecord:
    """Create an isolated worktree for one task.

    Refuses before touching anything when the branch or the path is already
    spoken for, when the base is not a real commit, or when the repository has
    uncommitted work (AGENT-01 §14).
    """
    root = validate_repository(repo, expected_remote=expected_remote)
    if not SHA_RE.match(base_sha):
        raise GitStateError(
            "base_sha must be a full 40-character lowercase hex commit id",
            details={"field": "base_sha", "value": base_sha},
        )
    if not _SLUG_RE.match(branch):
        raise GitStateError(
            "branch is not a valid branch name",
            details={"field": "branch", "value": branch},
        )
    target = _require_inside_repository(root, path)
    if require_clean and not _worktree_is_clean(root):
        raise GitStateError(
            "repository working tree is not clean",
            details={"repo": str(root)},
        )
    if target.exists():
        raise WorktreeConflictError(
            "worktree path already exists on disk",
            details={"path": str(target), "task_id": task_id},
        )
    if find_worktree(root, target) is not None:
        raise WorktreeConflictError(
            "another worktree is already registered at this path",
            details={"path": str(target), "task_id": task_id},
        )
    if _branch_ref_exists(root, branch):
        raise WorktreeConflictError(
            "branch already exists",
            details={"branch": branch, "task_id": task_id},
        )
    for existing in list_worktrees(root):
        if existing.branch == branch:
            raise WorktreeConflictError(
                "branch is already checked out in another worktree",
                details={"branch": branch, "worktree": str(existing.path), "task_id": task_id},
            )
    head = resolve_revision(base_sha, cwd=root)
    git(["worktree", "add", "-b", branch, str(target), head], cwd=root)
    record = find_worktree(root, target)
    if record is None:
        raise GitStateError(
            "git reported success but the worktree is not registered",
            details={"path": str(target)},
        )
    return record


def _worktree_is_clean(root: Path) -> bool:
    return not git_lines(["status", "--porcelain"], cwd=root)


def _branch_ref_exists(root: Path, branch: str) -> bool:
    result = git(
        ["show-ref", "--verify", "--quiet", f"{_BRANCH_PREFIX}{branch}"],
        cwd=root,
        check=False,
    )
    return result.returncode == 0


def validate_worktree(
    repo: Path,
    path: Path,
    *,
    expected_branch: str | None = None,
    expected_head: str | None = None,
) -> WorktreeRecord:
    """Confirm the worktree at ``path`` is the one the task expects (§33)."""
    root = validate_repository(repo)
    record = find_worktree(root, path)
    if record is None:
        raise WorktreeConflictError(
            "no worktree is registered at this path",
            details={"path": str(path)},
        )
    if expected_branch is not None and record.branch != expected_branch:
        raise WorktreeConflictError(
            "worktree is on a different branch than the task declares",
            details={
                "path": str(record.path),
                "branch": record.branch,
                "expected_branch": expected_branch,
            },
        )
    if expected_head is not None and record.head != expected_head:
        raise WorktreeConflictError(
            "worktree HEAD does not match the expected commit",
            details={
                "path": str(record.path),
                "head": record.head,
                "expected_head": expected_head,
            },
        )
    return record


def remove_worktree(
    repo: Path,
    path: Path,
    *,
    integration_branch: str = "main",
    allow_unmerged: bool = False,
    delete_branch: bool = False,
) -> None:
    """Remove a task worktree, refusing to destroy unreviewed work (§14, §46).

    The worktree must be clean, and its branch must already be contained in the
    integration branch unless ``allow_unmerged`` is set explicitly. Removal goes
    through ``git worktree remove``; there is no recursive directory delete.
    """
    root = validate_repository(repo)
    record = find_worktree(root, path)
    if record is None:
        raise WorktreeConflictError(
            "no worktree is registered at this path",
            details={"path": str(path)},
        )
    if record.path.resolve() == root.resolve():
        raise WorktreeConflictError(
            "the main worktree may not be removed by the task helper",
            details={"path": str(record.path)},
        )
    if git_lines(["status", "--porcelain"], cwd=record.path):
        raise WorktreeDirtyError(
            "worktree has uncommitted work and will not be removed",
            details={"path": str(record.path)},
        )
    head = record.head or resolve_revision("HEAD", cwd=record.path)
    if not allow_unmerged:
        integration_ref = _integration_ref(root, integration_branch)
        if not contains(root, head, integration_ref):
            raise BranchUnmergedError(
                "worktree branch is not contained in the integration branch",
                details={
                    "path": str(record.path),
                    "head": head,
                    "integration_branch": integration_branch,
                },
            )
    git(["worktree", "remove", str(record.path)], cwd=root)
    if delete_branch and record.branch:
        _delete_branch(root, record.branch)


def _integration_ref(root: Path, integration_branch: str) -> str:
    return resolve_revision(integration_branch, cwd=root)


def _delete_branch(root: Path, branch: str) -> None:
    """Delete a merged branch with ``git branch -d`` (never ``-D``)."""
    git(["branch", "-d", branch], cwd=root)


def current_worktree(repo: Path) -> WorktreeRecord | None:
    """The record for the worktree containing ``repo``."""
    root = validate_repository(repo)
    branch = current_branch(root)
    for record in list_worktrees(root):
        if record.branch == branch and record.path.resolve() == root.resolve():
            return record
    return None
