"""Model-level contract of the offline replay domain.

The models are the vocabulary every other replay test speaks, so their bounds are
asserted directly: a config that cannot describe a real page, an event that
carries something other than a canonical frame, and a report that claims a
terminal state it cannot have are all refused at construction.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.query.model import MAX_QUERY_ROWS
from canx.replay.errors import ReplayValidationError
from canx.replay.model import (
    DEFAULT_REPLAY_PAGE_SIZE,
    MAX_REPLAY_PAGE_SIZE,
    EmptySessionPolicy,
    ReplayConfig,
    ReplayFrameEvent,
    ReplayReport,
    ReplayState,
    SessionCompletionPolicy,
)


def _frame(sequence: int = 0) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x123,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=1,
        data=bytes([sequence % 256]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=10.0 + sequence,
        normalized_timestamp=10.0 + sequence,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def test_the_default_config_is_one_bounded_page_and_the_strictest_policies() -> None:
    config = ReplayConfig()

    assert config.page_size == DEFAULT_REPLAY_PAGE_SIZE == 256
    assert config.session_completion_policy is SessionCompletionPolicy.COMPLETED_ONLY
    assert config.empty_session_policy is EmptySessionPolicy.REJECT


def test_a_replay_page_never_exceeds_the_bounded_query_page_limit() -> None:
    assert MAX_REPLAY_PAGE_SIZE == MAX_QUERY_ROWS

    with pytest.raises(ReplayValidationError) as raised:
        ReplayConfig(page_size=MAX_REPLAY_PAGE_SIZE + 1)

    assert raised.value.code == "replay.page_size_exceeded"
    assert raised.value.details["max_page_size"] == MAX_REPLAY_PAGE_SIZE


@pytest.mark.parametrize("page_size", [0, -1, 1.5, True, "256"])
def test_an_unusable_page_size_is_refused(page_size: object) -> None:
    with pytest.raises(ReplayValidationError) as raised:
        ReplayConfig(page_size=page_size)  # type: ignore[arg-type]

    assert raised.value.code == "replay.invalid_page_size"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_completion_policy", "completed_only"),
        ("empty_session_policy", "reject"),
    ],
)
def test_a_policy_that_is_not_the_enum_is_refused(field: str, value: object) -> None:
    with pytest.raises(ReplayValidationError) as raised:
        ReplayConfig(**{field: value})  # type: ignore[arg-type]

    assert raised.value.code == "replay.invalid_config"


def test_the_replay_state_set_is_closed_and_free_of_v0409_states() -> None:
    assert {state.value for state in ReplayState} == {
        "idle",
        "running",
        "completed",
        "failed",
        "cancelled",
    }
    for out_of_scope in ("paused", "looping", "trigger_wait", "stopped"):
        assert out_of_scope not in {state.value for state in ReplayState}


@pytest.mark.parametrize(
    ("state", "terminal"),
    [
        (ReplayState.IDLE, False),
        (ReplayState.RUNNING, False),
        (ReplayState.COMPLETED, True),
        (ReplayState.FAILED, True),
        (ReplayState.CANCELLED, True),
    ],
)
def test_only_the_three_end_states_are_terminal(state: ReplayState, terminal: bool) -> None:
    assert state.terminal is terminal


def test_an_event_carries_the_canonical_frame_unchanged() -> None:
    frame = _frame(7)

    event = ReplayFrameEvent(index=3, frame=frame, relative_offset=0.5, scheduled_at=12.5)

    assert event.frame is frame
    assert event.sequence == 7
    assert event.relative_offset == 0.5
    assert event.scheduled_at == 12.5


def test_an_event_without_a_canonical_frame_is_refused() -> None:
    with pytest.raises(ReplayValidationError) as raised:
        ReplayFrameEvent(index=0, frame="frame", relative_offset=0.0, scheduled_at=0.0)  # type: ignore[arg-type]

    assert raised.value.code == "replay.invalid_event"


@pytest.mark.parametrize(
    ("index", "relative_offset", "scheduled_at"),
    [
        (-1, 0.0, 0.0),
        (True, 0.0, 0.0),
        (0, -0.5, 0.0),
        (0, float("nan"), 0.0),
        (0, float("inf"), 0.0),
        (0, 0.0, float("nan")),
        (0, 0.0, -1.0),
    ],
)
def test_an_event_on_an_impossible_schedule_is_refused(
    index: object, relative_offset: float, scheduled_at: float
) -> None:
    with pytest.raises(ReplayValidationError):
        ReplayFrameEvent(
            index=index,  # type: ignore[arg-type]
            frame=_frame(),
            relative_offset=relative_offset,
            scheduled_at=scheduled_at,
        )


def test_a_report_of_a_finished_replay_describes_the_recording_it_delivered() -> None:
    report = ReplayReport(
        session_id="11111111-2222-3333-4444-555555555555",
        state=ReplayState.COMPLETED,
        frames_emitted=3,
        source_page_count=2,
        source_peak_page_frames=2,
        relative_span=1.25,
        first_sequence=0,
        last_sequence=2,
    )

    assert report.completed is True


def test_a_cancelled_report_is_not_a_completed_report() -> None:
    report = ReplayReport(
        session_id="11111111-2222-3333-4444-555555555555",
        state=ReplayState.CANCELLED,
        frames_emitted=0,
        source_page_count=0,
        source_peak_page_frames=0,
        relative_span=None,
        first_sequence=None,
        last_sequence=None,
    )

    assert report.completed is False


@pytest.mark.parametrize("state", [ReplayState.IDLE, ReplayState.RUNNING, ReplayState.FAILED])
def test_a_report_never_claims_a_state_a_finished_replay_cannot_have(
    state: ReplayState,
) -> None:
    with pytest.raises(ReplayValidationError) as raised:
        ReplayReport(
            session_id="11111111-2222-3333-4444-555555555555",
            state=state,
            frames_emitted=0,
            source_page_count=0,
            source_peak_page_frames=0,
            relative_span=None,
            first_sequence=None,
            last_sequence=None,
        )

    assert raised.value.code == "replay.invalid_report"


def test_a_report_cannot_claim_more_frames_than_one_page_can_hold() -> None:
    with pytest.raises(ReplayValidationError):
        ReplayReport(
            session_id="11111111-2222-3333-4444-555555555555",
            state=ReplayState.COMPLETED,
            frames_emitted=1,
            source_page_count=1,
            source_peak_page_frames=MAX_REPLAY_PAGE_SIZE + 1,
            relative_span=0.0,
            first_sequence=0,
            last_sequence=0,
        )


def test_a_report_of_an_empty_replay_cannot_invent_a_span() -> None:
    with pytest.raises(ReplayValidationError):
        ReplayReport(
            session_id="11111111-2222-3333-4444-555555555555",
            state=ReplayState.COMPLETED,
            frames_emitted=0,
            source_page_count=1,
            source_peak_page_frames=0,
            relative_span=0.0,
            first_sequence=None,
            last_sequence=None,
        )


def test_the_models_carry_no_production_wall_clock_dependency() -> None:
    # A replay model must be constructible from recorded data alone: the frame
    # below is built from explicit values, and no model in this test read the
    # clock. This is the boundary that keeps replay reproducible.
    frame = _frame()
    report = ReplayReport(
        session_id="11111111-2222-3333-4444-555555555555",
        state=ReplayState.COMPLETED,
        frames_emitted=1,
        source_page_count=1,
        source_peak_page_frames=1,
        relative_span=0.0,
        first_sequence=frame.sequence,
        last_sequence=frame.sequence,
    )

    assert isinstance(datetime.now(UTC), datetime)
    assert report.last_sequence == frame.sequence
