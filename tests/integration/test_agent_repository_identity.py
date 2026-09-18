"""P2: the repository-identity check against a real git remote.

RED -> GREEN: every origin shape in the "substring lookalike" cases used to pass
``validate_repository`` because the old check asked whether the expected identity
appeared *anywhere* in the URL.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_git_sandbox import git, make_repository

from tools.agent.errors import GitStateError
from tools.agent.worktree import validate_repository

CANONICAL = "yjw17694927050-art/CAN-X"


def _with_remote(tmp_path: Path, origin: str) -> Path:
    repo = make_repository(tmp_path)
    git(repo.root, ["remote", "add", "origin", origin])
    return repo.root


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/evil/yjw17694927050-art/CAN-X-copy.git",
        "https://example.com/yjw17694927050-art/CAN-X.git",
        "git@github.com:yjw17694927050-art/CAN-X-evil.git",
    ],
)
def test_a_substring_lookalike_origin_is_refused(tmp_path: Path, origin: str) -> None:
    root = _with_remote(tmp_path, origin)
    with pytest.raises(GitStateError) as raised:
        validate_repository(root, expected_remote=CANONICAL)
    assert raised.value.code == "agent.git_state_error"


@pytest.mark.parametrize(
    "origin",
    [
        f"https://github.com/{CANONICAL}.git",
        f"git@github.com:{CANONICAL}.git",
    ],
)
def test_the_real_origin_shapes_are_accepted(tmp_path: Path, origin: str) -> None:
    root = _with_remote(tmp_path, origin)
    assert validate_repository(root, expected_remote=CANONICAL) == root


def test_a_repository_without_origin_fails_closed(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    with pytest.raises(GitStateError):
        validate_repository(repo.root, expected_remote=CANONICAL)


def test_an_unrecognised_remote_host_fails_closed(tmp_path: Path) -> None:
    root = _with_remote(tmp_path, f"https://gitlab.example.com/{CANONICAL}.git")
    with pytest.raises(GitStateError) as raised:
        validate_repository(root, expected_remote=CANONICAL)
    assert "supported GitHub remote shape" in raised.value.message


def test_no_expected_remote_still_validates_the_repository(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    assert validate_repository(repo.root) == repo.root
