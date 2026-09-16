"""Cross-segment historical query over a multi-segment data session.

This is the acceptance path for the V0.2-03 query foundation: a single session
spans many Parquet segments, and a query must return exactly the frames the
filter describes no matter which segment a frame lives in — while leaving
segments the filter cannot touch out of the scan entirely.

Expected results are computed independently in Python from the source frames, so
a DuckDB-specific mistake cannot be confirmed by DuckDB itself.
"""

from collections.abc import Iterable
from pathlib import Path

import pytest
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.query.model import FrameFilter, FrameQuery
from canx.query.service import QueryService

STREAM_ID = "across-stream"
FRAMES_PER_SEGMENT = 10
TOTAL_FRAMES = 120


def frame(sequence: int) -> Frame:
    is_fd = sequence % 11 == 0
    return Frame(
        sequence=sequence,
        channel_id=f"can{sequence % 3}",
        arbitration_id=0x100 + (sequence % 5),
        is_extended=sequence % 7 == 0,
        is_fd=is_fd,
        bitrate_switch=is_fd and sequence % 22 == 0,
        error_state_indicator=is_fd and sequence % 33 == 0,
        dlc=2,
        data=bytes([sequence % 256, 0x42]),
        direction=Direction.RX if sequence % 2 == 0 else Direction.TX,
        hardware_timestamp=1000.0 + sequence if sequence % 4 == 0 else None,
        host_timestamp=2000.0 + sequence,
        normalized_timestamp=sequence / 100.0,
        clock_domain="host.monotonic",
        timestamp_quality=(
            TimestampQuality.HARDWARE if sequence % 4 == 0 else TimestampQuality.HOST
        ),
        flags=0,
    )


SOURCE_FRAMES = [frame(sequence) for sequence in range(TOTAL_FRAMES)]


def _write_session(handle: ProjectHandle) -> tuple[DataSessionService, str]:
    service = DataSessionService(
        handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT
    )
    writer = service.start(stream_id=STREAM_ID)
    for start in range(0, TOTAL_FRAMES, FRAMES_PER_SEGMENT):
        writer.append(
            FrameBatch.create(
                stream_id=STREAM_ID,
                frames=SOURCE_FRAMES[start : start + FRAMES_PER_SEGMENT],
            )
        )
    writer.finalize()
    return service, writer.session_id


def _selected(frames: Iterable[Frame], **bounds: object) -> list[int]:
    """Independently filter frames, mirroring the documented predicate semantics."""
    selected = []
    for item in frames:
        if "sequence_start" in bounds and item.sequence < bounds["sequence_start"]:  # type: ignore[operator]
            continue
        if "sequence_end" in bounds and item.sequence > bounds["sequence_end"]:  # type: ignore[operator]
            continue
        if (
            "timestamp_start" in bounds
            and item.normalized_timestamp < bounds["timestamp_start"]  # type: ignore[operator]
        ):
            continue
        if (
            "timestamp_end" in bounds
            and item.normalized_timestamp > bounds["timestamp_end"]  # type: ignore[operator]
        ):
            continue
        if "channels" in bounds and item.channel_id not in bounds["channels"]:  # type: ignore[operator]
            continue
        if (
            "arbitration_ids" in bounds
            and item.arbitration_id not in bounds["arbitration_ids"]  # type: ignore[operator]
        ):
            continue
        if "directions" in bounds and item.direction not in bounds["directions"]:  # type: ignore[operator]
            continue
        if "is_extended" in bounds and item.is_extended is not bounds["is_extended"]:
            continue
        if "is_fd" in bounds and item.is_fd is not bounds["is_fd"]:
            continue
        selected.append(item.sequence)
    return selected


def test_a_window_spanning_several_segments_returns_exactly_those_frames(
    tmp_path: Path,
) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        _service, session_id = _write_session(handle)
        service = QueryService(handle.root)

        page = service.query_frames(
            FrameQuery(
                filter=FrameFilter(session_id=session_id, sequence_start=35, sequence_end=54),
                limit=1_000,
            )
        )

    assert [item.sequence for item in page.frames] == list(range(35, 55))


