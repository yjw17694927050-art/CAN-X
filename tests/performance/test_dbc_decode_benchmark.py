"""A measured decode-throughput baseline, not a throughput target.

DBC decoding is on the path of a future live pipeline, so this increment records
what the engine actually does on this machine instead of leaving the question
open. It deliberately asserts **no** frames-per-second threshold: there is no
agreed requirement to fail against, and inventing one would turn a measurement
into a claim.

What is asserted is that the work really happened — every input frame produced
an outcome, and the measured span is positive — so the numbers printed below
cannot come from a loop that silently did nothing.

The frame count is bounded by construction: frames are generated on demand and
consumed batch by batch, so the benchmark never materialises a whole dataset
(``50 GB → list`` is exactly what this stage must not do).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

from canx.dbc.decode import DbcDecoder
from canx.dbc.service import DbcImportService
from canx.domain.batch import FrameBatch, batch_frames
from canx.domain.frame import Direction, Frame, TimestampQuality

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "dbc"

#: The measured unit the increment asks for.
FRAMES = 100_000

#: Batch size for the batched measurement; the input is generated, never stored.
BATCH_SIZE = 1_000

ENGINE_DATA = bytes([0xB8, 0x0B, 0x50, 0x80]) + bytes(4)
MUX_DATA = bytes([1]) + bytes(range(1, 8))


def decoder_for(asset: str) -> DbcDecoder:
    document = DbcImportService().import_file(FIXTURES / asset)
    return DbcDecoder(document.database)


def frame(data: bytes, *, sequence: int, arbitration_id: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=arbitration_id,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=len(data),
        data=data,
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=1.0,
        normalized_timestamp=0.0,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def frames(start: int, count: int, data: bytes, arbitration_id: int) -> Iterator[Frame]:
    """Yield ``count`` frames from ``start`` without building a list."""
    for sequence in range(start, start + count):
        yield frame(data, sequence=sequence, arbitration_id=arbitration_id)


def report(label: str, frames_decoded: int, seconds: float) -> None:
    """Print one measurement line so a run can be recorded verbatim."""
    rate = frames_decoded / seconds
    print(
        f"dbc-decode {label}: {frames_decoded} frames in {seconds:.3f}s"
        f" -> {rate:,.0f} frames/sec"
    )


def test_decoding_a_simple_message_is_measured() -> None:
    decoder = decoder_for("basic_standard.dbc")
    subject = frame(ENGINE_DATA, sequence=1, arbitration_id=0x123)

    started = time.perf_counter()
    decoded = sum(1 for _ in range(FRAMES) if decoder.decode_frame(subject) is not None)
    elapsed = time.perf_counter() - started

    assert decoded == FRAMES
    assert elapsed > 0
    report("simple", decoded, elapsed)


def test_decoding_a_multiplexed_message_is_measured() -> None:
    decoder = decoder_for("multiplexed.dbc")
    subject = frame(MUX_DATA, sequence=1, arbitration_id=1024)

    started = time.perf_counter()
    decoded = sum(1 for _ in range(FRAMES) if decoder.decode_frame(subject) is not None)
    elapsed = time.perf_counter() - started

    assert decoded == FRAMES
    assert elapsed > 0
    report("multiplexed", decoded, elapsed)


def test_decoding_a_stream_in_batches_is_measured() -> None:
    """The batched path, with the input generated batch by batch."""
    decoder = decoder_for("basic_standard.dbc")
    batches = batch_frames(
        frames(1, FRAMES, ENGINE_DATA, 0x123), size=BATCH_SIZE, stream_id="benchmark"
    )

    started = time.perf_counter()
    decoded = 0
    for batch in batches:
        assert isinstance(batch, FrameBatch)
        decoded += decoder.decode_batch(batch).frame_count
    elapsed = time.perf_counter() - started

    assert decoded == FRAMES
    assert elapsed > 0
    report("batched", decoded, elapsed)
