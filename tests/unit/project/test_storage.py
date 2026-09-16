"""Behavior tests for the SQLite project metadata store."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from canx.project.errors import InvalidProjectError, UnsupportedProjectVersionError
from canx.project.model import ProjectMetadata
from canx.project.storage import (
    DATABASE_FILENAME,
    DATABASE_SCHEMA_VERSION,
    create_database,
    open_database,
    read_metadata,
)

PROJECT_ID = "2f1c3d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"


def _metadata() -> ProjectMetadata:
    created = datetime(2026, 9, 16, 8, 30, tzinfo=UTC)
    return ProjectMetadata(
        project_id=PROJECT_ID,
        display_name="Vehicle A",
        created_at=created,
        updated_at=created,
    )


def _raw_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(path))


def test_create_database_persists_identity_and_utc_timestamps(tmp_path: Path) -> None:
    """Metadata survives both the creating connection and a fresh open."""
    path = tmp_path / DATABASE_FILENAME

    connection = create_database(path, _metadata())
    try:
        assert read_metadata(connection) == _metadata()
    finally:
        connection.close()

    reopened = open_database(path)
    try:
        assert read_metadata(reopened) == _metadata()
    finally:
        reopened.close()


def test_create_database_writes_a_versioned_metadata_schema(tmp_path: Path) -> None:
    """The schema is versioned so future migrations have a boundary."""
    path = tmp_path / DATABASE_FILENAME

    connection = create_database(path, _metadata())
    try:
        version = connection.execute("PRAGMA user_version").fetchone()
        assert version is not None
        assert version[0] == DATABASE_SCHEMA_VERSION
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(project_metadata)")
        }
        assert columns == {"id", "project_id", "display_name", "created_at", "updated_at"}
    finally:
        connection.close()


def test_open_rejects_a_newer_database_schema(tmp_path: Path) -> None:
    """A database written by a future runtime is refused, not read partially."""
    path = tmp_path / DATABASE_FILENAME
    connection = create_database(path, _metadata())
    connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION + 1}")
    connection.commit()
    connection.close()

    with pytest.raises(UnsupportedProjectVersionError) as info:
        open_database(path)

    assert info.value.details["source"] == "database"
    assert info.value.details["schema_version"] == DATABASE_SCHEMA_VERSION + 1


def test_open_rejects_a_foreign_sqlite_database(tmp_path: Path) -> None:
    """An unrelated SQLite file is not a CAN-X project database."""
    path = tmp_path / DATABASE_FILENAME
    raw = _raw_connection(path)
    try:
        raw.execute("CREATE TABLE unrelated (id INTEGER)")
        raw.commit()
    finally:
        raw.close()

    with pytest.raises(InvalidProjectError) as info:
        open_database(path)

    assert info.value.code == "project.database_schema_invalid"


def test_open_rejects_a_non_database_file(tmp_path: Path) -> None:
    """A file that is not SQLite at all is rejected with a typed error."""
    path = tmp_path / DATABASE_FILENAME
    path.write_bytes(b"CANX not a database")

    with pytest.raises(InvalidProjectError) as info:
        open_database(path)

    assert info.value.code == "project.database_unreadable"


def test_open_rejects_a_versioned_database_without_the_metadata_table(
    tmp_path: Path,
) -> None:
    """A correct version stamp is not enough; the schema itself is verified."""
    path = tmp_path / DATABASE_FILENAME
    raw = _raw_connection(path)
    try:
        raw.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        raw.commit()
    finally:
        raw.close()

    with pytest.raises(InvalidProjectError) as info:
        open_database(path)

    assert info.value.code == "project.database_schema_invalid"
    assert info.value.details["missing_columns"] == [
        "project_id",
        "display_name",
        "created_at",
        "updated_at",
    ]


def test_open_releases_the_connection_when_validation_fails(tmp_path: Path) -> None:
    """A rejected database must not leave an open handle behind."""
    path = tmp_path / DATABASE_FILENAME
    path.write_bytes(b"CANX not a database")

    with pytest.raises(InvalidProjectError):
        open_database(path)

    path.unlink()
    assert not path.exists()


def test_read_metadata_rejects_a_missing_row(tmp_path: Path) -> None:
    """An empty-but-valid schema has no project identity to report."""
    path = tmp_path / DATABASE_FILENAME
    raw = _raw_connection(path)
    try:
        raw.execute(
            "CREATE TABLE project_metadata ("
            "id INTEGER PRIMARY KEY, project_id TEXT NOT NULL, display_name TEXT NOT NULL,"
            " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        raw.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        raw.commit()
    finally:
        raw.close()

    connection = open_database(path)
    try:
        with pytest.raises(InvalidProjectError) as info:
            read_metadata(connection)
    finally:
        connection.close()

    assert info.value.code == "project.metadata_missing"
