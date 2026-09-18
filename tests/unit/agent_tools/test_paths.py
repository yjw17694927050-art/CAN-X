"""The path contract: normalisation, glob compilation and overlap detection."""

from __future__ import annotations

import pytest
from agent_tools_support import BASE_SHA, HEAD_SHA, OTHER_SHA

from tools.agent.errors import PathInvalidError
from tools.agent.paths import (
    compile_glob,
    is_glob,
    is_within,
    matches_pattern,
    matching_pattern,
    normalize_repo_path,
    normalize_repo_pattern,
    patterns_overlap,
)


@pytest.mark.parametrize(
    "raw",
    [
        "/etc/passwd",
        "C:/Windows",
        "c:relative",
        "runtime\\canx\\foo.py",
        "../outside.py",
        "a/../../outside.py",
        "",
        "   ",
        "a\x00b",
    ],
)
def test_a_path_that_is_not_repository_relative_is_refused(raw: str) -> None:
    with pytest.raises(PathInvalidError) as raised:
        normalize_repo_path(raw)
    assert raised.value.code == "agent.path_invalid"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("./runtime/canx/a.py", "runtime/canx/a.py"),
        ("runtime//canx/a.py", "runtime/canx/a.py"),
        ("runtime/canx/sub/../a.py", "runtime/canx/a.py"),
        ("docs/x.md", "docs/x.md"),
    ],
)
def test_ordinary_relative_paths_are_canonicalised(raw: str, expected: str) -> None:
    assert normalize_repo_path(raw) == expected


def test_a_pattern_may_carry_wildcards_but_not_escape() -> None:
    assert normalize_repo_pattern("runtime/canx/foo/**") == "runtime/canx/foo/**"
    for bad in ("runtime/canx/foo/", "../runtime/**", "/abs/**", "a//b/**"):
        with pytest.raises(PathInvalidError):
            normalize_repo_pattern(bad)


def test_a_globstar_pattern_covers_a_subtree_but_not_the_directory_itself() -> None:
    pattern = compile_glob("runtime/canx/foo/**")
    assert pattern.match("runtime/canx/foo/a.py")
    assert pattern.match("runtime/canx/foo/deep/nested/a.py")
    assert not pattern.match("runtime/canx/foo")
    assert not pattern.match("runtime/canx/foobar/a.py")


def test_a_globstar_in_the_middle_matches_zero_or_more_segments() -> None:
    assert matches_pattern("a/b.py", "a/**/b.py")
    assert matches_pattern("a/x/y/b.py", "a/**/b.py")
    assert not matches_pattern("a/b/c.py", "a/**/b.py")


def test_a_single_star_stays_inside_one_segment() -> None:
    assert matches_pattern("runtime/canx/a.py", "runtime/canx/*.py")
    assert not matches_pattern("runtime/canx/deep/a.py", "runtime/canx/*.py")


def test_a_question_mark_matches_one_character_and_never_a_separator() -> None:
    assert matches_pattern("a1.py", "a?.py")
    assert matches_pattern("ab.py", "a?.py")
    assert not matches_pattern("a/b.py", "a?b.py")


def test_a_character_class_is_treated_literally_not_as_a_regex() -> None:
    assert is_glob("a[bc].py") is False
    assert not matches_pattern("ab.py", "a[bc].py")
    assert matches_pattern("a[bc].py", "a[bc].py")


def test_matching_pattern_returns_the_first_covering_pattern() -> None:
    patterns = ("tests/unit/agent_tools/**", "docs/**")
    assert matching_pattern("tests/unit/agent_tools/x.py", patterns) == patterns[0]
    assert matching_pattern("tools/agent/x.py", patterns) is None


def test_overlap_is_conservative_and_deterministic() -> None:
    assert patterns_overlap("a/b.py", "a/b.py")
    assert not patterns_overlap("a/b.py", "a/c.py")
    assert patterns_overlap("a/b.py", "a/**")
    assert not patterns_overlap("a/b.py", "c/**")
    # Two globs with disjoint literal prefixes cannot overlap.
    assert not patterns_overlap("runtime/canx/alpha/**", "runtime/canx/domain/**")
    assert patterns_overlap("runtime/canx/**", "runtime/canx/domain/**")


def test_is_within_covers_the_directory_itself_and_its_children() -> None:
    assert is_within(".worktrees", ".worktrees")
    assert is_within(".worktrees/task-a", ".worktrees")
    assert not is_within(".worktrees-other/task-a", ".worktrees")
    assert not is_within("worktrees/task-a", ".worktrees")


def test_the_shared_commit_ids_are_well_formed() -> None:
    # Guards the shared fixture: a malformed sha would make sibling tests lie.
    for sha in (BASE_SHA, HEAD_SHA, OTHER_SHA):
        assert len(sha) == 40
        assert sha.islower()
