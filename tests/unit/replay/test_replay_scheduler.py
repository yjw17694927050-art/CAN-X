"""The deterministic scheduler contract of offline replay.

Every test here drives a real :class:`~canx.replay.session.ReplaySession` over an
injected clock and an injected bounded page source. Nothing sleeps, nothing
touches a project, and every failure arm of the replay failure model is exercised
with an input that really produces it.

The central claim under test is determinism: the recorded schedule, the event
order and the terminal state are a pure function of the session metadata, the
pages and the clock, and they survive a different page split.
"""

from __future__ import annotations

from collections.abc import Generator, Sequence
from datetime import UTC, datetime

import pytest
from canx.data.model import DataSession, DataSessionState
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.query.errors import QueryExecutionError
from canx.query.model import FrameQueryPage
from canx.replay.cancellation import ReplayCancellation
from canx.replay.clock import ReplayClock, VirtualReplayClock
from canx.replay.errors import (
    ReplaySchedulerError,
    ReplaySessionError,
    ReplaySinkError,
    ReplaySourceError,
    ReplayStateError,
    ReplayValidationError,
)
from canx.replay.model import (
    EmptySessionPolicy,
    ReplayConfig,
    ReplayFrameEvent,
    ReplayReport,
    ReplayState,
    SessionCompletionPolicy,
)
from canx.replay.session import ReplaySession
from canx.replay.sink import ReplaySink
from canx.replay.source import ReplaySource

SESSION_ID = "11111111-2222-3333-4444-555555555555"
PROJECT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
STREAM_ID = "offline-replay-stream"


# --------------------------------------------------------------------------- #
# Test doubles. The source, the sink and the clock are the three injected ports
# of the scheduler; replacing them is how the scheduler is observed at all.
# --------------------------------------------------------------------------- #
def frame(
    sequence: int,
    *,
    timestamp: float,
    channel_id: str = "can0",
    arbitration_id: int = 0x100,
    is_extended: bool = False,
    is_fd: bool = False,
    bitrate_switch: bool = False,
    error_state_indicator: bool = False,
    dlc: int = 1,
    data: bytes | None = None,
    direction: Direction = Direction.RX,
    hardware_timestamp: float | None = None,
    flags: int = 0,
) -> Frame:
    payload = bytes([sequence % 256]) if data is None else data
    return Frame(
        sequence=sequence,
        channel_id=channel_id,
        arbitration_id=arbitration_id,
        is_extended=is_extended,
        is_fd=is_fd,
        bitrate_switch=bitrate_switch,
        error_state_indicator=error_state_indicator,
        dlc=dlc,
        data=payload,
        direction=direction,
        hardware_timestamp=hardware_timestamp,
        host_timestamp=1_000.0 + timestamp,
        normalized_timestamp=timestamp,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=flags,
    )


def page(
    frames: Sequence[Frame], *, has_more: bool
) -> FrameQueryPage:
    """Build a real bounded page, deriving the cursor from the last frame."""
    if has_more:
        return FrameQueryPage(
            session_id=SESSION_ID,
            frames=tuple(frames),
            has_more=True,
            next_after_sequence=frames[-1].sequence,
        )
    return FrameQueryPage(
        session_id=SESSION_ID,
        frames=tuple(frames),
        has_more=False,
        next_after_sequence=None,
    )


class TrackingSource(ReplaySource):
    """A page source that records how far it was drained and when it was closed."""

    def __init__(self, pages: Sequence[FrameQueryPage], *, session_id: str = SESSION_ID) -> None:
        super().__init__()
        self._pages = tuple(pages)
        self._session_id = session_id
        self.page_reads = 0
        self.exhausted = False
        self.stream_exited = False

    @property
    def session_id(self) -> str:
        return self._session_id

    def pages(self) -> Generator[FrameQueryPage]:
        self.stream_exited = False
        try:
            for one_page in self._pages:
                self.page_reads += 1
                yield one_page
            self.exhausted = True
        finally:
            self.stream_exited = True


class FailingSource(ReplaySource):
    """A source whose query fails, optionally after a number of good pages."""

    def __init__(self, error: Exception, *, pages_before: Sequence[FrameQueryPage] = ()) -> None:
        super().__init__()
        self._error = error
        self._pages = tuple(pages_before)
        self.page_reads = 0

    def pages(self) -> Generator[FrameQueryPage]:
        for one_page in self._pages:
            self.page_reads += 1
            yield one_page
        raise self._error


