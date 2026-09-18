"""Worktree lifecycle against a real, throwaway git repository.

These tests exercise ``git worktree`` for real inside ``tmp_path``; they never
touch the CAN-X repository they run from.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from tools.agent.errors import (
    BranchUnmergedError,
    GitStateError,
    WorktreeConflictError,
    WorktreeDirtyError,
)
from tools.agent.gitcmd import current_branch, git, resolve_revision
from tools.agent.worktree import (
    create_worktree,
    find_worktree,
    list_worktrees,
    remove_worktree,
    validate_repository,
    validate_worktree,
)

GIT_IDENTITY = (
    "-c",
    "user.email=canx-test@example.com",
    "-c",
    "user.name=CAN-X Test",
)


def _git(repo: Path, args: Sequence[str]) -> str:
    return git(args, cwd=repo).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, [*GIT_IDENTITY, "commit", "--allow-empty", "-m", message])
    return resolve_revision("HEAD", cwd=repo)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """A real git repository with one commit, isolated per test.

    ``.worktrees/`` is ignored, exactly as CAN-X's own ``.gitignore`` ignores it.
    A worktrees directory that shows up as untracked would make the repository
    dirty the moment the first task worktree is created, and
    ``create_worktree`` refuses to work in a dirty repository.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git(["init", "-b", "main"], cwd=repo)
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    (repo / "README.md").write_text("temp repo\n", encoding="utf-8")
    git(["add", "."], cwd=repo)
    _commit(repo, "init")
    return repo


def test_the_temp_repository_is_a_real_work_tree(temp_repo: Path) -> None:
    assert validate_repository(temp_repo) == temp_repo.resolve()
    assert current_branch(temp_repo) == "main"


def test_a_worktree_created_from_an_explicit_base_is_registered(temp_repo: Path) -> None:
    base = resolve_revision("HEAD", cwd=temp_repo)
    path = temp_repo / ".worktrees" / "task-a"
    record = create_worktree(
        temp_repo,
        task_id="AGENT-02-A",
        branch="agent/AGENT-02-A-alpha",
        path=path,
        base_sha=base,
    )
    assert record.branch == "agent/AGENT-02-A-alpha"
    assert record.head == base
    assert path.is_dir()
    assert find_worktree(temp_repo, path) is not None


def test_a_second_worktree_on_the_same_path_is_refused(temp_repo: Path) -> None:
    base = resolve_revision("HEAD", cwd=temp_repo)
    path = temp_repo / ".worktrees" / "task-a"
    create_worktree(
        temp_repo,
        task_id="AGENT-02-A",
        branch="agent/AGENT-02-A-alpha",
        path=path,
        base_sha=base,
    )
    with pytest.raises(WorktreeConflictError):
        create_worktree(
            temp_repo,
            task_id="AGENT-02-B",
            branch="agent/AGENT-02-B-beta",
            path=path,
            base_sha=base,
        )


def test_a_second_worktree_on_the_same_branch_is_refused(temp_repo: Path) -> None:
    base = resolve_revision("HEAD", cwd=temp_repo)
    create_worktree(
        temp_repo,
        task_id="AGENT-02-A",
        branch="agent/AGENT-02-A-alpha",
        path=temp_repo / ".worktrees" / "task-a",
        base_sha=base,
    )
    with pytest.raises(WorktreeConflictError) as raised:
        create_worktree(
            temp_repo,
            task_id="AGENT-02-A",
            branch="agent/AGENT-02-A-alpha",
            path=temp_repo / ".worktrees" / "task-a-copy",
            base_sha=base,
        )
    assert raised.value.code == "agent.worktree_conflict"


def test_a_worktree_path_outside_the_repository_is_refused(
    temp_repo: Path, tmp_path: Path
) -> None:
    base = resolve_revision("HEAD", cwd=temp_repo)
    with pytest.raises(WorktreeConflictError) as raised:
        create_worktree(
            temp_repo,
            task_id="AGENT-02-A",
            branch="agent/AGENT-02-A-escape",
            path=tmp_path / "outside",
            base_sha=base,
        )
    assert "escapes the repository" in raised.value.message
    assert not (tmp_path / "outside").exists()


def test_a_dirty_repository_may_not_spawn_a_worktree(temp_repo: Path) -> None:
    base = resolve_revision("HEAD", cwd=temp_repo)
    (temp_repo / "uncommitted.txt").write_text("oops\n", encoding="utf-8")
    with pytest.raises(GitStateError) as raised:
        create_worktree(
            temp_repo,
            task_id="AGENT-02-A",
            branch="agent/AGENT-02-A-alpha",
            path=temp_repo / ".worktrees" / "task-a",
            base_sha=base,
        )
    assert raised.value.code == "agent.git_state_error"


def test_an_unresolvable_base_is_refused(temp_repo: Path) -> None:
    with pytest.raises(GitStateError):
        create_worktree(
            temp_repo,
            task_id="AGENT-02-A",
            branch="agent/AGENT-02-A-alpha",
            path=temp_repo / ".worktrees" / "task-a",
            base_sha="0" * 40,
        )


def test_a_short_base_sha_is_refused(temp_repo: Path) -> None:
    with pytest.raises(GitStateError):
        create_worktree(
            temp_repo,
            task_id="AGENT-02-A",
            branch="agent/AGENT-02-A-alpha",
            path=temp_repo / ".worktrees" / "task-a",
            base_sha="deadbeef",
        )


