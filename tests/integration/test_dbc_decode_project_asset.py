"""A project-owned DBC asset reopened from disk and used to decode real frames.

V0.3-03 made a DBC a durable, verifiable project asset. V0.3-04 made a canonical
database decodable. This is the increment's end-to-end proof that the two
compose: the decoder is built from the *reopened* document, not from the file
that was imported, and the frames it decodes carry their own provenance.

The two layers stay coupled through the canonical domain only — the asset layer
hands over a :class:`~canx.dbc.model.DbcDocument` and knows nothing about
decoding, and the decoder takes a :class:`~canx.dbc.model.DbcDatabase` and knows
nothing about projects.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from canx.dbc.decode import DbcDecoder
from canx.dbc.decode_model import DecodedFrameBatch
from canx.dbc.errors import DbcMessageNotFoundError
from canx.dbc.project_service import ProjectDbcService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectService

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "dbc"

BASIC_FIXTURE = FIXTURES / "basic_standard.dbc"
MULTIPLEXED_FIXTURE = FIXTURES / "multiplexed.dbc"


def frame(data: bytes, *, sequence: int, arbitration_id: int) -> Frame:
    """Build one canonical frame with explicit provenance."""
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
        hardware_timestamp=12.5,
        host_timestamp=100.25,
        normalized_timestamp=0.25,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HARDWARE,
        flags=0,
    )


def import_asset(root: Path, fixture: Path) -> str:
    """Copy one fixture into an inbox under ``root`` and import it as an asset."""
    inbox = root.parent / f"inbox-{fixture.stem}"
    inbox.mkdir(parents=True, exist_ok=True)
    source = inbox / fixture.name
    source.write_bytes(fixture.read_bytes())
    return ProjectDbcService(root).import_asset(source).asset_id


ENGINE_DATA = bytes([0xB8, 0x0B, 0x50, 0x80]) + bytes(4)


def test_a_reopened_asset_decodes_a_frame_end_to_end(tmp_path: Path) -> None:
    """create → import → close → reopen → load_asset → DbcDecoder → decode."""
    root = tmp_path / "vehicle.canx"

    with ProjectService().create(root, display_name="Decode") as handle:
        asset_id = import_asset(handle.root, BASIC_FIXTURE)

    with ProjectService().open(root) as reopened:
        document = ProjectDbcService(reopened.root).load_asset(asset_id)

    decoded = DbcDecoder(document.database).decode_frame(
        frame(ENGINE_DATA, sequence=1, arbitration_id=0x123)
    )

    assert decoded.message_name == "EngineData"
    assert [item.name for item in decoded.signals] == [
        "EngineSpeed",
        "CoolantTemp",
        "ThrottlePosition",
    ]
    assert decoded.signals[0].raw_value == 3000
    assert decoded.signals[0].physical_value == pytest.approx(750.0)
    assert decoded.signals[0].unit == "rpm"
    assert decoded.signals[1].raw_value == 80
    assert decoded.signals[1].physical_value == pytest.approx(40.0)
    assert decoded.signals[2].physical_value == pytest.approx(50.196078, rel=1e-6)


def test_the_decoded_frame_keeps_the_provenance_of_the_frame_it_came_from(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Decode") as handle:
        asset_id = import_asset(handle.root, BASIC_FIXTURE)

    with ProjectService().open(root) as reopened:
        document = ProjectDbcService(reopened.root).load_asset(asset_id)

    original = frame(ENGINE_DATA, sequence=7, arbitration_id=0x123)
    decoded = DbcDecoder(document.database).decode_frame(original)

    assert decoded.frame is original
    assert decoded.frame.sequence == 7
    assert decoded.frame.channel_id == "can0"
    assert decoded.frame.direction is Direction.RX
    assert decoded.frame.hardware_timestamp == 12.5
    assert decoded.frame.normalized_timestamp == 0.25
    assert decoded.frame.timestamp_quality is TimestampQuality.HARDWARE
    assert decoded.frame.is_fd is False


def test_a_reopened_multiplexed_asset_decodes_the_selected_branch(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Decode") as handle:
        asset_id = import_asset(handle.root, MULTIPLEXED_FIXTURE)

    with ProjectService().open(root) as reopened:
        document = ProjectDbcService(reopened.root).load_asset(asset_id)

    decoder = DbcDecoder(document.database)
    decoded = decoder.decode_frame(
        frame(bytes([1]) + bytes(range(1, 8)), sequence=1, arbitration_id=1024)
    )

    assert [item.name for item in decoded.signals] == ["ModeSwitch", "ModeBSignal"]
    assert decoded.signal("ModeASignal") is None


def test_a_reopened_asset_decodes_a_batch(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Decode") as handle:
        asset_id = import_asset(handle.root, BASIC_FIXTURE)

    with ProjectService().open(root) as reopened:
        document = ProjectDbcService(reopened.root).load_asset(asset_id)

    batch = FrameBatch.create(
        stream_id="stream-1",
        frames=[
            frame(ENGINE_DATA, sequence=1, arbitration_id=0x123),
            frame(bytes(8), sequence=2, arbitration_id=0x7FF),
            frame(ENGINE_DATA, sequence=3, arbitration_id=0x123),
        ],
    )

    decoded = DbcDecoder(document.database).decode_batch(batch)

    assert isinstance(decoded, DecodedFrameBatch)
    assert decoded.frame_count == 3
    assert [outcome.ok for outcome in decoded.outcomes] == [True, False, True]
    failure = decoded.outcomes[1].failure
    assert failure is not None
    assert failure.code == "dbc.message_not_found"


def test_a_frame_whose_identifier_the_asset_does_not_define_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Decode") as handle:
        asset_id = import_asset(handle.root, BASIC_FIXTURE)

    with ProjectService().open(root) as reopened:
        document = ProjectDbcService(reopened.root).load_asset(asset_id)

    with pytest.raises(DbcMessageNotFoundError):
        DbcDecoder(document.database).decode_frame(
            frame(bytes(8), sequence=1, arbitration_id=0x7FF)
        )


def test_two_assets_reopened_together_do_not_cross_their_messages(tmp_path: Path) -> None:
    """A decoder answers for one database; a project may own several."""
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Decode") as handle:
        basic_id = import_asset(handle.root, BASIC_FIXTURE)
        multiplexed_id = import_asset(handle.root, MULTIPLEXED_FIXTURE)

    with ProjectService().open(root) as reopened:
        service = ProjectDbcService(reopened.root)
        basic = DbcDecoder(service.load_asset(basic_id).database)
        multiplexed = DbcDecoder(service.load_asset(multiplexed_id).database)

    engine_data = basic.decode_frame(frame(ENGINE_DATA, sequence=1, arbitration_id=0x123))

    assert engine_data.message_name == "EngineData"
    with pytest.raises(DbcMessageNotFoundError):
        multiplexed.decode_frame(frame(ENGINE_DATA, sequence=1, arbitration_id=0x123))
    with pytest.raises(DbcMessageNotFoundError):
        basic.decode_frame(frame(bytes(8), sequence=1, arbitration_id=1024))
