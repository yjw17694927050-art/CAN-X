"""Bit extraction: Intel and Motorola layout, unsigned and two's complement.

Every expectation here is hand-computed from one fixed payload, so the test does
not re-derive the answer with the implementation's own arithmetic. The broad
sweep lives in ``test_dbc_decode_differential.py``, which compares this engine
against the pinned DBC engine over thousands of vectors; these tests pin the
readable, named cases.

The payload is ``12 34 56 78 9A BC DE F0``. In Intel (sawtooth) numbering bit
``n`` is ``byte n // 8`` at bit ``n % 8``, counting up. In Motorola numbering the
declared start bit is the **most significant** bit and each byte counts *down*
from its own bit 7.
"""

from __future__ import annotations

import pytest
from decode_builders import BE, LE, decoder_for, frame, message, signal

PAYLOAD = bytes.fromhex("123456789abcdef0")


def raw(
    name: str, start_bit: int, length: int, *, byte_order: LE | BE, signed: bool = False
) -> int:
    """Decode exactly one signal out of the fixed payload and return its raw value."""
    decoder = decoder_for(
        message(
            (signal(name, start_bit, length, byte_order=byte_order, is_signed=signed),),
            name="Only",
        )
    )
    decoded = decoder.decode_frame(frame(PAYLOAD))
    return decoded.signals[0].raw_value


# --- Intel (little endian) --------------------------------------------------


@pytest.mark.parametrize(
    ("start_bit", "length", "expected"),
    [
        (0, 1, 0),  # bit 0 of 0x12
        (1, 1, 1),  # bit 1 of 0x12
        (0, 8, 0x12),
        (4, 12, 0x341),  # crosses the byte boundary
        (0, 16, 0x3412),
        (11, 9, 0xC6),  # non-zero start, spans three bytes
        (0, 32, 2018915346),
        (0, 64, 17356517385562371090),
    ],
)
def test_little_endian_extraction(start_bit: int, length: int, expected: int) -> None:
    assert raw("S", start_bit, length, byte_order=LE) == expected


def test_little_endian_reads_the_lowest_bit_as_the_least_significant() -> None:
    """Bit 0 of the first byte is the LSB of the value, not its MSB."""
    assert raw("S", 0, 4, byte_order=LE) == 0x2
    assert raw("S", 4, 4, byte_order=LE) == 0x1


# --- Motorola (big endian) --------------------------------------------------


@pytest.mark.parametrize(
    ("start_bit", "length", "expected"),
    [
        (7, 8, 0x12),  # one whole byte
        (7, 16, 0x1234),  # two bytes in network order
        (7, 32, 305419896),
        (7, 64, 1311768467463790320),
        (3, 4, 0x2),  # inside a single byte, non-byte-aligned
        (11, 12, 1110),  # crosses a byte boundary without starting on one
        (39, 16, 0x9ABC),  # starts at the top of the fifth byte
    ],
)
def test_big_endian_extraction(start_bit: int, length: int, expected: int) -> None:
    assert raw("S", start_bit, length, byte_order=BE) == expected


def test_big_endian_reads_the_declared_start_bit_as_the_most_significant_bit() -> None:
    """The classic Motorola trap: the start bit is the MSB, not the LSB.

    ``start_bit = 7`` takes bits 7, 8, 9, 10 of the network stream — the low bit
    of byte 0 followed by the top three bits of byte 1 — so the value is ``0x1``,
    not ``0x8``.
    """
    assert raw("S", 7, 4, byte_order=BE) == 0x1
    assert raw("S", 3, 4, byte_order=BE) == 0x2


def test_big_endian_continues_into_the_next_byte_after_a_byte_boundary() -> None:
    """Bits run down to bit 0 of a byte and then resume at bit 7 of the next."""
    # Bits 0..11 of the network stream: byte 0 in full, then the top nibble of byte 1.
    assert raw("S", 7, 12, byte_order=BE) == 0x123


