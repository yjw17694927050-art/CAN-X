"""Real Git-backed diff coverage for the CI-03 classifier.

The classifier's two load-bearing Git decisions are exercised here against
*temporary* repositories — no test touches the real CAN-X checkout:

* a pull request is diffed as ``merge-base(base, head)..head`` and never as
  ``base..head`` (which would also report the base branch's own progress);
* renames are read with ``--no-renames``, so a rename is a delete of the old path
  *and* an add of the new one and both sides are classified.

These tests pin behaviour that the classifier already documents; they exist so
that the two decisions are proven against real Git rather than reasoned about.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.ci.classify_changes import FULL, Classification, classify_event


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """An isolated, configured, empty Git repository."""

    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "ci-03-tests@example.invalid")
    _git(path, "config", "user.name", "CI-03 Tests")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "config", "core.autocrlf", "false")
    return path


def _classify_pull_request(repo: Path, base: str, head: str) -> Classification:
    return classify_event("pull_request", repo=str(repo), base=base, head=head)


# --------------------------------------------------------------------------
# Renames — `--no-renames` means both sides of the rename are classified
# --------------------------------------------------------------------------


def test_rename_of_a_critical_file_out_of_its_location_is_full(repo: Path) -> None:
    """CASE A — `SPEC.md` renamed into `docs/`: the *old* path is critical, so the
    change stays FULL even though the new path looks like documentation."""

    _write(repo, "SPEC.md", "# spec\n")
    fork = _commit(repo, "add the specification authority")

    (repo / "docs").mkdir(exist_ok=True)
    _git(repo, "mv", "SPEC.md", "docs/old-spec.md")
    head = _commit(repo, "move the specification into docs")

    result = _classify_pull_request(repo, fork, head)

    assert result.full_required is True
    assert result.classification == FULL
    assert "SPEC.md" in result.paths
    assert "docs/old-spec.md" in result.paths


def test_rename_of_a_document_onto_a_critical_path_is_full(repo: Path) -> None:
    """CASE B — `docs/foo.md` renamed to `SPEC.md`: the *new* path is critical."""

    _write(repo, "docs/foo.md", "foo\n")
    fork = _commit(repo, "add a document")

    _git(repo, "mv", "docs/foo.md", "SPEC.md")
    head = _commit(repo, "promote the document to the specification authority")

    result = _classify_pull_request(repo, fork, head)

    assert result.full_required is True
    assert "SPEC.md" in result.paths


def test_deletion_of_a_critical_file_is_full(repo: Path) -> None:
    """CASE C — a deleted critical file is still a change to that authority."""

    _write(repo, "SPEC.md", "# spec\n")
    _write(repo, "docs/keep.md", "keep\n")
    fork = _commit(repo, "add the specification and a document")

    _git(repo, "rm", "-q", "SPEC.md")
    head = _commit(repo, "delete the specification")

    result = _classify_pull_request(repo, fork, head)

    assert result.full_required is True
    assert "SPEC.md" in result.paths


# --------------------------------------------------------------------------
# Multiple commits — the whole PR diff is classified, not the last commit
# --------------------------------------------------------------------------


def test_a_multi_commit_pull_request_classifies_the_entire_effective_diff(repo: Path) -> None:
    """CASE D — a runtime change followed by a docs-only commit still requires the
    Python job. Checking only the head commit would have said `docs_only`."""

    _write(repo, "README.md", "root\n")
    base = _commit(repo, "root")

    _write(repo, "runtime/canx/thing.py", "VALUE = 1\n")
    _commit(repo, "add a runtime module")

    _write(repo, "docs/notes.md", "notes\n")
    head = _commit(repo, "docs-only head commit")

    result = _classify_pull_request(repo, base, head)

    assert result.runtime_required is True
    assert result.classification == "runtime"
    assert set(result.paths) == {"runtime/canx/thing.py", "docs/notes.md"}


# --------------------------------------------------------------------------
# merge-base — the base branch's own progress is not the PR's change
# --------------------------------------------------------------------------


def test_base_branch_progress_is_not_counted_as_pull_request_change(repo: Path) -> None:
    """CASE E — the compared range must be merge-base(base, head)..head.

    The base branch advances with an unrecognised path (which would escalate to
    FULL if it were counted). The PR, branched from the fork point, touches only
    documentation. A naive ``base..head`` diff would see the base's new file and
    report FULL; the correct merge-base diff reports ``docs_only``.
    """

    _write(repo, "docs/readme.md", "readme\n")
    fork = _commit(repo, "root")

    # The base branch moves ahead on its own.
    _write(repo, "mystery-base-only.bin", "not part of the pull request\n")
    base = _commit(repo, "base branch makes its own progress")

    # The pull request branches from the fork point and touches only docs.
    _git(repo, "checkout", "-q", fork)
    _write(repo, "docs/pr-note.md", "a note\n")
    head = _commit(repo, "docs-only pull request")

    # Sanity: a naive base..head diff really would have counted the base file.
    naive = _git(repo, "diff", "--name-only", "--no-renames", base, head)
    assert "mystery-base-only.bin" in naive

    result = _classify_pull_request(repo, base, head)

    assert result.classification == "docs_only"
    assert result.full_required is False
    assert "mystery-base-only.bin" not in result.paths
    assert set(result.paths) == {"docs/pr-note.md"}


# --------------------------------------------------------------------------
# Push events — before..head
# --------------------------------------------------------------------------


def test_a_push_from_a_readable_before_sha_classifies_that_range(repo: Path) -> None:
    _write(repo, "docs/start.md", "start\n")
    before = _commit(repo, "start")

    _write(repo, "runtime/canx/thing.py", "VALUE = 1\n")
    after = _commit(repo, "add a runtime module")

    result = classify_event("push", repo=str(repo), base=before, head=after)

    assert result.runtime_required is True
    assert result.frontend_required is False
    assert result.rust_required is False
