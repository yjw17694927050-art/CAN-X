"""Canonical, UI-independent DBC domain model.

Nothing in this module imports ``cantools``. A ``cantools.database.Database``, a
``cantools.database.can.Message``, a ``cantools.database.can.Signal`` or a
``NamedSignalValue`` must never reach CAN-X code: the parser adapter in
:mod:`canx.dbc.parser` is the only module that knows about the third-party DBC
engine, and it translates in one direction only.

This module owns exactly one thing: what a DBC document *is* once it has been
imported into CAN-X. It does not read files, it does not decode frames, and it
does not persist anything.

The models are immutable, ``slots`` dataclasses whose every sequence is a
``tuple``. A caller therefore cannot mutate canonical state in place, and a
repeated parse of the same text can be compared for equality directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

#: The largest arbitration id an 11-bit standard CAN frame can carry.
MAX_STANDARD_FRAME_ID = 0x7FF

#: The largest arbitration id a 29-bit extended CAN frame can carry.
MAX_EXTENDED_FRAME_ID = 0x1FFFFFFF

#: The largest payload a Classic CAN message can declare.
MAX_CLASSIC_PAYLOAD_LENGTH = 8

#: The largest payload a CAN FD message can declare.
MAX_FD_PAYLOAD_LENGTH = 64

#: The number of hexadecimal characters in a SHA-256 digest.
SHA256_HEX_LENGTH = 64


class DbcByteOrder(StrEnum):
    """Byte order of a DBC signal, named in CAN-X terms.

    The DBC file format speaks of "Intel" and "Motorola"; the third-party engine
    speaks of ``little_endian`` and ``big_endian``. Neither vocabulary is the
    CAN-X runtime vocabulary, so the parser adapter maps into this enum and the
    rest of the runtime only ever sees these two members.
    """

    LITTLE_ENDIAN = "little_endian"
    BIG_ENDIAN = "big_endian"


@dataclass(frozen=True, slots=True)
class DbcChoice:
    """One named value a DBC signal can take, as declared by the source file."""

    value: int
    label: str

    def __post_init__(self) -> None:
        """Reject a choice that cannot name a concrete raw value."""
        if not isinstance(self.value, int) or isinstance(self.value, bool):
            raise ValueError("choice value must be an integer")
        if not isinstance(self.label, str):
            raise ValueError("choice label must be a string")


@dataclass(frozen=True, slots=True)
class DbcSignal:
    """One immutable DBC signal definition.

    ``start_bit`` is carried exactly as the DBC/cantools signal definition
    expresses it. CAN-X deliberately does not re-derive a Motorola bit layout
    here: signal decoding is a later increment, and inventing a second bit
    numbering now would be a silent correctness risk.

    ``factor`` and ``offset`` express ``physical = raw * factor + offset``. This
    model stores that definition; it does not apply it.
    """

    name: str
    start_bit: int
    length: int
    byte_order: DbcByteOrder
    is_signed: bool
    factor: float
    offset: float
    minimum: float | None
    maximum: float | None
    unit: str | None
    receivers: tuple[str, ...]
    choices: tuple[DbcChoice, ...]
    is_multiplexer: bool
    multiplexer_signal: str | None
    multiplexer_ids: tuple[int, ...] | None
    comment: str | None

    def __post_init__(self) -> None:
        """Reject a signal that cannot describe a usable raw-to-physical mapping."""
        _require_name(self.name, field="name")
        _require_integer(self.start_bit, field="start_bit", minimum=0)
        _require_integer(self.length, field="length", minimum=1)
        if not isinstance(self.byte_order, DbcByteOrder):
            raise ValueError("byte_order must be a DbcByteOrder")
        _require_bool(self.is_signed, field="is_signed")
        object.__setattr__(self, "factor", _require_finite(self.factor, field="factor"))
        object.__setattr__(self, "offset", _require_finite(self.offset, field="offset"))
        minimum = _require_optional_finite(self.minimum, field="minimum")
        maximum = _require_optional_finite(self.maximum, field="maximum")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("signal minimum must not be greater than its maximum")
        if self.unit is not None and not isinstance(self.unit, str):
            raise ValueError("unit must be a string or None")
        object.__setattr__(
            self, "receivers", _require_name_tuple(self.receivers, field="receivers")
        )
        object.__setattr__(self, "choices", _require_choices(self.choices))
        _require_bool(self.is_multiplexer, field="is_multiplexer")
        _require_optional_name(self.multiplexer_signal, field="multiplexer_signal")
        multiplexer_ids = _require_optional_multiplexer_ids(self.multiplexer_ids)
        object.__setattr__(self, "multiplexer_ids", multiplexer_ids)
        _require_optionally_multiplexed(self)
        if self.comment is not None and not isinstance(self.comment, str):
            raise ValueError("comment must be a string or None")


@dataclass(frozen=True, slots=True)
class DbcMessage:
    """One immutable DBC message definition.

    ``frame_id`` is the ordinary CAN arbitration id together with
    ``is_extended``: the pair — never the id alone — decides whether the message
    addresses the 11-bit or the 29-bit space. An extended message with id
    ``0x123`` and a standard message with id ``0x123`` are two different
    messages, and the canonical model keeps them apart.
    """

    frame_id: int
    name: str
    length: int
    is_extended: bool
    is_fd: bool
    senders: tuple[str, ...]
    signals: tuple[DbcSignal, ...]
    comment: str | None
    cycle_time: int | None

    def __post_init__(self) -> None:
        """Reject a message that cannot address a real CAN frame."""
        _require_name(self.name, field="name")
        _require_bool(self.is_extended, field="is_extended")
        _require_bool(self.is_fd, field="is_fd")
        kind = "extended" if self.is_extended else "standard"
        _require_integer(
            self.frame_id,
            field="frame_id",
            minimum=0,
            maximum=MAX_EXTENDED_FRAME_ID if self.is_extended else MAX_STANDARD_FRAME_ID,
            mismatch=f"{kind} frame_id is out of range",
        )
        _require_integer(
            self.length, field="length", minimum=0, maximum=MAX_FD_PAYLOAD_LENGTH
        )
        if not self.is_fd and self.length > MAX_CLASSIC_PAYLOAD_LENGTH:
            raise ValueError(
                "Classic CAN cannot carry more than"
                f" {MAX_CLASSIC_PAYLOAD_LENGTH} payload bytes"
            )
        object.__setattr__(self, "senders", _require_name_tuple(self.senders, field="senders"))
        object.__setattr__(self, "signals", _require_signals(self.signals))
        if self.comment is not None and not isinstance(self.comment, str):
            raise ValueError("comment must be a string or None")
        if self.cycle_time is not None:
            _require_integer(self.cycle_time, field="cycle_time", minimum=1)


@dataclass(frozen=True, slots=True)
class DbcNode:
    """One immutable DBC network node (an ECU in the file's own vocabulary)."""

    name: str
    comment: str | None

    def __post_init__(self) -> None:
        """Reject a node that cannot be named."""
        _require_name(self.name, field="name")
        if self.comment is not None and not isinstance(self.comment, str):
            raise ValueError("comment must be a string or None")


@dataclass(frozen=True, slots=True)
class DbcDatabase:
    """The canonical content of one imported DBC document.

    Source provenance deliberately lives on :class:`DbcDocument`, not here: the
    content of a database is the same whether it arrived from a file, a string
    or a future project asset, and the canonical content must not be bound to a
    filesystem.

    ``messages`` keeps the order the source file declared. That order is a
    stable, reviewed property of the DBC document, so the model preserves it
    rather than imposing an alphabetical sort that would lose engineering
    meaning.
    """

    messages: tuple[DbcMessage, ...]
    nodes: tuple[DbcNode, ...]
    version: str | None

    def __post_init__(self) -> None:
        """Reject a database whose keys would be ambiguous."""
        if not isinstance(self.messages, tuple) or not all(
            isinstance(message, DbcMessage) for message in self.messages
        ):
            raise ValueError("messages must be a tuple of DbcMessage values")
        _require_unique(
            (message.name for message in self.messages),
            error="two messages must not share a name: message names must be unique",
        )
        _require_unique(
            ((message.frame_id, message.is_extended) for message in self.messages),
            error=(
                "two messages must not share a frame_id and frame kind:"
                " the identifier pair must be unique"
            ),
        )
        if not isinstance(self.nodes, tuple) or not all(
            isinstance(node, DbcNode) for node in self.nodes
        ):
            raise ValueError("nodes must be a tuple of DbcNode values")
        _require_unique(
            (node.name for node in self.nodes),
            error="two nodes must not share a name: node names must be unique",
        )
        if self.version is not None and not isinstance(self.version, str):
            raise ValueError("version must be a string or None")


@dataclass(frozen=True, slots=True)
class DbcSource:
    """Where an imported DBC database came from, in a filesystem-free shape.

    Kept separate from :class:`DbcDatabase` so provenance can be recorded,
    compared and later persisted without the canonical content ever depending on
    an operating-system path API.
    """

    name: str
    path: str | None
    sha256: str
    size_bytes: int
    encoding: str

    def __post_init__(self) -> None:
        """Reject provenance that could not identify a source unambiguously."""
        _require_name(self.name, field="name")
        if self.path is not None and (not isinstance(self.path, str) or not self.path):
            raise ValueError("path must be a non-empty string or None")
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != SHA256_HEX_LENGTH
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValueError("sha256 must be a lowercase hexadecimal SHA-256 digest")
        _require_integer(self.size_bytes, field="size_bytes", minimum=0)
        _require_name(self.encoding, field="encoding")


@dataclass(frozen=True, slots=True)
class DbcDocument:
    """A canonical DBC database together with the provenance of its source."""

    database: DbcDatabase
    source: DbcSource

    def __post_init__(self) -> None:
        """Reject a document that is not a canonical pair."""
        if not isinstance(self.database, DbcDatabase):
            raise ValueError("database must be a DbcDatabase")
        if not isinstance(self.source, DbcSource):
            raise ValueError("source must be a DbcSource")


def _require_name(value: object, *, field: str) -> str:
    """Return ``value`` when it is a non-blank name, else raise."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value


def _require_optional_name(value: object, *, field: str) -> str | None:
    """Return ``value`` when it is ``None`` or a non-blank name, else raise."""
    if value is None:
        return None
    return _require_name(value, field=field)


def _require_bool(value: object, *, field: str) -> bool:
    """Return ``value`` when it is a real boolean, else raise."""
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _require_integer(
    value: object,
    *,
    field: str,
    minimum: int | None = None,
    maximum: int | None = None,
    mismatch: str | None = None,
) -> int:
    """Return ``value`` when it is an integer inside the requested bounds.

    ``mismatch`` overrides the message for a bounds failure so a caller can name
    the fact that actually went wrong (for example "extended frame_id") instead
    of restating the raw bound.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(mismatch or f"{field} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(mismatch or f"{field} must be at most {maximum}")
    return value


def _require_finite(value: object, *, field: str) -> float:
    """Return ``value`` as a finite float, else raise.

    Integers that cannot be represented as a float (an implausible but reachable
    literal in a hand-written DBC) are reported as a non-finite number rather
    than escaping as an ``OverflowError``.
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


def _require_optional_finite(value: object, *, field: str) -> float | None:
    """Return ``None`` or ``value`` as a finite float, else raise."""
    if value is None:
        return None
    return _require_finite(value, field=field)


def _require_name_tuple(value: object, *, field: str) -> tuple[str, ...]:
    """Return ``value`` when it is a tuple of non-blank names, else raise."""
    if not isinstance(value, tuple):
        raise ValueError(f"{field} must be a tuple of non-blank strings")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"each of {field} must be a non-blank string")
    return value


def _require_choices(value: object) -> tuple[DbcChoice, ...]:
    """Return ``value`` when it is a tuple of choices with distinct values."""
    if not isinstance(value, tuple) or not all(
        isinstance(choice, DbcChoice) for choice in value
    ):
        raise ValueError("choices must be a tuple of DbcChoice values")
    _require_unique(
        (choice.value for choice in value),
        error="two choices must not share a value: choices must be unique by value",
    )
    return value


def _require_signals(value: object) -> tuple[DbcSignal, ...]:
    """Return ``value`` when it is a tuple of signals with distinct names."""
    if not isinstance(value, tuple) or not all(
        isinstance(signal, DbcSignal) for signal in value
    ):
        raise ValueError("signals must be a tuple of DbcSignal values")
    _require_unique(
        (signal.name for signal in value),
        error="two signals must not share a name: signal names must be unique",
    )
    return value


def _require_optional_multiplexer_ids(value: object) -> tuple[int, ...] | None:
    """Return ``None`` or a non-empty tuple of distinct non-negative ids."""
    if value is None:
        return None
    if not isinstance(value, tuple) or not value:
        raise ValueError("multiplexer_ids must be a non-empty tuple of integers or None")
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise ValueError(
                "each multiplexer id must be a non-negative integer"
            )
    _require_unique(
        value,
        error="multiplexer_ids must not repeat a value",
    )
    return value


def _require_optionally_multiplexed(signal: DbcSignal) -> None:
    """Reject a signal whose multiplexing metadata contradicts itself.

    Exactly three shapes are legal, and nothing else:

    * a multiplexer switch — ``is_multiplexer`` and no parent;
    * a multiplexed signal — a parent name *and* at least one multiplexer id;
    * a plain signal — no parent and no ids.

    Half a relation is the dangerous case: a parent without ids would decode as
    "belongs to this multiplexer, for any value", which is not what the file
    said.
    """
    has_parent = signal.multiplexer_signal is not None
    has_ids = signal.multiplexer_ids is not None
    if signal.is_multiplexer:
        if has_parent or has_ids:
            raise ValueError(
                "a multiplexer signal must not declare a parent multiplexer"
                " or multiplexer ids"
            )
        return
    if has_parent != has_ids:
        raise ValueError(
            "multiplexer_signal and multiplexer_ids must be given together,"
            " or both left unset"
        )


def _require_unique(values: object, *, error: str) -> None:
    """Raise when an iterable repeats a value."""
    seen: set[object] = set()
    for value in values:  # type: ignore[union-attr]
        if value in seen:
            raise ValueError(error)
        seen.add(value)
