"""Offline replay against a real project, real segments and the real query path.

These tests instantiate the production dependencies — a real ``ProjectService``
project, a real ``DataSessionService`` writer, real Parquet segments and the real
``QueryService``-backed source — with only the clock faked. That is the point:
the bounded pagination, the terminal frame-count gate and every failure translation
are exercised across a real module boundary, where a mock would happily fake the
very agreement that can break.

The domain is offline only. Nothing here opens an adapter, and no replay ever
reaches a transmit path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from canx.data.parquet import segment_filename
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.query.model import FrameFilter, FrameQuery
from canx.query.service import QueryService
from canx.replay.clock import MonotonicReplayClock, VirtualReplayClock
from canx.replay.errors import (
    ReplayProjectError,
    ReplaySessionError,
    ReplaySourceError,
    ReplayStateError,
)
from canx.replay.model import (
    EmptySessionPolicy,
    ReplayConfig,
    ReplayFrameEvent,
    ReplayState,
    SessionCompletionPolicy,
)
from canx.replay.session import ReplaySession

STREAM_ID = "offline-replay-stream"
FRAMES_PER_SEGMENT = 5
TOTAL_FRAMES = 23
FRAME_SPACING = 0.25
UNKNOWN_SESSION_ID = "99999999-8888-4777-8666-555555555555"


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0" if sequence % 2 == 0 else "can1",
        arbitration_id=0x300 + sequence,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=2,
        data=bytes([sequence % 256, 0x5A]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=9_000.0 + sequence * FRAME_SPACING,
        normalized_timestamp=sequence * FRAME_SPACING,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


class RecordingSink:
    """A sink that keeps every event it is handed, in order."""

    def __init__(self) -> None:
        self.events: list[ReplayFrameEvent] = []
        self.replay: ReplaySession | None = None
        self.cancel_after: int | None = None

    def emit(self, event: ReplayFrameEvent) -> None:
        self.events.append(event)
        if self.cancel_after is not None and len(self.events) == self.cancel_after:
            assert self.replay is not None
            self.replay.cancel()

    @property
    def sequences(self) -> list[int]:
        return [event.sequence for event in self.events]


def _project(tmp_path: Path) -> ProjectHandle:
    return ProjectService().create(tmp_path / "vehicle.canx", display_name="Vehicle A")


def _record(
    handle: ProjectHandle,
    *,
    total: int = TOTAL_FRAMES,
    frames_per_segment: int = FRAMES_PER_SEGMENT,
    finalize: bool = True,
) -> str:
    service = DataSessionService(handle.root, max_frames_per_segment=frames_per_segment)
    writer = service.start(stream_id=STREAM_ID)
    for start in range(0, total, frames_per_segment):
        size = min(frames_per_segment, total - start)
        writer.append(
            FrameBatch.create(
                stream_id=STREAM_ID,
                frames=[frame(sequence) for sequence in range(start, start + size)],
            )
        )
    if finalize:
        writer.finalize()
    return writer.session_id


def _segment_path(handle: ProjectHandle, session_id: str, index: int) -> Path:
    return (
        handle.root
        / "data"
        / "sessions"
        / session_id
        / "segments"
        / segment_filename(index)
    )


def test_a_recorded_session_replays_every_frame_in_order(tmp_path: Path) -> None:
    sink = RecordingSink()
    with _project(tmp_path) as handle:
        session_id = _record(handle)
        replay = ReplaySession.open(
            handle.root,
            session_id=session_id,
            sink=sink,
            config=ReplayConfig(page_size=4),
            clock=VirtualReplayClock(),
        )

        report = replay.run()

        single_query = QueryService(handle.root).query_frames(
            FrameQuery(filter=FrameFilter(session_id=session_id), limit=100)
        )

    assert report.completed is True
    assert sink.sequences == list(range(TOTAL_FRAMES))
    assert [event.frame for event in sink.events] == list(single_query.frames)
    assert report.frames_emitted == TOTAL_FRAMES
    assert report.first_sequence == 0
    assert report.last_sequence == TOTAL_FRAMES - 1


def test_a_multi_segment_recording_is_read_one_bounded_page_at_a_time(
    tmp_path: Path,
) -> None:
    sink = RecordingSink()
    with _project(tmp_path) as handle:
        session_id = _record(handle)
        replay = ReplaySession.open(
            handle.root,
            session_id=session_id,
            sink=sink,
            config=ReplayConfig(page_size=4),
            clock=VirtualReplayClock(),
        )

        report = replay.run()
        segments = DataSessionService(handle.root).list_segments(session_id)

    assert len(segments) == 5
    assert report.source_page_count == 6  # ceil(23 / 4)
    assert report.source_peak_page_frames == 4
    assert report.frames_emitted == TOTAL_FRAMES


def test_the_recorded_timeline_is_reproduced_from_the_first_frame(tmp_path: Path) -> None:
    clock = VirtualReplayClock()
    sink = RecordingSink()
    with _project(tmp_path) as handle:
        session_id = _record(handle)
        replay = ReplaySession.open(
            handle.root,
            session_id=session_id,
            sink=sink,
            config=ReplayConfig(page_size=7),
            clock=clock,
        )

        report = replay.run()

    assert [event.relative_offset for event in sink.events] == pytest.approx(
        [sequence * FRAME_SPACING for sequence in range(TOTAL_FRAMES)]
    )
    assert report.relative_span == pytest.approx((TOTAL_FRAMES - 1) * FRAME_SPACING)
    assert clock.total_slept == pytest.approx(report.relative_span)


def test_two_replays_of_the_same_recording_are_identical(tmp_path: Path) -> None:
    first_sink = RecordingSink()
    second_sink = RecordingSink()
    with _project(tmp_path) as handle:
        session_id = _record(handle)
        first = ReplaySession.open(
            handle.root,
            session_id=session_id,
            sink=first_sink,
            config=ReplayConfig(page_size=6),
            clock=VirtualReplayClock(),
        ).run()
        second = ReplaySession.open(
            handle.root,
            session_id=session_id,
            sink=second_sink,
            config=ReplayConfig(page_size=5),
            clock=VirtualReplayClock(),
        ).run()

    assert (first.state, first.frames_emitted, first.relative_span) == (
        second.state,
        second.frames_emitted,
        second.relative_span,
    )
    assert (first.first_sequence, first.last_sequence) == (
        second.first_sequence,
        second.last_sequence,
    )
    assert [(event.sequence, event.relative_offset) for event in first_sink.events] == [
        (event.sequence, event.relative_offset) for event in second_sink.events
    ]


def test_the_production_clock_replays_a_real_recording(tmp_path: Path) -> None:
    sink = RecordingSink()
    with _project(tmp_path) as handle:
        session_id = _record(handle, total=3, frames_per_segment=3)
        replay = ReplaySession.open(
            handle.root,
            session_id=session_id,
            sink=sink,
            clock=MonotonicReplayClock(),
        )

        report = replay.run()

    assert report.completed is True
    assert sink.sequences == [0, 1, 2]
    assert report.relative_span == pytest.approx(2 * FRAME_SPACING)


def test_a_cancelled_replay_leaves_no_orphan_and_reads_no_more(tmp_path: Path) -> None:
    sink = RecordingSink()
    sink.cancel_after = 3
    with _project(tmp_path) as handle:
        session_id = _record(handle)
        replay = ReplaySession.open(
            handle.root,
            session_id=session_id,
            sink=sink,
            config=ReplayConfig(page_size=3),
            clock=VirtualReplayClock(),
        )
        sink.replay = replay

        report = replay.run()

        assert replay.state is ReplayState.CANCELLED
        assert replay.source.closed is True
        with pytest.raises(ReplayStateError) as raised:
            replay.run()

    assert report.state is ReplayState.CANCELLED
    assert report.completed is False
    assert sink.sequences == [0, 1, 2]
    assert raised.value.code == "replay.invalid_run_state"


def test_a_missing_segment_file_fails_the_replay_typed(tmp_path: Path) -> None:
    sink = RecordingSink()
    with _project(tmp_path) as handle:
        session_id = _record(handle)
        _segment_path(handle, session_id, 1).unlink()
        replay = ReplaySession.open(
            handle.root,
            session_id=session_id,
            sink=sink,
            config=ReplayConfig(page_size=4),
            clock=VirtualReplayClock(),
        )

        with pytest.raises(ReplaySourceError) as raised:
            replay.run()

        assert replay.state is ReplayState.FAILED
        assert replay.source.closed is True

    assert raised.value.code == "replay.source_unavailable"
    assert raised.value.details["cause"] == "query.segment_missing"
    assert raised.value.recoverable is True
    # The page a replay reads is validated before any of its frames is emitted,
    # so a recording with a hole in it fails without replaying a single frame.
    assert sink.events == []


def test_a_corrupt_segment_file_fails_the_replay_typed(tmp_path: Path) -> None:
    sink = RecordingSink()
    with _project(tmp_path) as handle:
        session_id = _record(handle)
        _segment_path(handle, session_id, 0).write_bytes(b"not a parquet file")
        replay = ReplaySession.open(
            handle.root,
            session_id=session_id,
            sink=sink,
            clock=VirtualReplayClock(),
        )

        with pytest.raises(ReplaySourceError) as raised:
            replay.run()

        assert replay.state is ReplayState.FAILED

    assert raised.value.code == "replay.source_unavailable"
    assert raised.value.details["cause"] == "query.segment_unreadable"
    assert raised.value.recoverable is False
    assert sink.events == []


def test_an_unknown_session_is_refused(tmp_path: Path) -> None:
    with _project(tmp_path) as handle, pytest.raises(ReplaySessionError) as raised:
        ReplaySession.open(
            handle.root,
            session_id=UNKNOWN_SESSION_ID,
            sink=RecordingSink(),
        )

    assert raised.value.code == "replay.session_not_found"


def test_a_directory_that_is_not_a_project_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-project"
    empty.mkdir()

    with pytest.raises(ReplayProjectError) as raised:
        ReplaySession.open(empty, session_id=UNKNOWN_SESSION_ID, sink=RecordingSink())

    assert raised.value.code == "replay.project_unavailable"


def test_a_project_root_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ReplayProjectError) as raised:
        ReplaySession.open(
            tmp_path / "missing.canx",
            session_id=UNKNOWN_SESSION_ID,
            sink=RecordingSink(),
        )

    assert raised.value.code == "replay.invalid_project"


def test_an_active_session_cannot_be_replayed(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _record(handle, finalize=False)

        with pytest.raises(ReplaySessionError) as raised:
            ReplaySession.open(
                handle.root,
                session_id=session_id,
                sink=RecordingSink(),
                config=ReplayConfig(
                    session_completion_policy=SessionCompletionPolicy.ALLOW_INCOMPLETE
                ),
            )

    assert raised.value.code == "replay.session_not_replayable"
    assert raised.value.details["state"] == "active"


def test_an_interrupted_session_is_replayable_only_when_the_policy_allows_it(
    tmp_path: Path,
) -> None:
    sink = RecordingSink()
    with _project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(
            FrameBatch.create(stream_id=STREAM_ID, frames=[frame(0), frame(1)])
        )
        writer.flush_pending()
        session_id = writer.session_id
        recovered = service.recover_incomplete_sessions()

        with pytest.raises(ReplaySessionError) as raised:
            ReplaySession.open(handle.root, session_id=session_id, sink=RecordingSink())

        report = ReplaySession.open(
            handle.root,
            session_id=session_id,
            sink=sink,
            config=ReplayConfig(
                session_completion_policy=SessionCompletionPolicy.ALLOW_INCOMPLETE
            ),
            clock=VirtualReplayClock(),
        ).run()

    assert recovered == (session_id,)
    assert raised.value.code == "replay.session_not_replayable"
    assert raised.value.details["state"] == "interrupted"
    assert report.completed is True
    assert sink.sequences == [0, 1]


def test_a_completed_session_without_frames_follows_the_empty_policy(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _record(handle, total=0)

        with pytest.raises(ReplaySessionError) as raised:
            ReplaySession.open(handle.root, session_id=session_id, sink=RecordingSink())

        sink = RecordingSink()
        report = ReplaySession.open(
            handle.root,
            session_id=session_id,
            sink=sink,
            config=ReplayConfig(empty_session_policy=EmptySessionPolicy.ALLOW),
            clock=VirtualReplayClock(),
        ).run()

    assert raised.value.code == "replay.empty_session"
    assert report.completed is True
    assert report.frames_emitted == 0
    assert sink.events == []
