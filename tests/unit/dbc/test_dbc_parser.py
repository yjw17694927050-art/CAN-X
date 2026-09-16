"""Conversion contract for the cantools DBC adapter.

These tests are the only place that is allowed to know the third-party engine
exists. They assert two separate things, and both matter:

1. the *content* mapping is field-level correct — not "one message came back";
2. the *boundary* holds — nothing that reaches a caller is a cantools object,
   and importing the canonical model does not drag the engine in at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest
from canx.dbc.errors import DbcModelError, DbcParseError
from canx.dbc.model import (
    DbcByteOrder,
    DbcChoice,
    DbcDatabase,
    DbcNode,
    DbcSignal,
)
from canx.dbc.parser import CantoolsDbcParser

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_ROOT = PROJECT_ROOT / "runtime"
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "dbc"

DUPLICATE_MESSAGE_NAME_DBC = """
VERSION "1.0"
BU_: N1
BO_ 256 First: 8 N1
 SG_ A : 0|8@1+ (1,0) [0|255] "" N1
BO_ 512 First: 8 N1
 SG_ B : 0|8@1+ (1,0) [0|255] "" N1
"""

DUPLICATE_FRAME_ID_DBC = """
VERSION "1.0"
BU_: N1
BO_ 256 First: 8 N1
 SG_ A : 0|8@1+ (1,0) [0|255] "" N1
BO_ 256 Second: 8 N1
 SG_ B : 0|8@1+ (1,0) [0|255] "" N1
"""

REVERSED_SIGNAL_RANGE_DBC = """
VERSION "1.0"
BU_: N1
BO_ 256 Only: 8 N1
 SG_ Reversed : 0|8@1+ (1,0) [100|0] "" N1
"""

DUPLICATE_NODE_DBC = """
VERSION "1.0"
BU_: Alpha Beta Alpha N1
BO_ 256 Only: 8 N1
 SG_ A : 0|8@1+ (1,0) [0|255] "" N1
"""


def fixture_text(name: str) -> str:
    """Return the text of a DBC fixture as the import path would decode it."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def parse_fixture(name: str) -> DbcDatabase:
    """Parse one fixture through the public adapter entry point."""
    return CantoolsDbcParser().parse_text(fixture_text(name))


def signal(database: DbcDatabase, message_name: str, signal_name: str) -> DbcSignal:
    """Return one signal by message and signal name."""
    message = next(item for item in database.messages if item.name == message_name)
    return next(item for item in message.signals if item.name == signal_name)


# --- content mapping --------------------------------------------------------


def test_a_standard_message_maps_every_declared_field() -> None:
    database = parse_fixture("basic_standard.dbc")

    assert database.version == "1.0"
    assert database.nodes == (
        DbcNode(name="Engine", comment=None),
        DbcNode(name="ECU", comment=None),
    )
    assert len(database.messages) == 1

    message = database.messages[0]

    assert message.name == "EngineData"
    assert message.frame_id == 0x123
    assert message.is_extended is False
    assert message.is_fd is False
    assert message.length == 8
    assert message.senders == ("Engine",)
    assert message.comment == "Primary engine broadcast frame"
    assert message.cycle_time is None
    assert [item.name for item in message.signals] == [
        "EngineSpeed",
        "CoolantTemp",
        "ThrottlePosition",
    ]


def test_a_signal_maps_factor_offset_range_unit_and_comment() -> None:
    database = parse_fixture("basic_standard.dbc")

    speed = signal(database, "EngineData", "EngineSpeed")
    assert speed.start_bit == 0
    assert speed.length == 16
    assert speed.byte_order is DbcByteOrder.LITTLE_ENDIAN
    assert speed.is_signed is False
    assert speed.factor == 0.25
    assert speed.offset == 0.0
    assert speed.minimum == 0.0
    assert speed.maximum == 16383.75
    assert speed.unit == "rpm"
    assert speed.receivers == ("ECU",)
    assert speed.choices == ()
    assert speed.is_multiplexer is False
    assert speed.multiplexer_signal is None
    assert speed.multiplexer_ids is None
    assert speed.comment == "Crank-shaft speed"

    coolant = signal(database, "EngineData", "CoolantTemp")
    assert coolant.offset == -40.0
    assert coolant.minimum == -40.0
    assert coolant.maximum == 215.0
    assert coolant.unit == "degC"
    assert coolant.comment is None