class RecordingSink:
    """Collects events, and can fail or cancel after a given number of frames."""

    def __init__(
        self,
        *,
        cancel_after: int | None = None,
        fail_on_index: int | None = None,
        state_sink: list[ReplayState] | None = None,
    ) -> None:
        self.events: list[ReplayFrameEvent] = []
        self.replay: ReplaySession | None = None
        self._cancel_after = cancel_after
        self._fail_on_index = fail_on_index
        self._state_sink = state_sink

    def emit(self, event: ReplayFrameEvent) -> None:
        if self._state_sink is not None:
            assert self.replay is not None
            self._state_sink.append(self.replay.state)
        if self._fail_on_index is not None and event.index == self._fail_on_index:
            raise OSError("the sink is gone")
        self.events.append(event)
        if self._cancel_after is not None and len(self.events) == self._cancel_after:
            assert self.replay is not None
            self.replay.cancel()


class BackwardsClock(ReplayClock):
    """A clock that jumps behind its own earlier reading once it is slept on."""

    def __init__(self) -> None:
        self._now = 100.0

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now = 0.0


class FrozenClock(ReplayClock):
    """A clock that never advances, so every deadline stays in the future."""

    def monotonic(self) -> float:
        return 5.0

    def sleep(self, seconds: float) -> None:
        return None


class NanClock(ReplayClock):
    """A clock whose reading is not a number."""

    def monotonic(self) -> float:
        return float("nan")

    def sleep(self, seconds: float) -> None:
        return None


def session(
    *,
    frame_count: int,
    state: DataSessionState = DataSessionState.COMPLETED,
    segment_count: int | None = None,
) -> DataSession:
    moment = datetime(2026, 9, 19, tzinfo=UTC)
    committed = frame_count > 0
    return DataSession(
        session_id=SESSION_ID,
        project_id=PROJECT_ID,
        stream_id=STREAM_ID,
        state=state,
        started_at=moment,
        ended_at=None if state is DataSessionState.ACTIVE else moment,
        frame_count=frame_count,
        segment_count=(1 if committed else 0) if segment_count is None else segment_count,
        first_sequence=0 if committed else None,
        last_sequence=frame_count - 1 if committed else None,
        first_timestamp=0.0 if committed else None,
        last_timestamp=0.0 if committed else None,
        created_at=moment,
        updated_at=moment,
    )


def build(
    *,
    frames: Sequence[Frame] = (),
    pages: Sequence[FrameQueryPage] | None = None,
    frame_count: int | None = None,
    data_session: DataSession | None = None,
    sink: ReplaySink | None = None,
    clock: ReplayClock | None = None,
    config: ReplayConfig | None = None,
    cancellation: ReplayCancellation | None = None,
    source: ReplaySource | None = None,
) -> ReplaySession:
    """Assemble a replay session over a real session model and a page source."""
    recorded = len(frames) if frame_count is None else frame_count
    resolved_session = (
        session(frame_count=recorded) if data_session is None else data_session
    )
    if source is None:
        resolved_pages = (page(frames, has_more=False),) if pages is None else tuple(pages)
        source = TrackingSource(resolved_pages)
    return ReplaySession(
        resolved_session,
        source,
        RecordingSink() if sink is None else sink,
        config=config,
        clock=VirtualReplayClock() if clock is None else clock,
        cancellation=cancellation,
    )


def schedule(events: Sequence[ReplayFrameEvent]) -> list[tuple[int, int, float, float]]:
    return [
        (event.index, event.sequence, event.relative_offset, event.scheduled_at)
        for event in events
    ]


# --------------------------------------------------------------------------- #
# The recorded schedule.
# --------------------------------------------------------------------------- #
def test_a_single_frame_replays_immediately_and_completes() -> None:
    sink = RecordingSink()
    clock = VirtualReplayClock()
    replay = build(frames=[frame(0, timestamp=100.0)], sink=sink, clock=clock)

    report = replay.run()

    assert report.state is ReplayState.COMPLETED
    assert report.completed is True
    assert [event.index for event in sink.events] == [0]
    assert [event.relative_offset for event in sink.events] == [0.0]
    assert [event.scheduled_at for event in sink.events] == [0.0]
    assert clock.sleeps == ()
    assert report.relative_span == 0.0
    assert report.frames_emitted == 1


