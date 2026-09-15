"""Behavior tests for the canonical CAN-X Frame domain model."""

from dataclasses import FrozenInstanceError

import pytest
from canx.domain.frame import Direction, Frame, TimestampQuality


def make_frame(**changes: object) -> Frame:
    """Create a valid Classic CAN frame with explicit domain fields."""
    values: dict[str, object] = {
        "sequence": 1,
        "channel_id": "can0",
        "arbitration_id": 0x123,
        "is_extended": False,
        "is_fd": False,
        "bitrate_switch": False,
        "error_state_indicator": False,
        "dlc": 3,
        "data": bytes.fromhex("010203"),
        "direction": Direction.RX,
        "hardware_timestamp": None,
        "host_timestamp": 100.25,
        "normalized_timestamp": 0.25,
        "clock_domain": "host.monotonic",
        "timestamp_quality": TimestampQuality.HOST,
        "flags": 0,
    }
    values.update(changes)
    return Frame(**values)  # type: ignore[arg-type]


def test_frame_accepts_a_valid_classic_can_message() -> None:
    frame = make_frame()

    assert frame.data == b"\x01\x02\x03"
    assert frame.arbitration_id == 0x123


@pytest.mark.parametrize("arbitration_id", [-1, 0x800])
def test_standard_frame_rejects_an_out_of_range_identifier(arbitration_id: int) -> None:
    with pytest.raises(ValueError, match="standard arbitration_id"):
        make_frame(arbitration_id=arbitration_id)


def test_extended_frame_accepts_the_largest_29_bit_identifier() -> None:
    assert make_frame(is_extended=True, arbitration_id=0x1FFFFFFF).arbitration_id == 0x1FFFFFFF


@pytest.mark.parametrize("dlc", [9, 10, 11, 13, 63])
def test_frame_rejects_payload_lengths_not_defined_by_can_or_can_fd(dlc: int) -> None:
    with pytest.raises(ValueError, match="DLC"):
        make_frame(is_fd=True, dlc=dlc, data=bytes(dlc))


def test_frame_rejects_data_length_that_differs_from_dlc() -> None:
    with pytest.raises(ValueError, match="data length"):
        make_frame(dlc=2)


def test_classic_frame_rejects_fd_only_flags() -> None:
    with pytest.raises(ValueError, match="Classic CAN"):
        make_frame(bitrate_switch=True)


def test_frame_is_immutable() -> None:
    frame = make_frame()

    with pytest.raises(FrozenInstanceError):
        frame.sequence = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [("sequence", True), ("arbitration_id", False), ("dlc", True), ("flags", False)],
)
def test_frame_rejects_boolean_values_for_integer_fields(field: str, value: bool) -> None:
    with pytest.raises(ValueError, match="integer"):
        make_frame(**{field: value})