def test_an_extended_message_keeps_its_identifier_and_its_kind() -> None:
    database = parse_fixture("extended.dbc")

    message = database.messages[0]

    assert message.frame_id == 0x123
    assert message.is_extended is True
    assert message.is_fd is False


def test_the_identifier_pair_separates_standard_from_extended() -> None:
    """0x123 standard and 0x123 extended are two different messages."""
    standard = parse_fixture("basic_standard.dbc").messages[0]
    extended = parse_fixture("extended.dbc").messages[0]

    assert standard.frame_id == extended.frame_id == 0x123
    assert (standard.is_extended, extended.is_extended) == (False, True)
    assert standard.name != extended.name


def test_a_can_fd_message_keeps_the_fd_flag_and_its_64_byte_length() -> None:
    database = parse_fixture("can_fd.dbc")

    message = database.messages[0]

    assert message.name == "FdFramePayload"
    assert message.is_fd is True
    assert message.is_extended is False
    assert message.length == 64
    assert [item.length for item in message.signals] == [32, 32]


def test_little_and_big_endian_signals_keep_their_declared_start_bit() -> None:
    """CAN-X carries the DBC start bit; it does not re-interpret Motorola layout."""
    database = parse_fixture("endian_signed_scale.dbc")

    little = signal(database, "MixedEndian", "LittleUnsigned")
    assert little.byte_order is DbcByteOrder.LITTLE_ENDIAN
    assert little.is_signed is False
    assert little.start_bit == 0
    assert little.factor == 2.0
    assert little.offset == 1.0
    assert little.minimum == 1.0
    assert little.maximum == 8191.0
    assert little.unit == "mV"

    big = signal(database, "MixedEndian", "BigSigned")
    assert big.byte_order is DbcByteOrder.BIG_ENDIAN
    assert big.is_signed is True
    assert big.start_bit == 39
    assert big.factor == 0.5
    assert big.offset == -100.0
    assert big.minimum == -1124.0
    assert big.maximum == 1947.5
    assert big.unit == "deg"


def test_choices_become_ordered_canonical_pairs() -> None:
    database = parse_fixture("choices.dbc")

    door = signal(database, "DoorStatus", "DoorState")
    assert door.choices == (
        DbcChoice(value=0, label="Closed"),
        DbcChoice(value=1, label="Open"),
        DbcChoice(value=2, label="Error"),
    )

    lock = signal(database, "DoorStatus", "LockState")
    assert lock.choices == (
        DbcChoice(value=0, label="Unlocked"),
        DbcChoice(value=1, label="Locked"),
    )


def test_a_signal_without_choices_reports_an_empty_tuple() -> None:
    database = parse_fixture("basic_standard.dbc")

    assert signal(database, "EngineData", "EngineSpeed").choices == ()


def test_multiplexing_metadata_is_preserved() -> None:
    database = parse_fixture("multiplexed.dbc")

    switch = signal(database, "MuxFrame", "ModeSwitch")
    assert switch.is_multiplexer is True
    assert switch.multiplexer_signal is None
    assert switch.multiplexer_ids is None
    assert switch.choices == (
        DbcChoice(value=0, label="ModeA"),
        DbcChoice(value=1, label="ModeB"),
        DbcChoice(value=2, label="ModeC"),
    )

    expected = {"ModeASignal": (0,), "ModeBSignal": (1,), "ModeCSignal": (2,)}
    for name, ids in expected.items():
        item = signal(database, "MuxFrame", name)
        assert item.is_multiplexer is False
        assert item.multiplexer_signal == "ModeSwitch"
        assert item.multiplexer_ids == ids


