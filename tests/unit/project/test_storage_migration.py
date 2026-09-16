"""Behavior tests for the V1 → V2 project database migration.

V0.2-01 shipped a metadata-only database stamped ``user_version = 1``. V0.2-02
adds the data-session/segment tables, so every existing project must migrate in
place without losing its identity or its metadata.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import canx.project.storage as storage_module
import pytest
from canx.project.errors import InvalidProjectError
from canx.project.model import ProjectMetadata
from canx.project.service import ProjectService
from canx.project.storage import (
    DATABASE_FILENAME,
    DATABASE_SCHEMA_VERSION,
    LEGACY_DATABASE_SCHEMA_VERSION,
    create_database,
    open_database,
    read_metadata,
)

PROJECT_ID = "2f1c3d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"

_V1_METADATA_TABLE = (
    "CREATE TABLE project_metadata ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), "
    "project_id TEXT NOT NULL, "
    "display_name TEXT NOT NULL, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)"
)


def _metadata() -> ProjectMetadata:
    created = datetime(2026, 9, 16, 8, 30, tzinfo=UTC)
    return ProjectMetadata(
        project_id=PROJECT_ID,
        display_name="Vehicle A",
        created_at=created,
        updated_at=created,
    )


def _write_v1_database(path: Path) -> None:
    """Write a genuine V0.2-01 database: metadata only, ``user_version = 1``."""
    metadata = _metadata()
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(_V1_METADATA_TABLE)
        connection.execute(
            "INSERT INTO project_metadata"
            " (id, project_id, display_name, created_at, updated_at)"
            " VALUES (1, ?, ?, ?, ?)",
            (
                metadata.project_id,
                metadata.display_name,
                metadata.created_at.isoformat(),
                metadata.updated_at.isoformat(),
            ),
        )
        connection.execute(f"PRAGMA user_version = {LEGACY_DATABASE_SCHEMA_VERSION}")
        connection.commit()
    finally:
        connection.close()


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def test_the_schema_version_advanced_to_two() -> None:
    """V0.2-02 owns schema version 2 and still recognizes version 1 as legacy."""
    assert DATABASE_SCHEMA_VERSION == 2
    assert LEGACY_DATABASE_SCHEMA_VERSION == 1


def test_v1_database_migrates_to_v2_and_preserves_project_identity(tmp_path: Path) -> None:
    path = tmp_path / DATABASE_FILENAME
    _write_v1_database(path)

    connection = open_database(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert read_metadata(connection) == _metadata()
    finally:
        connection.close()


def test_v1_migration_creates_the_data_session_tables(tmp_path: Path) -> None:
    path = tmp_path / DATABASE_FILENAME
    _write_v1_database(path)

    connection = open_database(path)
    try:
        assert {"project_metadata", "data_sessions", "data_segments"} <= _table_names(connection)
    finally:
        connection.close()

    reopened = open_database(path)
    try:
        assert reopened.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
    finally:
        reopened.close()


def test_migrated_v1_database_keeps_its_original_timestamps(tmp_path: Path) -> None:
    """Migration must not arbitrarily rewrite ``created_at`` or ``updated_at``."""
    path = tmp_path / DATABASE_FILENAME
    _write_v1_database(path)

    connection = open_database(path)
    try:
        metadata = read_metadata(connection)
    finally:
        connection.close()

    assert metadata == _metadata()


def test_new_database_is_created_directly_at_v2_with_the_data_tables(tmp_path: Path) -> None:
    path = tmp_path / DATABASE_FILENAME

    connection = create_database(path, _metadata())
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert {"project_metadata", "data_sessions", "data_segments"} <= _table_names(connection)
        assert read_metadata(connection) == _metadata()
    finally:
        connection.close()


def test_v2_schema_enforces_the_segment_session_foreign_key(tmp_path: Path) -> None:
    """The data_segments → data_sessions link is enforced, not merely declared."""
    path = tmp_path / DATABASE_FILENAME
    connection = create_database(path, _metadata())
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO data_segments (segment_id, session_id, segment_index,"
                " relative_path, frame_count, first_sequence, last_sequence,"
                " first_timestamp, last_timestamp, byte_size, created_at)"
                " VALUES ('s1', 'missing-session', 0, 'p', 1, 0, 0, 0.0, 1.0, 1, 'now')"
            )
    finally:
        connection.close()


def test_migration_is_idempotent_across_reopens(tmp_path: Path) -> None:
    path = tmp_path / DATABASE_FILENAME
    _write_v1_database(path)

    for _ in range(3):
        connection = open_database(path)
        try:
            assert (
                connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
            )
            assert read_metadata(connection) == _metadata()
        finally:
            connection.close()


def test_failed_migration_rolls_back_and_keeps_the_v1_project_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migration that fails halfway must leave a valid V1 project, not a hybrid."""
    path = tmp_path / DATABASE_FILENAME
    _write_v1_database(path)

    def broken_ddl() -> tuple[str, ...]:
        return (
            "CREATE TABLE data_sessions (session_id TEXT PRIMARY KEY)",
            # Malformed on purpose: raises sqlite3.OperationalError mid-migration.
            "CREATE TABLE data_segments (",
        )

    monkeypatch.setattr(storage_module, "_v2_ddl_statements", broken_ddl)

    with pytest.raises(InvalidProjectError) as info:
        open_database(path)

    assert info.value.code == "project.database_migration_failed"

    monkeypatch.undo()

    raw = sqlite3.connect(str(path))
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == LEGACY_DATABASE_SCHEMA_VERSION
        assert "data_sessions" not in _table_names(raw)
    finally:
        raw.close()

    connection = open_database(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert read_metadata(connection) == _metadata()
        assert {"data_sessions", "data_segments"} <= _table_names(connection)
    finally:
        connection.close()


def test_migration_never_rewrites_a_stamped_database_without_the_metadata_table(
    tmp_path: Path,
) -> None:
    """A ``user_version = 1`` stamp alone is not enough to migrate a foreign file."""
    path = tmp_path / DATABASE_FILENAME
    raw = sqlite3.connect(str(path))
    try:
        raw.execute("CREATE TABLE unrelated (id INTEGER)")
        raw.execute(f"PRAGMA user_version = {LEGACY_DATABASE_SCHEMA_VERSION}")
        raw.commit()
    finally:
        raw.close()

    with pytest.raises(InvalidProjectError) as info:
        open_database(path)

    assert info.value.code == "project.database_schema_invalid"

    raw = sqlite3.connect(str(path))
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == LEGACY_DATABASE_SCHEMA_VERSION
        assert "data_sessions" not in _table_names(raw)
    finally:
        raw.close()


# ---------------------------------------------------------------------------
# A version stamp alone is not a schema. V2 must be complete to open.
# ---------------------------------------------------------------------------


def _complete_v2_database(path: Path) -> None:
    """Create a full V2 database at ``path`` and release the connection."""
    create_database(path, _metadata()).close()


def _drop_table(path: Path, name: str) -> None:
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(f"DROP TABLE {name}")
        connection.commit()
    finally:
        connection.close()


def _drop_column(path: Path, table: str, column: str) -> None:
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        connection.commit()
    finally:
        connection.close()


def test_a_complete_v2_database_opens(tmp_path: Path) -> None:
    """The complete schema remains the happy path."""
    path = tmp_path / DATABASE_FILENAME
    _complete_v2_database(path)

    connection = open_database(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert read_metadata(connection) == _metadata()
    finally:
        connection.close()


@pytest.mark.parametrize("table", ["data_sessions", "data_segments"])
def test_a_v2_database_missing_a_data_table_is_rejected(tmp_path: Path, table: str) -> None:
    """A V2 stamp must not be accepted when a required table is absent."""
    path = tmp_path / DATABASE_FILENAME
    _complete_v2_database(path)
    _drop_table(path, table)

    with pytest.raises(InvalidProjectError) as info:
        open_database(path)

    assert info.value.code == "project.database_schema_invalid"
    assert info.value.details["missing_tables"] == [table]


@pytest.mark.parametrize(
    ("table", "column"),
    [("data_sessions", "updated_at"), ("data_segments", "created_at")],
)
def test_a_v2_database_missing_a_required_column_is_rejected(
    tmp_path: Path, table: str, column: str
) -> None:
    """A present table with the wrong columns is still not a usable V2 schema."""
    path = tmp_path / DATABASE_FILENAME
    _complete_v2_database(path)
    _drop_column(path, table, column)

    with pytest.raises(InvalidProjectError) as info:
        open_database(path)

    assert info.value.code == "project.database_schema_invalid"
    assert info.value.details["table"] == table
    assert info.value.details["missing_columns"] == [column]


def test_project_open_rejects_a_v2_database_missing_a_data_table(tmp_path: Path) -> None:
    """A corrupt V2 project is refused at open, not later by the data service."""
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    handle.close()
    _drop_table(root / DATABASE_FILENAME, "data_segments")

    with pytest.raises(InvalidProjectError) as info:
        ProjectService().open(root)

    assert info.value.code == "project.database_schema_invalid"
    assert info.value.details["missing_tables"] == ["data_segments"]


def test_create_database_self_checks_the_schema_it_just_wrote(tmp_path: Path) -> None:
    """Creation validates its own output rather than trusting the DDL."""
    path = tmp_path / DATABASE_FILENAME

    connection = create_database(path, _metadata())

    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert read_metadata(connection) == _metadata()
    finally:
        connection.close()
