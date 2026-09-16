"""SQLite-backed project metadata storage for the CAN-X project domain.

This module owns the connection lifecycle and the versioned schema. Callers
never receive a raw ``sqlite3.Error``: every failure is translated into a
:class:`~canx.project.errors.ProjectError`.

Schema history:

* ``1`` — V0.2-01: ``project_metadata`` only.
* ``2`` — V0.2-02: adds ``data_sessions`` and ``data_segments`` so a project
  can hold persisted data sessions. A ``1`` database is migrated in place.
"""

import sqlite3
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from canx.project.errors import InvalidProjectError, UnsupportedProjectVersionError
from canx.project.model import ProjectMetadata, normalize_project_id

DATABASE_FILENAME = "project.db"
DATABASE_SCHEMA_VERSION = 2
LEGACY_DATABASE_SCHEMA_VERSION = 1

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

# The V0.2-02 data tables. The project layer owns their schema and migration;
# the ``canx.data`` repository owns writing and reading the rows.
_CREATE_DATA_SESSIONS_TABLE = """
CREATE TABLE data_sessions (
    session_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'completed', 'interrupted', 'failed')),
    started_at TEXT NOT NULL,
    ended_at TEXT NULL,
    frame_count INTEGER NOT NULL CHECK (frame_count >= 0),
    segment_count INTEGER NOT NULL CHECK (segment_count >= 0),
    first_sequence INTEGER NULL,
    last_sequence INTEGER NULL,
    first_timestamp REAL NULL,
    last_timestamp REAL NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_DATA_SEGMENTS_TABLE = """
CREATE TABLE data_segments (
    segment_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES data_sessions (session_id),
    segment_index INTEGER NOT NULL CHECK (segment_index >= 0),
    relative_path TEXT NOT NULL,
    frame_count INTEGER NOT NULL CHECK (frame_count >= 0),
    first_sequence INTEGER NOT NULL,
    last_sequence INTEGER NOT NULL,
    first_timestamp REAL NOT NULL,
    last_timestamp REAL NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    created_at TEXT NOT NULL,
    UNIQUE (session_id, segment_index),
    UNIQUE (relative_path)
)
"""


def _v2_ddl_statements() -> tuple[str, ...]:
    """Return the V2 table DDL in creation order.

    Kept behind a function so a migration failure test can inject a broken
    statement without patching the schema constants themselves.
    """
    return (_CREATE_DATA_SESSIONS_TABLE, _CREATE_DATA_SEGMENTS_TABLE)


def create_database(path: Path, metadata: ProjectMetadata) -> sqlite3.Connection:
    """Create the current-version schema and return the owned connection.

    Raises:
        InvalidProjectError: If the database cannot be initialized.
    """
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(path)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(_CREATE_METADATA_TABLE)
        for statement in _v2_ddl_statements():
            connection.execute(statement)
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
        connection.execute("COMMIT")
    except sqlite3.Error as error:
        _rollback_quietly(connection)
        close_quietly(connection)
        raise InvalidProjectError(
            "The project database could not be initialized.",
            code="project.database_init_failed",
            details={"path": str(path)},
        ) from error
    return connection


def open_database(path: Path) -> sqlite3.Connection:
    """Open an existing database, migrating a V1 project to the current schema.

    A database stamped with the legacy schema version is upgraded in place,
    inside one transaction, before it is handed back. Anything newer is refused
    rather than read partially.

    Raises:
        InvalidProjectError: If the file is not a readable CAN-X project
            database, or if a required migration fails.
        UnsupportedProjectVersionError: If the schema was written by a newer
            runtime.
    """
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(path)
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
        if version == LEGACY_DATABASE_SCHEMA_VERSION:
            # Only a database that really carries project identity may migrate;
            # a bare version stamp on a foreign file is rejected instead.
            _verify_metadata_columns(connection, path)
            migrate_database(connection, path)
        elif version != DATABASE_SCHEMA_VERSION:
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


def migrate_database(connection: sqlite3.Connection, path: Path) -> None:
    """Upgrade a legacy database to the current schema version.

    The upgrade and the version stamp commit together. If any statement fails
    the transaction is rolled back, so the caller keeps a readable previous-version
    database rather than a half-migrated one that looks valid.

    Raises:
        InvalidProjectError: If the migration cannot be committed.
    """
    source_version = _read_schema_version(connection, path)
    if source_version == DATABASE_SCHEMA_VERSION:
        return
    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in _v2_ddl_statements():
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        connection.execute("COMMIT")
    except sqlite3.Error as error:
        _rollback_quietly(connection)
        raise InvalidProjectError(
            "The project database could not be migrated to the current schema.",
            code="project.database_migration_failed",
            details={
                "path": str(path),
                "source_version": source_version,
                "target_version": DATABASE_SCHEMA_VERSION,
            },
        ) from error


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


def table_names(connection: sqlite3.Connection) -> set[str]:
    """Return the table names present in the schema.

    Raises:
        InvalidProjectError: If the schema cannot be inspected.
    """
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    except sqlite3.Error as error:
        raise InvalidProjectError(
            "The project database schema could not be inspected.",
            code="project.database_unreadable",
        ) from error
    return {str(row[0]) for row in rows}


def _connect(path: Path) -> sqlite3.Connection:
    """Open a connection with explicit transaction control and foreign keys on.

    ``isolation_level=None`` disables the stdlib's implicit transactions so DDL
    and ``PRAGMA user_version`` share one explicit transaction; foreign keys are
    not enabled by default in SQLite and must be turned on per connection.
    """
    connection = sqlite3.connect(str(path), isolation_level=None)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _rollback_quietly(connection: sqlite3.Connection | None) -> None:
    if connection is None:
        return
    with suppress(sqlite3.Error):
        connection.execute("ROLLBACK")


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
