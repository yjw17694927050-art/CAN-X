"""Basic single-level multiplexing: the switch chooses, and it is not consumed.

The selection value is the switch's **raw** value. A ``VAL_`` label on the switch
names the mode and a ``factor`` scales it, but neither chooses the branch — only
the bit pattern does. Inactive signals are omitted, active ones keep the order
the DBC document declared them in, and the switch itself is returned as a signal
rather than being used up as control information.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from canx.dbc.decode import DbcDecoder
from canx.dbc.decode_model import DecodedFrame
from canx.dbc.errors import DbcDecodeUnsupportedError
from canx.dbc.model import DbcChoice
from decode_builders import LE, decode_text, decoder_for, frame, message, signal

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "dbc"

MUX_FRAME_ID = 1024

#: A nested / extended multiplexing document: ``m0M`` makes ``Inner`` a switch
#: inside the branch selected by ``Outer``. The pinned engine flattens those
#: markers and reports every signal as unmultiplexed.
EXTENDED_MULTIPLEXED_DBC = """VERSION "1.0"
BU_: N1
BO_ 291 M: 8 N1
 SG_ Outer M : 0|4@1+ (1,0) [0|15] "" N1
 SG_ Inner m0M : 4|4@1+ (1,0) [0|15] "" N1
 SG_ LeafA m0m0 : 8|8@1+ (1,0) [0|255] "" N1
 SG_ Other m1 : 24|8@1+ (1,0) [0|255] "" N1
