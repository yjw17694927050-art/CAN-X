"""Behavior tests for the canonical CAN-X DBC domain model.

Nothing here imports ``cantools``: the canonical model is meant to be readable
and testable entirely on its own, and the third-party boundary is exercised in
``test_dbc_parser.py`` instead.
"""

from dataclasses import FrozenInstanceError

import pytest
from canx.dbc.model import (
    MAX_EXTENDED_FRAME_ID,
    MAX_STANDARD_FRAME_ID,
    DbcByteOrder,
    DbcChoice,
    DbcDatabase,
    DbcDocument,
    DbcMessage,
    DbcNode,
    DbcSignal,
    DbcSource,
)


def make_signal(**changes: object) -> DbcSignal:
    """Create a valid little-endian unsigned signal with explicit fields."""
    values: dict[str, object] = {
        "name": "EngineSpeed",
        "start_bit": 0,
        "length": 16,
        "byte_order": DbcByteOrder.LITTLE_ENDIAN,
        "is_signed": False,
        "factor": 0.25,
        "offset": -10.0,
        "minimum": 0.0,
        "maximum": 16383.75,
        "unit": "rpm",
        "receivers": ("ECU2",),
        "choices": (),
        "is_multiplexer": False,
        "multiplexer_signal": None,
        "multiplexer_ids": None,
        "comment": None,
    }
    values.update(changes)
    return DbcSignal(**values)  # type: ignore[arg-type]


def make_message(**changes: object) -> DbcMessage:
    """Create a valid standard classic-CAN message with explicit fields."""
    values: dict[str, object] = {
        "frame_id": 0x123,
        "name": "EngineData",
        "length": 8,
        "is_extended": False,
        "is_fd": False,
        "senders": ("ECU1",),
        "signals": (make_signal(),),
        "comment": None,
        "cycle_time": None,
    }
    values.update(changes)
    return DbcMessage(**values)  # type: ignore[arg-type]


def make_database(**changes: object) -> DbcDatabase:
    """Create a valid database carrying one message and one node."""
    values: dict[str, object] = {
        "messages": (make_message(),),
        "nodes": (DbcNode(name="ECU1", comment=None),),
        "version": "1.2.3",
    }
    values.update(changes)
    return DbcDatabase(**values)  # type: ignore[arg-type]


# --- identifier space -------------------------------------------------------


def test_standard_message_accepts_the_largest_11_bit_identifier() -> None:
    message = make_message(frame_id=MAX_STANDARD_FRAME_ID)

    assert message.frame_id == 0x7FF
    assert message.is_extended is False


def test_extended_message_accepts_the_largest_29_bit_identifier() -> None:
    message = make_message(frame_id=MAX_EXTENDED_FRAME_ID, is_extended=True)

    assert message.frame_id == 0x1FFFFFFF
    assert message.is_extended is True


@pytest.mark.parametrize("frame_id", [0x800, 0x1FFFFFFF])
def test_standard_message_rejects_an_identifier_beyond_11_bits(frame_id: int) -> None:
    with pytest.raises(ValueError, match="standard frame_id"):
        make_message(frame_id=frame_id)


def test_extended_message_rejects_an_identifier_beyond_29_bits() -> None:
    with pytest.raises(ValueError, match="extended frame_id"):
        make_message(frame_id=0x20000000, is_extended=True)


def test_the_same_numeric_identifier_may_be_standard_and_extended() -> None:
    """The identifier space is (frame_id, is_extended), not frame_id alone."""
    standard = make_message(frame_id=0x123, is_extended=False, name="A")
    extended = make_message(frame_id=0x123, is_extended=True, name="B")

    database = make_database(messages=(standard, extended))

    assert [message.frame_id for message in database.messages] == [0x123, 0x123]


@pytest.mark.parametrize("frame_id", [-1, True, 1.5])
def test_message_rejects_a_non_identifier_frame_id(frame_id: object) -> None:
    with pytest.raises(ValueError):
        make_message(frame_id=frame_id)


