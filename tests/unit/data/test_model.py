"""Behavior tests for the UI-, SQLite-, and PyArrow-independent data models."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from canx.data.errors import DataError, DataValidationError
from canx.data.model import (
    DataIntegrityReport,
    DataSegment,
    DataSession,
    DataSessionState,
)

SESSION_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
PROJECT_ID = "2f1c3d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
SEGMENT_ID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"


def _moment() -> datetime:
    return datetime(2026, 9, 16, 9, 0, tzinfo=UTC)


def _session(**changes: object) -> DataSession:
    values: dict[str, object] = {
        "session_id": SESSION_ID,
        "project_id": PROJECT_ID,
        "stream_id": "stream-1",
        "state": DataSessionState.ACTIVE,
        "started_at": _moment(),
        "ended_at": None,
        "frame_count": 0,
        "segment_count": 0,
        "first_sequence": None,
        "last_sequence": None,
        "first_timestamp": None,
        "last_timestamp": None,
        "created_at": _moment(),
        "updated_at": _moment(),
    }
    values.update(changes)
    return DataSession(**values)  # type: ignore[arg-type]


def _segment(**changes: object) -> DataSegment:
    values: dict[str, object] = {
        "segment_id": SEGMENT_ID,
        "session_id": SESSION_ID,
        "segment_index": 0,
        "relative_path": "data/sessions/x/segments/000000.parquet",
        "frame_count": 3,
        "first_sequence": 0,
        "last_sequence": 2,
        "first_timestamp": 0.0,
        "last_timestamp": 2.0,
        "byte_size": 128,
        "created_at": _moment(),
    }
    values.update(changes)
    return DataSegment(**values)  # type: ignore[arg-type]


def test_session_state_is_a_closed_string_enum() -> None:
    assert [state.value for state in DataSessionState] == [
        "active",
        "completed",
        "interrupted",
        "failed",
    ]


def test_an_active_session_accepts_an_empty_aggregate() -> None:
    session = _session()

    assert session.state is DataSessionState.ACTIVE
    assert session.frame_count == 0
    assert session.segment_count == 0
    assert session.first_sequence is None
    assert session.ended_at is None


def test_a_completed_session_requires_an_end_instant() -> None:
    with pytest.raises(DataValidationError) as info:
        _session(state=DataSessionState.COMPLETED, ended_at=None)

    assert info.value.code == "data.session.ended_at_required"


def test_a_completed_session_accepts_an_explicit_end_instant() -> None:
    session = _session(state=DataSessionState.COMPLETED, ended_at=_moment())

    assert session.ended_at == _moment()


@pytest.mark.parametrize(
    "changes",
    [
        {"session_id": "not-a-uuid"},
        {"project_id": "not-a-uuid"},
        {"stream_id": ""},
        {"frame_count": -1},
        {"segment_count": -1},
        {"started_at": datetime(2026, 9, 16, 9, 0)},
        {"first_sequence": 5, "last_sequence": None},
        {"first_sequence": None, "last_sequence": 5},
        {"first_sequence": 5, "last_sequence": 4},
        {"first_timestamp": 1.0, "last_timestamp": None},
        {"first_timestamp": 2.0, "last_timestamp": 1.0},
        {"state": "active"},
    ],
    ids=[
        "session-id",
        "project-id",
        "stream-id",
        "frame-count",
        "segment-count",
        "naive-started-at",
        "last-sequence-missing",
        "first-sequence-missing",
        "sequence-inverted",
        "last-timestamp-missing",
        "timestamp-inverted",
        "raw-string-state",
    ],
)
def test_an_invalid_session_is_never_accepted(changes: dict[str, object]) -> None:
    with pytest.raises(DataValidationError) as info:
        _session(**changes)

    assert isinstance(info.value, DataError)


def test_session_identity_is_normalized_to_the_canonical_uuid_form() -> None:
    session = _session(session_id=SESSION_ID.upper())

    assert session.session_id == SESSION_ID


def test_a_session_is_immutable() -> None:
    session = _session()

    with pytest.raises(FrozenInstanceError):
        session.frame_count = 1  # type: ignore[misc]


def test_a_committed_segment_reports_its_bounds() -> None:
    segment = _segment()

    assert segment.segment_index == 0
    assert (segment.first_sequence, segment.last_sequence) == (0, 2)
    assert segment.frame_count == 3
    assert segment.session_id == SESSION_ID


@pytest.mark.parametrize(
    "changes",
    [
        {"segment_id": "not-a-uuid"},
        {"session_id": ""},
        {"segment_index": -1},
        {"relative_path": ""},
        {"relative_path": "C:/absolute/segment.parquet"},
        {"relative_path": "/rooted/segment.parquet"},
        {"relative_path": "data\\sessions\\segment.parquet"},
        {"frame_count": 0},
        {"frame_count": -1},
        {"first_sequence": -1},
        {"last_sequence": 1, "first_sequence": 2},
        {"last_timestamp": -0.5, "first_timestamp": -1.0},
        {"byte_size": -1},
    ],
    ids=[
        "segment-id",
        "session-id",
        "segment-index",
        "relative-path-empty",
        "relative-path-absolute",
        "relative-path-rooted",
        "relative-path-backslash",
        "frame-count-zero",
        "frame-count-negative",
        "first-sequence-negative",
        "sequence-inverted",
        "negative-timestamp",
        "byte-size",
    ],
)
def test_an_invalid_segment_is_never_accepted(changes: dict[str, object]) -> None:
    with pytest.raises(DataValidationError):
        _segment(**changes)


def test_a_clean_integrity_report_reports_no_findings() -> None:
    report = DataIntegrityReport(
        scanned_sessions=2,
        temporary_files=(),
        orphan_segments=(),
        missing_segments=(),
        metadata_mismatches=(),
    )

    assert report.clean is True


@pytest.mark.parametrize(
    "field",
    ["temporary_files", "orphan_segments", "missing_segments", "metadata_mismatches"],
)
def test_any_integrity_finding_makes_the_report_dirty(field: str) -> None:
    findings: dict[str, object] = {
        "temporary_files": (),
        "orphan_segments": (),
        "missing_segments": (),
        "metadata_mismatches": (),
    }
    findings[field] = ("data/sessions/x/segments/000000.parquet",)

    report = DataIntegrityReport(scanned_sessions=1, **findings)  # type: ignore[arg-type]

    assert report.clean is False


def test_data_errors_carry_the_structured_contract_fields() -> None:
    error = DataValidationError("bad", code="data.example", details={"field": "x"})

    assert isinstance(error, DataError)
    assert not isinstance(error, ValueError)
    assert error.message == "bad"
    assert error.code == "data.example"
    assert error.details == {"field": "x"}
    assert error.recoverable is False
    assert error.source == "data"
