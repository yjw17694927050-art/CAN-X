"""Schema V3: the DBC asset registry table and the migrations that create it.

V0.3-03 adds ``dbc_assets`` so a project can own imported DBC files. Three paths
have to end at the same schema — a brand-new project, a V2 project and a V1
project — and a failed upgrade has to leave the *previous* version complete
rather than half migrated.

The historical DDL below is written out here on purpose: a migration fixture must
describe what the old runtime actually wrote, not what today's schema constants
happen to build.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import canx.project.storage as storage_module
import pytest
from canx.project.errors import InvalidProjectError
from canx.project.model import ProjectMetadata
from canx.project.storage import (
    DATABASE_FILENAME,
    DATABASE_SCHEMA_VERSION,
    LEGACY_DATABASE_SCHEMA_VERSION,
    create_database,
    open_database,
    read_metadata,
)

PROJECT_ID = "2f1c3d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
SESSION_ID = "3b0f9a5c-1d2e-4f3a-8b4c-5d6e7f8091a2"
SEGMENT_ID = "7d1c2b3a-4e5f-4061-8273-94a5b6c7d8e9"
STREAM_ID = "v2-stream"
V2_SCHEMA_VERSION = 2

_CREATED_AT = "2026-09-16T08:30:00+00:00"
_SESSION_STARTED_AT = "2026-09-16T09:00:00+00:00"
_SESSION_ENDED_AT = "2026-09-16T09:00:05+00:00"

_V1_METADATA_TABLE = (
    "CREATE TABLE project_metadata ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), "
    "project_id TEXT NOT NULL, "
    "display_name TEXT NOT NULL, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)"
)

# The V2 data tables, exactly as the V0.2-02 runtime declared them.
_V2_SESSIONS_TABLE = (
    "CREATE TABLE data_sessions ("
    "session_id TEXT PRIMARY KEY, "
    "project_id TEXT NOT NULL, "
    "stream_id TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK (state IN ('active', 'completed', 'interrupted', 'failed')), "
    "started_at TEXT NOT NULL, "
    "ended_at TEXT NULL, "
    "frame_count INTEGER NOT NULL CHECK (frame_count >= 0), "
    "segment_count INTEGER NOT NULL CHECK (segment_count >= 0), "
    "first_sequence INTEGER NULL, "
    "last_sequence INTEGER NULL, "
    "first_timestamp REAL NULL, "
    "last_timestamp REAL NULL, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)"
)
_V2_SEGMENTS_TABLE = (
    "CREATE TABLE data_segments ("
    "segment_id TEXT PRIMARY KEY, "
    "session_id TEXT NOT NULL REFERENCES data_sessions (session_id), "
    "segment_index INTEGER NOT NULL CHECK (segment_index >= 0), "
    "relative_path TEXT NOT NULL, "
    "frame_count INTEGER NOT NULL CHECK (frame_count >= 0), "
    "first_sequence INTEGER NOT NULL, "
    "last_sequence INTEGER NOT NULL, "
    "first_timestamp REAL NOT NULL, "
    "last_timestamp REAL NOT NULL, "
    "byte_size INTEGER NOT NULL CHECK (byte_size >= 0), "
    "created_at TEXT NOT NULL, "
    "UNIQUE (session_id, segment_index), "
    "UNIQUE (relative_path))"
)

_ASSET_COLUMNS = (
    "asset_id",
    "project_id",
    "source_name",
    "relative_path",
    "sha256",
    "size_bytes",
    "encoding",
    "imported_at",
)


def _metadata() -> ProjectMetadata:
    created = datetime(2026, 9, 16, 8, 30, tzinfo=UTC)
    return ProjectMetadata(
        project_id=PROJECT_ID,
        display_name="Vehicle A",
        created_at=created,
        updated_at=created,
    )


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _raw(path: Path) -> sqlite3.Connection:
    """Open the database file with plain sqlite3, bypassing the project layer."""
    return sqlite3.connect(str(path))


def _write_v1_database(path: Path) -> None:
    """Write exactly what the V0.2-01 runtime produced: metadata only, version 1."""
    connection = _raw(path)
    try:
        connection.execute(_V1_METADATA_TABLE)
        connection.execute(
            "INSERT INTO project_metadata"
            " (id, project_id, display_name, created_at, updated_at)"
            " VALUES (1, ?, ?, ?, ?)",
            (PROJECT_ID, "Vehicle A", _CREATED_AT, _CREATED_AT),
        )
        connection.execute(f"PRAGMA user_version = {LEGACY_DATABASE_SCHEMA_VERSION}")
        connection.commit()
    finally:
        connection.close()


def _write_v2_database(path: Path) -> None:
    """Write a genuine V2 database holding one completed session and one segment."""
    connection = _raw(path)
    try:
        connection.execute(_V1_METADATA_TABLE)
        connection.execute(_V2_SESSIONS_TABLE)
        connection.execute(_V2_SEGMENTS_TABLE)
        connection.execute(
            "INSERT INTO project_metadata"
            " (id, project_id, display_name, created_at, updated_at)"
            " VALUES (1, ?, ?, ?, ?)",
            (PROJECT_ID, "Vehicle A", _CREATED_AT, _CREATED_AT),
        )
        connection.execute(
            "INSERT INTO data_sessions (session_id, project_id, stream_id, state,"
            " started_at, ended_at, frame_count, segment_count, first_sequence,"
            " last_sequence, first_timestamp, last_timestamp, created_at, updated_at)"
            " VALUES (?, ?, ?, 'completed', ?, ?, 4, 1, 0, 3, 0.0, 3.0, ?, ?)",
            (
                SESSION_ID,
                PROJECT_ID,
                STREAM_ID,
                _SESSION_STARTED_AT,
                _SESSION_ENDED_AT,
                _CREATED_AT,
                _CREATED_AT,
            ),
        )
        connection.execute(
            "INSERT INTO data_segments (segment_id, session_id, segment_index,"
            " relative_path, frame_count, first_sequence, last_sequence,"
            " first_timestamp, last_timestamp, byte_size, created_at)"
            " VALUES (?, ?, 0, ?, 4, 0, 3, 0.0, 3.0, 2048, ?)",
            (
                SEGMENT_ID,
                SESSION_ID,
                f"data/sessions/{SESSION_ID}/segments/000000.parquet",
                _CREATED_AT,
            ),
        )
        connection.execute(f"PRAGMA user_version = {V2_SCHEMA_VERSION}")
        connection.commit()
    finally:
        connection.close()


def _stored_session(connection: sqlite3.Connection) -> tuple[object, ...]:
    row = connection.execute(
        "SELECT session_id, project_id, stream_id, state, started_at, ended_at,"
        " frame_count, segment_count, first_sequence, last_sequence, first_timestamp,"
        " last_timestamp, created_at, updated_at FROM data_sessions"
    ).fetchone()
    assert row is not None
    return tuple(row)


def _stored_segment(connection: sqlite3.Connection) -> tuple[object, ...]:
    row = connection.execute(
        "SELECT segment_id, session_id, segment_index, relative_path, frame_count,"
        " first_sequence, last_sequence, first_timestamp, last_timestamp, byte_size,"
        " created_at FROM data_segments"
    ).fetchone()
    assert row is not None
    return tuple(row)


def _broken_v3_ddl() -> tuple[str, ...]:
    """A V3 step that fails after its first statement is valid on purpose."""
    return (
        "CREATE TABLE dbc_assets (asset_id TEXT PRIMARY KEY)",
        # Malformed on purpose: raises sqlite3.OperationalError mid-migration.
        "CREATE TABLE dbc_asset_index (",
    )


# --- a new project starts at V3 ---------------------------------------------


def test_a_new_database_is_created_at_v3_with_the_dbc_asset_registry(tmp_path: Path) -> None:
    path = tmp_path / DATABASE_FILENAME

    connection = create_database(path, _metadata())
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert _table_names(connection) >= {
            "project_metadata",
            "data_sessions",
            "data_segments",
            "dbc_assets",
        }
        assert _column_names(connection, "dbc_assets") == set(_ASSET_COLUMNS)
        assert read_metadata(connection) == _metadata()
    finally:
        connection.close()


def test_the_registry_table_enforces_a_non_negative_size(tmp_path: Path) -> None:
    """The declared bounds are enforced by the database, not merely documented."""
    connection = create_database(tmp_path / DATABASE_FILENAME, _metadata())
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO dbc_assets (asset_id, project_id, source_name,"
                " relative_path, sha256, size_bytes, encoding, imported_at)"
                " VALUES ('3b0f9a5c-1d2e-4f3a-8b4c-5d6e7f8091a2', ?, 'a.dbc',"
                " 'dbc/a.dbc', ?, -1, 'utf-8-sig', ?)",
                (PROJECT_ID, "0" * 64, _CREATED_AT),
            )
    finally:
        connection.close()


def test_the_registry_table_requires_canonical_asset_and_digest_lengths(tmp_path: Path) -> None:
    """A short asset id or digest cannot reach the registry even by hand."""
    connection = create_database(tmp_path / DATABASE_FILENAME, _metadata())
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO dbc_assets (asset_id, project_id, source_name,"
                " relative_path, sha256, size_bytes, encoding, imported_at)"
                " VALUES ('not-a-uuid', ?, 'a.dbc', 'dbc/a.dbc', ?, 0, 'utf-8-sig', ?)",
                (PROJECT_ID, "0" * 64, _CREATED_AT),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO dbc_assets (asset_id, project_id, source_name,"
                " relative_path, sha256, size_bytes, encoding, imported_at)"
                " VALUES ('3b0f9a5c-1d2e-4f3a-8b4c-5d6e7f8091a2', ?, 'a.dbc',"
                " 'dbc/a.dbc', 'short', 0, 'utf-8-sig', ?)",
                (PROJECT_ID, _CREATED_AT),
            )
    finally:
        connection.close()


# --- V2 -> V3 ---------------------------------------------------------------


def test_a_v2_database_migrates_to_v3_and_keeps_its_persisted_data(tmp_path: Path) -> None:
    """The compatibility gate: real V0.2-02 session metadata survives the upgrade."""
    path = tmp_path / DATABASE_FILENAME
    _write_v2_database(path)
    raw = _raw(path)
    try:
        session_before = _stored_session(raw)
        segment_before = _stored_segment(raw)
    finally:
        raw.close()

    connection = open_database(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert "dbc_assets" in _table_names(connection)
        assert _column_names(connection, "dbc_assets") == set(_ASSET_COLUMNS)
        assert read_metadata(connection) == _metadata()
        assert _stored_session(connection) == session_before
        assert _stored_segment(connection) == segment_before
    finally:
        connection.close()


def test_the_v2_migration_is_idempotent_across_reopens(tmp_path: Path) -> None:
    path = tmp_path / DATABASE_FILENAME
    _write_v2_database(path)

    for _ in range(3):
        connection = open_database(path)
        try:
            assert (
                connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
            )
            assert read_metadata(connection) == _metadata()
        finally:
            connection.close()


# --- V1 -> V3 ---------------------------------------------------------------


def test_a_v1_database_migrates_straight_to_v3_in_one_step(tmp_path: Path) -> None:
    """A V0.2-01 project skips nothing: it gains the V2 *and* the V3 tables."""
    path = tmp_path / DATABASE_FILENAME
    _write_v1_database(path)

    connection = open_database(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert _table_names(connection) >= {
            "project_metadata",
            "data_sessions",
            "data_segments",
            "dbc_assets",
        }
        assert read_metadata(connection) == _metadata()
    finally:
        connection.close()


# --- failed migrations keep the previous version complete -------------------


def test_a_failed_v2_to_v3_migration_leaves_a_complete_v2_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A V2 project whose upgrade fails must still be a complete V2 project."""
    path = tmp_path / DATABASE_FILENAME
    _write_v2_database(path)
    monkeypatch.setattr(storage_module, "_v3_ddl_statements", _broken_v3_ddl)

    with pytest.raises(InvalidProjectError) as info:
        open_database(path)

    assert info.value.code == "project.database_migration_failed"
    assert info.value.details["source_version"] == V2_SCHEMA_VERSION
    assert info.value.details["target_version"] == DATABASE_SCHEMA_VERSION

    monkeypatch.undo()

    raw = _raw(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == V2_SCHEMA_VERSION
        assert "dbc_assets" not in _table_names(raw)
        assert _table_names(raw) >= {"project_metadata", "data_sessions", "data_segments"}
        assert _stored_session(raw)[0] == SESSION_ID
        assert _stored_segment(raw)[0] == SEGMENT_ID
    finally:
        raw.close()

    connection = open_database(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert "dbc_assets" in _table_names(connection)
    finally:
        connection.close()


def test_a_failed_v1_to_v3_migration_rolls_back_the_v2_step_as_well(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A V1 → V3 upgrade is one transaction: a V3 failure must not leave V2 committed."""
    path = tmp_path / DATABASE_FILENAME
    _write_v1_database(path)
    monkeypatch.setattr(storage_module, "_v3_ddl_statements", _broken_v3_ddl)

    with pytest.raises(InvalidProjectError) as info:
        open_database(path)

    assert info.value.code == "project.database_migration_failed"
    assert info.value.details["source_version"] == LEGACY_DATABASE_SCHEMA_VERSION

    monkeypatch.undo()

    raw = _raw(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == LEGACY_DATABASE_SCHEMA_VERSION
        assert _table_names(raw) == {"project_metadata"}
        assert raw.execute("SELECT display_name FROM project_metadata").fetchone()[0] == "Vehicle A"
    finally:
        raw.close()

    connection = open_database(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert read_metadata(connection) == _metadata()
    finally:
        connection.close()


# --- a version stamp is not a schema ----------------------------------------


def test_a_v3_database_missing_the_registry_table_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / DATABASE_FILENAME
    create_database(path, _metadata()).close()
    raw = _raw(path)
    try:
        raw.execute("DROP TABLE dbc_assets")
        raw.commit()
    finally:
        raw.close()

    with pytest.raises(InvalidProjectError) as info:
        open_database(path)

    assert info.value.code == "project.database_schema_invalid"
    assert info.value.details["missing_tables"] == ["dbc_assets"]


@pytest.mark.parametrize("column", ["encoding", "sha256", "imported_at"])
def test_a_v3_database_missing_a_registry_column_is_rejected(
    tmp_path: Path, column: str
) -> None:
    """A registry table with the wrong columns is still not a usable V3 schema."""
    path = tmp_path / DATABASE_FILENAME
    create_database(path, _metadata()).close()
    raw = _raw(path)
    try:
        raw.execute(f"ALTER TABLE dbc_assets DROP COLUMN {column}")
        raw.commit()
    finally:
        raw.close()

    with pytest.raises(InvalidProjectError) as info:
        open_database(path)

    assert info.value.code == "project.database_schema_invalid"
    assert info.value.details["table"] == "dbc_assets"
    assert info.value.details["missing_columns"] == [column]


def test_a_v3_database_missing_a_data_table_is_rejected(tmp_path: Path) -> None:
    """Upgrading the schema must not stop verifying the tables it already had."""
    path = tmp_path / DATABASE_FILENAME
    create_database(path, _metadata()).close()
    raw = _raw(path)
    try:
        raw.execute("DROP TABLE data_segments")
        raw.commit()
    finally:
        raw.close()

    with pytest.raises(InvalidProjectError) as info:
        open_database(path)

    assert info.value.code == "project.database_schema_invalid"
    assert info.value.details["missing_tables"] == ["data_segments"]
