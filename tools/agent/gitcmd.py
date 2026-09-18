"""A minimal, argument-list-only wrapper around the git CLI.

Every call passes an explicit argument list to ``subprocess`` - task data is
never interpolated into a shell string, so a branch name or a path can never
become a command (AGENT-01 §47, §49). Nothing here writes to the repository
except the worktree operations that ``worktree.py`` asks for explicitly.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tools.agent.errors import GitStateError

GIT_EXECUTABLE: Final[str] = "git"
_TIMEOUT_SECONDS: Final[int] = 120


@dataclass(frozen=True)
class GitResult:
    """The captured result of one git invocation."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def git(args: Sequence[str], *, cwd: Path, check: bool = True) -> GitResult:
    """Run ``git <args>`` in ``cwd`` and capture its output."""
    completed = subprocess.run(
        [GIT_EXECUTABLE, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TIMEOUT_SECONDS,
        check=False,
    )
    result = GitResult(
        args=tuple(args),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if check and result.returncode != 0:
        raise GitStateError(
            f"git {' '.join(args)} failed with exit code {result.returncode}",
            details={
                "args": list(args),
                "cwd": str(cwd),
                "returncode": result.returncode,
                "stderr": result.stderr.strip(),
            },
        )
    return result


def git_lines(args: Sequence[str], *, cwd: Path, check: bool = True) -> tuple[str, ...]:
    """Run git and return its non-empty stdout lines."""
    return tuple(
        line for line in git(args, cwd=cwd, check=check).stdout.splitlines() if line.strip()
    )


def is_git_repository(cwd: Path) -> bool:
    """True when ``cwd`` is inside a git work tree."""
    result = git(["rev-parse", "--is-inside-work-tree"], cwd=cwd, check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def repository_root(cwd: Path) -> Path:
    """The top level of the work tree containing ``cwd``."""
    return Path(git(["rev-parse", "--show-toplevel"], cwd=cwd).stdout.strip())


def resolve_revision(revision: str, *, cwd: Path) -> str:
    """Resolve any revision expression to a full 40-hex commit id."""
    result = git(["rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=cwd, check=False)
    if result.returncode != 0:
        raise GitStateError(
            f"unknown revision: {revision}",
            details={"revision": revision, "stderr": result.stderr.strip()},
        )
    return result.stdout.strip()


def current_branch(cwd: Path) -> str:
    """The checked-out branch name, or ``HEAD`` when detached."""
    result = git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=cwd, check=False)
    return result.stdout.strip() if result.returncode == 0 else "HEAD"


def is_clean(cwd: Path) -> bool:
    """True when the work tree has no staged, unstaged or untracked changes."""
    return not git_lines(["status", "--porcelain"], cwd=cwd)


def remote_url(cwd: Path, remote: str = "origin") -> str | None:
    """The fetch URL of ``remote``, or ``None`` when it is not configured."""
    result = git(["remote", "get-url", remote], cwd=cwd, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def contains(cwd: Path, ancestor: str, descendant: str) -> bool:
    """True when ``ancestor`` is an ancestor of (or equal to) ``descendant``."""
    result = git(["merge-base", "--is-ancestor", ancestor, descendant], cwd=cwd, check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise GitStateError(
        "git merge-base --is-ancestor could not be evaluated",
        details={"ancestor": ancestor, "descendant": descendant, "stderr": result.stderr.strip()},
    )


# -- repository identity --------------------------------------------------------

_OWNER_REPO_SEGMENT: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Origin shapes GitHub actually hands out. Anything else fails closed.
_GITHUB_PREFIXES: Final[tuple[str, ...]] = (
    "https://github.com/",
    "http://github.com/",
    "ssh://git@github.com/",
    "git@github.com:",
)


def canonical_repository(url: str | None) -> str | None:
    """Canonicalise a GitHub origin URL to ``owner/repo``, or ``None``.

    Serving the identity question by substring would accept
    ``github.com/evil/owner-repo-copy.git`` as ``owner/repo``. Only the four
    documented shapes are recognised, the result must have exactly two path
    segments, and a trailing ``.git`` is stripped before comparison.
    """
    if not url:
        return None
    value = url.strip()
    for prefix in _GITHUB_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    else:
        return None
    value = value.strip("/")
    if value.endswith(".git"):
        value = value[: -len(".git")]
    segments = value.split("/")
    if len(segments) != 2:
        return None
    owner, repo = segments
    if not _OWNER_REPO_SEGMENT.match(owner) or not _OWNER_REPO_SEGMENT.match(repo):
        return None
    return f"{owner}/{repo}"


def remote_identity(cwd: Path, remote: str = "origin") -> str | None:
    """``owner/repo`` for the configured ``remote``, or ``None``."""
    return canonical_repository(remote_url(cwd, remote))


# -- change-set derivation ------------------------------------------------------


def diff_name_status(
    cwd: Path, base: str, head: str
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """``git diff --name-status -M -C base..head`` as ``(status, paths)`` records.

    Rename and copy records carry two paths. Both sides are preserved: a
    protected file renamed into an owned subtree must still appear under its old
    path, and a deleted protected path is still a protected-path modification.
    """
    output = git(["diff", "--name-status", "-M", "-C", f"{base}..{head}"], cwd=cwd).stdout
    records: list[tuple[str, tuple[str, ...]]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        status = fields[0].strip()
        paths = tuple(field for field in fields[1:] if field)
        if paths:
            records.append((status, paths))
    return tuple(records)


def touched_paths(cwd: Path, base: str, head: str) -> tuple[str, ...]:
    """Every path the range touches, de-duplicated, both sides of R/C included."""
    seen: dict[str, None] = {}
    for _status, paths in diff_name_status(cwd, base, head):
        for path in paths:
            seen.setdefault(path, None)
    return tuple(seen)


def commit_shas(cwd: Path, base: str, head: str) -> tuple[str, ...]:
    """Full commit ids reachable from ``head`` but not from ``base``."""
    return git_lines(["log", "--format=%H", f"{base}..{head}"], cwd=cwd)