def test_a_window_spanning_segments_three_to_five_prunes_the_rest(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        _service, session_id = _write_session(handle)
        service = QueryService(handle.root)

        plan = service.plan_frames(
            FrameFilter(session_id=session_id, sequence_start=35, sequence_end=54)
        )

    assert plan.registered_segment_count == 12
    assert plan.candidate_segment_count == 3
    assert plan.pruned_segment_count == 9
    assert [path.rsplit("/", 1)[-1] for path in plan.relative_paths] == [
        "000003.parquet",
        "000004.parquet",
        "000005.parquet",
    ]


def test_a_query_over_the_whole_session_reconstructs_every_source_frame(
    tmp_path: Path,
) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        _service, session_id = _write_session(handle)
        service = QueryService(handle.root)

        collected = []
        cursor = None
        while True:
            page = service.query_frames(
                FrameQuery(
                    filter=FrameFilter(session_id=session_id),
                    after_sequence=cursor,
                    limit=1_000,
                )
            )
            collected.extend(page.frames)
            if not page.has_more:
                break
            cursor = page.next_after_sequence

    assert tuple(collected) == tuple(SOURCE_FRAMES)


@pytest.mark.parametrize(
    "bounds",
    [
        {"sequence_start": 17, "sequence_end": 94},
        {"timestamp_start": 0.17, "timestamp_end": 0.94},
        {"arbitration_ids": (0x100, 0x102)},
        {"channels": ("can1",)},
        {"directions": (Direction.TX,)},
        {"is_extended": True},
        {"is_fd": True},
        {
            "sequence_start": 10,
            "sequence_end": 80,
            "timestamp_start": 0.1,
            "timestamp_end": 0.8,
            "arbitration_ids": (0x100, 0x103),
            "channels": ("can0", "can1"),
            "directions": (Direction.RX,),
            "is_extended": False,
            "is_fd": False,
        },
    ],
    ids=[
        "sequence-window",
        "timestamp-window",
        "arbitration-ids",
        "channel",
        "direction",
        "extended",
        "fd",
        "all-axes-combined",
    ],
)
def test_every_filter_axis_matches_the_independent_python_selection(
    tmp_path: Path, bounds: dict[str, object]
) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        _service, session_id = _write_session(handle)
        service = QueryService(handle.root)

        frame_filter = FrameFilter(
            session_id=session_id,
            sequence_start=bounds.get("sequence_start"),  # type: ignore[arg-type]
            sequence_end=bounds.get("sequence_end"),  # type: ignore[arg-type]
            normalized_timestamp_start=bounds.get("timestamp_start"),  # type: ignore[arg-type]
            normalized_timestamp_end=bounds.get("timestamp_end"),  # type: ignore[arg-type]
            channel_ids=bounds.get("channels"),  # type: ignore[arg-type]
            arbitration_ids=bounds.get("arbitration_ids"),  # type: ignore[arg-type]
            directions=bounds.get("directions"),  # type: ignore[arg-type]
            is_extended=bounds.get("is_extended"),  # type: ignore[arg-type]
            is_fd=bounds.get("is_fd"),  # type: ignore[arg-type]
        )
        page = service.query_frames(FrameQuery(filter=frame_filter, limit=1_000))

    assert [item.sequence for item in page.frames] == _selected(SOURCE_FRAMES, **bounds)


def test_classic_extended_and_fd_metadata_round_trip_through_a_query(
    tmp_path: Path,
) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        _service, session_id = _write_session(handle)
        service = QueryService(handle.root)

        page = service.query_frames(
            FrameQuery(filter=FrameFilter(session_id=session_id), limit=1_000)
        )

    returned = {item.sequence: item for item in page.frames}
    extended = [item for item in page.frames if item.is_extended]
    fd = [item for item in page.frames if item.is_fd]
    hardware = [item for item in page.frames if item.hardware_timestamp is not None]

    assert returned[0] == SOURCE_FRAMES[0]
    assert extended, "the fixture must contain extended ids"
    assert all(item.arbitration_id <= 0x1FFFFFFF for item in extended)
    assert fd, "the fixture must contain CAN FD frames"
    assert any(item.bitrate_switch for item in fd)
    assert any(item.error_state_indicator for item in fd)
    assert hardware and len(hardware) + len(
        [item for item in page.frames if item.hardware_timestamp is None]
    ) == TOTAL_FRAMES
    assert {item.direction for item in page.frames} == {Direction.RX, Direction.TX}
