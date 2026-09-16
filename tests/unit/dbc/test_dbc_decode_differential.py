"""Differential verification of the CAN-X decoder against the pinned DBC engine.

This is the acceptance-critical file for V0.3-04. The CAN-X decode engine is an
independent implementation — it never imports the third-party engine and shares
no code with it — so the only way to know its bit extraction is right is to
compare the two over many cases and require exact agreement.

Three rules this file follows:

* **the engine is imported here and nowhere in production.** ``cantools`` is a
  *test oracle*: it appears in ``tests/`` and must never appear in
  ``runtime/canx/dbc/decode.py``;
* **only the engine's public surface is used.** ``load_string``,
  ``Message.decode``, ``Database.decode_message`` and ``NamedSignalValue``.
  No ``_codec``, no ``_signals``, no ``cantools.database.utils`` helper — an
  oracle that reaches into private internals would only be verifying today's
  implementation detail;
* **raw integers are compared exactly and physical values with ``isclose``.**
  A float comparison by string would pass for the wrong reasons.

Randomised vectors are seeded, so a failure is reproducible from the seed alone.
No property-testing dependency is introduced: the loop below is the whole
generator.
"""

from __future__ import annotations

import math
import os
import random
import subprocess
import sys
from pathlib import Path

import cantools
import pytest
from canx.dbc.decode import DbcDecoder
from canx.dbc.parser import CantoolsDbcParser
from decode_builders import frame

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "dbc"

#: Fixed so a failure can be reproduced from the seed alone.
SEED = 20260628

#: Signal widths the sweep covers: boundaries, sub-byte, and multi-byte.
WIDTHS = (1, 2, 3, 4, 5, 7, 8, 9, 12, 15, 16, 17, 24, 31, 32, 40, 48, 63, 64)

#: Message identifier used by every generated document.
FRAME_ID = 0x123


def build_single_signal_dbc(
    *,
    start_bit: int,
    length: int,
    big_endian: bool,
    signed: bool,
    scale: float,
    offset: float,
    message_length: int,
) -> str:
    """Render one single-signal DBC document."""
    order = "0" if big_endian else "1"
    sign = "-" if signed else "+"
    return (
        'VERSION "1.0"\n'
        "BU_: N1\n"
        f"BO_ {FRAME_ID} M: {message_length} N1\n"
        f' SG_ S : {start_bit}|{length}@{order}{sign} ({scale},{offset}) [0|0] "" N1\n'
    )


