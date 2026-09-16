"""The project-owned DBC asset record and the paths that belong to it.

Two properties matter more than the field list:

* identity is a UUID, never a file name. A source file name is untrusted input
  from a user's disk and must never become part of a project path;
* a stored path is never trusted just because the database held it, so every
  escape shape has a deterministic answer.
"""

from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest
from canx.dbc.asset import (
    ASSET_DIRECTORY,
    ASSET_SUFFIX,
    DbcAsset,
    asset_relative_path,
    resolve_asset_path,
)
from canx.dbc.errors import DbcAssetIntegrityError, DbcAssetValidationError

ASSET_ID = "3b0f9a5c-1d2e-4f3a-8b4c-5d6e7f8091a2"
OTHER_ID = "11111111-1111-4111-8111-111111111111"
PROJECT_ID = "2f1c3d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
DIGEST = "0123456789abcdef" * 4
IMPORTED_AT = datetime(2026, 9, 16, 10, 15, tzinfo=UTC)


def make_asset(**overrides: object) -> DbcAsset:
    values: dict[str, object] = {
        "asset_id": ASSET_ID,
        "project_id": PROJECT_ID,
        "source_name": "vehicle.dbc",
        "relative_path": f"{ASSET_DIRECTORY}/{ASSET_ID}{ASSET_SUFFIX}",
        "sha256": DIGEST,
        "size_bytes": 512,
        "encoding": "utf-8-sig",
        "imported_at": IMPORTED_AT,
    }
    values.update(overrides)
    return DbcAsset(**values)  # type: ignore[arg-type]


# --- the happy path ---------------------------------------------------------


def test_a_valid_asset_records_identity_provenance_and_integrity_fields() -> None:
    asset = make_asset()

    assert asset.asset_id == ASSET_ID
    assert asset.project_id == PROJECT_ID
    assert asset.source_name == "vehicle.dbc"
    assert asset.relative_path == f"dbc/{ASSET_ID}.dbc"
    assert asset.sha256 == DIGEST
    assert asset.size_bytes == 512
    assert asset.encoding == "utf-8-sig"
    assert asset.imported_at == IMPORTED_AT


def test_an_asset_is_immutable_and_slotted() -> None:
    asset = make_asset()

    with pytest.raises(FrozenInstanceError):
        asset.source_name = "other.dbc"  # type: ignore[misc]
    assert not hasattr(asset, "__dict__")


def test_a_uuid_is_stored_in_its_canonical_string_form() -> None:
    """A pasted upper-case UUID is normalized, so identity comparison is stable."""
    asset = make_asset(asset_id=ASSET_ID.upper(), project_id=PROJECT_ID.upper())

    assert asset.asset_id == ASSET_ID
    assert asset.project_id == PROJECT_ID


def test_a_zero_size_asset_is_allowed() -> None:
    """An empty DBC is a parse failure, not an invalid registry record."""
    assert make_asset(size_bytes=0).size_bytes == 0


# --- asset identity ---------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   ", "not-a-uuid", "1234", "dbc/asset.dbc"])
def test_an_asset_id_that_is_not_a_uuid_is_rejected(value: str) -> None:
    with pytest.raises(DbcAssetValidationError) as info:
        make_asset(asset_id=value)

    assert info.value.code == "dbc.invalid_asset"
    assert info.value.source == "dbc"
    assert info.value.recoverable is False


def test_a_non_string_asset_id_is_rejected() -> None:
    with pytest.raises(DbcAssetValidationError):
        make_asset(asset_id=1234)


def test_a_project_id_that_is_not_a_uuid_is_rejected() -> None:
    with pytest.raises(DbcAssetValidationError):
        make_asset(project_id="project-one")


# --- source name ------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["", "   ", "folder/vehicle.dbc", "folder\\vehicle.dbc", "..", ".", "/"],
    ids=["empty", "blank", "posix-path", "windows-path", "parent", "self", "root"],
)
def test_a_source_name_that_is_a_path_or_blank_is_rejected(value: str) -> None:
    """``source_name`` is provenance: it records a base name, never a location."""
    with pytest.raises(DbcAssetValidationError) as info:
        make_asset(source_name=value)

    assert "source_name" in info.value.details


@pytest.mark.parametrize("value", ["vehicle.dbc", "network.dbc", "车辆.dbc", " my file.dbc"])
def test_an_ordinary_file_name_is_accepted(value: str) -> None:
    """A file name is not policed beyond "it is a name": spaces are legal on disk."""
    assert make_asset(source_name=value).source_name == value


