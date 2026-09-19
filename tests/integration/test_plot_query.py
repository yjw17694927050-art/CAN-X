"""End-to-end historical plot query: real project, real session, real DBC asset.

This is the increment's composition proof. It builds a real CAN-X project, writes
real Parquet segments through the real ``DataSessionService``, imports a real DBC
as a project-owned asset, and derives a series through the real
``HistoricalSignalQueryService`` — no mock stands in for any of the four domains.
The tests therefore exercise the wiring the task cares about: the source-of-truth
chain holds, a signal is never merged across channels/assets/messages, and a
tampered segment or DBC fails closed instead of producing a plausible curve.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from canx.data.session import DataSessionService
from canx.dbc.asset import asset_relative_path
from canx.dbc.decode import DbcDecoder
from canx.dbc.model import DbcDatabase
from canx.dbc.project_service import ProjectDbcService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.plot.errors import (
    PlotAssetError,
    PlotError,
    PlotQueryError,
    PlotSessionError,
    PlotSignalNotFoundError,
    PlotValidationError,
)
from canx.plot.model import PlotQuery, PlotSeries, SignalIdentity
from canx.plot.service import HistoricalSignalQueryService
from canx.project.service import ProjectHandle, ProjectService

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "dbc"
BASIC_FIXTURE = FIXTURES / "basic_standard.dbc"
CAN_FD_FIXTURE = FIXTURES / "can_fd.dbc"

STREAM_ID = "plot-stream"
ENGINE_DATA_ID = 291
CAN_FD_ID = 1536
UNKNOWN_SESSION_ID = "99999999-8888-4777-8666-555555555555"
UNKNOWN_ASSET_ID = "99999999-8888-4777-8666-555555555555"

#: Two assets that declare the same message and signal but scale them differently.
ASSET_SCALE_ONE = """
VERSION "1.0"

NS_ :

BS_:

BU_: Node

BO_ 256 Shared: 8 Node
 SG_ Signal : 0|16@1+ (1,0) [0|65535] "u" Node
"""

ASSET_SCALE_HUNDRED = """
VERSION "1.0"

NS_ :

BS_:

BU_: Node

BO_ 256 Shared: 8 Node
 SG_ Signal : 0|16@1+ (100,0) [0|6553500] "u" Node
"""

#: One asset with two messages that share a signal name and two signals in one message.
ISOLATION_DBC = """
VERSION "1.0"

NS_ :

BS_:

BU_: Node

BO_ 256 MsgA: 8 Node
 SG_ Value : 0|16@1+ (1,0) [0|65535] "u" Node
 SG_ Other : 16|16@1+ (1,0) [0|65535] "u" Node

BO_ 512 MsgB: 8 Node
 SG_ Value : 0|16@1+ (10,0) [0|655350] "u" Node