def test_multiple_frames_are_emitted_in_recorded_order() -> None:
    frames = [frame(sequence, timestamp=1.0 + sequence) for sequence in range(5)]
    sink = RecordingSink()

    report = build(frames=frames, sink=sink).run()

    assert [event.sequence for event in sink.events] == [0, 1, 2, 3, 4]
    assert [event.index for event in sink.events] == [0, 1, 2, 3, 4]
    assert report.first_sequence == 0
    assert report.last_sequence == 4


def test_equal_recorded_timestamps_never_wait() -> None:
    frames = [frame(sequence, timestamp=5.0) for sequence in range(3)]
    clock = VirtualReplayClock()
    sink = RecordingSink()

    report = build(frames=frames, sink=sink, clock=clock).run()

    assert [event.relative_offset for event in sink.events] == [0.0, 0.0, 0.0]
    assert clock.sleeps == ()
    assert clock.total_slept == 0.0
    assert report.relative_span == 0.0


def test_increasing_recorded_timestamps_produce_the_recorded_schedule() -> None:
    frames = [
        frame(0, timestamp=100.0),
        frame(1, timestamp=100.5),
        frame(2, timestamp=101.25),
    ]
    clock = VirtualReplayClock()
    sink = RecordingSink()

    report = build(frames=frames, sink=sink, clock=clock).run()

    assert [event.relative_offset for event in sink.events] == [0.0, 0.5, 1.25]
    assert [event.scheduled_at for event in sink.events] == [0.0, 0.5, 1.25]
    assert clock.sleeps == (0.5, 0.75)
    assert clock.total_slept == report.relative_span == 1.25


def test_the_schedule_is_measured_from_the_first_frame_of_the_session() -> None:
    first = page([frame(0, timestamp=10.0), frame(1, timestamp=11.0)], has_more=True)
    second = page([frame(2, timestamp=13.5)], has_more=False)
    sink = RecordingSink()

    report = build(pages=[first, second], frame_count=3, sink=sink).run()

    assert [event.relative_offset for event in sink.events] == [0.0, 1.0, 3.5]
    assert report.relative_span == 3.5


def test_a_clock_that_starts_elsewhere_shifts_the_deadlines_only() -> None:
    frames = [frame(0, timestamp=2.0), frame(1, timestamp=2.75)]
    clock = VirtualReplayClock(start=1_000.0)
    sink = RecordingSink()

    build(frames=frames, sink=sink, clock=clock).run()

    assert [event.relative_offset for event in sink.events] == [0.0, 0.75]
    assert [event.scheduled_at for event in sink.events] == [1_000.0, 1_000.75]


def test_derived_offsets_are_exactly_the_recorded_differences() -> None:
    timestamps = [0.1, 0.2, 0.35, 1.0]
    frames = [frame(index, timestamp=value) for index, value in enumerate(timestamps)]
    sink = RecordingSink()

    build(frames=frames, sink=sink).run()

    expected = [value - timestamps[0] for value in timestamps]
    assert [event.relative_offset for event in sink.events] == expected


# --------------------------------------------------------------------------- #
# Determinism.
# --------------------------------------------------------------------------- #
def test_the_same_recording_replays_identically_twice() -> None:
    frames = [frame(sequence, timestamp=sequence / 4) for sequence in range(6)]
    first_sink = RecordingSink()
    second_sink = RecordingSink()

    first = build(frames=frames, sink=first_sink).run()
    second = build(frames=frames, sink=second_sink).run()

    assert schedule(first_sink.events) == schedule(second_sink.events)
    assert first == second


def test_the_page_split_never_changes_the_events_or_the_schedule() -> None:
    frames = [frame(sequence, timestamp=sequence / 4) for sequence in range(6)]
    one_page_sink = RecordingSink()
    three_page_sink = RecordingSink()
    three_pages = [
        page(frames[0:2], has_more=True),
        page(frames[2:4], has_more=True),
        page(frames[4:6], has_more=False),
    ]

    one_page_report = build(frames=frames, sink=one_page_sink).run()
    split_report = build(
        pages=three_pages, frame_count=6, sink=three_page_sink
    ).run()

    assert schedule(one_page_sink.events) == schedule(three_page_sink.events)
    assert one_page_report.relative_span == split_report.relative_span
    assert one_page_report.state is split_report.state is ReplayState.COMPLETED
    assert split_report.source_page_count == 3
    assert one_page_report.source_page_count == 1


