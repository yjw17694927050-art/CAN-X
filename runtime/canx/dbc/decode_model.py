"""Immutable results of decoding canonical frames against a canonical database.

This module owns exactly one thing: what a decode *produced*. It does not read a
database, does not extract bits and does not know what ``cantools`` is. The
decoder in :mod:`canx.dbc.decode` builds these values; everything above it reads
them.

Three decisions are deliberate:

* **the raw frame is kept, not summarised.** A :class:`DecodedFrame` carries the
  canonical :class:`~canx.domain.frame.Frame` it came from, so ``sequence``,
  ``channel_id``, ``direction``, every timestamp field, ``timestamp_quality``,
  ``is_fd``, ``bitrate_switch`` and ``flags`` survive the decode. Returning
  ``dict[str, float]`` would throw that provenance away and force every later
  timeline, Trace view or Agent tool to reinvent it;
* **a signal keeps its number.** A named choice is *additional* semantics, never
  a replacement: ``raw = 2`` stays visible next to ``choice_label = "Error"``, so
  raw/decoded comparison and plotting stay possible;
* **a batch never drops a frame.** A :class:`DecodedFrameBatch` holds exactly one
  :class:`DecodedFrameOutcome` per input frame, in the input order. A frame whose
  message is unknown or whose payload is short is an outcome with a failure, not
  a missing row — otherwise no caller could tell "this frame was skipped" from
  "this frame never existed".

Every model is a frozen, ``slots`` dataclass whose sequences are tuples and whose
only mapping (a failure's ``details``) is a read-only view. None of them can be
mutated in place, so a decoded result is safe to hand to a worker, cache, or
compare for equality.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import ClassVar

from canx.dbc.errors import DbcError
from canx.domain.frame import Frame


@dataclass(frozen=True, slots=True)
class DecodedSignal:
    """One decoded signal value, with its raw value kept alongside.

    Args:
        name: The signal's DBC name.
        raw_value: The integer the signal's bits decoded to, **before** scaling
            and after two's-complement interpretation when the signal is signed.
        physical_value: ``raw_value * factor + offset``, always a finite float.
        choice_label: The ``VAL_`` label for ``raw_value``, or ``None`` when the
            document declares no choice for it. The raw value is never replaced
            by the label.
        unit: The declared unit, or ``None``.
    """

    name: str
    raw_value: int
    physical_value: float
    choice_label: str | None
    unit: str | None

    def __post_init__(self) -> None:
        """Reject a decoded value that could not have come from a real signal."""
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-blank string")
        if not isinstance(self.raw_value, int) or isinstance(self.raw_value, bool):
            raise ValueError("raw_value must be an integer")
        object.__setattr__(
            self, "physical_value", _require_finite(self.physical_value, field="physical_value")
        )
        if self.choice_label is not None and not isinstance(self.choice_label, str):
            raise ValueError("choice_label must be a string or None")
        if self.unit is not None and not isinstance(self.unit, str):
            raise ValueError("unit must be a string or None")


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    """One canonical frame together with the signals its message defines.

    ``signals`` holds only the signals that are *active* for this frame, in the
    order the DBC document declared them. For a multiplexed message that means
    the common signals, the multiplexer switch itself, and the signals whose
    multiplexer ids contain the switch's raw value — never the inactive ones.

    ``signals`` may be empty: a DBC message with no signals is legal, and a
    decoded frame is then simply a frame that is known to the database.
    """

    frame: Frame
    message_name: str
    signals: tuple[DecodedSignal, ...]

    def __post_init__(self) -> None:
        """Reject a decoded frame that could not have come from a real decode."""
        if not isinstance(self.frame, Frame):
            raise ValueError("frame must be a canonical Frame")
        if not isinstance(self.message_name, str) or not self.message_name.strip():
            raise ValueError("message_name must be a non-blank string")
        if not isinstance(self.signals, tuple) or not all(
            isinstance(signal, DecodedSignal) for signal in self.signals
        ):
            raise ValueError("signals must be a tuple of DecodedSignal values")
        names = [signal.name for signal in self.signals]
        if len(set(names)) != len(names):
            raise ValueError("two decoded signals must not share a name")

    def signal(self, name: str) -> DecodedSignal | None:
        """Return one decoded signal by name, or ``None`` when it is inactive."""
        return next((signal for signal in self.signals if signal.name == name), None)


@dataclass(frozen=True, slots=True)
class DbcDecodeFailure:
    """An immutable snapshot of one typed decode failure.

    A long-lived domain result must not hold a live exception object, so the five
    structured fields of :class:`~canx.dbc.errors.DbcError` are copied into this
    value. ``details`` is exposed as a read-only mapping.
    """

    code: str
    message: str
    recoverable: bool
    source: str
    details: Mapping[str, object]

    def __post_init__(self) -> None:
        """Reject a snapshot that does not carry the shared failure envelope."""
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("code must be a non-blank string")
        if not isinstance(self.message, str):
            raise ValueError("message must be a string")
        if not isinstance(self.recoverable, bool):
            raise ValueError("recoverable must be a boolean")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-blank string")
        if not isinstance(self.details, Mapping):
            raise ValueError("details must be a mapping")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    @classmethod
    def from_error(cls, error: DbcError) -> DbcDecodeFailure:
        """Copy one typed DBC failure into a durable, read-only snapshot."""
        return cls(
            code=error.code,
            message=error.message,
            recoverable=error.recoverable,
            source=error.source,
            details=error.details,
        )


@dataclass(frozen=True, slots=True)
class DecodedFrameOutcome:
    """What happened to exactly one frame: a decoded result *or* a failure.

    The two are mutually exclusive by construction — building one with both or
    with neither is refused — so a caller can branch on a single fact instead of
    guessing which field to trust.
    """

    frame: Frame
    decoded: DecodedFrame | None
    failure: DbcDecodeFailure | None

    def __post_init__(self) -> None:
        """Reject an outcome that is not exactly one of a result or a failure."""
        if not isinstance(self.frame, Frame):
            raise ValueError("frame must be a canonical Frame")
        if (self.decoded is None) == (self.failure is None):
            raise ValueError("an outcome must carry exactly one of a result or a failure")
        if self.decoded is not None:
            if not isinstance(self.decoded, DecodedFrame):
                raise ValueError("decoded must be a DecodedFrame or None")
            if self.decoded.frame != self.frame:
                raise ValueError("a decoded result must belong to the outcome's frame")
        if self.failure is not None and not isinstance(self.failure, DbcDecodeFailure):
            raise ValueError("failure must be a DbcDecodeFailure or None")

    @classmethod
    def succeeded(cls, frame: Frame, decoded: DecodedFrame) -> DecodedFrameOutcome:
        """Build the outcome of a frame that decoded successfully."""
        return cls(frame=frame, decoded=decoded, failure=None)

    @classmethod
    def failed(cls, frame: Frame, failure: DbcDecodeFailure) -> DecodedFrameOutcome:
        """Build the outcome of a frame that could not be decoded."""
        return cls(frame=frame, decoded=None, failure=failure)

    @property
    def ok(self) -> bool:
        """Return whether this frame decoded successfully."""
        return self.failure is None


@dataclass(frozen=True, slots=True)
class DecodedFrameBatch:
    """The one-to-one decode outcomes of a :class:`~canx.domain.batch.FrameBatch`.

    The binding is structural, not advisory: ``outcomes`` must be non-empty,
    ordered by contiguous sequence, and agree with ``first_sequence``,
    ``last_sequence`` and ``frame_count``. A batch that decoded successfully is
    therefore impossible to distinguish from its input in length or ordering,
    which is what makes "no frame was silently dropped" a checkable property.

    This is a new domain schema, so it carries its own version rather than
    reusing the raw batch's ``schema_version`` — the two describe different
    things and must be free to move independently.
    """

    CURRENT_SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int
    stream_id: str
    first_sequence: int
    last_sequence: int
    frame_count: int
    outcomes: tuple[DecodedFrameOutcome, ...]

    def __post_init__(self) -> None:
        """Reject a batch whose bookkeeping does not describe its outcomes."""
        if self.schema_version != self.CURRENT_SCHEMA_VERSION:
            raise ValueError("unsupported DecodedFrameBatch schema_version")
        if not isinstance(self.stream_id, str) or not self.stream_id.strip():
            raise ValueError("stream_id must be a non-blank string")
        if not isinstance(self.outcomes, tuple) or not all(
            isinstance(outcome, DecodedFrameOutcome) for outcome in self.outcomes
        ):
            raise ValueError("outcomes must be a tuple of DecodedFrameOutcome values")
        if not self.outcomes:
            raise ValueError("DecodedFrameBatch outcomes must be non-empty")
        sequences = tuple(outcome.frame.sequence for outcome in self.outcomes)
        expected = tuple(range(sequences[0], sequences[0] + len(sequences)))
        if sequences != expected:
            raise ValueError("DecodedFrameBatch sequences must be contiguous")
        if self.first_sequence != sequences[0] or self.last_sequence != sequences[-1]:
            raise ValueError("DecodedFrameBatch sequence bounds do not match outcomes")
        if self.frame_count != len(self.outcomes):
            raise ValueError("DecodedFrameBatch frame_count does not match outcomes")

    @classmethod
    def create(
        cls, *, stream_id: str, outcomes: Sequence[DecodedFrameOutcome]
    ) -> DecodedFrameBatch:
        """Create a validated batch and derive all redundant metadata."""
        frozen = tuple(outcomes)
        if not frozen:
            raise ValueError("DecodedFrameBatch outcomes must be non-empty")
        return cls(
            schema_version=cls.CURRENT_SCHEMA_VERSION,
            stream_id=stream_id,
            first_sequence=frozen[0].frame.sequence,
            last_sequence=frozen[-1].frame.sequence,
            frame_count=len(frozen),
            outcomes=frozen,
        )


def _require_finite(value: object, *, field: str) -> float:
    """Return ``value`` as a finite float, else raise.

    Mirrors the canonical DBC model's helper: an integer is normalised to a
    float, and a value that cannot be represented as a finite float is refused
    rather than allowed to become ``nan`` or ``inf`` downstream.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{field} must be a finite number") from error
    if not isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return number