# --- message invariants -----------------------------------------------------


def test_message_accepts_a_can_fd_payload_of_64_bytes() -> None:
    message = make_message(is_fd=True, length=64)

    assert message.length == 64
    assert message.is_fd is True


def test_classic_message_rejects_a_payload_beyond_eight_bytes() -> None:
    with pytest.raises(ValueError, match="Classic CAN"):
        make_message(is_fd=False, length=12)


@pytest.mark.parametrize("name", ["", "   "])
def test_message_rejects_a_blank_name(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        make_message(name=name)


def test_message_rejects_two_signals_with_the_same_name() -> None:
    with pytest.raises(ValueError, match="signal names"):
        make_message(signals=(make_signal(name="S"), make_signal(name="S", start_bit=16)))


@pytest.mark.parametrize("cycle_time", [0, -1, 1.5, True])
def test_message_rejects_an_unusable_cycle_time(cycle_time: object) -> None:
    with pytest.raises(ValueError, match="cycle_time"):
        make_message(cycle_time=cycle_time)


# --- signal invariants ------------------------------------------------------


@pytest.mark.parametrize("start_bit", [-1, True, "0"])
def test_signal_rejects_an_invalid_start_bit(start_bit: object) -> None:
    with pytest.raises(ValueError, match="start_bit"):
        make_signal(start_bit=start_bit)


@pytest.mark.parametrize("length", [0, -1, True, 1.5])
def test_signal_rejects_a_non_positive_length(length: object) -> None:
    with pytest.raises(ValueError, match="length"):
        make_signal(length=length)


def test_signal_rejects_an_empty_name() -> None:
    with pytest.raises(ValueError, match="name"):
        make_signal(name="")


@pytest.mark.parametrize("field", ["factor", "offset", "minimum", "maximum"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_signal_rejects_a_non_finite_numeric_field(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=field):
        make_signal(**{field: value})


@pytest.mark.parametrize("field", ["factor", "offset"])
def test_signal_rejects_a_non_numeric_scale_field(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        make_signal(**{field: "1"})


def test_signal_rejects_a_minimum_above_its_maximum() -> None:
    with pytest.raises(ValueError, match="minimum"):
        make_signal(minimum=100.0, maximum=0.0)


def test_signal_accepts_an_absent_range_and_an_absent_unit() -> None:
    signal = make_signal(minimum=None, maximum=None, unit=None)

    assert signal.minimum is None
    assert signal.maximum is None
    assert signal.unit is None


def test_signal_normalizes_its_scale_fields_to_float() -> None:
    """One canonical representation, whichever numeric literal the source used."""
    signal = make_signal(factor=1, offset=0, minimum=0, maximum=255)

    assert (signal.factor, signal.offset) == (1.0, 0.0)
    assert (signal.minimum, signal.maximum) == (0.0, 255.0)
    assert all(
        isinstance(value, float)
        for value in (signal.factor, signal.offset, signal.minimum, signal.maximum)
    )


def test_signal_rejects_a_non_byte_order_value() -> None:
    with pytest.raises(ValueError, match="byte_order"):
        make_signal(byte_order="intel")


def test_signal_accepts_both_canonical_byte_orders() -> None:
    assert make_signal(byte_order=DbcByteOrder.BIG_ENDIAN).byte_order is DbcByteOrder.BIG_ENDIAN
    assert (
        make_signal(byte_order=DbcByteOrder.LITTLE_ENDIAN).byte_order
        is DbcByteOrder.LITTLE_ENDIAN
    )


@pytest.mark.parametrize("receivers", [("",), ("ECU2", " "), ["ECU2"]])
def test_signal_rejects_unusable_receivers(receivers: object) -> None:
    with pytest.raises(ValueError, match="receiver"):
        make_signal(receivers=receivers)


# --- multiplexing metadata --------------------------------------------------


def test_signal_represents_a_multiplexer_switch() -> None:
    switch = make_signal(name="Mux", is_multiplexer=True)

    assert switch.is_multiplexer is True
    assert switch.multiplexer_signal is None
    assert switch.multiplexer_ids is None


def test_signal_represents_a_multiplexed_signal() -> None:
    payload = make_signal(
        name="Payload", is_multiplexer=False, multiplexer_signal="Mux", multiplexer_ids=(0, 1)
    )

    assert payload.multiplexer_signal == "Mux"
    assert payload.multiplexer_ids == (0, 1)


def test_signal_rejects_a_multiplexer_switch_carrying_a_parent() -> None:
    with pytest.raises(ValueError, match="multiplexer"):
        make_signal(is_multiplexer=True, multiplexer_signal="Mux", multiplexer_ids=(0,))


def test_signal_rejects_a_parent_without_any_multiplexer_id() -> None:
    with pytest.raises(ValueError, match="multiplexer"):
        make_signal(multiplexer_signal="Mux")


def test_signal_rejects_multiplexer_ids_without_a_parent() -> None:
    with pytest.raises(ValueError, match="multiplexer"):
        make_signal(multiplexer_ids=(0,))


@pytest.mark.parametrize("multiplexer_ids", [(), (-1,), (1.0,), (0, 0)])
def test_signal_rejects_unusable_multiplexer_ids(multiplexer_ids: object) -> None:
    with pytest.raises(ValueError, match="multiplexer"):
        make_signal(multiplexer_signal="Mux", multiplexer_ids=multiplexer_ids)


# --- choices ----------------------------------------------------------------


def test_signal_carries_ordered_choices() -> None:
    signal = make_signal(
        choices=(
            DbcChoice(value=0, label="Off"),
            DbcChoice(value=1, label="On"),
            DbcChoice(value=2, label="Error"),
        )
    )

    assert [choice.label for choice in signal.choices] == ["Off", "On", "Error"]
    assert [choice.value for choice in signal.choices] == [0, 1, 2]


def test_a_choice_accepts_an_empty_label_that_the_source_declared() -> None:
    assert DbcChoice(value=0, label="").label == ""


def test_signal_rejects_two_choices_for_the_same_value() -> None:
    with pytest.raises(ValueError, match="choices"):
        make_signal(
            choices=(DbcChoice(value=1, label="A"), DbcChoice(value=1, label="B"))
        )


@pytest.mark.parametrize(
    ("value", "label"),
    [("0", "A"), (True, "A"), (0, 1)],
)
def test_choice_rejects_an_unusable_value_or_label(value: object, label: object) -> None:
    with pytest.raises(ValueError):
        DbcChoice(value=value, label=label)  # type: ignore[arg-type]


def test_signal_rejects_a_plain_list_of_choices() -> None:
    with pytest.raises(ValueError, match="choices"):
        make_signal(choices=[DbcChoice(value=0, label="Off")])


# --- database invariants ----------------------------------------------------


def test_database_keeps_the_source_message_order() -> None:
    database = make_database(
        messages=(
            make_message(name="Second", frame_id=0x200),
            make_message(name="First", frame_id=0x100),
        )
    )

    assert [message.name for message in database.messages] == ["Second", "First"]


def test_database_rejects_two_messages_with_the_same_name() -> None:
    with pytest.raises(ValueError, match="message names"):
        make_database(
            messages=(
                make_message(name="EngineData", frame_id=0x100),
                make_message(name="EngineData", frame_id=0x200),
            )
        )


def test_database_rejects_two_messages_on_the_same_identifier_and_kind() -> None:
    with pytest.raises(ValueError, match="frame_id"):
        make_database(
            messages=(
                make_message(name="A", frame_id=0x100, is_extended=False),
                make_message(name="B", frame_id=0x100, is_extended=False),
            )
        )


def test_database_rejects_two_nodes_with_the_same_name() -> None:
    with pytest.raises(ValueError, match="node names"):
        make_database(
            nodes=(DbcNode(name="ECU1", comment=None), DbcNode(name="ECU1", comment="dup"))
        )


def test_database_rejects_a_plain_list_of_messages() -> None:
    with pytest.raises(ValueError, match="messages"):
        make_database(messages=[make_message()])


def test_database_accepts_an_absent_version_and_no_nodes() -> None:
    database = make_database(version=None, nodes=())

    assert database.version is None
    assert database.nodes == ()


# --- provenance -------------------------------------------------------------


def test_source_accepts_a_file_backed_origin() -> None:
    source = DbcSource(
        name="basic_standard.dbc",
        path="C:/work/basic_standard.dbc",
        sha256="0" * 64,
        size_bytes=128,
        encoding="utf-8-sig",
    )

    assert source.path == "C:/work/basic_standard.dbc"
    assert source.size_bytes == 128


def test_source_accepts_a_text_only_origin_without_a_path() -> None:
    source = DbcSource(
        name="<text>", path=None, sha256="a" * 64, size_bytes=0, encoding="utf-8-sig"
    )

    assert source.path is None


@pytest.mark.parametrize(
    "sha256",
    ["", "abc", "Z" * 64, "A" * 64, "0" * 63, "0" * 65],
)
def test_source_rejects_anything_but_a_lowercase_sha256_digest(sha256: str) -> None:
    with pytest.raises(ValueError, match="sha256"):
        DbcSource(name="x.dbc", path=None, sha256=sha256, size_bytes=0, encoding="utf-8-sig")


@pytest.mark.parametrize("size_bytes", [-1, True, 1.5])
def test_source_rejects_an_unusable_size(size_bytes: object) -> None:
    with pytest.raises(ValueError, match="size_bytes"):
        DbcSource(
            name="x.dbc", path=None, sha256="0" * 64, size_bytes=size_bytes, encoding="utf-8-sig"
        )


@pytest.mark.parametrize("encoding", ["", "   "])
def test_source_rejects_a_blank_encoding(encoding: str) -> None:
    with pytest.raises(ValueError, match="encoding"):
        DbcSource(name="x.dbc", path=None, sha256="0" * 64, size_bytes=0, encoding=encoding)


def test_document_pairs_a_database_with_its_source() -> None:
    document = DbcDocument(
        database=make_database(),
        source=DbcSource(
            name="x.dbc", path="C:/x.dbc", sha256="0" * 64, size_bytes=1, encoding="utf-8-sig"
        ),
    )

    assert document.database.version == "1.2.3"
    assert document.source.name == "x.dbc"


def test_document_requires_a_canonical_database_and_source() -> None:
    source = DbcSource(
        name="x.dbc", path=None, sha256="0" * 64, size_bytes=0, encoding="utf-8-sig"
    )

    with pytest.raises(ValueError, match="database"):
        DbcDocument(database={"messages": []}, source=source)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="source"):
        DbcDocument(database=make_database(), source={"name": "x.dbc"})  # type: ignore[arg-type]


# --- immutability -----------------------------------------------------------


def test_the_whole_canonical_graph_is_immutable() -> None:
    document = DbcDocument(
        database=make_database(),
        source=DbcSource(
            name="x.dbc", path=None, sha256="0" * 64, size_bytes=1, encoding="utf-8-sig"
        ),
    )

    with pytest.raises(FrozenInstanceError):
        document.source = document.source  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        document.database.messages[0].frame_id = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        document.database.messages[0].signals[0].name = "other"  # type: ignore[misc]


def test_sequences_are_tuples_so_no_canonical_state_can_be_mutated_in_place() -> None:
    database = make_database()

    assert isinstance(database.messages, tuple)
    assert isinstance(database.nodes, tuple)
    assert isinstance(database.messages[0].signals, tuple)
    assert isinstance(database.messages[0].senders, tuple)
    assert isinstance(database.messages[0].signals[0].receivers, tuple)
    assert isinstance(database.messages[0].signals[0].choices, tuple)