def required_bytes(start_bit: int, length: int, *, big_endian: bool) -> int:
    """Return the smallest message length that can hold one signal definition."""
    if big_endian:
        network_start = 8 * (start_bit // 8) + (7 - start_bit % 8)
        highest = network_start + length - 1
    else:
        highest = start_bit + length - 1
    return highest // 8 + 1


def oracle(text: str):
    """Parse one document with the pinned engine and return (database, message)."""
    database = cantools.database.load_string(
        text, database_format="dbc", strict=True, sort_signals=None
    )
    return database, database.messages[0]


def canx_decoder(text: str) -> DbcDecoder:
    """Build a CAN-X decoder over the same document, through the owned parser."""
    return DbcDecoder(CantoolsDbcParser().parse_text(text))


def scale_pairs() -> list[tuple[float, float]]:
    """Return the (factor, offset) pairs the sweep mixes into the vectors."""
    return [(1.0, 0.0), (0.25, 0.0), (2.0, -100.0), (0.1, 5.0), (1.0, -40.0)]


# --- the raw sweep ----------------------------------------------------------


SINGLE_SIGNAL_VECTORS: list[tuple[int, int, bool, bool, float, float]] = []
for _big_endian in (False, True):
    for _signed in (False, True):
        for _length in WIDTHS:
            for _start_bit in range(0, 64):
                _size = required_bytes(_start_bit, _length, big_endian=_big_endian)
                if _size > 8:
                    continue
                for _scale, _offset in scale_pairs()[:2]:
                    SINGLE_SIGNAL_VECTORS.append(
                        (_start_bit, _length, _big_endian, _signed, _scale, _offset)
                    )


def test_the_sweep_is_not_vacuous() -> None:
    """A generator that silently produced nothing would make the next tests pass."""
    assert len(SINGLE_SIGNAL_VECTORS) > 1000
    assert any(vector[2] for vector in SINGLE_SIGNAL_VECTORS)  # Motorola covered
    assert any(not vector[2] for vector in SINGLE_SIGNAL_VECTORS)  # Intel covered
    assert any(vector[3] for vector in SINGLE_SIGNAL_VECTORS)  # signed covered


def test_raw_and_scaled_decode_matches_the_engine_over_a_seeded_sweep() -> None:
    """16k+ vectors of one signal each, compared signal by signal."""
    rng = random.Random(SEED)
    compared = 0
    for start_bit, length, big_endian, signed, scale, offset in SINGLE_SIGNAL_VECTORS:
        size = required_bytes(start_bit, length, big_endian=big_endian)
        text = build_single_signal_dbc(
            start_bit=start_bit,
            length=length,
            big_endian=big_endian,
            signed=signed,
            scale=scale,
            offset=offset,
            message_length=size,
        )
        _database, message = oracle(text)
        decoder = canx_decoder(text)
        for _ in range(3):
            data = bytes(rng.randrange(256) for _ in range(size))
            reference_raw = message.decode(data, decode_choices=False, scaling=False)["S"]
            reference_scaled = message.decode(data, decode_choices=False, scaling=True)["S"]
            decoded = decoder.decode_frame(
                frame(data, arbitration_id=FRAME_ID, is_fd=False)
            ).signals[0]
            context = (
                f"start={start_bit} length={length} big_endian={big_endian}"
                f" signed={signed} data={data.hex()}"
            )
            assert decoded.raw_value == reference_raw, context
            assert math.isclose(decoded.physical_value, reference_scaled), context
            compared += 1

    assert compared > 15000, compared


# --- several signals in one message -----------------------------------------


MULTI_SIGNAL_DBC = """VERSION "1.0"
BU_: N1
BO_ 291 MixedSignals: 8 N1
 SG_ Little16 : 0|16@1+ (0.25,0) [0|16383.75] "rpm" N1
 SG_ LittleSigned : 16|8@1- (1,0) [-128|127] "" N1
 SG_ Big16 : 31|16@0+ (0.00390625,0) [0|255] "" N1
 SG_ Tail : 40|8@1+ (1,0) [0|255] "" N1
 SG_ BigSigned8 : 63|8@0- (0.5,-100) [-164|127.5] "deg" N1
"""


def test_a_multi_signal_message_agrees_signal_by_signal() -> None:
    _database, message = oracle(MULTI_SIGNAL_DBC)
    decoder = canx_decoder(MULTI_SIGNAL_DBC)
    rng = random.Random(SEED + 1)

    for _ in range(500):
        data = bytes(rng.randrange(256) for _ in range(8))
        reference_raw = message.decode(data, decode_choices=False, scaling=False)
        reference_scaled = message.decode(data, decode_choices=False, scaling=True)
        decoded = decoder.decode_frame(frame(data, arbitration_id=291))

        assert [item.name for item in decoded.signals] == list(reference_raw)
        for item in decoded.signals:
            context = f"signal={item.name} data={data.hex()}"
            assert item.raw_value == reference_raw[item.name], context
            assert math.isclose(item.physical_value, reference_scaled[item.name]), context


def test_integer_values_and_their_scaling_are_both_compared() -> None:
    """Guard against a sweep that only ever compares one of the two."""
    _database, message = oracle(MULTI_SIGNAL_DBC)
    decoder = canx_decoder(MULTI_SIGNAL_DBC)
    data = bytes.fromhex("e8037f0000000000")

    reference_raw = message.decode(data, decode_choices=False, scaling=False)
    reference_scaled = message.decode(data, decode_choices=False, scaling=True)
    decoded = decoder.decode_frame(frame(data, arbitration_id=291))

    little = next(item for item in decoded.signals if item.name == "Little16")
    assert little.raw_value == reference_raw["Little16"] == 1000
    assert little.physical_value == reference_scaled["Little16"] == 250.0

    signed = next(item for item in decoded.signals if item.name == "LittleSigned")
    assert signed.raw_value == reference_raw["LittleSigned"] == 127
    assert signed.physical_value == reference_scaled["LittleSigned"] == 127.0


def test_a_two_s_complement_signal_agrees_with_the_engine_on_a_negative_value() -> None:
    _database, message = oracle(MULTI_SIGNAL_DBC)
    decoder = canx_decoder(MULTI_SIGNAL_DBC)
    data = bytes.fromhex("0000800000000000")

    reference_raw = message.decode(data, decode_choices=False, scaling=False)
    decoded = decoder.decode_frame(frame(data, arbitration_id=291))

    signed = next(item for item in decoded.signals if item.name == "LittleSigned")
    assert signed.raw_value == reference_raw["LittleSigned"] == -128


# --- choices ----------------------------------------------------------------


def choices_dbc() -> str:
    return (FIXTURES / "choices.dbc").read_text(encoding="utf-8")


def test_a_choice_label_matches_the_engine_s_public_label() -> None:
    _database, message = oracle(choices_dbc())
    decoder = canx_decoder(choices_dbc())

    for raw in (0, 1, 2):
        data = bytes([raw, 0, 0, 0])
        reference = message.decode(data, decode_choices=True, scaling=True)["DoorState"]
        decoded = decoder.decode_frame(frame(data, arbitration_id=768)).signal("DoorState")

        assert decoded is not None
        assert decoded.choice_label == reference.name
        assert decoded.raw_value == raw
        assert math.isclose(decoded.physical_value, reference.value)


def test_an_undeclared_choice_stays_numeric_in_both_implementations() -> None:
    _database, message = oracle(choices_dbc())
    decoder = canx_decoder(choices_dbc())
    data = bytes([9, 0, 0, 0])

    reference = message.decode(data, decode_choices=True, scaling=True)["DoorState"]
    decoded = decoder.decode_frame(frame(data, arbitration_id=768)).signal("DoorState")

    assert decoded is not None
    assert not hasattr(reference, "name")  # the engine did not name it either
    assert decoded.choice_label is None
    assert decoded.raw_value == 9


# --- multiplexing -----------------------------------------------------------


def multiplexed_dbc() -> str:
    return (FIXTURES / "multiplexed.dbc").read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", [0, 1, 2])
def test_the_active_signal_set_matches_the_engine(mode: int) -> None:
    """Same signals, same values — the decoded *set* is the multiplexing contract."""
    _database, message = oracle(multiplexed_dbc())
    decoder = canx_decoder(multiplexed_dbc())
    data = bytes([mode]) + bytes(range(1, 8))

    reference_raw = message.decode(data, decode_choices=False, scaling=False)
    reference_scaled = message.decode(data, decode_choices=False, scaling=True)
    decoded = decoder.decode_frame(frame(data, arbitration_id=1024))

    assert sorted(item.name for item in decoded.signals) == sorted(reference_raw)
    for item in decoded.signals:
        assert item.raw_value == reference_raw[item.name]
        assert math.isclose(item.physical_value, reference_scaled[item.name])


def test_an_undeclared_switch_value_is_the_documented_divergence_from_the_engine() -> None:
    """Where the two deliberately differ, and why.

    The engine refuses a switch value no branch declares. CAN-X reports what the
    payload actually contained — the switch itself and every signal that is
    always active — instead of discarding a frame that still carries valid data.
    A Trace view can then show the anomalous switch value; an exception could
    not. Both behaviours are pinned here so neither drifts unnoticed.
    """
    _database, message = oracle(multiplexed_dbc())
    decoder = canx_decoder(multiplexed_dbc())
    data = bytes([7]) + bytes(range(1, 8))

    with pytest.raises(cantools.database.errors.DecodeError):
        message.decode(data, decode_choices=False, scaling=False)

    decoded = decoder.decode_frame(frame(data, arbitration_id=1024))

    assert [item.name for item in decoded.signals] == ["ModeSwitch"]
    switch = decoded.signals[0]
    assert switch.raw_value == 7
    assert switch.choice_label is None


def test_the_multiplexer_switch_is_present_in_both_active_sets() -> None:
    _database, message = oracle(multiplexed_dbc())
    decoder = canx_decoder(multiplexed_dbc())

    for mode in (0, 1, 2):
        data = bytes([mode]) + bytes(7)
        reference = message.decode(data, decode_choices=False, scaling=False)
        decoded = decoder.decode_frame(frame(data, arbitration_id=1024))
        assert "ModeSwitch" in reference
        assert decoded.signal("ModeSwitch") is not None


# --- CAN FD -----------------------------------------------------------------


def test_an_fd_message_agrees_with_the_engine() -> None:
    text = (FIXTURES / "can_fd.dbc").read_text(encoding="utf-8")
    _database, message = oracle(text)
    decoder = canx_decoder(text)
    rng = random.Random(SEED + 2)

    for _ in range(50):
        data = bytes(rng.randrange(256) for _ in range(64))
        reference_raw = message.decode(data, decode_choices=False, scaling=False)
        decoded = decoder.decode_frame(frame(data, arbitration_id=1536, is_fd=True))
        for item in decoded.signals:
            assert item.raw_value == reference_raw[item.name]


# --- the engine stays a test oracle ----------------------------------------


def test_the_production_decoder_never_imports_the_dbc_engine() -> None:
    """The differential test is only meaningful if the two are independent."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import canx.dbc.decode; print('cantools' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "runtime")},
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False"
