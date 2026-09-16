"""Small builders shared by the V0.3-04 decode-engine tests.

These are *not* fixtures and never become canonical state by themselves: each
builder returns a canonical, validated value, so a test that constructs a
deliberately odd definition is expressing that oddity in the same vocabulary the
runtime uses.

Most builders avoid the DBC engine entirely — a hand-written canonical database
is exactly the input ``DbcDecoder`` must survive, since its public argument is
the canonical model and a future test or adopter can build one without a file.
``decode_text`` is the one helper that goes through the parser, for the tests
that start from DBC source.
"""

from __future__ import annotations

from canx.dbc.decode import DbcDecoder
from canx.dbc.model import (
    DbcByteOrder,
    DbcChoice,
    DbcDatabase,
    DbcMessage,
    DbcNode,
    DbcSignal,
)
from canx.dbc.parser import CantoolsDbcParser
from canx.domain.frame import Direction, Frame, TimestampQuality

LE = DbcByteOrder.LITTLE_ENDIAN
BE = DbcByteOrder.BIG_ENDIAN


def signal(
    name: str,
    start_bit: int = 0,
    length: int = 8,
    *,
    byte_order: DbcByteOrder = LE,
    is_signed: bool = False,
    is_float: bool = False,
    factor: float = 1.0,
    offset: float = 0.0,
    minimum: float | None = None,
    maximum: float | None = None,
    unit: str | None = None,
    choices: tuple[DbcChoice, ...] = (),
    is_multiplexer: bool = False,
    multiplexer_signal: str | None = None,
    multiplexer_ids: tuple[int, ...] | None = None,
) -> DbcSignal:
    """Build one canonical signal definition."""
    return DbcSignal(
        name=name,
        start_bit=start_bit,
        length=length,
        byte_order=byte_order,
        is_signed=is_signed,
        is_float=is_float,
        factor=factor,
        offset=offset,
        minimum=minimum,
        maximum=maximum,
        unit=unit,
        receivers=("ECU",),
        choices=choices,
        is_multiplexer=is_multiplexer,
        multiplexer_signal=multiplexer_signal,
        multiplexer_ids=multiplexer_ids,
        comment=None,
    )


def message(
    signals: tuple[DbcSignal, ...],
    *,
    frame_id: int = 0x123,
    name: str = "Message",
    length: int = 8,
    is_extended: bool = False,
    is_fd: bool = False,
) -> DbcMessage:
    """Build one canonical message definition around the given signals."""
    return DbcMessage(
        frame_id=frame_id,
        name=name,
        length=length,
        is_extended=is_extended,
        is_fd=is_fd,
        senders=("ECU",),
        signals=signals,
        comment=None,
        cycle_time=None,
    )


def database(*messages: DbcMessage) -> DbcDatabase:
    """Build a canonical database from the given messages."""
    return DbcDatabase(
        messages=tuple(messages), nodes=(DbcNode(name="ECU", comment=None),), version="1.0"
    )


def decoder_for(*messages: DbcMessage) -> DbcDecoder:
    """Build a decoder over a canonical database of hand-written messages."""
    return DbcDecoder(database(*messages))


def decode_text(text: str) -> DbcDecoder:
    """Parse DBC text through the adapter and build a decoder over the result."""
    return DbcDecoder(CantoolsDbcParser().parse_text(text))


def frame(
    data: bytes,
    *,
    sequence: int = 1,
    arbitration_id: int = 0x123,
    is_extended: bool = False,
    is_fd: bool = False,
    dlc: int | None = None,
    channel_id: str = "can0",
    direction: Direction = Direction.RX,
    bitrate_switch: bool = False,
    error_state_indicator: bool = False,
) -> Frame:
    """Build one canonical frame carrying ``data``."""
    return Frame(
        sequence=sequence,
        channel_id=channel_id,
        arbitration_id=arbitration_id,
        is_extended=is_extended,
        is_fd=is_fd,
        bitrate_switch=bitrate_switch,
        error_state_indicator=error_state_indicator,
        dlc=len(data) if dlc is None else dlc,
        data=data,
        direction=direction,
        hardware_timestamp=None,
        host_timestamp=100.25,
        normalized_timestamp=0.25,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def payload(pairs: dict[int, int], *, length: int = 8) -> bytes:
    """Build a payload by writing one byte value at each given index."""
    buffer = bytearray(length)
    for index, value in pairs.items():
        buffer[index] = value
    return bytes(buffer)
