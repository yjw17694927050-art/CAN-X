"""Typed, UI-, SQLite-, and PyArrow-independent data-session domain models.

Nothing in this module imports ``sqlite3`` or ``pyarrow``: a ``pyarrow.Table``
or a ``sqlite3.Row`` must never leak into the public data model. Storage layers
translate rows and Arrow tables into these models.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite
from pathlib import PurePosixPath, PureWindowsPath
from uuid import UUID

from canx.data.errors import DataValidationError


class DataSessionState(StrEnum):
    """Lifecycle state of one persisted data session."""

    ACTIVE = "active"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DataSession:
    """Metadata for one bounded capture session inside a project.

    ``frame_count`` and ``segment_count`` count only frames durably committed to
    Parquet segments, so an ``ACTIVE`` session with frames still buffered reports
    the durable total rather than the number appended. Aggregate bounds are
    ``None`` until the first segment commits.
    """

    session_id: str
    project_id: str
    stream_id: str
    state: DataSessionState
    started_at: datetime
    ended_at: datetime | None
    frame_count: int
    segment_count: int
    first_sequence: int | None
    last_sequence: int | None
    first_timestamp: float | None
    last_timestamp: float | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "session_id",
            _normalize_uuid(self.session_id, field="session_id", scope="session"),
        )
        object.__setattr__(
            self,
            "project_id",
            _normalize_uuid(self.project_id, field="project_id", scope="session"),
        )
        if not isinstance(self.stream_id, str) or not self.stream_id:
            raise DataValidationError(
                "stream_id must be a non-empty string.",
                code="data.session.invalid_stream_id",
            )
        if not isinstance(self.state, DataSessionState):
            raise DataValidationError(
                "state must be a DataSessionState.",
                code="data.session.invalid_state",
            )
        _require_aware(self.created_at, field="created_at", scope="session")
        _require_aware(self.started_at, field="started_at", scope="session")
        _require_aware(self.updated_at, field="updated_at", scope="session")
        if self.ended_at is not None:
            _require_aware(self.ended_at, field="ended_at", scope="session")
        if self.state in (DataSessionState.COMPLETED, DataSessionState.INTERRUPTED) and (
            self.ended_at is None
        ):
            raise DataValidationError(
                f"A {self.state.value} session must record ended_at.",
                code="data.session.ended_at_required",
                details={"state": self.state.value},
            )
        _require_count(self.frame_count, field="frame_count", scope="session")
        _require_count(self.segment_count, field="segment_count", scope="session")
        _require_optional_sequence_bounds(self.first_sequence, self.last_sequence)
        _require_optional_timestamp_bounds(self.first_timestamp, self.last_timestamp)
        committed = self.frame_count > 0
        if committed and self.first_sequence is None:
            raise DataValidationError(
                "A session with committed frames must report its sequence bounds.",
                code="data.session.invalid_sequence",
            )
        if not committed and self.first_sequence is not None:
            raise DataValidationError(
                "A session with no committed frames must not report sequence bounds.",
                code="data.session.invalid_sequence",
            )


@dataclass(frozen=True, slots=True)
class DataSegment:
    """Metadata for one committed Parquet segment of a data session.

    ``relative_path`` is always relative to the project root and uses ``/``
    separators, so moving the project directory never invalidates the metadata.
    """

    segment_id: str
    session_id: str
    segment_index: int
    relative_path: str
    frame_count: int
    first_sequence: int
    last_sequence: int
    first_timestamp: float
    last_timestamp: float
    byte_size: int
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "segment_id",
            _normalize_uuid(self.segment_id, field="segment_id", scope="segment"),
        )
        object.__setattr__(
            self,
            "session_id",
            _normalize_uuid(self.session_id, field="session_id", scope="segment"),
        )
        _require_count(self.segment_index, field="segment_index", scope="segment")
        _require_relative_path(self.relative_path)
        if not isinstance(self.frame_count, int) or isinstance(self.frame_count, bool):
            raise DataValidationError(
                "frame_count must be an integer.",
                code="data.segment.invalid_frame_count",
            )
        if self.frame_count <= 0:
            raise DataValidationError(
                "A committed segment must hold at least one frame.",
                code="data.segment.invalid_frame_count",
            )
        _require_sequence(self.first_sequence, field="first_sequence")
        _require_sequence(self.last_sequence, field="last_sequence")
        if self.last_sequence < self.first_sequence:
            raise DataValidationError(
                "segment sequence bounds are inverted.",
                code="data.segment.invalid_sequence",
            )
        _require_timestamp(self.first_timestamp, field="first_timestamp")
        _require_timestamp(self.last_timestamp, field="last_timestamp")
        if self.last_timestamp < self.first_timestamp:
            raise DataValidationError(
                "segment timestamp bounds are inverted.",
                code="data.segment.invalid_timestamp",
            )
        if not isinstance(self.byte_size, int) or isinstance(self.byte_size, bool):
            raise DataValidationError(
                "byte_size must be an integer.",
                code="data.segment.invalid_byte_size",
            )
        if self.byte_size <= 0:
            raise DataValidationError(
                "A committed segment file must not be empty.",
                code="data.segment.invalid_byte_size",
            )
        _require_aware(self.created_at, field="created_at", scope="segment")


@dataclass(frozen=True, slots=True)
class DataIntegrityReport:
    """Read-only diagnosis of the segment files against the persisted metadata.

    Inspection only detects and reports; it never repairs. Every entry is a
    project-relative POSIX path so a finding can be traced to a real file.
    """

    scanned_sessions: int
    temporary_files: tuple[str, ...]
    orphan_segments: tuple[str, ...]
    missing_segments: tuple[str, ...]
    metadata_mismatches: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_count(self.scanned_sessions, field="scanned_sessions", scope="integrity")
        for field in (
            "temporary_files",
            "orphan_segments",
            "missing_segments",
            "metadata_mismatches",
        ):
            value = getattr(self, field)
            if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
                raise DataValidationError(
                    f"{field} must be a tuple of relative paths.",
                    code="data.integrity.invalid_report",
                )

    @property
    def clean(self) -> bool:
        """Return whether the inspection found no inconsistency at all."""
        return not (
            self.temporary_files
            or self.orphan_segments
            or self.missing_segments
            or self.metadata_mismatches
        )


def _normalize_uuid(value: object, *, field: str, scope: str) -> str:
    if not isinstance(value, str) or not value:
        raise DataValidationError(
            f"{field} must be a non-empty UUID string.",
            code=f"data.{scope}.invalid_{field}",
            details={field: repr(value)},
        )
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError) as error:
        raise DataValidationError(
            f"{field} is not a UUID.",
            code=f"data.{scope}.invalid_{field}",
            details={field: value},
        ) from error


def _require_aware(value: object, *, field: str, scope: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DataValidationError(
            f"{field} must be a timezone-aware datetime.",
            code=f"data.{scope}.invalid_{field}",
        )
    return value


def _require_count(value: object, *, field: str, scope: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DataValidationError(
            f"{field} must be a non-negative integer.",
            code=f"data.{scope}.invalid_{field}",
        )
    return value


def _require_sequence(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DataValidationError(
            f"{field} must be a non-negative integer.",
            code="data.segment.invalid_sequence",
        )
    return value


def _require_timestamp(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise DataValidationError(
            f"{field} must be a finite non-negative number of seconds.",
            code="data.segment.invalid_timestamp",
        )
    return float(value)


def _require_optional_sequence_bounds(
    first: int | None, last: int | None
) -> None:
    if (first is None) != (last is None):
        raise DataValidationError(
            "session sequence bounds must be both set or both absent.",
            code="data.session.invalid_sequence",
        )
    if first is None or last is None:
        return
    _require_sequence(first, field="first_sequence")
    _require_sequence(last, field="last_sequence")
    if last < first:
        raise DataValidationError(
            "session sequence bounds are inverted.",
            code="data.session.invalid_sequence",
        )


def _require_optional_timestamp_bounds(
    first: float | None, last: float | None
) -> None:
    if (first is None) != (last is None):
        raise DataValidationError(
            "session timestamp bounds must be both set or both absent.",
            code="data.session.invalid_timestamp",
        )
    if first is None or last is None:
        return
    _require_timestamp(first, field="first_timestamp")
    _require_timestamp(last, field="last_timestamp")
    if last < first:
        raise DataValidationError(
            "session timestamp bounds are inverted.",
            code="data.session.invalid_timestamp",
        )


def _require_relative_path(value: object) -> None:
    if not isinstance(value, str) or not value:
        raise DataValidationError(
            "relative_path must be a non-empty string.",
            code="data.segment.invalid_relative_path",
        )
    if "\\" in value:
        raise DataValidationError(
            "relative_path must use '/' separators.",
            code="data.segment.invalid_relative_path",
            details={"relative_path": value},
        )
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise DataValidationError(
            "relative_path must be relative to the project root.",
            code="data.segment.invalid_relative_path",
            details={"relative_path": value},
        )
