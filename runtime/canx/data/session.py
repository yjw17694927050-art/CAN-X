"""Data session lifecycle: bounded Parquet segments, recovery, and inspection.

A session is the durable unit of captured data inside one project. Frames enter
through :meth:`DataSessionWriter.append`, are held only until a bounded threshold
is reached, and are then committed as one Parquet segment whose metadata lands in
SQLite only after the file is safely in place.

Nothing here is async or threaded on purpose: the durable semantics have to be
right before they are attached to the realtime capture path.
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

from canx.data import repository
from canx.data.errors import (
    DataError,
    DataIntegrityError,
    DataSessionStateError,
    DataStorageError,
    DataStreamMismatchError,
    DataValidationError,
)
from canx.data.model import (
    DataIntegrityReport,
    DataSegment,
    DataSession,
    DataSessionState,
)
from canx.data.parquet import TEMPORARY_SUFFIX, segment_filename
from canx.data.parquet import read_segment as read_segment_file
from canx.data.parquet import write_segment as write_segment_file
from canx.domain.batch import FrameBatch
from canx.domain.frame import Frame

#: Foundation default. Large enough that a segment is a meaningful unit and
#: small enough that the in-memory working set stays bounded; tests override it.
DEFAULT_MAX_FRAMES_PER_SEGMENT = 65_536

DATA_DIRECTORY = "data"
SESSIONS_DIRECTORY = "sessions"
SEGMENTS_DIRECTORY = "segments"
PARQUET_SUFFIX = ".parquet"


def session_relative_directory(session_id: str) -> str:
    """Return the project-relative directory that owns one session."""
    return f"{DATA_DIRECTORY}/{SESSIONS_DIRECTORY}/{session_id}"


def segment_relative_path(session_id: str, segment_index: int) -> str:
    """Return the project-relative path of one segment file."""
    return (
        f"{session_relative_directory(session_id)}/{SEGMENTS_DIRECTORY}"
        f"/{segment_filename(segment_index)}"
    )


def resolve_within_root(project_root: Path, relative_path: str) -> Path:
    """Resolve a stored relative path, refusing anything outside the project.

    A persisted path is never trusted just because the database held it.

    Raises:
        DataIntegrityError: If the path escapes the project root.
    """
    root = project_root.resolve()
    candidate = (root / PurePosixPath(relative_path)).resolve()
    if not candidate.is_relative_to(root):
        raise DataIntegrityError(
            "A stored segment path points outside the project root.",
            code="data.integrity.path_escape",
            details={"relative_path": relative_path, "project_root": str(root)},
        )
    return candidate


class DataSessionWriter:
    """An ``ACTIVE`` session that commits bounded Parquet segments.

    The writer owns only in-memory state between flushes; every durable write
    opens and closes its own short-lived SQLite connection.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        session: DataSession,
        max_frames_per_segment: int,
    ) -> None:
        self._root = project_root
        self._session = session
        self._max_frames_per_segment = max_frames_per_segment
        self._buffer: list[Frame] = []
        self._last_appended_sequence: int | None = None
        self._last_appended_timestamp: float | None = None

    @property
    def session(self) -> DataSession:
        """Return the current durable snapshot of this session."""
        return self._session

    @property
    def session_id(self) -> str:
        """Return the stable session identity."""
        return self._session.session_id

    @property
    def project_id(self) -> str:
        """Return the owning project identity."""
        return self._session.project_id

    @property
    def stream_id(self) -> str:
        """Return the single capture stream this session accepts frames from."""
        return self._session.stream_id

    @property
    def state(self) -> DataSessionState:
        """Return the current lifecycle state."""
        return self._session.state

    @property
    def frame_count(self) -> int:
        """Return how many frames have been durably committed."""
        return self._session.frame_count

    @property
    def segment_count(self) -> int:
        """Return how many segments have been durably committed."""
        return self._session.segment_count

    @property
    def max_frames_per_segment(self) -> int:
        """Return the bounded threshold that triggers a flush."""
        return self._max_frames_per_segment

    def append(self, batch: FrameBatch) -> None:
        """Accept one contiguous batch, flushing full segments as they fill.

        Raises:
            DataValidationError: If ``batch`` is not a :class:`FrameBatch`.
            DataSessionStateError: If the session is no longer ``ACTIVE``.
            DataStreamMismatchError: If the batch belongs to another stream.
            DataIntegrityError: If the batch would break sequence or timestamp
                ordering within this session.
            ParquetWriteError: If a segment could not be durably committed; the
                session is then ``FAILED`` and its counters are unchanged.
            DataStorageError: If a committed segment could not be registered.
        """
        if not isinstance(batch, FrameBatch):
            raise DataValidationError(
                "append expects a FrameBatch.",
                code="data.session.invalid_batch",
                details={"session_id": self._session.session_id, "type": type(batch).__name__},
            )
        self._require_active()
        if batch.stream_id != self._session.stream_id:
            raise DataStreamMismatchError(
                "The batch belongs to a different capture stream than this session.",
                details={
                    "session_id": self._session.session_id,
                    "expected_stream_id": self._session.stream_id,
                    "batch_stream_id": batch.stream_id,
                },
            )
        self._require_contiguous_sequence(batch)
        self._require_ordered_timestamps(batch)
        self._buffer.extend(batch.frames)
        self._last_appended_sequence = batch.last_sequence
        self._last_appended_timestamp = batch.frames[-1].normalized_timestamp
        self._flush_full_segments()

    def finalize(self) -> DataSession:
        """Flush the partial segment and mark the session ``COMPLETED``.

        Raises:
            DataSessionStateError: If the session is not ``ACTIVE``.
            ParquetWriteError: If the partial segment could not be committed.
            DataStorageError: If the new state could not be persisted.
        """
        self._require_active()
        if self._buffer:
            self._commit_segment(tuple(self._buffer))
            self._buffer.clear()
        ended_at = _utc_now()
        completed = replace(
            self._session,
            state=DataSessionState.COMPLETED,
            ended_at=ended_at,
            updated_at=ended_at,
        )
        try:
            with repository.data_connection(self._root) as connection:
                repository.update_session(connection, completed)
        except (DataError, sqlite3.Error, OSError) as error:
            self._mark_failed()
            raise DataStorageError(
                "The completed data session could not be persisted.",
                code="data.session.finalize_failed",
                details={"session_id": self._session.session_id},
            ) from error
        self._session = completed
        return completed

    def close(self) -> None:
        """Finalize the session when it is still ``ACTIVE``; otherwise do nothing."""
        if self._session.state is DataSessionState.ACTIVE:
            self.finalize()

    def __enter__(self) -> Self:
        self._require_active()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.close()

    def _require_active(self) -> None:
        if self._session.state is not DataSessionState.ACTIVE:
            raise DataSessionStateError(
                "The data session is no longer active.",
                code="data.session.not_active",
                details={
                    "session_id": self._session.session_id,
                    "state": self._session.state.value,
                },
            )

    def _require_contiguous_sequence(self, batch: FrameBatch) -> None:
        if self._last_appended_sequence is None:
            return
        if batch.first_sequence <= self._last_appended_sequence:
            raise DataIntegrityError(
                "The batch does not continue this session's sequence.",
                code="data.integrity.sequence_regression",
                details={
                    "session_id": self._session.session_id,
                    "previous_last_sequence": self._last_appended_sequence,
                    "batch_first_sequence": batch.first_sequence,
                },
            )

    def _require_ordered_timestamps(self, batch: FrameBatch) -> None:
        first = batch.frames[0].normalized_timestamp
        last = batch.frames[-1].normalized_timestamp
        if last < first:
            raise DataIntegrityError(
                "The batch timestamps are not in order.",
                code="data.integrity.timestamp_regression",
                details={
                    "session_id": self._session.session_id,
                    "first_timestamp": first,
                    "last_timestamp": last,
                },
            )
        if self._last_appended_timestamp is not None and first < self._last_appended_timestamp:
            raise DataIntegrityError(
                "The batch starts before the last committed frame in this session.",
                code="data.integrity.timestamp_regression",
                details={
                    "session_id": self._session.session_id,
                    "previous_last_timestamp": self._last_appended_timestamp,
                    "batch_first_timestamp": first,
                },
            )

    def _flush_full_segments(self) -> None:
        while len(self._buffer) >= self._max_frames_per_segment:
            chunk = tuple(self._buffer[: self._max_frames_per_segment])
            self._commit_segment(chunk)
            del self._buffer[: self._max_frames_per_segment]

    def _commit_segment(self, frames: tuple[Frame, ...]) -> None:
        session = self._session
        segment_index = session.segment_count
        relative_path = segment_relative_path(session.session_id, segment_index)
        target = resolve_within_root(self._root, relative_path)
        first_timestamp = frames[0].normalized_timestamp
        last_timestamp = frames[-1].normalized_timestamp
        created_at = _utc_now()
        try:
            byte_size = write_segment_file(
                target,
                session_id=session.session_id,
                stream_id=session.stream_id,
                segment_index=segment_index,
                frames=frames,
            )
        except DataError:
            self._mark_failed()
            raise
        segment = DataSegment(
            segment_id=str(uuid4()),
            session_id=session.session_id,
            segment_index=segment_index,
            relative_path=relative_path,
            frame_count=len(frames),
            first_sequence=frames[0].sequence,
            last_sequence=frames[-1].sequence,
            first_timestamp=first_timestamp,
            last_timestamp=last_timestamp,
            byte_size=byte_size,
            created_at=created_at,
        )
        updated = replace(
            session,
            frame_count=session.frame_count + len(frames),
            segment_count=segment_index + 1,
            first_sequence=(
                frames[0].sequence if session.first_sequence is None else session.first_sequence
            ),
            last_sequence=frames[-1].sequence,
            first_timestamp=(
                first_timestamp if session.first_timestamp is None else session.first_timestamp
            ),
            last_timestamp=last_timestamp,
            updated_at=_utc_now(),
        )
        try:
            with repository.data_connection(self._root) as connection:
                repository.register_segment(connection, segment=segment, session=updated)
        except (DataError, sqlite3.Error, OSError) as error:
            # The file exists but no row will describe it: remove it, and if even
            # that fails it stays visible to integrity inspection as an orphan.
            _remove_quietly(target)
            self._mark_failed()
            raise DataStorageError(
                "The committed segment could not be registered in the project database.",
                code="data.session.registration_failed",
                details={
                    "session_id": session.session_id,
                    "segment_index": segment_index,
                    "relative_path": relative_path,
                },
            ) from error
        self._session = updated

    def _mark_failed(self) -> None:
        """Record the failure in memory, and best-effort in the database."""
        failed = replace(self._session, state=DataSessionState.FAILED, updated_at=_utc_now())
        self._session = failed
        with suppress(
            DataError, sqlite3.Error, OSError
        ), repository.data_connection(self._root) as connection:
            repository.update_session(connection, failed)