def test_validate_worktree_refuses_a_branch_mismatch(temp_repo: Path) -> None:
    base = resolve_revision("HEAD", cwd=temp_repo)
    path = temp_repo / ".worktrees" / "task-a"
    create_worktree(
        temp_repo,
        task_id="AGENT-02-A",
        branch="agent/AGENT-02-A-alpha",
        path=path,
        base_sha=base,
    )
    validate_worktree(temp_repo, path, expected_branch="agent/AGENT-02-A-alpha", expected_head=base)
    with pytest.raises(WorktreeConflictError):
        validate_worktree(temp_repo, path, expected_branch="agent/AGENT-02-B-beta")


def test_validate_worktree_refuses_an_unregistered_path(temp_repo: Path) -> None:
    with pytest.raises(WorktreeConflictError):
        validate_worktree(temp_repo, temp_repo / ".worktrees" / "never-created")


def test_the_listed_worktrees_include_the_created_one(temp_repo: Path) -> None:
    base = resolve_revision("HEAD", cwd=temp_repo)
    path = temp_repo / ".worktrees" / "task-a"
    create_worktree(
        temp_repo,
        task_id="AGENT-02-A",
        branch="agent/AGENT-02-A-alpha",
        path=path,
        base_sha=base,
    )
    branches = {record.branch for record in list_worktrees(temp_repo)}
    assert "main" in branches
    assert "agent/AGENT-02-A-alpha" in branches


def test_a_merged_worktree_is_removed_cleanly(temp_repo: Path) -> None:
    base = resolve_revision("HEAD", cwd=temp_repo)
    path = temp_repo / ".worktrees" / "task-a"
    branch = "agent/AGENT-02-A-alpha"
    create_worktree(
        temp_repo, task_id="AGENT-02-A", branch=branch, path=path, base_sha=base
    )
    (path / "work.txt").write_text("done\n", encoding="utf-8")
    git(["add", "."], cwd=path)
    _commit(path, "task work")
    git(["merge", "--ff-only", branch], cwd=temp_repo)

    remove_worktree(temp_repo, path)

    assert not path.exists()
    assert find_worktree(temp_repo, path) is None


def test_an_unmerged_worktree_is_not_removed_by_default(temp_repo: Path) -> None:
    base = resolve_revision("HEAD", cwd=temp_repo)
    path = temp_repo / ".worktrees" / "task-a"
    create_worktree(
        temp_repo,
        task_id="AGENT-02-A",
        branch="agent/AGENT-02-A-alpha",
        path=path,
        base_sha=base,
    )
    (path / "work.txt").write_text("unreviewed\n", encoding="utf-8")
    git(["add", "."], cwd=path)
    _commit(path, "unmerged task work")

    with pytest.raises(BranchUnmergedError) as raised:
        remove_worktree(temp_repo, path)
    assert raised.value.code == "agent.branch_unmerged"
    assert path.exists(), "a refused cleanup must leave the evidence in place"


def test_an_unmerged_worktree_can_be_removed_when_explicitly_allowed(temp_repo: Path) -> None:
    base = resolve_revision("HEAD", cwd=temp_repo)
    path = temp_repo / ".worktrees" / "task-a"
    create_worktree(
        temp_repo,
        task_id="AGENT-02-A",
        branch="agent/AGENT-02-A-alpha",
        path=path,
        base_sha=base,
    )
    (path / "work.txt").write_text("abandoned\n", encoding="utf-8")
    git(["add", "."], cwd=path)
    _commit(path, "abandoned task work")

    remove_worktree(temp_repo, path, allow_unmerged=True)
    assert not path.exists()


def test_a_dirty_worktree_is_never_removed(temp_repo: Path) -> None:
    base = resolve_revision("HEAD", cwd=temp_repo)
    path = temp_repo / ".worktrees" / "task-a"
    create_worktree(
        temp_repo,
        task_id="AGENT-02-A",
        branch="agent/AGENT-02-A-alpha",
        path=path,
        base_sha=base,
    )
    (path / "uncommitted.txt").write_text("work in progress\n", encoding="utf-8")

    with pytest.raises(WorktreeDirtyError) as raised:
        remove_worktree(temp_repo, path, allow_unmerged=True)
    assert raised.value.code == "agent.worktree_dirty"
    assert path.exists()


def test_the_main_worktree_is_never_removed(temp_repo: Path) -> None:
    with pytest.raises(WorktreeConflictError):
        remove_worktree(temp_repo, temp_repo, allow_unmerged=True)


def test_the_helper_never_shells_out(temp_repo: Path) -> None:
    """A branch name carrying shell metacharacters is data, never a command."""
    base = resolve_revision("HEAD", cwd=temp_repo)
    marker = temp_repo / "INJECTED.txt"
    with pytest.raises(GitStateError):
        create_worktree(
            temp_repo,
            task_id="AGENT-02-A",
            branch=f"agent/AGENT-02-A-x;touch {marker.name}",
            path=temp_repo / ".worktrees" / "task-a",
            base_sha=base,
        )
    assert not marker.exists()


def test_no_stray_processes_are_left_behind(temp_repo: Path) -> None:
    # A guard against a test helper that forgets to wait on a child process.
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(temp_repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