"""


def fixture_decoder() -> DbcDecoder:
    """Build a decoder over ``multiplexed.dbc``."""
    return decode_text((FIXTURES / "multiplexed.dbc").read_text(encoding="utf-8"))


def names(decoded: DecodedFrame) -> list[str]:
    """Return the decoded signal names, in decode order."""
    return [item.name for item in decoded.signals]


def mux_payload(mode: int) -> bytes:
    """Build an eight-byte payload whose multiplexer switch reads ``mode``."""
    return bytes([mode]) + bytes([0xAA]) * 7


def common_and_branches() -> DbcDecoder:
    """A message with a switch, one common signal and two branches."""
    return decoder_for(
        message(
            (
                signal("Switch", 0, 8, byte_order=LE, is_multiplexer=True),
                signal("Common", 8, 8, byte_order=LE),
                signal(
                    "BranchZero",
                    16,
                    8,
                    byte_order=LE,
                    multiplexer_signal="Switch",
                    multiplexer_ids=(0,),
                ),
                signal(
                    "BranchOne",
                    24,
                    8,
                    byte_order=LE,
                    multiplexer_signal="Switch",
                    multiplexer_ids=(1,),
                ),
            ),
            name="Muxed",
        )
    )


# --- the fixture ------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "active"),
    [
        (0, "ModeASignal"),
        (1, "ModeBSignal"),
        (2, "ModeCSignal"),
    ],
)
def test_only_the_selected_branch_is_decoded(mode: int, active: str) -> None:
    decoder = fixture_decoder()

    decoded = decoder.decode_frame(frame(mux_payload(mode), arbitration_id=MUX_FRAME_ID))

    assert names(decoded) == ["ModeSwitch", active]


def test_the_inactive_branches_are_omitted_entirely() -> None:
    decoder = fixture_decoder()

    decoded = decoder.decode_frame(frame(mux_payload(0), arbitration_id=MUX_FRAME_ID))

    assert decoded.signal("ModeBSignal") is None
    assert decoded.signal("ModeCSignal") is None


def test_the_switch_itself_is_returned_rather_than_consumed() -> None:
    decoder = fixture_decoder()

    decoded = decoder.decode_frame(frame(mux_payload(1), arbitration_id=MUX_FRAME_ID))

    selector = decoded.signal("ModeSwitch")
    assert selector is not None
    assert selector.raw_value == 1
    assert selector.choice_label == "ModeB"


def test_an_undeclared_switch_value_selects_no_branch() -> None:
    """Nothing is guessed: the switch is reported and no branch is invented."""
    decoder = fixture_decoder()

    decoded = decoder.decode_frame(frame(mux_payload(9), arbitration_id=MUX_FRAME_ID))

    assert names(decoded) == ["ModeSwitch"]


# --- common signals ---------------------------------------------------------


def test_a_signal_without_a_parent_is_always_active() -> None:
    decoder = common_and_branches()

    for mode in (0, 1, 7):
        decoded = decoder.decode_frame(frame(bytes([mode, 42, 1, 2]) + bytes(4)))
        common = decoded.signal("Common")
        assert common is not None
        assert common.raw_value == 42


def test_a_common_signal_survives_an_undeclared_switch_value() -> None:
    decoder = common_and_branches()

    decoded = decoder.decode_frame(frame(bytes([7, 42]) + bytes(6)))

    assert names(decoded) == ["Switch", "Common"]


def test_a_signal_can_belong_to_several_switch_values() -> None:
    decoder = decoder_for(
        message(
            (
                signal("Switch", 0, 8, byte_order=LE, is_multiplexer=True),
                signal(
                    "Shared",
                    8,
                    8,
                    byte_order=LE,
                    multiplexer_signal="Switch",
                    multiplexer_ids=(0, 2),
                ),
            ),
            name="Muxed",
        )
    )

    for mode in (0, 2):
        assert names(decoder.decode_frame(frame(bytes([mode, 5]) + bytes(6)))) == [
            "Switch",
            "Shared",
        ]
    assert names(decoder.decode_frame(frame(bytes([1, 5]) + bytes(6)))) == ["Switch"]


# --- order ------------------------------------------------------------------


def test_decoded_signals_keep_the_order_the_document_declared() -> None:
    """Common signals keep their declared position relative to the switch."""
    decoder = common_and_branches()

    decoded = decoder.decode_frame(frame(bytes([0, 42, 1, 2]) + bytes(4)))

    assert names(decoded) == ["Switch", "Common", "BranchZero"]


def test_a_child_declared_first_stays_first() -> None:
    decoder = decoder_for(
        message(
            (
                signal(
                    "Child",
                    8,
                    8,
                    byte_order=LE,
                    multiplexer_signal="Switch",
                    multiplexer_ids=(0,),
                ),
                signal("Switch", 0, 8, byte_order=LE, is_multiplexer=True),
            ),
            name="Muxed",
        )
    )

    assert names(decoder.decode_frame(frame(bytes([0, 9]) + bytes(6)))) == ["Child", "Switch"]


# --- the selection is the raw value ----------------------------------------


def test_the_branch_is_chosen_by_the_raw_switch_value_not_the_scaled_one() -> None:
    """Raw 1 scales to 2 — so a physical-value lookup would pick the wrong branch."""
    decoder = decoder_for(
        message(
            (
                signal("Switch", 0, 8, byte_order=LE, is_multiplexer=True, factor=2.0),
                signal(
                    "BranchOne",
                    8,
                    8,
                    byte_order=LE,
                    multiplexer_signal="Switch",
                    multiplexer_ids=(1,),
                ),
                signal(
                    "BranchTwo",
                    16,
                    8,
                    byte_order=LE,
                    multiplexer_signal="Switch",
                    multiplexer_ids=(2,),
                ),
            ),
            name="Muxed",
        )
    )

    decoded = decoder.decode_frame(frame(bytes([1, 0xAA, 0xBB]) + bytes(5)))

    selector = decoded.signal("Switch")
    assert selector is not None
    assert (selector.raw_value, selector.physical_value) == (1, 2.0)
    assert names(decoded) == ["Switch", "BranchOne"]
    active = decoded.signal("BranchOne")
    assert active is not None
    assert active.raw_value == 0xAA


def test_the_branch_is_not_chosen_by_the_switch_s_choice_label() -> None:
    decoder = decoder_for(
        message(
            (
                signal(
                    "Switch",
                    0,
                    8,
                    byte_order=LE,
                    is_multiplexer=True,
                    choices=(DbcChoice(value=0, label="Zero"), DbcChoice(value=1, label="One")),
                ),
                signal(
                    "BranchOne",
                    8,
                    8,
                    byte_order=LE,
                    multiplexer_signal="Switch",
                    multiplexer_ids=(1,),
                ),
            ),
            name="Muxed",
        )
    )

    decoded = decoder.decode_frame(frame(bytes([1, 0xAA]) + bytes(6)))

    selector = decoded.signal("Switch")
    assert selector is not None
    assert selector.choice_label == "One"
    assert names(decoded) == ["Switch", "BranchOne"]


# --- a switch with no children ---------------------------------------------


def test_a_switch_with_no_children_decodes_on_its_own() -> None:
    decoder = decoder_for(
        message((signal("Switch", 0, 8, byte_order=LE, is_multiplexer=True),), name="Muxed")
    )

    decoded = decoder.decode_frame(frame(bytes([3]) + bytes(7)))

    assert names(decoded) == ["Switch"]


def test_the_switch_is_not_special_cased_out_of_a_plain_message() -> None:
    """A message with a switch and nothing multiplexed is still decodable."""
    decoder = decoder_for(
        message(
            (
                signal("Switch", 0, 8, byte_order=LE, is_multiplexer=True),
                signal("Plain", 8, 8, byte_order=LE),
            ),
            name="Muxed",
        )
    )

    assert names(decoder.decode_frame(frame(bytes([0, 1]) + bytes(6)))) == ["Switch", "Plain"]


# --- topologies this increment refuses --------------------------------------


def test_extended_multiplexing_source_is_refused_rather_than_half_decoded() -> None:
    """A nested topology that collapses into two switches is reported, not guessed.

    Decoding it as "everything is always active" would be silently wrong — the
    inactive ``m1`` branch would be reported as if it were live — so the decoder
    refuses the message instead.
    """
    decoder = decode_text(EXTENDED_MULTIPLEXED_DBC)

    with pytest.raises(DbcDecodeUnsupportedError) as info:
        decoder.decode_frame(frame(bytes(8)))

    assert info.value.details["reason"] == "ambiguous_multiplexing"
    assert info.value.details["message_name"] == "M"


def test_a_switch_carrying_a_parent_leaves_no_ambiguous_definition_to_decode() -> None:
    """The canonical model refuses the half-relation, so the decoder never sees it."""
    with pytest.raises(ValueError, match="multiplexer"):
        signal(
            "Switch",
            0,
            8,
            is_multiplexer=True,
            multiplexer_signal="Parent",
            multiplexer_ids=(0,),
        )
