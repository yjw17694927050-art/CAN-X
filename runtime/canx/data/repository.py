"""SQLite persistence for data sessions and segments (internal).

Raw SQLite stays behind this module: every function returns typed models, and no
``sqlite3.Connection``, ``Cursor`` or ``Row`` is ever handed to a caller. Each
public function opens its own short-lived connection through
:func:`data_connection`, so a data operation can never leak a Windows file
handle into the project directory.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from canx.data.errors import (
    DataError,
    DataIntegrityError,
    DataSessionError,
    DataStorageError,
)
from canx.data.model import DataSegment, DataSession, DataSessionState
from canx.project.errors import ProjectError
from canx.project.storage import (
    DATABASE_FILENAME,
    close_quietly,
    open_database,
    read_metadata,
)

SESSIONS_TABLE = "data_sessions"
SEGMENTS_TABLE = "data_segments"

_REQUIRED_TABLES = (SESSIONS_TABLE, SEGMENTS_TABLE)

_SESSION_COLUMNS = (
    "session_id, project_id, stream_id, state, started_at, ended_at, frame_count,"
    " segment_count, first_sequence, last_sequence, first_timestamp, last_timestamp,"
    " created_at, updated_at"
)
_SEGMENT_COLUMNS = (
    "segment_id, session_id, segment_index, relative_path, frame_count, first_sequence,"
    " last_sequence, first_timestamp, last_timestamp, byte_size, created_at"
)
_SESSION_UPDATE = (
    f"UPDATE {SESSIONS_TABLE} SET"
    " project_id = ?, stream_id = ?, state = ?, started_at = ?, ended_at = ?,"
    " frame_count = ?, segment_count = ?, first_sequence = ?, last_sequence = ?,"
    " first_timestamp = ?, last_timestamp = ?, created_at = ?, updated_at = ?"
    " WHERE session_id = ?"
)
_SESSION_ADVANCE = (
    f"UPDATE {SESSIONS_TABLE} SET state = ?, ended_at = ?, updated_at = ?"
    " WHERE session_id = ? AND state = ?"
)


@contextmanager
def data_connection(project_root: Path) -> Iterator[sqlite3.Connection]:
    """Yield a short-lived connection to the project database.

    The connection is validated against the project schema, has foreign keys
    enabled, and is always closed before the context exits.

    Raises:
        DataStorageError: If the project database is missing, unreadable, of an
            unsupported schema, or missing the data tables.
    """
    database_path = project_root / DATABASE_FILENAME
    if not database_path.is_file():
        raise DataStorageError(
            "The project database is missing.",
            code="data.project_database_missing",
            details={"path": str(database_path)},
        )
    try:
        connection = open_database(database_path)
    except ProjectError as error:
        raise DataStorageError(
            "The project database is not available for data storage.",
            code="data.project_database_unavailable",
            details={
                "path": str(database_path),
                "project_error_code": error.code,
            },
        ) from error
    try:
        _require_tables(connection, database_path)
        yield connection
    finally:
        close_quietly(connection)


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a unit of work in one explicit SQLite transaction."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        with suppress(sqlite3.Error):
            connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def read_project_id(connection: sqlite3.Connection) -> str:
    """Return the identity stored in the project metadata row.

    Raises:
        DataStorageError: If the identity cannot be read.
    """
    try:
        return read_metadata(connection).project_id
    except ProjectError as error:
        raise DataStorageError(
            "The project identity could not be read for data storage.",
            code="data.project_identity_unavailable",
            details={"project_error_code": error.code},
        ) from error


def insert_session(connection: sqlite3.Connection, session: DataSession) -> None:
    """Insert a new session row.

    Raises:
        DataStorageError: If the row could not be committed.
    """
    placeholders = ", ".join("?" for _ in range(14))
    with _storage_errors(
        code="data.session.insert_failed", session_id=session.session_id
    ), transaction(connection):
        connection.execute(
            f"INSERT INTO {SESSIONS_TABLE} ({_SESSION_COLUMNS}) VALUES ({placeholders})",
            _session_values(session),
        )


def update_session(connection: sqlite3.Connection, session: DataSession) -> None:
    """Rewrite every mutable column of an existing session row.

    The caller passes absolute aggregate values, so a failed write can never
    leave the counters partially advanced.

    Raises:
        DataStorageError: If the row could not be committed.
    """
    with _storage_errors(
        code="data.session.update_failed", session_id=session.session_id
    ), transaction(connection):
        connection.execute(_SESSION_UPDATE, (*_session_values(session)[1:], session.session_id))


def advance_session_state(connection: sqlite3.Connection, session: DataSession) -> bool:
    """Advance an ``ACTIVE`` session row to ``session.state``; report who won.

    Only a row still in ``ACTIVE`` is touched. The database therefore decides
    between two concurrent terminal writers — a session another writer already
    completed or failed is never overwritten, and the loser learns it lost
    instead of silently rewriting the winner's decision. This is what makes the
    terminal transition single-shot while the recorder and the runtime can reach
    it from different threads.

    Returns:
        ``True`` when this call advanced the row, ``False`` when the row was no
        longer ``ACTIVE`` (another writer got there first).

    Raises:
        DataStorageError: If the row could not be committed.
    """
    with _storage_errors(
        code="data.session.update_failed", session_id=session.session_id
    ), transaction(connection):
        cursor = connection.execute(
            _SESSION_ADVANCE,
            (
                session.state.value,
                None if session.ended_at is None else session.ended_at.isoformat(),
                session.updated_at.isoformat(),
                session.session_id,
                DataSessionState.ACTIVE.value,
            ),
        )
        return cursor.rowcount == 1


def register_segment(
    connection: sqlite3.Connection, *, segment: DataSegment, session: DataSession
) -> None:
    """Register a committed segment and advance the session aggregate atomically.

    Either both rows land or neither does, so the session counters can never
    claim a segment that was not recorded.

    Raises:
        DataStorageError: If the rows could not be committed.
    """
    placeholders = ", ".join("?" for _ in range(11))
    with _storage_errors(
        code="data.session.registration_failed", session_id=session.session_id
    ), transaction(connection):
        connection.execute(
            f"INSERT INTO {SEGMENTS_TABLE} ({_SEGMENT_COLUMNS}) VALUES ({placeholders})",
            _segment_values(segment),
        )
        connection.execute(_SESSION_UPDATE, (*_session_values(session)[1:], session.session_id))


def fail_active_session(
    connection: sqlite3.Connection, *, session_id: str, at: datetime
) -> bool:
    """Move one ``ACTIVE`` session to ``FAILED``; report whether this call won.

    The durable sibling of :func:`advance_session_state`, for a writer that no
    longer holds the session object — typically after its own worker was
    abandoned mid-write. Only an ``ACTIVE`` row is touched, so a session another
    writer already completed is never overwritten.

    Returns:
        ``True`` when this call advanced the row, ``False`` when the row was no
        longer ``ACTIVE``.

    Raises:
        DataStorageError: If the update could not be committed.
    """
    with _storage_errors(
        code="data.session.update_failed", session_id=session_id
    ), transaction(connection):
        cursor = connection.execute(
            _SESSION_ADVANCE,
            (
                DataSessionState.FAILED.value,
                None,
                at.isoformat(),
                session_id,
                DataSessionState.ACTIVE.value,
            ),
        )
        return cursor.rowcount == 1


def mark_session_interrupted(
    connection: sqlite3.Connection, *, session_id: str, at: datetime
) -> None:
    """Move one ``ACTIVE`` session to ``INTERRUPTED``.

    Only an ``ACTIVE`` row is touched; a completed, failed or already
    interrupted session keeps its recorded state.

    Raises:
        DataStorageError: If the update could not be committed.
    """
    with _storage_errors(
        code="data.session.recovery_failed", session_id=session_id
    ), transaction(connection):
        connection.execute(
            f"UPDATE {SESSIONS_TABLE} SET state = ?, ended_at = ?, updated_at = ?"
            " WHERE session_id = ? AND state = ?",
            (
                DataSessionState.INTERRUPTED.value,
                at.isoformat(),
                at.isoformat(),
                session_id,
                DataSessionState.ACTIVE.value,
            ),
        )


def read_session(connection: sqlite3.Connection, session_id: str) -> DataSession | None:
    """Return one session, or ``None`` when it is not registered."""
    row = _fetch_one(
        connection,
        f"SELECT {_SESSION_COLUMNS} FROM {SESSIONS_TABLE} WHERE session_id = ?",
        (session_id,),
    )
    return None if row is None else _session_from_row(row)


def list_sessions(connection: sqlite3.Connection) -> tuple[DataSession, ...]:
    """Return every registered session in deterministic order."""
    rows = _fetch_all(
        connection,
        f"SELECT {_SESSION_COLUMNS} FROM {SESSIONS_TABLE}"
        " ORDER BY started_at ASC, session_id ASC",
        (),
    )
    return tuple(_session_from_row(row) for row in rows)


def list_sessions_in_state(
    connection: sqlite3.Connection, state: DataSessionState
) -> tuple[DataSession, ...]:
    """Return every session currently in ``state``, in deterministic order."""
    rows = _fetch_all(
        connection,
        f"SELECT {_SESSION_COLUMNS} FROM {SESSIONS_TABLE} WHERE state = ?"
        " ORDER BY started_at ASC, session_id ASC",
        (state.value,),
    )
    return tuple(_session_from_row(row) for row in rows)


def list_segments(connection: sqlite3.Connection, session_id: str) -> tuple[DataSegment, ...]:
    """Return every registered segment of one session, ordered by index."""
    rows = _fetch_all(
        connection,
        f"SELECT {_SEGMENT_COLUMNS} FROM {SEGMENTS_TABLE} WHERE session_id = ?"
        " ORDER BY segment_index ASC",
        (session_id,),
    )
    return tuple(_segment_from_row(row) for row in rows)


def list_all_segments(connection: sqlite3.Connection) -> tuple[DataSegment, ...]:
    """Return every registered segment in deterministic order."""
    rows = _fetch_all(
        connection,
        f"SELECT {_SEGMENT_COLUMNS} FROM {SEGMENTS_TABLE}"
        " ORDER BY session_id ASC, segment_index ASC",
        (),
    )
    return tuple(_segment_from_row(row) for row in rows)


def read_segment_row(
    connection: sqlite3.Connection, session_id: str, segment_index: int
) -> DataSegment | None:
    """Return one registered segment row, or ``None`` when it is not registered."""
    row = _fetch_one(
        connection,
        f"SELECT {_SEGMENT_COLUMNS} FROM {SEGMENTS_TABLE}"
        " WHERE session_id = ? AND segment_index = ?",
        (session_id, segment_index),
    )
    return None if row is None else _segment_from_row(row)


def require_registered_session(session: DataSession | None, session_id: str) -> DataSession:
    """Return ``session`` or raise when the session is not registered.

    Raises:
        DataSessionError: If no session with ``session_id`` is registered.
    """
    if session is None:
        raise DataSessionError(
            "The data session is not registered in this project.",
            code="data.session.not_found",
            details={"session_id": session_id},
        )
    return session


def _require_tables(connection: sqlite3.Connection, path: Path) -> None:
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        names = {str(row[0]) for row in rows}
    except sqlite3.Error as error:
        raise DataStorageError(
            "The project database schema could not be inspected.",
            code="data.project_database_unavailable",
            details={"path": str(path)},
        ) from error
    missing = [name for name in _REQUIRED_TABLES if name not in names]
    if missing:
        raise DataStorageError(
            "The project database is missing the data-session tables.",
            code="data.schema_missing",
            details={"path": str(path), "missing_tables": missing},
        )


@contextmanager
def _storage_errors(*, code: str, session_id: str) -> Iterator[None]:
    try:
        yield
    except sqlite3.Error as error:
        raise DataStorageError(
            "The data-session metadata could not be persisted.",
            code=code,
            details={"session_id": session_id, "error": str(error)},
        ) from error


def _fetch_one(
    connection: sqlite3.Connection, statement: str, parameters: tuple[object, ...]
) -> Sequence[Any] | None:
    try:
        row: Sequence[Any] | None = connection.execute(statement, parameters).fetchone()
    except sqlite3.Error as error:
        raise DataStorageError(
            "The data-session metadata could not be read.",
            code="data.session.read_failed",
        ) from error
    return row


def _fetch_all(
    connection: sqlite3.Connection, statement: str, parameters: tuple[object, ...]
) -> list[Sequence[Any]]:
    try:
        rows = list(connection.execute(statement, parameters).fetchall())
    except sqlite3.Error as error:
        raise DataStorageError(
            "The data-session metadata could not be read.",
            code="data.session.read_failed",
        ) from error
    return rows


def _session_values(session: DataSession) -> tuple[object, ...]:
    return (
        session.session_id,
        session.project_id,
        session.stream_id,
        session.state.value,
        session.started_at.isoformat(),
        None if session.ended_at is None else session.ended_at.isoformat(),
        session.frame_count,
        session.segment_count,
        session.first_sequence,
        session.last_sequence,
        session.first_timestamp,
        session.last_timestamp,
        session.created_at.isoformat(),
        session.updated_at.isoformat(),
    )


def _segment_values(segment: DataSegment) -> tuple[object, ...]:
    return (
        segment.segment_id,
        segment.session_id,
        segment.segment_index,
        segment.relative_path,
        segment.frame_count,
        segment.first_sequence,
        segment.last_sequence,
        segment.first_timestamp,
        segment.last_timestamp,
        segment.byte_size,
        segment.created_at.isoformat(),
    )


def _session_from_row(row: Sequence[Any]) -> DataSession:
    try:
        return DataSession(
            session_id=str(row[0]),
            project_id=str(row[1]),
            stream_id=str(row[2]),
            state=DataSessionState(str(row[3])),
            started_at=_parse_timestamp(row[4], field="started_at"),
            ended_at=None if row[5] is None else _parse_timestamp(row[5], field="ended_at"),
            frame_count=int(row[6]),
            segment_count=int(row[7]),
            first_sequence=None if row[8] is None else int(row[8]),
            last_sequence=None if row[9] is None else int(row[9]),
            first_timestamp=None if row[10] is None else float(row[10]),
            last_timestamp=None if row[11] is None else float(row[11]),
            created_at=_parse_timestamp(row[12], field="created_at"),
            updated_at=_parse_timestamp(row[13], field="updated_at"),
        )
    except DataError as error:
        raise DataIntegrityError(
            "A stored data session row does not describe a valid session.",
            code="data.integrity.session_row_invalid",
            details={"session_id": str(row[0]), "cause": error.code},
        ) from error
    except (TypeError, ValueError, IndexError) as error:
        raise DataIntegrityError(
            "A stored data session row could not be decoded.",
            code="data.integrity.session_row_invalid",
            details={"session_id": str(row[0])},
        ) from error


def _segment_from_row(row: Sequence[Any]) -> DataSegment:
    try:
        return DataSegment(
            segment_id=str(row[0]),
            session_id=str(row[1]),
            segment_index=int(row[2]),
            relative_path=str(row[3]),
            frame_count=int(row[4]),
            first_sequence=int(row[5]),
            last_sequence=int(row[6]),
            first_timestamp=float(row[7]),
            last_timestamp=float(row[8]),
            byte_size=int(row[9]),
            created_at=_parse_timestamp(row[10], field="created_at"),
        )
    except DataError as error:
        raise DataIntegrityError(
            "A stored data segment row does not describe a valid segment.",
            code="data.integrity.segment_row_invalid",
            details={"segment_id": str(row[0]), "cause": error.code},
        ) from error
    except (TypeError, ValueError, IndexError) as error:
        raise DataIntegrityError(
            "A stored data segment row could not be decoded.",
            code="data.integrity.segment_row_invalid",
            details={"segment_id": str(row[0])},
        ) from error


def _parse_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise DataIntegrityError(
            f"The stored {field} is not a timestamp string.",
            code="data.integrity.session_row_invalid",
            details={"field": field},
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise DataIntegrityError(
            f"The stored {field} is not an ISO-8601 timestamp.",
            code="data.integrity.session_row_invalid",
            details={"field": field, "value": value},
        ) from error
    if parsed.tzinfo is None:
        raise DataIntegrityError(
            f"The stored {field} has no UTC offset.",
            code="data.integrity.session_row_invalid",
            details={"field": field, "value": value},
        )
    return parsed