class DataSessionService:
    """Stateless entry point for starting, reading and recovering data sessions.

    The service keeps no connection between calls. Every operation opens a
    short-lived, schema-validated connection to the project database.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        max_frames_per_segment: int = DEFAULT_MAX_FRAMES_PER_SEGMENT,
    ) -> None:
        if (
            not isinstance(max_frames_per_segment, int)
            or isinstance(max_frames_per_segment, bool)
            or max_frames_per_segment <= 0
        ):
            raise DataValidationError(
                "max_frames_per_segment must be a positive integer.",
                code="data.session.invalid_segment_threshold",
                details={"max_frames_per_segment": repr(max_frames_per_segment)},
            )
        self._root = Path(project_root)
        self._max_frames_per_segment = max_frames_per_segment

    @property
    def project_root(self) -> Path:
        """Return the project root this service reads and writes."""
        return self._root

    @property
    def max_frames_per_segment(self) -> int:
        """Return the bounded segment threshold used by new sessions."""
        return self._max_frames_per_segment

    def start(self, *, stream_id: str) -> DataSessionWriter:
        """Register a new ``ACTIVE`` session for one capture stream.

        The session directory is created before the session row, so a caller can
        never receive an ``ACTIVE`` session it is unable to write segments into.
        Either both exist or neither does: a failure on either side removes only
        the directory this call just created and raises a typed error.

        Raises:
            DataValidationError: If ``stream_id`` is blank.
            DataStorageError: If the session directory or the session row could
                not be created, or if the project database is unusable.
        """
        if not isinstance(stream_id, str) or not stream_id.strip():
            raise DataValidationError(
                "stream_id must contain at least one non-whitespace character.",
                code="data.session.invalid_stream_id",
                details={"stream_id": repr(stream_id)},
            )
        started_at = _utc_now()
        session_id = str(uuid4())
        try:
            session_directory = _create_session_directory(self._root, session_id)
        except OSError as error:
            raise DataStorageError(
                "The session directory could not be created.",
                code="data.session.directory_create_failed",
                details={"session_id": session_id, "project_root": str(self._root)},
            ) from error
        try:
            with repository.data_connection(self._root) as connection:
                project_id = repository.read_project_id(connection)
                session = DataSession(
                    session_id=session_id,
                    project_id=project_id,
                    stream_id=stream_id,
                    state=DataSessionState.ACTIVE,
                    started_at=started_at,
                    ended_at=None,
                    frame_count=0,
                    segment_count=0,
                    first_sequence=None,
                    last_sequence=None,
                    first_timestamp=None,
                    last_timestamp=None,
                    created_at=started_at,
                    updated_at=started_at,
                )
                repository.insert_session(connection, session)
        except (DataError, sqlite3.Error, OSError) as error:
            # Only this session's own directory is removed. Existing user data is
            # never touched, and the shared ``data/sessions`` parent is left in
            # place so a failure cannot disturb anything else that uses it.
            _remove_directory_quietly(session_directory)
            if isinstance(error, DataError):
                raise
            raise DataStorageError(
                "The data session could not be registered.",
                code="data.session.start_failed",
                details={"session_id": session_id, "project_root": str(self._root)},
            ) from error
        return DataSessionWriter(
            project_root=self._root,
            session=session,
            max_frames_per_segment=self._max_frames_per_segment,
        )

    def get_session(self, session_id: str) -> DataSession:
        """Return one registered session.

        Raises:
            DataValidationError: If ``session_id`` is not a UUID.
            DataSessionError: If no such session is registered.
        """
        normalized = _normalize_session_id(session_id)
        with repository.data_connection(self._root) as connection:
            session = repository.read_session(connection, normalized)
        return repository.require_registered_session(session, normalized)

    def list_sessions(self) -> tuple[DataSession, ...]:
        """Return every registered session in deterministic order."""
        with repository.data_connection(self._root) as connection:
            return repository.list_sessions(connection)

    def list_segments(self, session_id: str) -> tuple[DataSegment, ...]:
        """Return every registered segment of one session, ordered by index.

        Raises:
            DataValidationError: If ``session_id`` is not a UUID.
            DataSessionError: If no such session is registered.
        """
        normalized = _normalize_session_id(session_id)
        with repository.data_connection(self._root) as connection:
            repository.require_registered_session(
                repository.read_session(connection, normalized), normalized
            )
            return repository.list_segments(connection, normalized)

    def read_segment(self, session_id: str, segment_index: int) -> tuple[Frame, ...]:
        """Read one committed segment back and verify it against its metadata.

        Raises:
            DataValidationError: If ``session_id`` is not a UUID.
            DataSessionError: If no such session is registered.
            DataIntegrityError: If the segment is not registered, its file is
                missing, or the file contradicts the registered metadata.
        """
        normalized = _normalize_session_id(session_id)
        with repository.data_connection(self._root) as connection:
            session = repository.require_registered_session(
                repository.read_session(connection, normalized), normalized
            )
            segment = repository.read_segment_row(connection, normalized, segment_index)
        if segment is None:
            raise DataIntegrityError(
                "The session has no registered segment at this index.",
                code="data.integrity.segment_missing",
                details={"session_id": normalized, "segment_index": segment_index},
            )
        path = resolve_within_root(self._root, segment.relative_path)
        if not path.is_file():
            raise DataIntegrityError(
                "The registered segment file is missing.",
                code="data.integrity.segment_missing",
                details={
                    "session_id": normalized,
                    "relative_path": segment.relative_path,
                },
            )
        frames = read_segment_file(
            path,
            expected_session_id=session.session_id,
            expected_stream_id=session.stream_id,
            expected_segment_index=segment.segment_index,
        )
        if len(frames) != segment.frame_count:
            raise DataIntegrityError(
                "The segment file row count does not match its registered metadata.",
                code="data.integrity.frame_count_mismatch",
                details={
                    "relative_path": segment.relative_path,
                    "registered_frame_count": segment.frame_count,
                    "actual_frame_count": len(frames),
                },
            )
        if (
            frames[0].sequence != segment.first_sequence
            or frames[-1].sequence != segment.last_sequence
        ):
            raise DataIntegrityError(
                "The segment sequence bounds do not match its registered metadata.",
                code="data.integrity.sequence_mismatch",
                details={
                    "relative_path": segment.relative_path,
                    "registered_first_sequence": segment.first_sequence,
                    "registered_last_sequence": segment.last_sequence,
                    "actual_first_sequence": frames[0].sequence,
                    "actual_last_sequence": frames[-1].sequence,
                },
            )
        return frames

    def recover_incomplete_sessions(self, *, now: datetime | None = None) -> tuple[str, ...]:
        """Move every ``ACTIVE`` session to ``INTERRUPTED`` and return their ids.

        Recovery is always explicit: opening a project never mutates sessions on
        its own, and sessions that are already ``COMPLETED``, ``FAILED`` or
        ``INTERRUPTED`` are never touched. No committed segment is deleted.

        Raises:
            DataValidationError: If ``now`` is not timezone-aware.
            DataStorageError: If the recovery could not be persisted.
        """
        recovered_at = _utc_now() if now is None else now
        if not isinstance(recovered_at, datetime) or recovered_at.tzinfo is None:
            raise DataValidationError(
                "now must be a timezone-aware datetime.",
                code="data.session.invalid_recovery_time",
            )
        with repository.data_connection(self._root) as connection:
            active = repository.list_sessions_in_state(connection, DataSessionState.ACTIVE)
            for session in active:
                repository.mark_session_interrupted(
                    connection, session_id=session.session_id, at=recovered_at
                )
        return tuple(session.session_id for session in active)

    def inspect_integrity(self) -> DataIntegrityReport:
        """Compare the persisted metadata against the segment files on disk.

        Inspection detects and reports only; it never repairs. Temporary files,
        files with no registered row (orphans), registered rows with no file
        (missing) and size mismatches are all reported.

        Raises:
            DataStorageError: If the project database is unusable.
        """
        with repository.data_connection(self._root) as connection:
            session_count = len(repository.list_sessions(connection))
            registered = repository.list_all_segments(connection)

        temporary_files: list[str] = []
        orphan_segments: list[str] = []
        missing_segments: list[str] = []
        metadata_mismatches: list[str] = []

        known: dict[str, DataSegment] = {
            segment.relative_path: segment for segment in registered
        }
        sessions_root = self._root / DATA_DIRECTORY / SESSIONS_DIRECTORY
        if sessions_root.is_dir():
            for path in sorted(sessions_root.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(self._root).as_posix()
                if path.name.endswith(TEMPORARY_SUFFIX):
                    temporary_files.append(relative)
                    continue
                if path.suffix == PARQUET_SUFFIX and relative not in known:
                    orphan_segments.append(relative)

        for relative, segment in sorted(known.items()):
            try:
                path = resolve_within_root(self._root, relative)
            except DataIntegrityError:
                metadata_mismatches.append(relative)
                continue
            if not path.is_file():
                missing_segments.append(relative)
                continue
            if path.stat().st_size != segment.byte_size:
                metadata_mismatches.append(relative)

        return DataIntegrityReport(
            scanned_sessions=session_count,
            temporary_files=tuple(temporary_files),
            orphan_segments=tuple(orphan_segments),
            missing_segments=tuple(missing_segments),
            metadata_mismatches=tuple(metadata_mismatches),
        )


def _normalize_session_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError) as error:
        raise DataValidationError(
            "session_id is not a UUID.",
            code="data.session.invalid_session_id",
            details={"session_id": repr(value)},
        ) from error


def _create_session_directory(project_root: Path, session_id: str) -> Path:
    """Create the directory tree that owns one session's segments.

    Kept as a module-level seam so a directory-creation failure can be injected
    deterministically instead of relying on real disk permissions.

    Raises:
        OSError: If the tree cannot be created.
    """
    session_directory = project_root / session_relative_directory(session_id)
    (session_directory / SEGMENTS_DIRECTORY).mkdir(parents=True, exist_ok=False)
    return session_directory


def _remove_directory_quietly(path: Path) -> None:
    """Best-effort removal of a directory this call just created."""
    with suppress(OSError):
        shutil.rmtree(path)


def _remove_quietly(path: Path) -> None:
    with suppress(OSError):
        path.unlink()


def _utc_now() -> datetime:
    return datetime.now(UTC)
