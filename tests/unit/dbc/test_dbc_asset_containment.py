"""Project containment: the boundary is the *resolved* project, not a name.

A ``dbc`` directory can look like part of a project while naming somewhere else
entirely — a junction, a symlink or a reparse point. The rule this module pins
down is therefore stated over resolved paths: the resolved DBC directory must be
inside the resolved project root, and the resolved asset must be inside that
directory.

The escape cases here fake path resolution instead of creating a link, so they run
everywhere. A containment test that only runs on hosts permissive enough to create
symlinks is not evidence on the hosts that need the check most; the real-link
counterpart lives in the integration suite.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from canx.dbc.asset import (
    ASSET_DIRECTORY,
    DbcAsset,
    asset_relative_path,
    resolve_asset_directory,
    resolve_asset_path,
)
from canx.dbc.errors import DbcAssetIntegrityError

ASSET_ID = "3b0f9a5c-1d2e-4f3a-8b4c-5d6e7f8091a2"
OTHER_ID = "11111111-1111-4111-8111-111111111111"
PROJECT_ID = "2f1c3d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
DIGEST = "0123456789abcdef" * 4
IMPORTED_AT = datetime(2026, 9, 16, 10, 15, tzinfo=UTC)


def make_asset(asset_id: str = ASSET_ID) -> DbcAsset:
    return DbcAsset(
        asset_id=asset_id,
        project_id=PROJECT_ID,
        source_name="vehicle.dbc",
        relative_path=asset_relative_path(asset_id),
        sha256=DIGEST,
        size_bytes=512,
        encoding="utf-8-sig",
        imported_at=IMPORTED_AT,
    )


def make_project(tmp_path: Path, name: str = "vehicle.canx") -> Path:
    project = tmp_path / name
    (project / ASSET_DIRECTORY).mkdir(parents=True)
    return project


def redirect(
    monkeypatch: pytest.MonkeyPatch, source: Path, destination: Path
) -> None:
    """Make ``source`` resolve to ``destination`` without touching the filesystem."""
    real_resolve = Path.resolve

    def fake_resolve(self: Path, strict: bool = False) -> Path:
        if os.path.normcase(str(self)) == os.path.normcase(str(source)):
            return destination
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", fake_resolve)


def redirect_tree(
    monkeypatch: pytest.MonkeyPatch, source: Path, destination: Path
) -> None:
    """Make every path under ``source`` resolve under ``destination``.

    This is what a directory junction does: the directory *and* everything below
    it are seen at another location, which is why a check on the candidate alone
    is not enough.
    """
    real_resolve = Path.resolve

    def fake_resolve(self: Path, strict: bool = False) -> Path:
        try:
            relative = self.relative_to(source)
        except ValueError:
            return real_resolve(self, strict=strict)
        return destination / relative

    monkeypatch.setattr(Path, "resolve", fake_resolve)


# --- the intact case --------------------------------------------------------


def test_an_intact_project_resolves_its_dbc_directory(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    assert resolve_asset_directory(project) == (project / ASSET_DIRECTORY).resolve()


def test_an_asset_of_an_intact_project_resolves_to_its_owned_file(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    owned = project / ASSET_DIRECTORY / f"{ASSET_ID}.dbc"
    owned.write_bytes(b'VERSION "1.0"\n')

    assert resolve_asset_path(project, make_asset()) == owned.resolve()


def test_an_upper_case_identity_resolves_to_the_canonical_path(tmp_path: Path) -> None:
    """The expected path is derived from the canonical identity, not the input."""
    project = make_project(tmp_path)
    owned = project / ASSET_DIRECTORY / f"{ASSET_ID}.dbc"
    owned.write_bytes(b'VERSION "1.0"\n')

    assert resolve_asset_path(project, make_asset(ASSET_ID.upper())) == owned.resolve()


# --- a dbc directory that is not in the project -----------------------------


def test_a_dbc_directory_that_resolves_outside_the_project_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The escape this increment closes: the directory itself is not the project's."""
    project = make_project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    redirect(monkeypatch, project / ASSET_DIRECTORY, outside)

    with pytest.raises(DbcAssetIntegrityError) as info:
        resolve_asset_directory(project)

    assert info.value.code == "dbc.asset_integrity_failed"
    assert info.value.recoverable is False
    assert info.value.details["dbc_directory"] == str(outside)
    assert info.value.details["project_root"] == str(project.resolve())


def test_an_asset_cannot_resolve_through_a_dbc_directory_that_left_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both the directory and everything below it can move together.

    This is the precise shape of a junction: the candidate *is* relative to the
    resolved directory, so only the project-level check notices the move.
    """
    project = make_project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / f"{ASSET_ID}.dbc").write_bytes(b'VERSION "1.0"\n')
    redirect_tree(monkeypatch, project / ASSET_DIRECTORY, outside)

    with pytest.raises(DbcAssetIntegrityError) as info:
        resolve_asset_path(project, make_asset())

    assert info.value.code == "dbc.asset_integrity_failed"
    assert info.value.details["dbc_directory"] == str(outside)


def test_a_missing_dbc_directory_is_never_recreated(tmp_path: Path) -> None:
    """A missing project directory is a tampered layout, not an invitation to mkdir."""
    project = tmp_path / "vehicle.canx"
    project.mkdir()

    with pytest.raises(DbcAssetIntegrityError) as info:
        resolve_asset_directory(project)

    assert info.value.code == "dbc.asset_integrity_failed"
    assert info.value.details["dbc_directory"] == str((project / ASSET_DIRECTORY).resolve())
    assert not (project / ASSET_DIRECTORY).exists()


def test_a_dbc_directory_replaced_by_a_file_is_refused(tmp_path: Path) -> None:
    project = tmp_path / "vehicle.canx"
    project.mkdir()
    (project / ASSET_DIRECTORY).write_bytes(b"not a directory")

    with pytest.raises(DbcAssetIntegrityError):
        resolve_asset_directory(project)


# --- defence in depth -------------------------------------------------------


def test_resolving_rechecks_identity_even_for_a_mutated_record(tmp_path: Path) -> None:
    """The gate re-derives the expected path instead of trusting the record.

    A ``DbcAsset`` cannot be built with a mismatched path, so this is the second
    layer: even a record mutated after construction does not get to name another
    identity's file.
    """
    project = make_project(tmp_path)
    (project / ASSET_DIRECTORY / f"{OTHER_ID}.dbc").write_bytes(b'VERSION "1.0"\n')
    asset = make_asset()
    object.__setattr__(asset, "relative_path", asset_relative_path(OTHER_ID))

    with pytest.raises(DbcAssetIntegrityError) as info:
        resolve_asset_path(project, asset)

    assert info.value.code == "dbc.asset_integrity_failed"
    assert info.value.details["expected_relative_path"] == asset_relative_path(ASSET_ID)
    assert info.value.details["relative_path"] == asset_relative_path(OTHER_ID)


# --- legitimate indirection is not an escape --------------------------------


def test_a_project_reached_through_a_link_is_not_mistaken_for_an_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """How the project is *reached* is not the question; where ``dbc`` lands is.

    Opening a project through a shortcut, a mapped drive or a linked directory is
    normal. The requirement is only that the resolved DBC directory stays inside
    the resolved project root.
    """
    real = make_project(tmp_path, "real-project.canx")
    shortcut = tmp_path / "shortcut.canx"
    shortcut.mkdir()
    redirect(monkeypatch, shortcut, real)

    assert resolve_asset_directory(shortcut) == (real / ASSET_DIRECTORY).resolve()
