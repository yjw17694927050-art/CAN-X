"""A large recording must replay with a working set of one page, not one session.

The claim "replay memory stays bounded" is only worth something if it is measured.
These tests measure it two ways: structurally, from the source's own page
accounting (a page can never be larger than ``page_size``, so the scheduler never
holds more than one page), and with ``tracemalloc``, by comparing the peak traced
memory of a replay that reads small pages with one that reads the whole recording
in a single page. The sink is a counter rather than a collector on purpose: a sink
that retains every frame would measure the sink, not the replay.
"""

from __future__ import annotations

import tracemalloc
from math import ceil
from pathlib import Path

import pytest
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.replay.clock import VirtualReplayClock
from canx.replay.model import ReplayConfig, ReplayFrameEvent, ReplayReport
from canx.replay.session import ReplaySession

STREAM_ID = "replay-soak-stream"
FRAMES_PER_SEGMENT = 500
TOTAL_FRAMES = 3_000


class CountingSink:
    """A sink that keeps counters, never frames: the replay's memory, not the sink's."""

    def __init__(self) -> None:
        self.count = 0
        self.first_sequence: int | None = None
        self.last_sequence: int | None = None
        self.ordered = True

    def emit(self, event: ReplayFrameEvent) -> None:
        sequence = event.frame.sequence
        if self.last_sequence is not None and sequence <= self.last_sequence:
            self.ordered = False
        if self.first_sequence is None:
            self.first_sequence = sequence
        self.last_sequence = sequence
        self.count += 1


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x100 + (sequence % 16),
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=8,
        data=bytes(sequence % 256 for _ in range(8)),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=1_000.0 + sequence / 100.0,
        normalized_timestamp=sequence / 100.0,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def _record(handle: ProjectHandle, *, total: int = TOTAL_FRAMES) -> str:
    service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    for start in range(0, total, FRAMES_PER_SEGMENT):
        size = min(FRAMES_PER_SEGMENT, total - start)
        writer.append(
            FrameBatch.create(
                stream_id=STREAM_ID,
                frames=[frame(sequence) for sequence in range(start, start + size)],
            )
        )
    writer.finalize()
    return writer.session_id


def _replay(
    handle: ProjectHandle, session_id: str, *, page_size: int
) -> tuple[ReplayReport, CountingSink]:
    sink = CountingSink()
    replay = ReplaySession.open(
        handle.root,
        session_id=session_id,
        sink=sink,
        config=ReplayConfig(page_size=page_size),
        clock=VirtualReplayClock(),
    )
    return replay.run(), sink


def _peak_bytes(handle: ProjectHandle, session_id: str, *, page_size: int) -> int:
    tracemalloc.start()
    try:
        _replay(handle, session_id, page_size=page_size)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def test_a_large_recording_replays_with_a_bounded_working_set(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="Soak") as handle:
        session_id = _record(handle)

        report, sink = _replay(handle, session_id, page_size=64)

    assert report.completed is True
    assert sink.count == TOTAL_FRAMES
    assert sink.first_sequence == 0
    assert sink.last_sequence == TOTAL_FRAMES - 1
    assert sink.ordered is True
    assert report.source_page_count == ceil(TOTAL_FRAMES / 64)
    assert report.source_peak_page_frames <= 64
    assert report.frames_emitted == TOTAL_FRAMES


def test_the_scheduler_never_holds_more_than_one_page(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="Soak") as handle:
        session_id = _record(handle)

        small, _ = _replay(handle, session_id, page_size=32)
        large, _ = _replay(handle, session_id, page_size=FRAMES_PER_SEGMENT)

    assert small.source_peak_page_frames == 32
    assert large.source_peak_page_frames == FRAMES_PER_SEGMENT
    assert small.source_page_count > large.source_page_count
    assert small.frames_emitted == large.frames_emitted == TOTAL_FRAMES


def test_peak_memory_follows_the_page_size_not_the_recording_size(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="Soak") as handle:
        session_id = _record(handle)
        # Warm the lazily imported query machinery first, so the comparison is
        # between two replays rather than between a cold and a warm interpreter.
        _replay(handle, session_id, page_size=64)

        bounded_pages_peak = _peak_bytes(handle, session_id, page_size=64)
        whole_session_peak = _peak_bytes(handle, session_id, page_size=TOTAL_FRAMES)

    assert bounded_pages_peak < whole_session_peak, (
        f"page-bounded replay peaked at {bounded_pages_peak} bytes,"
        f" single-page replay at {whole_session_peak} bytes"
    )


def test_a_large_recording_is_delivered_exactly_once_each(tmp_path: Path) -> None:
    seen: list[int] = []

    class CollectingSink:
        def emit(self, event: ReplayFrameEvent) -> None:
            seen.append(event.frame.sequence)

    with ProjectService().create(tmp_path / "vehicle.canx", display_name="Soak") as handle:
        session_id = _record(handle, total=1_000)
        replay = ReplaySession.open(
            handle.root,
            session_id=session_id,
            sink=CollectingSink(),
            config=ReplayConfig(page_size=128),
            clock=VirtualReplayClock(),
        )

        report = replay.run()

    assert report.completed is True
    assert seen == list(range(1_000))
    assert len(set(seen)) == len(seen)


@pytest.mark.parametrize("page_size", [1, 7, 500])
def test_every_page_size_produces_the_same_stream(tmp_path: Path, page_size: int) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="Soak") as handle:
        session_id = _record(handle, total=FRAMES_PER_SEGMENT * 2)

        report, sink = _replay(handle, session_id, page_size=page_size)

    assert sink.count == FRAMES_PER_SEGMENT * 2
    assert report.first_sequence == 0
    assert report.last_sequence == FRAMES_PER_SEGMENT * 2 - 1
    assert report.completed is True