# --- two's complement -------------------------------------------------------


@pytest.mark.parametrize(
    ("start_bit", "length", "byte_order", "expected_signed", "expected_unsigned"),
    [
        (0, 1, LE, 0, 0),
        (0, 8, LE, 0x12, 0x12),
        (0, 16, LE, 0x3412, 0x3412),
        (7, 8, BE, 0x12, 0x12),
        (7, 16, BE, 0x1234, 0x1234),
    ],
)
def test_a_positive_value_reads_the_same_either_way(
    start_bit: int,
    length: int,
    byte_order: LE | BE,
    expected_signed: int,
    expected_unsigned: int,
) -> None:
    assert raw("S", start_bit, length, byte_order=byte_order, signed=True) == expected_signed
    assert raw("S", start_bit, length, byte_order=byte_order, signed=False) == expected_unsigned


@pytest.mark.parametrize(
    ("start_bit", "length", "byte_order", "payload", "expected"),
    [
        (0, 1, LE, bytes([1]) + bytes(7), -1),  # the single bit is the sign bit
        (0, 8, LE, bytes([0xFF]) + bytes(7), -1),
        (0, 8, LE, bytes([0x80]) + bytes(7), -128),
        (0, 8, LE, bytes([0x7F]) + bytes(7), 127),
        (7, 8, BE, bytes([0xFF]) + bytes(7), -1),
        (7, 16, BE, bytes([0xFF, 0xFF]) + bytes(6), -1),
    ],
)
def test_a_signed_signal_decodes_two_s_complement(
    start_bit: int,
    length: int,
    byte_order: LE | BE,
    payload: bytes,
    expected: int,
) -> None:
    decoder = decoder_for(
        message(
            (signal("S", start_bit, length, byte_order=byte_order, is_signed=True),),
            name="Only",
        )
    )

    assert decoder.decode_frame(frame(payload)).signals[0].raw_value == expected


def test_a_signed_multi_byte_signal_that_crosses_a_boundary_keeps_its_sign() -> None:
    """12 bits in Intel order, all ones: the raw value is -1, not 4095."""
    decoder = decoder_for(
        message((signal("S", 4, 12, byte_order=LE, is_signed=True),), name="Only")
    )

    decoded = decoder.decode_frame(frame(bytes([0xF0, 0xFF]) + bytes(6)))

    assert decoded.signals[0].raw_value == -1


def test_a_signed_motorola_signal_that_crosses_a_boundary_keeps_its_sign() -> None:
    decoder = decoder_for(
        message((signal("S", 7, 12, byte_order=BE, is_signed=True),), name="Only")
    )

    decoded = decoder.decode_frame(frame(bytes([0xFF, 0xF0]) + bytes(6)))

    assert decoded.signals[0].raw_value == -1


def test_signedness_is_the_only_difference_for_the_same_bits() -> None:
    signed = decoder_for(
        message((signal("S", 0, 8, byte_order=LE, is_signed=True),), name="Only")
    )
    unsigned = decoder_for(
        message((signal("S", 0, 8, byte_order=LE, is_signed=False),), name="Only")
    )
    data = bytes([0xFF]) + bytes(7)

    assert signed.decode_frame(frame(data)).signals[0].raw_value == -1
    assert unsigned.decode_frame(frame(data)).signals[0].raw_value == 255


# --- independent signals in one message -------------------------------------


def test_several_signals_decode_independently_out_of_one_payload() -> None:
    decoder = decoder_for(
        message(
            (
                signal("Little16", 0, 16, byte_order=LE),
                signal("Big16", 23, 16, byte_order=BE),
                signal("Little8", 32, 8, byte_order=LE),
            ),
            name="Only",
        )
    )

    decoded = decoder.decode_frame(frame(PAYLOAD))

    assert [(item.name, item.raw_value) for item in decoded.signals] == [
        ("Little16", 0x3412),
        ("Big16", 0x5678),
        ("Little8", 0x9A),
    ]
