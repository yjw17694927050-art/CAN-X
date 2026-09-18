"""Throwaway-git helpers shared by the AGENT-01-FIX-1 evidence tests.

Every test builds its own repository under ``tmp_path``. The real CAN-X
repository is never mutated by pytest, and no shell is involved: git is invoked
with an explicit argument list, which is the same discipline the tooling itself
uses.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_IDENTITY = (
    "-c",
    "user.email=canx-test@example.com",
    "-c",
    "user.name=CAN-X Test",
)


def git(repo: Path, args: list[str]) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return done.stdout.strip()


@dataclass(frozen=True)
class SandboxRepository:
    """A real git repository with a helper for the shapes these tests need."""

    root: Path

    def sha(self, revision: str = "HEAD") -> str:
        return git(self.root, ["rev-parse", revision])

    def write(self, relative: str, content: str) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self, message: str) -> str:
        git(self.root, ["add", "-A"])
        git(self.root, [*GIT_IDENTITY, "commit", "-m", message])
        return self.sha()

    def branch(self, name: str) -> None:
        git(self.root, ["checkout", "-b", name])

    def checkout(self, name: str) -> None:
        git(self.root, ["checkout", name])

    def rename(self, source: str, destination: str) -> None:
        git(self.root, ["mv", source, destination])

    def short_commits(self, base: str, head: str) -> tuple[str, ...]:
        output = git(self.root, ["log", "--format=%h %s", f"{base}..{head}"])
        return tuple(line for line in output.splitlines() if line.strip())


def make_repository(tmp_path: Path, name: str = "repo") -> SandboxRepository:
    """A repository with one commit, a ``main`` branch and a worktrees ignore."""
    root = tmp_path / name
    root.mkdir()
    git(root, ["init", "-b", "main"])
    repo = SandboxRepository(root=root)
    repo.write(".gitignore", ".worktrees/\n")
    repo.write("SPEC.md", "frozen\n")
    repo.write("AGENTS.md", "rules\n")
    repo.write("runtime/canx/foo/a.py", "a = 1\n")
    repo.write("runtime/canx/bar/b.py", "b = 1\n")
    repo.commit("init")
    return repo
