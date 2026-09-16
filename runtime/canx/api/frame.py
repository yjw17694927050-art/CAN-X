"""The canonical frame as it crosses the HTTP boundary — one definition, shared.

Trace already publishes a canonical frame as JSON, and the DBC decode surface has
to accept that very same shape as a request and return it as a response. Two
hand-maintained copies of a sixteen-field wire schema would drift: a field added
on one side and forgotten on the other is a silent contract break that no test of
either surface alone would catch.

So the projection lives here, once:

* :class:`FrameWire` is the sixteen-field JSON shape. ``data`` is uppercase
  hexadecimal — the stable, lossless, self-describing encoding Trace already
  publishes, and one that no JSON encoder has to guess at;
* :func:`frame_to_wire` renders a canonical :class:`~canx.domain.frame.Frame`
  for the wire;
* :func:`wire_to_frame` is the other direction, and it is deliberately *not* a
  second copy of the frame's rules. It decodes the hexadecimal payload and then
  builds a canonical :class:`~canx.domain.frame.Frame`, so the domain object is
  what decides whether the values are legal. No DLC table, no arbitration-id
  range and no timestamp policy is restated here — a rule written twice is a rule
  that can disagree with itself.

This module holds no state, imports no DBC engine, and knows nothing about
projects.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from canx.domain.frame import Direction, Frame, TimestampQuality

#: The characters a hexadecimal payload may contain. Both cases are accepted on
#: input; output is always uppercase.
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


class FrameWireError(ValueError):
    """Raised when a wire frame cannot become a canonical frame.

    A ``ValueError`` subclass on purpose: a caller validating untrusted input (a
    Pydantic model, for instance) turns it into its own validation failure without
    this module having to know what that caller is.
    """


class FrameWire(BaseModel):
    """One canonical frame in the wire representation consumers rely on.

    This is the full projection, so no field of the canonical model is silently
    dropped at the boundary. In particular the timestamp provenance
    (``hardware_timestamp`` / ``host_timestamp`` / ``clock_domain`` /
    ``timestamp_quality``) is carried through: a later Trace timestamp mode needs
    it, and re-adding a field later would be a breaking schema change.
    """

    model_config = ConfigDict(frozen=True)

    sequence: int
    channel_id: str
    arbitration_id: int
    is_extended: bool
    is_fd: bool
    bitrate_switch: bool
    error_state_indicator: bool
    dlc: int
    data: str
    direction: Literal["rx", "tx"]
    hardware_timestamp: float | None
    host_timestamp: float
    normalized_timestamp: float
    clock_domain: str
    timestamp_quality: Literal["hardware", "host", "estimated"]
    flags: int


def frame_to_wire(frame: Frame) -> FrameWire:
    """Render one canonical frame for the wire."""
    return FrameWire(
        sequence=frame.sequence,
        channel_id=frame.channel_id,
        arbitration_id=frame.arbitration_id,
        is_extended=frame.is_extended,
        is_fd=frame.is_fd,
        bitrate_switch=frame.bitrate_switch,
        error_state_indicator=frame.error_state_indicator,
        dlc=frame.dlc,
        data=frame.data.hex().upper(),
        direction=frame.direction.value,
        hardware_timestamp=frame.hardware_timestamp,
        host_timestamp=frame.host_timestamp,
        normalized_timestamp=frame.normalized_timestamp,
        clock_domain=frame.clock_domain,
        timestamp_quality=frame.timestamp_quality.value,
        flags=frame.flags,
    )


def wire_to_frame(wire: FrameWire) -> Frame:
    """Build the canonical frame a wire payload describes.

    Everything except the hexadecimal payload is handed straight to
    :class:`~canx.domain.frame.Frame`, which is the single authority on what a
    legal frame is; the only translation done here is the one the wire format
    forces, namely ``data`` being text.

    Args:
        wire: The frame as it arrived over HTTP.

    Returns:
        The validated canonical frame.

    Raises:
        FrameWireError: If the payload is not an even-length hexadecimal string,
            or the values do not describe a legal canonical frame.
    """
    data = _decode_hex(wire.data)
    try:
        return Frame(
            sequence=wire.sequence,
            channel_id=wire.channel_id,
            arbitration_id=wire.arbitration_id,
            is_extended=wire.is_extended,
            is_fd=wire.is_fd,
            bitrate_switch=wire.bitrate_switch,
            error_state_indicator=wire.error_state_indicator,
            dlc=wire.dlc,
            data=data,
            direction=Direction(wire.direction),
            hardware_timestamp=wire.hardware_timestamp,
            host_timestamp=wire.host_timestamp,
            normalized_timestamp=wire.normalized_timestamp,
            clock_domain=wire.clock_domain,
            timestamp_quality=TimestampQuality(wire.timestamp_quality),
            flags=wire.flags,
        )
    except ValueError as error:
        raise FrameWireError(str(error)) from error


def _decode_hex(value: str) -> bytes:
    """Decode a hexadecimal payload strictly, refusing anything ambiguous.

    ``bytes.fromhex`` accepts embedded whitespace and is therefore too lenient for
    a field that has to round-trip: several spellings of the same bytes would be
    accepted while only one is ever produced. Odd length and non-hex characters are
    refused explicitly, and an empty payload decodes to empty bytes — whether that
    is legal is the frame model's decision, not this function's.
    """
    if len(value) % 2 != 0:
        raise FrameWireError("data must have an even number of hexadecimal characters")
    if not all(character in _HEX_DIGITS for character in value):
        raise FrameWireError("data must be a hexadecimal string")
    return bytes.fromhex(value)