def test_node_sender_receiver_comments_and_cycle_time_are_preserved() -> None:
    database = parse_fixture("metadata.dbc")

    assert database.version == "2.0"
    assert database.nodes == (
        DbcNode(name="Gateway", comment="Gateway node comment"),
        DbcNode(name="Sensor", comment=None),
        DbcNode(name="Actuator", comment=None),
    )

    message = database.messages[0]
    assert message.senders == ("Gateway",)
    assert message.comment == "Status frame comment"
    assert message.cycle_time == 50

    node_id = next(item for item in message.signals if item.name == "NodeId")
    assert node_id.receivers == ("Sensor", "Actuator")
    counter = next(item for item in message.signals if item.name == "Counter")
    assert counter.receivers == ("Sensor",)
    assert counter.comment == "Rolling counter"


def test_non_ascii_content_survives_the_conversion() -> None:
    database = parse_fixture("non_ascii.dbc")

    message = database.messages[0]
    assert message.comment == "Türstatus — température naïve — 车门状态"
    assert message.signals[0].unit == "état"


def test_message_and_signal_order_follow_the_source_document() -> None:
    """Order is a reviewed property of the file, not something to re-sort."""
    database = parse_fixture("basic_standard.dbc")

    assert [item.name for item in database.messages] == ["EngineData"]
    assert [item.name for item in database.messages[0].signals] == [
        "EngineSpeed",
        "CoolantTemp",
        "ThrottlePosition",
    ]


def test_repeated_parses_of_the_same_text_are_equal() -> None:
    text = fixture_text("metadata.dbc")

    first = CantoolsDbcParser().parse_text(text)
    second = CantoolsDbcParser().parse_text(text)

    assert first == second
    assert first is not second


def test_a_second_parser_instance_produces_the_same_database() -> None:
    text = fixture_text("multiplexed.dbc")

    assert CantoolsDbcParser().parse_text(text) == CantoolsDbcParser().parse_text(text)


# --- conversion invariants --------------------------------------------------


def test_a_reversed_signal_range_becomes_a_typed_invalid_model() -> None:
    """The engine accepts ``[100|0]``; the CAN-X canonical model does not."""
    with pytest.raises(DbcModelError) as captured:
        CantoolsDbcParser().parse_text(REVERSED_SIGNAL_RANGE_DBC)

    error = captured.value
    assert error.code == "dbc.invalid_model"
    assert error.source == "dbc"
    assert error.recoverable is False


def test_a_duplicate_message_name_becomes_a_typed_invalid_model() -> None:
    with pytest.raises(DbcModelError) as captured:
        CantoolsDbcParser().parse_text(DUPLICATE_MESSAGE_NAME_DBC)

    assert captured.value.code == "dbc.invalid_model"


def test_a_duplicate_frame_identifier_becomes_a_typed_invalid_model() -> None:
    with pytest.raises(DbcModelError) as captured:
        CantoolsDbcParser().parse_text(DUPLICATE_FRAME_ID_DBC)

    assert captured.value.code == "dbc.invalid_model"


def test_a_duplicate_node_name_becomes_a_typed_invalid_model() -> None:
    with pytest.raises(DbcModelError) as captured:
        CantoolsDbcParser().parse_text(DUPLICATE_NODE_DBC)

    assert captured.value.code == "dbc.invalid_model"


# --- parse failures ---------------------------------------------------------


def test_a_non_string_source_is_a_typed_parse_failure() -> None:
    with pytest.raises(DbcParseError) as captured:
        CantoolsDbcParser().parse_text(123)  # type: ignore[arg-type]

    assert captured.value.code == "dbc.parse_failed"


def test_malformed_text_is_a_typed_parse_failure_with_a_position() -> None:
    with pytest.raises(DbcParseError) as captured:
        CantoolsDbcParser().parse_text(fixture_text("malformed.dbc"))

    error = captured.value

    assert error.code == "dbc.parse_failed"
    assert error.source == "dbc"
    assert error.recoverable is False
    # ``malformed.dbc`` breaks on its ninth line, five characters in ("BO_ ").
    assert error.details["line"] == 9
    assert error.details["column"] == 5
    assert isinstance(error.details["parser_error"], str)
    assert error.details["parser_error"]


