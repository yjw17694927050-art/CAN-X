"""The project DBC asset registry: rows in, typed assets out.

The repository is the only module that sees SQLite for DBC assets. These tests
therefore check both directions — a stored row must come back as the asset that
was written — and the leak-free contract: no ``sqlite3`` exception and no
surviving file handle reach the caller.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from canx.dbc import repository
from canx.dbc.asset import DbcAsset, asset_relative_path
from canx.dbc.errors import DbcAssetIntegrityError, DbcAssetRegistryError
from canx.project.service import ProjectService
from canx.project.storage import DATABASE_FILENAME

PROJECT_ID = "2f1c3d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
FIRST_ID = "11111111-1111-4111-8111-111111111111"
SECOND_ID = "22222222-2222-4222-8222-222222222222"
THIRD_ID = "33333333-3333-4333-8333-333333333333"
DIGEST = "0123456789abcdef" * 4
IMPORTED_AT = datetime(2026, 9, 16, 10, 15, tzinfo=UTC)


def make_project(tmp_path: Path) -> tuple[Path, str]:
    """Create a real project and return its root and identity."""
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="DBC registry")
    try:
        return root, handle.project_id
    finally:
        handle.close()


def make_asset(
    project_id: str,
    *,
    asset_id: str = FIRST_ID,
    source_name: str = "vehicle.dbc",
    imported_at: datetime = IMPORTED_AT,
    encoding: str = "utf-8-sig",
    size_bytes: int = 512,
    sha256: str = DIGEST,
) -> DbcAsset:
    return DbcAsset(
        asset_id=asset_id,
        project_id=project_id,
        source_name=source_name,
        relative_path=asset_relative_path(asset_id),
        sha256=sha256,
        size_bytes=size_bytes,
        encoding=encoding,
        imported_at=imported_at,
    )


def store(root: Path, asset: DbcAsset) -> None:
    with repository.asset_connection(root) as connection:
        repository.insert_asset(connection, asset)


def list_all(root: Path, project_id: str) -> tuple[DbcAsset, ...]:
    with repository.asset_connection(root) as connection:
        return repository.list_assets(connection, project_id)


def fetch(root: Path, asset_id: str) -> DbcAsset | None:
    with repository.asset_connection(root) as connection:
        return repository.read_asset(connection, asset_id)


def raw(path: Path) -> sqlite3.Connection:
    """Open the database file directly, bypassing the repository."""
    return sqlite3.connect(str(path))


# --- round trips ------------------------------------------------------------


def test_an_inserted_asset_reads_back_field_for_field(tmp_path: Path) -> None:
    root, project_id = make_project(tmp_path)
    asset = make_asset(project_id)

    store(root, asset)

    assert fetch(root, asset.asset_id) == asset


def test_every_round_tripped_field_is_preserved_individually(tmp_path: Path) -> None:
    """A field-by-field check, so one silently swapped column would be caught."""
    root, project_id = make_project(tmp_path)
    asset = make_asset(
        project_id,
        source_name="gateway.dbc",
        imported_at=datetime(2026, 9, 16, 11, 45, 30, tzinfo=UTC),
        encoding="cp1252",
        size_bytes=0,
        sha256="f" * 64,
    )

    store(root, asset)
    stored = fetch(root, asset.asset_id)

    assert stored is not None
    assert stored.asset_id == asset.asset_id
    assert stored.project_id == asset.project_id
    assert stored.source_name == "gateway.dbc"
    assert stored.relative_path == f"dbc/{asset.asset_id}.dbc"
    assert stored.sha256 == "f" * 64
    assert stored.size_bytes == 0
    assert stored.encoding == "cp1252"
    assert stored.imported_at == datetime(2026, 9, 16, 11, 45, 30, tzinfo=UTC)


def test_an_unknown_asset_id_reads_as_none(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)

    assert fetch(root, FIRST_ID) is None


def test_an_empty_registry_lists_nothing(tmp_path: Path) -> None:
    root, project_id = make_project(tmp_path)

    assert list_all(root, project_id) == ()


def test_several_assets_can_share_one_project(tmp_path: Path) -> None:
    root, project_id = make_project(tmp_path)

    store(root, make_asset(project_id, asset_id=FIRST_ID, source_name="a.dbc"))
    store(root, make_asset(project_id, asset_id=SECOND_ID, source_name="b.dbc"))

    assert {asset.asset_id for asset in list_all(root, project_id)} == {FIRST_ID, SECOND_ID}


def test_assets_are_listed_in_import_order(tmp_path: Path) -> None:
    root, project_id = make_project(tmp_path)

    store(root, make_asset(project_id, asset_id=SECOND_ID, imported_at=IMPORTED_AT))
    store(
        root,
        make_asset(
            project_id, asset_id=FIRST_ID, imported_at=IMPORTED_AT + timedelta(minutes=5)
        ),
    )
    store(
        root,
        make_asset(
            project_id, asset_id=THIRD_ID, imported_at=IMPORTED_AT - timedelta(minutes=5)
        ),
    )

    assert [asset.asset_id for asset in list_all(root, project_id)] == [
        THIRD_ID,
        SECOND_ID,
        FIRST_ID,
    ]


def test_assets_imported_in_the_same_instant_have_a_stable_order(tmp_path: Path) -> None:
    """The order must not depend on whatever SQLite happens to return."""
    root, project_id = make_project(tmp_path)

    for asset_id in (SECOND_ID, THIRD_ID, FIRST_ID):
        store(root, make_asset(project_id, asset_id=asset_id, imported_at=IMPORTED_AT))

    order = [asset.asset_id for asset in list_all(root, project_id)]

    assert order == sorted((FIRST_ID, SECOND_ID, THIRD_ID))
    assert order == [asset.asset_id for asset in list_all(root, project_id)]


def test_another_projects_assets_are_not_listed(tmp_path: Path) -> None:
    """The registry answers for the project it is asked about."""
    root, project_id = make_project(tmp_path)
    other_project_id = "9e8d7c6b-5a4f-4321-9876-543210fedcba"
    store(root, make_asset(project_id, asset_id=FIRST_ID))
    store(root, make_asset(other_project_id, asset_id=SECOND_ID))

    assert [asset.asset_id for asset in list_all(root, project_id)] == [FIRST_ID]


def test_the_stored_identity_matches_the_project_metadata(tmp_path: Path) -> None:
    root, project_id = make_project(tmp_path)

    with repository.asset_connection(root) as connection:
        assert repository.read_project_id(connection) == project_id


# --- failure surfaces -------------------------------------------------------


def test_the_connection_is_closed_after_an_operation(tmp_path: Path) -> None:
    """A short-lived connection must not keep a Windows handle on the database."""
    root, project_id = make_project(tmp_path)

    store(root, make_asset(project_id))
    list_all(root, project_id)

    database = root / DATABASE_FILENAME
    database.unlink()

    assert not database.exists()


def test_a_missing_project_database_is_a_typed_registry_failure(tmp_path: Path) -> None:
    root = tmp_path / "absent.canx"
    root.mkdir()

    with pytest.raises(DbcAssetRegistryError) as info:
        list_all(root, PROJECT_ID)

    assert info.value.code == "dbc.project_database_missing"
    assert info.value.source == "dbc"


def test_a_database_without_the_registry_table_is_a_typed_registry_failure(
    tmp_path: Path,
) -> None:
    root, project_id = make_project(tmp_path)
    connection = raw(root / DATABASE_FILENAME)
    try:
        connection.execute("DROP TABLE dbc_assets")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DbcAssetRegistryError) as info:
        list_all(root, project_id)

    assert info.value.code == "dbc.project_database_unavailable"
    assert info.value.details["project_error_code"] == "project.database_schema_invalid"


def test_a_duplicate_asset_id_is_a_typed_registry_failure(tmp_path: Path) -> None:
    """A raw ``sqlite3.IntegrityError`` must never reach a caller."""
    root, project_id = make_project(tmp_path)
    store(root, make_asset(project_id))

    with pytest.raises(DbcAssetRegistryError) as info:
        store(root, make_asset(project_id, source_name="duplicate.dbc"))

    assert info.value.code == "dbc.asset_registry_failed"
    assert info.value.details["asset_id"] == FIRST_ID
    assert isinstance(info.value.__cause__, sqlite3.Error)


def test_a_hand_edited_row_that_is_not_an_asset_is_an_integrity_failure(
    tmp_path: Path,
) -> None:
    """The registry is a file a user can edit; a tampered row must not be returned."""
    root, project_id = make_project(tmp_path)
    connection = raw(root / DATABASE_FILENAME)
    try:
        connection.execute(
            "INSERT INTO dbc_assets (asset_id, project_id, source_name, relative_path,"
            " sha256, size_bytes, encoding, imported_at)"
            " VALUES (?, ?, 'outside.dbc', '../outside.dbc', ?, 10, 'utf-8-sig', ?)",
            (FIRST_ID, project_id, DIGEST, IMPORTED_AT.isoformat()),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DbcAssetIntegrityError) as info:
        fetch(root, FIRST_ID)

    assert info.value.code == "dbc.asset_integrity_failed"
    assert info.value.details["cause"] == "dbc.invalid_asset"


def test_a_row_with_an_unparseable_timestamp_is_an_integrity_failure(tmp_path: Path) -> None:
    root, project_id = make_project(tmp_path)
    connection = raw(root / DATABASE_FILENAME)
    try:
        connection.execute(
            "INSERT INTO dbc_assets (asset_id, project_id, source_name, relative_path,"
            " sha256, size_bytes, encoding, imported_at)"
            " VALUES (?, ?, 'vehicle.dbc', ?, ?, 10, 'utf-8-sig', 'yesterday')",
            (FIRST_ID, project_id, asset_relative_path(FIRST_ID), DIGEST),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DbcAssetIntegrityError):
        fetch(root, FIRST_ID)


def test_a_row_with_a_naive_timestamp_is_an_integrity_failure(tmp_path: Path) -> None:
    root, project_id = make_project(tmp_path)
    connection = raw(root / DATABASE_FILENAME)
    try:
        connection.execute(
            "INSERT INTO dbc_assets (asset_id, project_id, source_name, relative_path,"
            " sha256, size_bytes, encoding, imported_at)"
            " VALUES (?, ?, 'vehicle.dbc', ?, ?, 10, 'utf-8-sig', '2026-09-16T10:15:00')",
            (FIRST_ID, project_id, asset_relative_path(FIRST_ID), DIGEST),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DbcAssetIntegrityError) as info:
        fetch(root, FIRST_ID)

    assert info.value.details["field"] == "imported_at"
