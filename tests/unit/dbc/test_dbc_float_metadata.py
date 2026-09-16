"""Float-signal metadata must survive the DBC import boundary.

A DBC declares an IEEE-754 payload with ``SIG_VALTYPE_ <message> <signal> : 1``
(32-bit float) or ``: 2`` (64-bit double). The engine reports it as
``signal.is_float``, and the raw bits of such a signal are **not** an integer
field: decoding them with integer semantics silently returns a plausible-looking
but wrong number.

The audit that produced V0.3-04 (cantools 44.0.0, recorded in PROJECT_STATE)
found a real metadata-loss defect: the engine reported ``is_float = True`` while
the canonical CAN-X signal had no way to carry that fact, so a later decoder
could not tell a float payload from an integer one. These tests pin the fix.

Nothing here imports ``cantools``: the canonical model is asserted on its own,
and the adapter is exercised through ``CantoolsDbcParser`` only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from canx.dbc.model import DbcByteOrder, DbcSignal
from canx.dbc.parser import CantoolsDbcParser

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "dbc"


def float_fixture_text() -> str:
    """Return the DBC fixture that declares one 32-bit float signal."""
    return (FIXTURES / "float_signal.dbc").read_text(encoding="utf-8")


def make_signal(**changes: object) -> DbcSignal:
    """Create a valid little-endian unsigned signal with explicit fields."""
    values: dict[str, object] = {
        "name": "EngineSpeed",
        "start_bit": 0,
        "length": 16,
        "byte_order": DbcByteOrder.LITTLE_ENDIAN,
        "is_signed": False,
        "is_float": False,
        "factor": 1.0,
        "offset": 0.0,
        "minimum": None,
        "maximum": None,
        "unit": None,
        "receivers": ("ECU",),
        "choices": (),
        "is_multiplexer": False,
        "multiplexer_signal": None,
        "multiplexer_ids": None,
        "comment": None,
    }
    values.update(changes)
    return DbcSignal(**values)  # type: ignore[arg-type]


# --- canonical model --------------------------------------------------------


def test_a_signal_carries_its_float_metadata() -> None:
    assert make_signal(is_float=True).is_float is True
    assert make_signal(is_float=False).is_float is False


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_a_signal_rejects_a_non_boolean_float_flag(value: object) -> None:
    with pytest.raises(ValueError, match="is_float"):
        make_signal(is_float=value)


def test_a_float_signal_is_still_a_signal_with_a_length_and_a_byte_order() -> None:
    """``is_float`` adds a payload kind; it never replaces the bit layout."""
    signal = make_signal(is_float=True, length=32, byte_order=DbcByteOrder.BIG_ENDIAN)

    assert (signal.length, signal.byte_order) == (32, DbcByteOrder.BIG_ENDIAN)
    assert signal.is_float is True


# --- parser boundary --------------------------------------------------------


def test_the_parser_preserves_float_metadata_from_the_source_document() -> None:
    """RED before the fix: the canonical signal had no ``is_float`` at all."""
    database = CantoolsDbcParser().parse_text(float_fixture_text())

    message = database.messages[0]
    by_name = {signal.name: signal for signal in message.signals}

    assert by_name["ManifoldPressure"].is_float is True
    assert by_name["ManifoldPressure"].length == 32
    assert by_name["EngineSpeed"].is_float is False
    assert by_name["EngineSpeed"].length == 16


def test_every_other_declared_field_of_a_float_signal_is_still_converted() -> None:
    database = CantoolsDbcParser().parse_text(float_fixture_text())

    pressure = database.messages[0].signals[1]

    assert pressure.name == "ManifoldPressure"
    assert pressure.start_bit == 16
    assert pressure.byte_order is DbcByteOrder.LITTLE_ENDIAN
    assert pressure.factor == 1.0
    assert pressure.offset == 0.0