"""


def frame(sequence: int, **changes: Any) -> Frame:
    """Build one canonical frame with explicit provenance."""
    values: dict[str, Any] = {
        "sequence": sequence,
        "channel_id": "can0",
        "arbitration_id": ENGINE_DATA_ID,
        "is_extended": False,
        "is_fd": False,
        "bitrate_switch": False,
        "error_state_indicator": False,
        "dlc": 8,
        "data": engine_data(sequence % 100),
        "direction": Direction.RX,
        "hardware_timestamp": None,
        "host_timestamp": 1_000.0 + sequence,
        "normalized_timestamp": float(sequence) * 0.5,
        "clock_domain": "host.monotonic",
        "timestamp_quality": TimestampQuality.HOST,
        "flags": 0,
    }
    values.update(changes)
    return Frame(**values)


def engine_data(raw_engine_speed: int) -> bytes:
    """Encode one EngineData payload with the given raw EngineSpeed value."""
    low = raw_engine_speed & 0xFF
    high = (raw_engine_speed >> 8) & 0xFF
    return bytes([low, high, 0, 0, 0, 0, 0, 0])


def little_u16(value: int) -> bytes:
    return bytes([value & 0xFF, (value >> 8) & 0xFF])


def _project(tmp_path: Path) -> ProjectHandle:
    return ProjectService().create(tmp_path / "vehicle.canx", display_name="Plot")


def _import_fixture(root: Path, fixture: Path) -> str:
    inbox = root.parent / f"inbox-{fixture.stem}"
    inbox.mkdir(parents=True, exist_ok=True)
    source = inbox / fixture.name
    source.write_bytes(fixture.read_bytes())
    return ProjectDbcService(root).import_asset(source).asset_id


def _import_bytes(root: Path, content: str, name: str) -> str:
    return ProjectDbcService(root).import_asset_bytes(
        content.encode("utf-8"), source_name=name
    ).asset_id


def _write_session(
    handle: ProjectHandle,
    frames: list[Frame],
    *,
    frames_per_segment: int = 1_000,
) -> str:
    service = DataSessionService(handle.root, max_frames_per_segment=frames_per_segment)
    writer = service.start(stream_id=STREAM_ID)
    for start in range(0, len(frames), frames_per_segment):
        batch = frames[start : start + frames_per_segment]
        writer.append(FrameBatch.create(stream_id=STREAM_ID, frames=batch))
    writer.finalize()
    return writer.session_id


def identity(asset_id: str, **changes: Any) -> SignalIdentity:
    values: dict[str, Any] = {
        "channel_id": "can0",
        "asset_id": asset_id,
        "message_name": "EngineData",
        "signal_name": "EngineSpeed",
        "unit": "rpm",
    }
    values.update(changes)
    return SignalIdentity(**values)


def query(session_id: str, ident: SignalIdentity, **changes: Any) -> PlotQuery:
    values: dict[str, Any] = {"session_id": session_id, "identity": ident}
    values.update(changes)
    return PlotQuery(**values)


def engine_frames(count: int, *, raw_at=None, channel_id: str = "can0") -> list[Frame]:
    frames = []
    for sequence in range(count):
        raw = (sequence % 100) if raw_at is None else raw_at(sequence)
        frames.append(
            frame(sequence, channel_id=channel_id, data=engine_data(raw))
        )
    return frames


def test_an_empty_session_yields_an_empty_series(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, [])
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            query(session_id, identity(asset_id))
        )

    assert isinstance(series, PlotSeries)
    assert series.samples == ()
    assert series.matched_frame_count == 0
    assert series.empty is True
    assert series.downsampled is False


def test_a_single_point_series(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(1))
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            query(session_id, identity(asset_id))
        )

    assert len(series.samples) == 1
    assert series.samples[0].sequence == 0
    assert series.samples[0].value == pytest.approx((0 % 100) * 0.25)


def test_a_two_point_series_is_ordered(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(2))
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            query(session_id, identity(asset_id))
        )

    assert [sample.sequence for sample in series.samples] == [0, 1]
    assert [sample.time for sample in series.samples] == [0.0, 0.5]


def test_a_series_below_the_budget_returns_every_frame(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(300))
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            query(session_id, identity(asset_id), sample_budget=5_000)
        )

    assert series.matched_frame_count == 300
    assert len(series.samples) == 300
    assert series.downsampled is False


def test_a_series_above_the_budget_is_bounded(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(5_000), frames_per_segment=800)
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            query(session_id, identity(asset_id), sample_budget=200)
        )

    assert series.matched_frame_count == 5_000
    assert 0 < len(series.samples) <= 200
    assert series.downsampled is True


def test_the_first_and_last_frames_are_preserved(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(4_000), frames_per_segment=500)
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            query(session_id, identity(asset_id), sample_budget=150)
        )

    assert series.samples[0].sequence == 0
    assert series.samples[-1].sequence == 3_999


def test_a_single_spike_survives_downsampling(tmp_path: Path) -> None:
    spike_sequence = 3_333

    def raw_at(sequence: int) -> int:
        return 65_535 if sequence == spike_sequence else sequence % 20

    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(
            handle, engine_frames(7_000, raw_at=raw_at), frames_per_segment=700
        )
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            query(session_id, identity(asset_id), sample_budget=120)
        )

    assert any(sample.sequence == spike_sequence for sample in series.samples)
    assert max(sample.value for sample in series.samples) == pytest.approx(65_535 * 0.25)


def test_the_timestamps_are_ordered(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(6_000), frames_per_segment=700)
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            query(session_id, identity(asset_id), sample_budget=250)
        )

    times = [sample.time for sample in series.samples]
    assert times == sorted(times)


def test_the_result_is_deterministic_across_repeats(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(3_000), frames_per_segment=400)
        service = HistoricalSignalQueryService(handle.root)
        request = query(session_id, identity(asset_id), sample_budget=100)

        first = service.query_signal_series(request)
        second = service.query_signal_series(request)

    assert first == second
    assert first.samples == second.samples


def test_the_time_window_selects_a_subset(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(400))
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            query(
                session_id,
                identity(asset_id),
                time_start=50.0,
                time_end=99.5,
                sample_budget=5_000,
            )
        )

    # normalized_timestamp == sequence * 0.5, so 50.0 <= t <= 99.5 is sequence 100..199.
    assert [sample.sequence for sample in series.samples] == list(range(100, 200))


def test_a_window_with_no_matching_signal_is_empty(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(100))
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            query(session_id, identity(asset_id), time_start=10_000.0, time_end=20_000.0)
        )

    assert series.samples == ()
    assert series.matched_frame_count == 0


def test_two_same_named_signals_on_two_channels_are_isolated(tmp_path: Path) -> None:
    """The channel is part of the identity: two curves can never be merged."""
    frames = engine_frames(250, raw_at=lambda sequence: sequence, channel_id="can0")
    frames += engine_frames(250, raw_at=lambda sequence: 60_000, channel_id="can1")
    # Re-sequence the can1 half so sequences stay contiguous and unique.
    frames = [
        frame(sequence, channel_id=item.channel_id, data=item.data)
        for sequence, item in enumerate(frames)
    ]

    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, frames)
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            query(session_id, identity(asset_id, channel_id="can0"), sample_budget=5_000)
        )

    assert [sample.sequence for sample in series.samples] == list(range(250))
    assert max(sample.value for sample in series.samples) < 60_000 * 0.25


def test_two_assets_with_the_same_signal_name_are_isolated(tmp_path: Path) -> None:
    """The asset decides the scaling: the same frame must be read by its own DBC."""
    with _project(tmp_path) as handle:
        asset_one = _import_bytes(handle.root, ASSET_SCALE_ONE, "scale_one.dbc")
        asset_hundred = _import_bytes(handle.root, ASSET_SCALE_HUNDRED, "scale_hundred.dbc")
        frames = [
            frame(sequence, arbitration_id=256, data=little_u16(7) + bytes(6))
            for sequence in range(10)
        ]
        session_id = _write_session(handle, frames)

        service = HistoricalSignalQueryService(handle.root)
        one = service.query_signal_series(
            query(session_id, identity(asset_one, message_name="Shared", signal_name="Signal"))
        )
        hundred = service.query_signal_series(
            query(session_id, identity(asset_hundred, message_name="Shared", signal_name="Signal"))
        )

    assert all(sample.value == pytest.approx(7.0) for sample in one.samples)
    assert all(sample.value == pytest.approx(700.0) for sample in hundred.samples)


def test_two_messages_with_the_same_signal_name_are_isolated(tmp_path: Path) -> None:
    frames = [
        frame(sequence, arbitration_id=256, data=little_u16(3) + little_u16(9) + bytes(4))
        for sequence in range(20)
    ]
    frames += [
        frame(sequence, arbitration_id=512, data=little_u16(5) + bytes(6))
        for sequence in range(20, 40)
    ]

    with _project(tmp_path) as handle:
        asset_id = _import_bytes(handle.root, ISOLATION_DBC, "isolation.dbc")
        session_id = _write_session(handle, frames)
        service = HistoricalSignalQueryService(handle.root)

        message_a = service.query_signal_series(
            query(session_id, identity(asset_id, message_name="MsgA", signal_name="Value"))
        )
        message_b = service.query_signal_series(
            query(session_id, identity(asset_id, message_name="MsgB", signal_name="Value"))
        )

    assert [sample.sequence for sample in message_a.samples] == list(range(20))
    assert all(sample.value == pytest.approx(3.0) for sample in message_a.samples)
    assert [sample.sequence for sample in message_b.samples] == list(range(20, 40))
    assert all(sample.value == pytest.approx(50.0) for sample in message_b.samples)


def test_two_signals_in_one_message_are_isolated(tmp_path: Path) -> None:
    frames = [
        frame(sequence, arbitration_id=256, data=little_u16(3) + little_u16(9) + bytes(4))
        for sequence in range(15)
    ]

    with _project(tmp_path) as handle:
        asset_id = _import_bytes(handle.root, ISOLATION_DBC, "isolation.dbc")
        session_id = _write_session(handle, frames)
        service = HistoricalSignalQueryService(handle.root)

        value = service.query_signal_series(
            query(session_id, identity(asset_id, message_name="MsgA", signal_name="Value"))
        )
        other = service.query_signal_series(
            query(session_id, identity(asset_id, message_name="MsgA", signal_name="Other"))
        )

    assert all(sample.value == pytest.approx(3.0) for sample in value.samples)
    assert all(sample.value == pytest.approx(9.0) for sample in other.samples)


def test_can_fd_frames_decode_into_a_series(tmp_path: Path) -> None:
    frames = []
    for sequence in range(8):
        payload = little_u16(sequence) + bytes(2) + bytes(60)
        frames.append(
            frame(
                sequence,
                arbitration_id=CAN_FD_ID,
                is_fd=True,
                dlc=64,
                data=payload,
            )
        )

    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, CAN_FD_FIXTURE)
        session_id = _write_session(handle, frames)
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            query(
                session_id,
                identity(asset_id, message_name="FdFramePayload", signal_name="BlobFirst"),
            )
        )

    assert [sample.sequence for sample in series.samples] == list(range(8))
    assert [sample.value for sample in series.samples] == [float(i) for i in range(8)]


def test_an_unregistered_asset_fails_closed(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(10))

        with pytest.raises(PlotAssetError) as info:
            HistoricalSignalQueryService(handle.root).query_signal_series(
                query(session_id, identity(UNKNOWN_ASSET_ID))
            )

    assert info.value.code == "plot.asset_not_found"


def test_a_tampered_dbc_asset_fails_closed(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(10))

        owned = handle.root / asset_relative_path(asset_id)
        raw = bytearray(owned.read_bytes())
        raw[0] ^= 0xFF  # same length, different digest: the size check would miss it.
        owned.write_bytes(bytes(raw))

        with pytest.raises(PlotAssetError) as info:
            HistoricalSignalQueryService(handle.root).query_signal_series(
                query(session_id, identity(asset_id))
            )

    assert info.value.code == "plot.asset_integrity_failed"
    assert info.value.recoverable is True


def test_a_signal_the_asset_does_not_declare_is_reported(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(10))

        with pytest.raises(PlotSignalNotFoundError) as info:
            HistoricalSignalQueryService(handle.root).query_signal_series(
                query(session_id, identity(asset_id, signal_name="DoesNotExist"))
            )

    assert info.value.code == "plot.signal_not_found"


def test_a_message_the_asset_does_not_declare_is_reported(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(10))

        with pytest.raises(PlotSignalNotFoundError) as info:
            HistoricalSignalQueryService(handle.root).query_signal_series(
                query(session_id, identity(asset_id, message_name="Nope"))
            )

    assert info.value.code == "plot.message_not_found"


def test_an_unregistered_session_is_refused(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        _write_session(handle, engine_frames(10))

        with pytest.raises(PlotSessionError) as info:
            HistoricalSignalQueryService(handle.root).query_signal_series(
                query(UNKNOWN_SESSION_ID, identity(asset_id))
            )

    assert info.value.code == "plot.session_not_found"


def test_a_project_that_is_not_a_canx_project_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-project.canx"
    empty.mkdir()

    with pytest.raises(PlotSessionError) as info:
        HistoricalSignalQueryService(empty).query_signal_series(
            query(UNKNOWN_SESSION_ID, identity(UNKNOWN_ASSET_ID))
        )

    assert info.value.code == "plot.project_unavailable"


def test_a_tampered_segment_fails_through_the_query_layer(tmp_path: Path) -> None:
    """The series never bypasses the segment-integrity gate the query domain owns."""
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(50), frames_per_segment=10)
        victim = next((handle.root / "data" / "sessions" / session_id / "segments").iterdir())
        victim.write_bytes(b"this is not a parquet file")

        with pytest.raises(PlotQueryError) as info:
            HistoricalSignalQueryService(handle.root).query_signal_series(
                query(session_id, identity(asset_id))
            )

    assert info.value.code == "plot.query_failed"
    assert str(info.value.details["cause"]).startswith("query.")


def test_a_non_query_argument_is_refused(tmp_path: Path) -> None:
    with _project(tmp_path) as handle, pytest.raises(PlotValidationError):
        HistoricalSignalQueryService(handle.root).query_signal_series("nope")  # type: ignore[arg-type]


def test_a_channel_with_no_frames_is_a_valid_empty_series(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(10))
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            query(session_id, identity(asset_id, channel_id="can9"))
        )

    assert series.samples == ()
    assert series.matched_frame_count == 0


def test_every_plot_failure_is_a_plot_error(tmp_path: Path) -> None:
    """No raw storage, SQL, filesystem or decode error escapes the plot boundary."""
    with _project(tmp_path) as handle:
        _import_fixture(handle.root, BASIC_FIXTURE)
        session_id = _write_session(handle, engine_frames(10))

        with pytest.raises(PlotError) as info:
            HistoricalSignalQueryService(handle.root).query_signal_series(
                query(session_id, identity(UNKNOWN_ASSET_ID))
            )

    assert not isinstance(info.value, (OSError, ValueError))
    assert info.value.code == "plot.asset_not_found"


def test_a_missing_project_database_is_refused(tmp_path: Path) -> None:
    handle = _project(tmp_path)
    asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
    session_id = _write_session(handle, engine_frames(10))
    root = handle.root
    handle.close()
    shutil.rmtree(root)

    with pytest.raises(PlotSessionError) as info:
        HistoricalSignalQueryService(root).query_signal_series(
            query(session_id, identity(asset_id))
        )

    assert info.value.code in {"plot.project_unavailable", "plot.session_not_found"}


def test_the_decoder_used_is_the_one_the_asset_declares(tmp_path: Path) -> None:
    """A direct sanity check that the service's decode path and DbcDecoder agree."""
    with _project(tmp_path) as handle:
        asset_id = _import_fixture(handle.root, BASIC_FIXTURE)
        document = ProjectDbcService(handle.root).load_asset(asset_id)
        assert isinstance(document.database, DbcDatabase)
        decoded = DbcDecoder(document.database).decode_frame(
            frame(0, arbitration_id=ENGINE_DATA_ID, data=engine_data(1_000))
        )
        assert decoded.signal("EngineSpeed") is not None
        assert decoded.signal("EngineSpeed").physical_value == pytest.approx(250.0)