def test_every_recorded_frame_is_read_back_at_most_one_page_at_a_time() -> None:
    frames = [frame(sequence, timestamp=sequence / 4) for sequence in range(5)]
    pages = [
        page(frames[0:2], has_more=True),
        page(frames[2:4], has_more=True),
        page(frames[4:5], has_more=False),
    ]
    source = TrackingSource(pages)

    report = build(pages=pages, frame_count=5, source=source).run()

    assert source.page_reads == 3
    assert report.source_page_count == 3
    assert report.source_peak_page_frames == 2
    assert report.frames_emitted == 5


# --------------------------------------------------------------------------- #
# Canonical frame preservation.
# --------------------------------------------------------------------------- #
def test_classic_and_fd_frames_are_replayed_field_for_field() -> None:
    classic = frame(
        0,
        timestamp=1.0,
        channel_id="can0",
        arbitration_id=0x2AB,
        is_extended=False,
        is_fd=False,
        dlc=8,
        data=bytes(range(8)),
        direction=Direction.TX,
        hardware_timestamp=0.25,
        flags=0x0000_0001,
    )
    fd = frame(
        1,
        timestamp=1.25,
        channel_id="can1",
        arbitration_id=0x1ABCDEF,
        is_extended=True,
        is_fd=True,
        bitrate_switch=True,
        error_state_indicator=True,
        dlc=64,
        data=bytes(range(64)),
        direction=Direction.RX,
        hardware_timestamp=0.5,
        flags=0x0000_00FF,
    )
    sink = RecordingSink()

    build(frames=[classic, fd], sink=sink).run()

    assert [event.frame for event in sink.events] == [classic, fd]
    assert sink.events[0].frame is classic
    assert sink.events[1].frame is fd
    fd_event = sink.events[1].frame
    assert (fd_event.sequence, fd_event.channel_id) == (1, "can1")
    assert (fd_event.arbitration_id, fd_event.is_extended) == (0x1ABCDEF, True)
    assert (fd_event.is_fd, fd_event.bitrate_switch, fd_event.error_state_indicator) == (
        True,
        True,
        True,
    )
    assert (fd_event.dlc, fd_event.data) == (64, bytes(range(64)))
    assert fd_event.direction is Direction.RX
    assert (fd_event.hardware_timestamp, fd_event.host_timestamp) == (0.5, 1_001.25)
    assert (fd_event.normalized_timestamp, fd_event.clock_domain) == (1.25, "host.monotonic")
    assert fd_event.timestamp_quality is TimestampQuality.HOST
    assert fd_event.flags == 0x0000_00FF


def test_multiple_channels_are_preserved_per_frame() -> None:
    frames = [
        frame(0, timestamp=0.0, channel_id="can0"),
        frame(1, timestamp=0.0, channel_id="can1"),
        frame(2, timestamp=0.0, channel_id="can8"),
    ]
    sink = RecordingSink()

    build(frames=frames, sink=sink).run()

    assert [event.frame.channel_id for event in sink.events] == ["can0", "can1", "can8"]


def test_brs_and_esi_survive_even_when_false_on_a_classic_frame() -> None:
    classic = frame(0, timestamp=0.0, is_fd=False)
    sink = RecordingSink()

    build(frames=[classic], sink=sink).run()

    assert sink.events[0].frame.bitrate_switch is False
    assert sink.events[0].frame.error_state_indicator is False


# --------------------------------------------------------------------------- #
# Lifecycle.
# --------------------------------------------------------------------------- #
def test_completed_is_reached_only_after_the_final_frame() -> None:
    frames = [frame(sequence, timestamp=sequence / 2) for sequence in range(3)]
    seen: list[ReplayState] = []
    sink = RecordingSink(state_sink=seen)
    replay = build(frames=frames, sink=sink)
    sink.replay = replay

    assert replay.state is ReplayState.IDLE
    report = replay.run()

    assert seen == [ReplayState.RUNNING, ReplayState.RUNNING, ReplayState.RUNNING]
    assert report.completed is True
    assert replay.state is ReplayState.COMPLETED


def test_a_replay_is_one_shot_and_refuses_a_second_run() -> None:
    replay = build(frames=[frame(0, timestamp=0.0)])
    replay.run()

    with pytest.raises(ReplayStateError) as raised:
        replay.run()

    assert raised.value.code == "replay.invalid_run_state"
    assert raised.value.details["state"] == "completed"


