"""Decode canonical frames against a canonical DBC database.

This module is the runtime decode engine. It sits strictly between two owned
contracts — :class:`~canx.domain.frame.Frame` on one side and
:class:`~canx.dbc.model.DbcDatabase` on the other — and it never touches the
third-party DBC engine. ``cantools`` is a *test oracle* for this module (see
``tests/unit/dbc/test_dbc_decode_differential.py``), never a dependency: nothing
that reaches a caller is a ``cantools.database.Database``,
``cantools.database.can.Message``, ``cantools.database.can.Signal`` or
``NamedSignalValue``, and this module does not import the engine at all.

Four decisions shape the engine:

**The database is compiled once.** :class:`DbcDecoder` builds, at construction,
a ``(frame_id, is_extended) -> compiled message`` index and one extraction recipe
per signal. Decoding a frame then performs no database scan and no bit-topology
arithmetic; live and historical decode both run against the same read-only,
effectively immutable plan.

**No bare exception crosses the boundary.** Every diagnosable failure is a
:class:`~canx.dbc.errors.DbcError` subclass carrying the five structured fields
SPEC §38 requires. A definition that cannot be decoded without guessing — bits
outside the message payload, an unresolvable multiplexing topology, an IEEE-754
payload this increment does not implement — is detected while compiling and
reported as ``dbc.decode_unsupported`` when such a frame is actually decoded.

**The payload policy is explicit.** A payload *shorter* than the message's
declared length is refused (partial decode is not supported); a payload of exactly
the declared length decodes; a payload *longer* than it decodes too, because a
CAN FD payload bucket may legitimately exceed the engineering length — only the
declared bytes take part, and the extra bytes are never interpreted.

**A frame and its message must agree about CAN FD.** A Classic definition is
never silently applied to an FD frame, or the reverse.

Signal order is deterministic: the decoded signals appear in the order the DBC
document declared them, with inactive multiplexed signals omitted. A choice label
is additional semantics attached to the raw value, never a replacement for it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType

from canx.dbc.decode_model import (
    DbcDecodeFailure,
    DecodedFrame,
    DecodedFrameBatch,
    DecodedFrameOutcome,
    DecodedSignal,
)
from canx.dbc.decode_set import DecodedFrameSet, DecodeFrameSet
from canx.dbc.errors import (
    DbcDecodeUnsupportedError,
    DbcError,
    DbcFrameTypeMismatchError,
    DbcMessageNotFoundError,
    DbcPayloadTooShortError,
    DbcSignalDecodeError,
)
from canx.dbc.model import DbcByteOrder, DbcDatabase, DbcMessage, DbcSignal
from canx.domain.batch import FrameBatch
from canx.domain.frame import Frame

#: Reported when a signal's bits would be read from outside the message payload.
_UNSUPPORTED_OUT_OF_BOUNDS = "signal_outside_message_payload"

#: Reported when the multiplexing topology cannot be expressed without guessing.
_UNSUPPORTED_MULTIPLEXING = "ambiguous_multiplexing"

#: Reported when a signal's payload is IEEE-754 rather than an integer field.
_UNSUPPORTED_FLOAT_PAYLOAD = "float_signal_payload"

#: Reported when scaling produced a value that is not a finite number.
_REASON_NON_FINITE = "non_finite_physical_value"


class DbcDecoder:
    """Decode canonical frames against one canonical database.

    The decoder is built from a :class:`~canx.dbc.model.DbcDatabase`, not from a
    ``cantools`` database, so the same engine serves a transiently imported DBC, a
    project-owned asset, a test fixture and a future Agent call through one
    contract.

    After construction the decoder mutates no state: it holds only tuples and
    read-only mappings, so concurrent readers are safe and a repeated decode of
    the same frame against the same database always returns an equal result.
    ``decode_frame`` does not touch the database at all.

    A definition that cannot be decoded unambiguously is recorded while
    compiling and reported as ``dbc.decode_unsupported`` when such a frame is
    decoded — rather than refusing the entire database at construction, which
    would make one defective message out of a large file render every other
    message undecodable.
    """

    def __init__(self, database: DbcDatabase) -> None:
        if not isinstance(database, DbcDatabase):
            raise TypeError("DbcDecoder requires a canonical DbcDatabase")
        self._plans: Mapping[tuple[int, bool], _MessagePlan] = MappingProxyType(
            {
                (message.frame_id, message.is_extended): _compile_message(message)
                for message in database.messages
            }
        )

    def decode_frame(self, frame: Frame) -> DecodedFrame:
        """Decode one canonical frame.

        Args:
            frame: The frame to decode. It is never modified.

        Returns:
            The decoded frame: the original canonical frame with the signals its
            message defines, in DBC document order.

        Raises:
            TypeError: If ``frame`` is not a canonical :class:`Frame`. A wrong
                argument type is a programming error, not a decode failure.
            DbcMessageNotFoundError: If ``(arbitration_id, is_extended)`` is not
                defined in the database. The strict entry point reports this
                instead of returning an empty result.
            DbcFrameTypeMismatchError: If the frame and its message disagree
                about being CAN FD.
            DbcPayloadTooShortError: If the payload is shorter than the message's
                declared length.
            DbcDecodeUnsupportedError: If the definition cannot be decoded
                unambiguously.
            DbcSignalDecodeError: If a signal's bits could not become a value.
        """
        if not isinstance(frame, Frame):
            raise TypeError("decode_frame requires a canonical Frame")
        plan = self._plans.get((frame.arbitration_id, frame.is_extended))
        if plan is None:
            raise DbcMessageNotFoundError(
                "The frame's identifier pair is not defined in this database.",
                details={
                    "sequence": frame.sequence,
                    "arbitration_id": frame.arbitration_id,
                    "is_extended": frame.is_extended,
                    "is_fd": frame.is_fd,
                },
            )
        if plan.message.is_fd != frame.is_fd:
            raise DbcFrameTypeMismatchError(
                "The frame and its DBC message disagree about being CAN FD.",
                details={
                    "sequence": frame.sequence,
                    "arbitration_id": frame.arbitration_id,
                    "is_extended": frame.is_extended,
                    "message_name": plan.message.name,
                    "message_is_fd": plan.message.is_fd,
                    "frame_is_fd": frame.is_fd,
                },
            )
        return plan.decode(frame)

    def decode_batch(self, batch: FrameBatch) -> DecodedFrameBatch:
        """Decode a canonical batch, capturing one outcome per input frame.

        A frame that cannot be decoded becomes an outcome carrying its typed
        failure rather than disappearing, so the result always has the same
        length and order as the input and no caller has to guess which frames
        were skipped. The batch itself only fails on a programming error: a
        per-frame decode failure is data, not an exception.

        Args:
            batch: The canonical batch to decode. It is never modified.

        Returns:
            One outcome per input frame, in the input order.

        Raises:
            TypeError: If ``batch`` is not a canonical :class:`FrameBatch`.
        """
        if not isinstance(batch, FrameBatch):
            raise TypeError("decode_batch requires a canonical FrameBatch")
        return DecodedFrameBatch.create(
            stream_id=batch.stream_id, outcomes=self._outcomes(batch.frames)
        )

    def decode_frames(self, work: DecodeFrameSet) -> DecodedFrameSet:
        """Decode one decode work set, capturing one outcome per input frame.

        The same decode loop as :meth:`decode_batch`, over a container whose
        sequences need not be consecutive. A decoder is a frame-local, stateless
        transformation — it answers "what does *this* frame mean", and never looks
        at a neighbouring sequence — so a request carrying 1, 3 and 5 is exactly as
        meaningful as one carrying 1, 2 and 3. What the batch container's
        contiguity was ever needed for is the *capture* stream's numbering, which
        this method does not touch.

        Args:
            work: The decode work set. It is never modified.

        Returns:
            One outcome per input frame, in the input order.

        Raises:
            TypeError: If ``work`` is not a canonical :class:`DecodeFrameSet`.
        """
        if not isinstance(work, DecodeFrameSet):
            raise TypeError("decode_frames requires a canonical DecodeFrameSet")
        return DecodedFrameSet.create(
            stream_id=work.stream_id, outcomes=self._outcomes(work.frames)
        )

    def _outcomes(self, frames: Sequence[Frame]) -> tuple[DecodedFrameOutcome, ...]:
        """Decode every frame, turning a per-frame failure into data.

        The one decode loop both containers share: a frame that cannot be decoded
        becomes an outcome carrying its typed failure rather than disappearing, so
        the result always has the same length and order as the input and no caller
        has to guess which frames were skipped. A failure here is *per frame*; only
        a programming error raises.
        """
        outcomes: list[DecodedFrameOutcome] = []
        for frame in frames:
            try:
                decoded = self.decode_frame(frame)
            except DbcError as error:
                outcomes.append(
                    DecodedFrameOutcome.failed(frame, DbcDecodeFailure.from_error(error))
                )
            else:
                outcomes.append(DecodedFrameOutcome.succeeded(frame, decoded))
        return tuple(outcomes)


@dataclass(frozen=True, slots=True)
class _SignalPlan:
    """One compiled extraction recipe: everything a frame decode needs, precomputed.

    The bit topology is reduced at compile time to a byte window, a right shift
    and a mask, so decoding performs one ``int.from_bytes`` and two integer
    operations per signal instead of walking individual bits. ``choices`` is a
    read-only view and every other field is a scalar, so a plan is immutable in
    practice as well as in type.

    Args:
        signal: The canonical definition this plan was compiled from.
        choices: ``raw value -> label``, empty when the document declares none.
        multiplexer_ids: The multiplexer values that activate this signal, or
            ``None`` when it is always active.
        big_endian: Whether the signal is Motorola (``True``) or Intel order.
        byte_start: Index of the first payload byte the signal touches.
        byte_count: How many payload bytes the signal spans.
        shift: Right shift applied to the byte window before masking.
        mask: The ``length``-bit mask of the signal.
        sign_bit: ``1 << (length - 1)`` when signed, else ``0``.
    """

    signal: DbcSignal
    choices: Mapping[int, str]
    multiplexer_ids: frozenset[int] | None
    big_endian: bool
    byte_start: int
    byte_count: int
    shift: int
    mask: int
    sign_bit: int

    def raw_value(self, data: bytes) -> int:
        """Return the signal's raw integer for one payload.

        Signed signals are returned in two's-complement interpretation, so an
        8-bit signed signal holding ``0xFF`` decodes to ``-1`` rather than
        ``255``. The bit window was proven to lie inside the declared payload
        when the message was compiled.
        """
        window = data[self.byte_start : self.byte_start + self.byte_count]
        number = int.from_bytes(window, "big" if self.big_endian else "little")
        value = (number >> self.shift) & self.mask
        if self.sign_bit and value & self.sign_bit:
            value -= self.mask + 1
        return value

    def decode(self, data: bytes, *, message_name: str) -> DecodedSignal:
        """Decode one signal value, translating a broken value into a typed error."""
        raw = self.raw_value(data)
        try:
            physical = float(raw) * self.signal.factor + self.signal.offset
        except (ValueError, OverflowError, ArithmeticError) as error:
            raise DbcSignalDecodeError(
                "The signal's bits could not be converted to a physical value.",
                details={
                    "message_name": message_name,
                    "signal_name": self.signal.name,
                    "reason": type(error).__name__,
                },
            ) from error
        if not isfinite(physical):
            raise DbcSignalDecodeError(
                "The signal's physical value is not a finite number.",
                details={
                    "message_name": message_name,
                    "signal_name": self.signal.name,
                    "reason": _REASON_NON_FINITE,
                },
            )
        return DecodedSignal(
            name=self.signal.name,
            raw_value=raw,
            physical_value=physical,
            # Choices are matched on the raw value, never on the scaled one: a
            # ``VAL_`` table names bit patterns, not engineering quantities.
            choice_label=self.choices.get(raw),
            unit=self.signal.unit,
        )


@dataclass(frozen=True, slots=True)
class _MessagePlan:
    """One compiled message: its signal recipes plus any reason it cannot decode.

    ``signals`` keeps the DBC document's own order, and the multiplexer switch is
    one of them — so the decoded order is the declared order with inactive
    signals omitted, and the switch is returned rather than consumed.
    ``multiplexer`` references that same switch, so selecting the active branch
    costs one lookup rather than a scan.

    ``unsupported`` is a short reason token, or ``None`` when every signal can be
    decoded. Checking it in :meth:`decode` keeps a defective definition from
    making the rest of the database unusable.
    """

    message: DbcMessage
    signals: tuple[_SignalPlan, ...]
    multiplexer: _SignalPlan | None
    unsupported: str | None

    def decode(self, frame: Frame) -> DecodedFrame:
        """Decode one already-matched, already-type-checked frame."""
        name = self.message.name
        if self.unsupported is not None:
            raise DbcDecodeUnsupportedError(
                "This DBC message definition cannot be decoded unambiguously.",
                details={"message_name": name, "reason": self.unsupported},
            )
        data = frame.data
        if len(data) < self.message.length:
            raise DbcPayloadTooShortError(
                "The frame payload is shorter than the DBC message defines.",
                details={
                    "sequence": frame.sequence,
                    "arbitration_id": frame.arbitration_id,
                    "is_extended": frame.is_extended,
                    "message_name": name,
                    "expected_length": self.message.length,
                    "actual_length": len(data),
                },
            )
        # The selection is the *raw* value of the switch: a ``VAL_`` label on it
        # names the mode but does not choose it, and neither does the scaled
        # value.
        selected = None if self.multiplexer is None else self.multiplexer.raw_value(data)
        signals = tuple(
            plan.decode(data, message_name=name)
            for plan in self.signals
            if plan.multiplexer_ids is None
            or (selected is not None and selected in plan.multiplexer_ids)
        )
        return DecodedFrame(frame=frame, message_name=name, signals=signals)


def _compile_message(message: DbcMessage) -> _MessagePlan:
    """Compile one canonical message into a decode plan.

    Compilation never raises for a defective definition: it records a reason
    instead, so one unusable message does not take the whole database with it.
    """
    switch, unsupported = _resolve_multiplexer(message)
    length_bits = message.length * 8
    plans: list[_SignalPlan] = []
    if unsupported is None:
        for signal in message.signals:
            if signal.is_float:
                # An IEEE-754 payload must never be read as an integer field: the
                # bits would produce a plausible-looking but wrong number. This
                # increment preserves the metadata and refuses to decode it.
                unsupported = _UNSUPPORTED_FLOAT_PAYLOAD
                break
            plan = _compile_signal(signal, length_bits)
            if plan is None:
                unsupported = _UNSUPPORTED_OUT_OF_BOUNDS
                break
            plans.append(plan)
    if unsupported is not None:
        return _MessagePlan(
            message=message, signals=(), multiplexer=None, unsupported=unsupported
        )
    switch_plan = None
    if switch is not None:
        switch_plan = next(plan for plan in plans if plan.signal.name == switch.name)
    return _MessagePlan(
        message=message, signals=tuple(plans), multiplexer=switch_plan, unsupported=None
    )


def _resolve_multiplexer(message: DbcMessage) -> tuple[DbcSignal | None, str | None]:
    """Return the message's single multiplexer switch, or the reason there is none.

    The canonical model can express exactly one level of multiplexing: one switch
    and a set of signals that name it. Anything else is refused here rather than
    guessed at decode time:

    * two switches in one message — the shape a nested/extended topology collapses
      into when the source document cannot express it in this model;
    * a signal that names a parent which is not in the message, or which is not a
      switch at all.

    Both would otherwise decode as "every signal is active", which is a silently
    wrong result rather than a missing one.
    """
    switches = [signal for signal in message.signals if signal.is_multiplexer]
    if len(switches) > 1:
        return None, _UNSUPPORTED_MULTIPLEXING
    switch = switches[0] if switches else None
    for signal in message.signals:
        if signal.multiplexer_signal is None:
            continue
        if switch is None or signal.multiplexer_signal != switch.name:
            return None, _UNSUPPORTED_MULTIPLEXING
    return switch, None


def _compile_signal(signal: DbcSignal, length_bits: int) -> _SignalPlan | None:
    """Compile one signal's extraction recipe, or ``None`` when its bits do not fit.

    The bit window is derived from the DBC start bit exactly as the source
    document and the engine define it — for a Motorola signal ``start_bit`` names
    the **most significant** bit, in a numbering where each byte counts down from
    its own bit 7 — and is then proven to lie inside the declared message payload.
    A window that does not fit is refused here, which is what keeps a later
    extraction from reading a neighbouring byte or raising ``IndexError``.

    Both orders reduce to "one byte window, one right shift, one mask": Intel
    signals read the window little-endian, Motorola signals read it big-endian.
    """
    if signal.length > length_bits:
        return None
    if signal.byte_order is DbcByteOrder.LITTLE_ENDIAN:
        if signal.start_bit + signal.length > length_bits:
            return None
        byte_start = signal.start_bit // 8
        shift = signal.start_bit % 8
        byte_count = (shift + signal.length + 7) // 8
        big_endian = False
    else:
        network_start = 8 * (signal.start_bit // 8) + (7 - signal.start_bit % 8)
        if network_start + signal.length > length_bits:
            return None
        byte_start = network_start // 8
        byte_count = (network_start + signal.length - 1) // 8 - byte_start + 1
        shift = byte_count * 8 - (network_start - byte_start * 8) - signal.length
        big_endian = True
    if shift < 0:
        return None
    return _SignalPlan(
        signal=signal,
        choices=MappingProxyType({choice.value: choice.label for choice in signal.choices}),
        multiplexer_ids=(
            None if signal.multiplexer_ids is None else frozenset(signal.multiplexer_ids)
        ),
        big_endian=big_endian,
        byte_start=byte_start,
        byte_count=byte_count,
        shift=shift,
        mask=(1 << signal.length) - 1,
        sign_bit=(1 << (signal.length - 1)) if signal.is_signed else 0,
    )