# --- relative path ----------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "../outside.dbc",
        "..\\outside.dbc",
        "dbc/../../outside.dbc",
        "dbc/../outside.dbc",
        "C:\\outside.dbc",
        "/absolute/outside.dbc",
        "data/segment.dbc",
        "dbc/nested/asset.dbc",
        "dbc/asset.txt",
        "",
        "   ",
        "dbc/asset.dbc ",
    ],
    ids=[
        "parent",
        "windows-parent",
        "dotted-escape",
        "dotted-inside",
        "drive-letter",
        "absolute",
        "wrong-directory",
        "nested",
        "wrong-extension",
        "empty",
        "blank",
        "padded",
    ],
)
def test_a_relative_path_outside_the_dbc_directory_is_rejected(value: str) -> None:
    with pytest.raises(DbcAssetValidationError) as info:
        make_asset(relative_path=value)

    assert "relative_path" in info.value.details


def test_the_generated_path_is_derived_from_the_asset_id() -> None:
    assert asset_relative_path(ASSET_ID) == f"dbc/{ASSET_ID}.dbc"
    assert asset_relative_path(ASSET_ID.upper()) == f"dbc/{ASSET_ID}.dbc"


def test_the_generated_path_rejects_a_non_uuid_asset_id() -> None:
    with pytest.raises(DbcAssetValidationError):
        asset_relative_path("customercar.dbc")


# --- digest, size, encoding, timestamp --------------------------------------


@pytest.mark.parametrize(
    "value",
    ["", DIGEST.upper(), "a" * 63, "a" * 65, "z" * 64],
    ids=["empty", "upper-case", "too-short", "too-long", "not-hex"],
)
def test_a_digest_that_is_not_a_lowercase_sha256_is_rejected(value: str) -> None:
    with pytest.raises(DbcAssetValidationError):
        make_asset(sha256=value)


@pytest.mark.parametrize("value", [-1, True, "512", 1.5])
def test_a_size_that_is_not_a_count_of_bytes_is_rejected(value: object) -> None:
    with pytest.raises(DbcAssetValidationError):
        make_asset(size_bytes=value)


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_encoding_is_rejected(value: str) -> None:
    with pytest.raises(DbcAssetValidationError):
        make_asset(encoding=value)


def test_a_naive_import_timestamp_is_rejected() -> None:
    """A local-time string without an offset would not identify an instant."""
    with pytest.raises(DbcAssetValidationError) as info:
        make_asset(imported_at=datetime(2026, 9, 16, 10, 15))

    assert info.value.details["imported_at"] == repr(datetime(2026, 9, 16, 10, 15))


def test_a_non_datetime_import_timestamp_is_rejected() -> None:
    with pytest.raises(DbcAssetValidationError):
        make_asset(imported_at="2026-09-16T10:15:00+00:00")


# --- resolving a stored path ------------------------------------------------


def test_a_registered_path_resolves_inside_the_project_dbc_directory(tmp_path: Path) -> None:
    asset_directory = tmp_path / ASSET_DIRECTORY
    asset_directory.mkdir()
    asset_file = asset_directory / f"{ASSET_ID}.dbc"
    asset_file.write_bytes(b'VERSION "1.0"\n')

    resolved = resolve_asset_path(tmp_path, make_asset())

    assert resolved == asset_file.resolve()
    assert resolved.is_file()


@pytest.mark.parametrize(
    "relative_path",
    [
        "../outside.dbc",
        "dbc/../../outside.dbc",
        "dbc/../secret/outside.dbc",
        f"dbc/{OTHER_ID}.dbc",
        "dbc/planted.dbc",
    ],
    ids=["parent", "dotted-escape", "dotted-inside", "other-asset-id", "unnamed-file"],
)
def test_a_path_that_is_not_the_one_the_identity_owns_cannot_be_expressed(
    tmp_path: Path, relative_path: str
) -> None:
    """Escapes and swaps alike are refused when the asset record is built.

    A hand-edited registry row that names this shape is refused by the repository
    as an integrity failure; this test is the model half of the same invariant.
    """
    with pytest.raises(DbcAssetValidationError) as info:
        make_asset(relative_path=relative_path)

    assert info.value.code == "dbc.invalid_asset"
    assert info.value.details["relative_path"] == relative_path


def test_a_dbc_directory_that_is_itself_a_link_out_of_the_project_is_refused(
    tmp_path: Path,
) -> None:
    """A ``dbc`` directory linked elsewhere is not a project-owned directory."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / f"{ASSET_ID}.dbc").write_bytes(b'VERSION "1.0"\n')
    project = tmp_path / "vehicle.canx"
    project.mkdir()
    try:
        os.symlink(outside, project / ASSET_DIRECTORY, target_is_directory=True)
    except (OSError, NotImplementedError) as error:  # pragma: no cover - host dependent
        pytest.skip(f"this environment cannot create a directory link: {error}")

    with pytest.raises(DbcAssetIntegrityError) as info:
        resolve_asset_path(project, make_asset())

    assert info.value.code == "dbc.asset_integrity_failed"