def test_a_failed_replay_is_terminal_and_leaves_no_running_task() -> None:
    source = TrackingSource((page([frame(0, timestamp=0.0)], has_more=False),))
    sink = RecordingSink(fail_on_index=0)
    replay = build(frames=[frame(0, timestamp=0.0)], sink=sink, source=source)

    with pytest.raises(ReplaySinkError):
        replay.run()

    assert replay.state is ReplayState.FAILED
    assert replay.state.terminal is True
    assert source.closed is True
    assert source.stream_exited is True
    with pytest.raises(ReplayStateError):
        replay.run()


def test_an_invalid_sink_is_refused_before_any_frame_is_read() -> None:
    source = TrackingSource((page([frame(0, timestamp=0.0)], has_more=False),))

    with pytest.raises(ReplayValidationError) as raised:
        ReplaySession(
            session(frame_count=1), source, object()  # type: ignore[arg-type]
        )

    assert raised.value.code == "replay.validation_failed"
    assert source.page_reads == 0


def test_an_invalid_clock_is_refused_before_any_frame_is_read() -> None:
    source = TrackingSource((page([frame(0, timestamp=0.0)], has_more=False),))

    with pytest.raises(ReplayValidationError):
        ReplaySession(
            session(frame_count=1),
            source,
            RecordingSink(),
            clock=object(),  # type: ignore[arg-type]
        )

    assert source.page_reads == 0


def test_a_config_that_is_not_a_replay_config_is_refused() -> None:
    source = TrackingSource((page([frame(0, timestamp=0.0)], has_more=False),))

    with pytest.raises(ReplayValidationError):
        ReplaySession(
            session(frame_count=1),
            source,
            RecordingSink(),
            config=object(),  # type: ignore[arg-type]
        )


def test_a_cancelled_replay_stops_at_a_frame_boundary() -> None:
    frames = [frame(sequence, timestamp=sequence / 2) for sequence in range(4)]
    sink = RecordingSink(cancel_after=2)
    source = TrackingSource((page(frames, has_more=False),))
    replay = build(frames=frames, sink=sink, source=source)
    sink.replay = replay

    report = replay.run()

    assert report.state is ReplayState.CANCELLED
    assert report.completed is False
    assert report.frames_emitted == 2
    assert [event.sequence for event in sink.events] == [0, 1]
    assert replay.state is ReplayState.CANCELLED
    assert source.closed is True
    assert source.exhausted is False
    assert source.stream_exited is True


def test_a_pre_cancelled_replay_never_reads_a_single_page() -> None:
    cancellation = ReplayCancellation()
    cancellation.cancel()
    source = TrackingSource((page([frame(0, timestamp=0.0)], has_more=False),))
    clock = VirtualReplayClock()

    report = build(
        frames=[frame(0, timestamp=0.0)],
        source=source,
        clock=clock,
        cancellation=cancellation,
    ).run()

    assert report.state is ReplayState.CANCELLED
    assert report.frames_emitted == 0
    assert report.relative_span is None
    assert source.page_reads == 0
    assert source.closed is True
    assert clock.sleeps == ()


def test_cancelling_after_the_run_changes_nothing() -> None:
    replay = build(frames=[frame(0, timestamp=0.0)])
    report = replay.run()

    replay.cancel()

    assert report.state is ReplayState.COMPLETED
    assert replay.state is ReplayState.COMPLETED
    assert replay.cancellation.cancelled is True


# --------------------------------------------------------------------------- #
# Failure model.
# --------------------------------------------------------------------------- #
def test_a_sink_failure_stops_emission_and_never_reports_completed() -> None:
    frames = [frame(sequence, timestamp=sequence / 2) for sequence in range(4)]
    sink = RecordingSink(fail_on_index=2)

    replay = build(frames=frames, sink=sink)

    with pytest.raises(ReplaySinkError) as raised:
        replay.run()

    assert raised.value.code == "replay.sink_failed"
    assert raised.value.details["sequence"] == 2
    assert isinstance(raised.value.__cause__, OSError)
    assert [event.sequence for event in sink.events] == [0, 1]
    assert replay.state is ReplayState.FAILED


def test_a_query_failure_fails_the_run() -> None:
    error = QueryExecutionError("duckdb is gone")
    replay = build(
        frame_count=1,
        source=FailingSource(error),
    )

    with pytest.raises(ReplaySourceError) as raised:
        replay.run()

    assert raised.value.code == "replay.source_unavailable"
    assert raised.value.recoverable is True
    assert raised.value.details["cause"] == "query.execution_failed"
    assert raised.value.details["cause_type"] == "QueryExecutionError"
    assert raised.value.__cause__ is error
    assert replay.state is ReplayState.FAILED


