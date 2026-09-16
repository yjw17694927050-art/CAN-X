"""Physical values: scaling, choice labels, units, and what the range is *not*.

Two contracts are pinned here.

**The number survives.** A ``VAL_`` label is additional semantics attached to a
raw value — ``raw = 2`` stays visible next to ``choice_label = "Error"`` — and
``minimum`` / ``maximum`` are metadata, not clamps. A decoded value outside the
declared range is reported as it is; CAN-X does not silently rewrite an
engineering quantity to fit a definition.

**Choice lookup is by raw value.** A ``VAL_`` table names bit patterns, not
engineering quantities, so a signal with ``factor = 2`` whose choice table names
``1`` must not match a physical value of ``2``.
"""

from __future__ import annotations

import pytest
from canx.dbc.errors import DbcSignalDecodeError
from canx.dbc.model import DbcChoice
from decode_builders import LE, decoder_for, frame, message, signal


def decode_one(definition: object, data: bytes):
    """Decode one signal out of a one-message database."""
    decoder = decoder_for(message((definition,), name="Only"))  # type: ignore[arg-type]
    return decoder.decode_frame(frame(data)).signals[0]


def payload_byte(value: int) -> bytes:
    """Build an eight-byte payload whose first byte is ``value``."""
    return bytes([value]) + bytes(7)


# --- scaling ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("factor", "offset", "raw", "expected"),
    [
        (1.0, 0.0, 100, 100.0),
        (0.5, 0.0, 100, 50.0),
        (2.0, 0.0, 100, 200.0),
        (-1.0, 0.0, 100, -100.0),
        (1.0, 5.0, 100, 105.0),
        (1.0, -40.0, 100, 60.0),
        (0.25, -40.0, 1000, 210.0),
        (0.0, 7.5, 123, 7.5),  # a zero factor is legal: a pure offset
    ],
)
def test_physical_value_is_raw_times_factor_plus_offset(
    factor: float, offset: float, raw: int, expected: float
) -> None:
    definition = signal("S", 0, 16, byte_order=LE, factor=factor, offset=offset)

    decoded = decode_one(definition, raw.to_bytes(2, "little") + bytes(6))

    assert decoded.raw_value == raw
    assert decoded.physical_value == pytest.approx(expected)
    assert isinstance(decoded.physical_value, float)


def test_a_negative_factor_keeps_the_sign_convention_of_the_document() -> None:
    """The definition says what it says; CAN-X does not reorder it."""
    decoded = decode_one(signal("S", 0, 8, factor=-0.5, offset=100.0), payload_byte(200))

    assert decoded.raw_value == 200
    assert decoded.physical_value == pytest.approx(0.0)


def test_scaling_applies_to_a_signed_raw_value() -> None:
    definition = signal("S", 0, 8, is_signed=True, factor=0.5, offset=-10.0)

    decoded = decode_one(definition, payload_byte(0xFE))

    assert decoded.raw_value == -2
    assert decoded.physical_value == pytest.approx(-11.0)


def test_a_scaling_step_that_is_not_finite_is_a_typed_failure() -> None:
    """A finite factor times a finite raw value can still overflow; say so."""
    definition = signal("S", 0, 64, byte_order=LE, factor=1e308)

    with pytest.raises(DbcSignalDecodeError) as info:
        decode_one(definition, bytes([0xFF]) * 8)

    error = info.value
    assert error.code == "dbc.signal_decode_failed"
    assert error.source == "dbc"
    assert error.recoverable is False
    assert error.details["signal_name"] == "S"
    assert error.details["message_name"] == "Only"


# --- choices ----------------------------------------------------------------


DOOR_CHOICES = (
    DbcChoice(value=0, label="Closed"),
    DbcChoice(value=1, label="Open"),
    DbcChoice(value=2, label="Error"),
)


@pytest.mark.parametrize(
    ("raw", "label"), [(0, "Closed"), (1, "Open"), (2, "Error")]
)
def test_a_declared_choice_names_the_raw_value(raw: int, label: str) -> None:
    decoded = decode_one(signal("S", 0, 8, choices=DOOR_CHOICES), payload_byte(raw))

    assert decoded.choice_label == label
    assert decoded.raw_value == raw
    assert decoded.physical_value == float(raw)


def test_an_undeclared_raw_value_keeps_its_number_and_reports_no_label() -> None:
    """A missing choice is not an error, and it never hides the value."""
    decoded = decode_one(signal("S", 0, 8, choices=DOOR_CHOICES), payload_byte(9))

    assert decoded.choice_label is None
    assert decoded.raw_value == 9
    assert decoded.physical_value == 9.0


def test_a_signal_without_any_choice_table_reports_no_label() -> None:
    assert decode_one(signal("S", 0, 8), payload_byte(3)).choice_label is None


def test_a_choice_is_matched_on_the_raw_value_not_the_scaled_one() -> None:
    """The trap: raw 1 scales to 2, and the table names 1 — not 2."""
    definition = signal(
        "S",
        0,
        8,
        factor=2.0,
        choices=(DbcChoice(value=1, label="One"), DbcChoice(value=2, label="Two")),
    )

    decoded = decode_one(definition, payload_byte(1))

    assert decoded.choice_label == "One"
    assert decoded.physical_value == 2.0


def test_a_choice_on_a_signed_signal_can_name_a_negative_raw_value() -> None:
    definition = signal(
        "S", 0, 8, is_signed=True, choices=(DbcChoice(value=-1, label="Invalid"),)
    )

    decoded = decode_one(definition, payload_byte(0xFF))

    assert (decoded.raw_value, decoded.choice_label) == (-1, "Invalid")


def test_a_choice_with_an_empty_label_is_reported_as_the_source_declared_it() -> None:
    definition = signal("S", 0, 8, choices=(DbcChoice(value=1, label=""),))

    assert decode_one(definition, payload_byte(1)).choice_label == ""


# --- units ------------------------------------------------------------------


def test_a_declared_unit_is_carried_through() -> None:
    decoded = decode_one(signal("S", 0, 8, unit="rpm"), payload_byte(1))

    assert decoded.unit == "rpm"


def test_an_absent_unit_stays_absent() -> None:
    assert decode_one(signal("S", 0, 8), payload_byte(1)).unit is None


# --- the declared range is metadata, not a clamp ----------------------------


def test_a_value_above_the_declared_maximum_is_reported_unchanged() -> None:
    """``maximum = 100`` must not turn a decoded 120 into 100."""
    definition = signal("S", 0, 8, minimum=0.0, maximum=100.0)

    decoded = decode_one(definition, payload_byte(120))

    assert decoded.raw_value == 120
    assert decoded.physical_value == 120.0


def test_a_value_below_the_declared_minimum_is_reported_unchanged() -> None:
    definition = signal("S", 0, 8, is_signed=True, minimum=-10.0, maximum=10.0)

    decoded = decode_one(definition, payload_byte(0x80))

    assert decoded.raw_value == -128
    assert decoded.physical_value == -128.0


def test_the_range_never_alters_the_raw_value() -> None:
    definition = signal("S", 0, 8, minimum=200.0, maximum=255.0)

    assert decode_one(definition, payload_byte(1)).raw_value == 1