def test_a_parse_failure_never_echoes_the_source_document() -> None:
    """An error response must not become a second copy of the caller's upload."""
    text = fixture_text("malformed.dbc")

    with pytest.raises(DbcParseError) as captured:
        CantoolsDbcParser().parse_text(text)

    rendered = json.dumps(captured.value.details, default=str)

    assert "not_a_frame_id" not in rendered
    assert "BrokenFrame" not in rendered
    assert "VERSION" not in rendered
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            assert stripped not in rendered


def test_a_parse_failure_never_leaks_a_traceback_or_third_party_repr() -> None:
    with pytest.raises(DbcParseError) as captured:
        CantoolsDbcParser().parse_text(fixture_text("malformed.dbc"))

    rendered = json.dumps(captured.value.details, default=str)

    for marker in ("Traceback", "textparser", "cantools", "object at 0x"):
        assert marker not in rendered


# --- third-party boundary ---------------------------------------------------

_PRIMITIVES = (str, int, float, bool, type(None))


def _walk(value: object, path: str) -> int:
    """Walk a value and return how many canonical objects it contains.

    Every leaf must be a primitive and every container a tuple; anything else
    must be a ``canx.dbc.model`` dataclass. That is a structural proof that no
    third-party object survived the conversion — it does not depend on guessing
    a class name.
    """
    if isinstance(value, _PRIMITIVES):
        return 0
    if isinstance(value, bytes):
        return 0
    if isinstance(value, tuple):
        return sum(_walk(item, f"{path}[{index}]") for index, item in enumerate(value))
    assert is_dataclass(value) and not isinstance(value, type), (
        f"{path} is {type(value).__module__}.{type(value).__name__},"
        " which is not a canonical CAN-X DBC value"
    )
    assert type(value).__module__ == "canx.dbc.model", (
        f"{path} comes from {type(value).__module__}, outside the canonical model"
    )
    count = 1
    for field in fields(value):
        count += _walk(getattr(value, field.name), f"{path}.{field.name}")
    return count


@pytest.mark.parametrize(
    "name",
    [
        "basic_standard.dbc",
        "extended.dbc",
        "endian_signed_scale.dbc",
        "choices.dbc",
        "multiplexed.dbc",
        "metadata.dbc",
        "can_fd.dbc",
        "non_ascii.dbc",
    ],
)
def test_the_canonical_graph_contains_only_can_x_types(name: str) -> None:
    count = _walk(parse_fixture(name), name)

    # Guard against a vacuous walk: every fixture carries at least a database,
    # a message and a signal.
    assert count >= 3


def test_the_canonical_graph_contains_no_mapping_where_a_choice_tuple_is_expected() -> None:
    """A ``dict`` is the shape a leaked third-party ``choices`` would take."""
    database = parse_fixture("choices.dbc")

    for message in database.messages:
        for item in message.signals:
            assert not isinstance(item.choices, dict)
            assert isinstance(item.choices, tuple)
            assert all(isinstance(choice, DbcChoice) for choice in item.choices)


def _run_in_subprocess(statement: str) -> str:
    """Run one statement in a fresh interpreter and return its stdout."""
    result = subprocess.run(
        [sys.executable, "-c", statement],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, "PYTHONPATH": str(RUNTIME_ROOT)},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_importing_the_canonical_model_does_not_import_the_dbc_engine() -> None:
    """The canonical model must be usable without cantools being installed."""
    output = _run_in_subprocess(
        "import sys; import canx.dbc.model; print('cantools' in sys.modules)"
    )

    assert output == "False"


def test_importing_the_dbc_package_does_not_import_the_dbc_engine() -> None:
    output = _run_in_subprocess("import sys; import canx.dbc; print('cantools' in sys.modules)")

    assert output == "False"


def test_importing_the_parser_is_what_pulls_the_dbc_engine_in() -> None:
    """Prove the previous two tests are not passing for an unrelated reason."""
    output = _run_in_subprocess(
        "import sys; import canx.dbc.parser; print('cantools' in sys.modules)"
    )

    assert output == "True"