def test_a_query_failure_on_a_later_page_keeps_the_frames_already_replayed() -> None:
    frames = [frame(sequence, timestamp=sequence / 2) for sequence in range(4)]
    first = page(frames[0:2], has_more=True)
    sink = RecordingSink()
    replay = build(
        frame_count=4,
        sink=sink,
        source=FailingSource(QueryExecutionError("boom"), pages_before=[first]),
    )

    with pytest.raises(ReplaySourceError):
        replay.run()

    assert [event.sequence for event in sink.events] == [0, 1]
    assert replay.state is ReplayState.FAILED


def test_recorded_time_that_moves_backwards_is_refused() -> None:
    frames = [frame(0, timestamp=10.0), frame(1, timestamp=9.0)]
    sink = RecordingSink()

    with pytest.raises(ReplaySourceError) as raised:
        build(frames=frames, sink=sink).run()

    assert raised.value.code == "replay.non_monotonic_timestamps"
    assert raised.value.details["previous_timestamp"] == 10.0
    assert raised.value.details["timestamp"] == 9.0
    assert [event.sequence for event in sink.events] == [0]


def test_a_repeated_sequence_is_refused() -> None:
    frames = [frame(0, timestamp=0.0), frame(0, timestamp=0.5)]

    with pytest.raises(ReplaySourceError) as raised:
        build(frames=frames).run()

    assert raised.value.code == "replay.non_increasing_sequence"


def test_a_source_that_delivers_more_frames_than_the_session_records_is_refused() -> None:
    frames = [frame(sequence, timestamp=0.0) for sequence in range(3)]
    sink = RecordingSink()

    with pytest.raises(ReplaySourceError) as raised:
        build(frames=frames, sink=sink, data_session=session(frame_count=2)).run()

    assert raised.value.code == "replay.source_overrun"
    assert [event.sequence for event in sink.events] == [0, 1]
    assert raised.value.details["recorded_frame_count"] == 2


def test_a_source_that_stops_dropping_the_final_page_never_reports_completed() -> None:
    frames = [frame(sequence, timestamp=0.0) for sequence in range(3)]
    truncating = TrackingSource((page(frames[0:1], has_more=True),))
    sink = RecordingSink()
    replay = build(frame_count=3, sink=sink, source=truncating)

    with pytest.raises(ReplaySourceError) as raised:
        replay.run()

    assert raised.value.code == "replay.source_truncated"
    assert replay.state is ReplayState.FAILED
    assert replay.state is not ReplayState.COMPLETED


def test_a_source_that_delivers_fewer_frames_than_the_session_records_is_refused() -> None:
    frames = [frame(sequence, timestamp=0.0) for sequence in range(2)]
    sink = RecordingSink()
    replay = build(frames=frames, sink=sink, data_session=session(frame_count=3))

    with pytest.raises(ReplaySourceError) as raised:
        replay.run()

    assert raised.value.code == "replay.source_incomplete"
    assert raised.value.details["recorded_frame_count"] == 3
    assert raised.value.details["replayed_frame_count"] == 2
    assert replay.state is ReplayState.FAILED


def test_a_page_belonging_to_another_session_is_refused() -> None:
    other = FrameQueryPage(
        session_id="99999999-8888-7777-6666-555555555555",
        frames=(frame(0, timestamp=0.0),),
        has_more=False,
        next_after_sequence=None,
    )
    source = TrackingSource((other,))

    with pytest.raises(ReplaySourceError) as raised:
        build(frames=[frame(0, timestamp=0.0)], source=source).run()

    assert raised.value.code == "replay.source_session_mismatch"


def test_a_clock_reading_that_is_not_a_number_fails_the_scheduler() -> None:
    replay = build(frames=[frame(0, timestamp=0.0)], clock=NanClock())

    with pytest.raises(ReplaySchedulerError) as raised:
        replay.run()

    assert raised.value.code == "replay.scheduler_failed"
    assert replay.state is ReplayState.FAILED


