"""SQLite-backed project metadata storage for the CAN-X project domain.

This module owns the connection lifecycle and the versioned schema. Callers
never receive a raw ``sqlite3.Error``: every failure is translated into a
:class:`~canx.project.errors.ProjectError`.
"""

import sqlite3
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from canx.project.errors import InvalidProjectError, UnsupportedProjectVersionError
from canx.project.model import ProjectMetadata, normalize_project_id

DATABASE_FILENAME = "project.db"
DATABASE_SCHEMA_VERSION = 1

_METADATA_TABLE = "project_metadata"
_METADATA_COLUMNS = ("project_id", "display_name", "created_at", "updated_at")
_CREATE_METADATA_TABLE = f"""
CREATE TABLE {_METADATA_TABLE} (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    project_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def create_database(path: Path, metadata: ProjectMetadata) -> sqlite3.Connection:
    """Create the versioned metadata schema and return the owned connection.

    Raises:
        InvalidProjectError: If the database cannot be initialized.
    """
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(path))
        connection.execute(_CREATE_METADATA_TABLE)
        connection.execute(
            f"INSERT INTO {_METADATA_TABLE}"
            " (id, project_id, display_name, created_at, updated_at)"
            " VALUES (1, ?, ?, ?, ?)",
            (
                metadata.project_id,
                metadata.display_name,
                metadata.created_at.isoformat(),
                metadata.updated_at.isoformat(),
            ),
        )
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        connection.commit()
    except sqlite3.Error as error:
        close_quietly(connection)
        raise InvalidProjectError(
            "The project database could not be initialized.",
            code="project.database_init_failed",
            details={"path": str(path)},
        ) from error
    return connection


def open_database(path: Path) -> sqlite3.Connection:
    """Open an existing database and validate its schema.

    Raises:
        InvalidProjectError: If the file is not a readable CAN-X project
            database.
        UnsupportedProjectVersionError: If the schema was written by a newer
            runtime.
    """
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(path))
        version = _read_schema_version(connection, path)
        if version > DATABASE_SCHEMA_VERSION:
            raise UnsupportedProjectVersionError(
                "The project database schema is newer than this runtime supports.",
                details={
                    "path": str(path),
                    "source": "database",
                    "schema_version": version,
                    "supported_schema_version": DATABASE_SCHEMA_VERSION,
                },
            )
        if version != DATABASE_SCHEMA_VERSION:
            raise InvalidProjectError(
                "The file is not a versioned CAN-X project database.",
                code="project.database_schema_invalid",
                details={"path": str(path), "schema_version": version},
            )
        _verify_metadata_columns(connection, path)
    except BaseException:
        close_quietly(connection)
        raise
    return connection


def read_metadata(connection: sqlite3.Connection) -> ProjectMetadata:
    """Read the single project metadata row from an opened database.

    Raises:
        InvalidProjectError: If the row is missing or holds an invalid value.
    """
    try:
        row = connection.execute(
            "SELECT project_id, display_name, created_at, updated_at"
            f" FROM {_METADATA_TABLE} WHERE id = 1"
        ).fetchone()
    except sqlite3.Error as error:
        raise InvalidProjectError(
            "The project metadata could not be read.",
            code="project.database_unreadable",
        ) from error
    if row is None:
        raise InvalidProjectError(
            "The project metadata row is missing.",
            code="project.metadata_missing",
        )
    project_id_text = str(row[0])
    try:
        project_id = normalize_project_id(project_id_text)
    except ValueError as error:
        raise InvalidProjectError(
            "The stored project identity is not a UUID.",
            code="project.metadata_invalid",
            details={"project_id": project_id_text},
        ) from error
    return ProjectMetadata(
        project_id=project_id,
        display_name=str(row[1]),
        created_at=_parse_timestamp(str(row[2]), field="created_at"),
        updated_at=_parse_timestamp(str(row[3]), field="updated_at"),
    )


def close_quietly(connection: sqlite3.Connection | None) -> None:
    """Best-effort close for failure paths where the original error must win."""
    if connection is None:
        return
    with suppress(sqlite3.Error):
        connection.close()


def _read_schema_version(connection: sqlite3.Connection, path: Path) -> int:
    try:
        row = connection.execute("PRAGMA user_version").fetchone()
    except sqlite3.Error as error:
        raise InvalidProjectError(
            "The project database could not be read.",
            code="project.database_unreadable",
            details={"path": str(path)},
        ) from error
    if row is None:
        raise InvalidProjectError(
            "The project database schema version is missing.",
            code="project.database_unreadable",
            details={"path": str(path)},
        )
    return int(row[0])


def _verify_metadata_columns(connection: sqlite3.Connection, path: Path) -> None:
    try:
        rows = connection.execute(f"PRAGMA table_info({_METADATA_TABLE})").fetchall()
    except sqlite3.Error as error:
        raise InvalidProjectError(
            "The project database schema could not be inspected.",
            code="project.database_unreadable",
            details={"path": str(path)},
        ) from error
    columns = {str(row[1]) for row in rows}
    missing = [name for name in _METADATA_COLUMNS if name not in columns]
    if missing:
        raise InvalidProjectError(
            "The project database is missing metadata columns.",
            code="project.database_schema_invalid",
            details={"path": str(path), "missing_columns": missing},
        )


def _parse_timestamp(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise InvalidProjectError(
            f"The stored {field} is not an ISO-8601 timestamp.",
            code="project.metadata_invalid",
            details={"field": field, "value": value},
        ) from error
    if parsed.tzinfo is None:
        raise InvalidProjectError(
            f"The stored {field} has no UTC offset.",
            code="project.metadata_invalid",
            details={"field": field, "value": value},
        )
    return parsed.astimezone(UTC)