def test_a_clock_that_moves_backwards_fails_the_scheduler() -> None:
    frames = [frame(0, timestamp=0.0), frame(1, timestamp=1.0)]
    sink = RecordingSink()
    replay = build(frames=frames, sink=sink, clock=BackwardsClock())

    with pytest.raises(ReplaySchedulerError) as raised:
        replay.run()

    assert raised.value.code == "replay.scheduler_clock_backwards"
    assert [event.sequence for event in sink.events] == [0]
    assert replay.state is ReplayState.FAILED


def test_a_frozen_clock_cannot_complete_a_realtime_schedule() -> None:
    frames = [frame(0, timestamp=0.0), frame(1, timestamp=1.0)]
    replay = build(frames=frames, clock=FrozenClock())

    with pytest.raises(ReplaySchedulerError):
        replay.run()

    assert replay.state is ReplayState.FAILED


# --------------------------------------------------------------------------- #
# Session policies.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "state",
    [DataSessionState.ACTIVE, DataSessionState.INTERRUPTED, DataSessionState.FAILED],
)
def test_only_a_completed_session_replays_by_default(state: DataSessionState) -> None:
    source = TrackingSource((page([frame(0, timestamp=0.0)], has_more=False),))

    with pytest.raises(ReplaySessionError) as raised:
        ReplaySession(session(frame_count=1, state=state), source, RecordingSink())

    assert raised.value.code == "replay.session_not_replayable"
    assert raised.value.details["state"] == state.value
    assert source.page_reads == 0


@pytest.mark.parametrize(
    "state",
    [DataSessionState.INTERRUPTED, DataSessionState.FAILED],
)
def test_a_terminal_incomplete_session_replays_when_the_policy_allows_it(
    state: DataSessionState,
) -> None:
    frames = [frame(0, timestamp=0.0)]
    sink = RecordingSink()
    replay = ReplaySession(
        session(frame_count=1, state=state),
        TrackingSource((page(frames, has_more=False),)),
        sink,
        config=ReplayConfig(
            session_completion_policy=SessionCompletionPolicy.ALLOW_INCOMPLETE
        ),
    )

    report = replay.run()

    assert report.completed is True
    assert [event.sequence for event in sink.events] == [0]


def test_an_active_session_is_never_replayable_whatever_the_policy() -> None:
    source = TrackingSource((page([frame(0, timestamp=0.0)], has_more=False),))

    with pytest.raises(ReplaySessionError):
        ReplaySession(
            session(frame_count=1, state=DataSessionState.ACTIVE),
            source,
            RecordingSink(),
            config=ReplayConfig(
                session_completion_policy=SessionCompletionPolicy.ALLOW_INCOMPLETE
            ),
        )

    assert source.page_reads == 0


def test_an_empty_session_is_rejected_by_default() -> None:
    source = TrackingSource(())

    with pytest.raises(ReplaySessionError) as raised:
        ReplaySession(session(frame_count=0), source, RecordingSink())

    assert raised.value.code == "replay.empty_session"
    assert source.page_reads == 0


def test_an_empty_session_completes_immediately_when_the_policy_allows_it() -> None:
    empty_page = FrameQueryPage(
        session_id=SESSION_ID, frames=(), has_more=False, next_after_sequence=None
    )
    source = TrackingSource((empty_page,))
    sink = RecordingSink()
    clock = VirtualReplayClock()

    report = ReplaySession(
        session(frame_count=0),
        source,
        sink,
        config=ReplayConfig(empty_session_policy=EmptySessionPolicy.ALLOW),
        clock=clock,
    ).run()

    assert report.completed is True
    assert report.frames_emitted == 0
    assert report.relative_span is None
    assert report.first_sequence is None
    assert report.source_page_count == 1
    assert sink.events == []
    assert clock.sleeps == ()


def test_an_empty_session_is_never_completed_by_a_source_that_invents_frames() -> None:
    frames = [frame(0, timestamp=0.0)]
    replay = ReplaySession(
        session(frame_count=0),
        TrackingSource((page(frames, has_more=False),)),
        RecordingSink(),
        config=ReplayConfig(empty_session_policy=EmptySessionPolicy.ALLOW),
    )

    with pytest.raises(ReplaySourceError) as raised:
        replay.run()

    assert raised.value.code == "replay.source_overrun"
    assert replay.state is ReplayState.FAILED


def test_the_report_of_a_completed_replay_is_a_value_object() -> None:
    frames = [frame(sequence, timestamp=sequence) for sequence in range(3)]
    first = build(frames=frames).run()
    second = build(frames=frames).run()

    assert isinstance(first, ReplayReport)
    assert first == second
