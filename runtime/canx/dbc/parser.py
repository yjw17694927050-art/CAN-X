"""The cantools adapter: the one place CAN-X is allowed to know about the DBC engine.

SPEC §1 fixes ``cantools`` as the DBC engine. That decision is honoured *behind
this boundary and nowhere else*. Nothing above this module — not the query
engine, not the project layer, not the future HTTP API — may import, accept or
return a ``cantools.database.Database``, ``cantools.database.can.Message``,
``cantools.database.can.Signal`` or ``NamedSignalValue``.

Two directions are fixed here, permanently:

```text
DBC text            ->  load_string(database_format="dbc")  ->  cantools objects
cantools objects    ->  _to_* conversion                    ->  canx.dbc.model
```

The second leg converts eagerly and completely: :meth:`CantoolsDbcParser.parse_text`
returns a graph built only from :mod:`canx.dbc.model`, so the engine's own
objects become unreachable the moment the call returns.
"""

from __future__ import annotations

from typing import Any

import cantools
from cantools.database.errors import UnsupportedDatabaseFormatError

from canx.dbc.errors import DbcModelError, DbcParseError
from canx.dbc.model import (
    DbcByteOrder,
    DbcChoice,
    DbcDatabase,
    DbcMessage,
    DbcNode,
    DbcSignal,
)

#: The only DBC dialect CAN-X imports in V0.3-02.
_DBC_FORMAT = "dbc"

#: The engine's byte-order vocabulary, mapped onto the CAN-X one.
_BYTE_ORDER = {
    "little_endian": DbcByteOrder.LITTLE_ENDIAN,
    "big_endian": DbcByteOrder.BIG_ENDIAN,
}

#: Upper bound on a third-party diagnostic string we are willing to surface.
_MAX_REASON_LENGTH = 200


class CantoolsDbcParser:
    """Turn DBC text into a canonical :class:`DbcDatabase`.

    The parser is stateless and its only public entry point takes text, not a
    path. Reading bytes is the import service's job, so the same parser serves a
    file, an in-memory string and (later) a project asset or test fixture without
    ever opening a file itself.
    """

    def parse_text(self, text: str) -> DbcDatabase:
        """Parse DBC text into a canonical, engine-free database.

        Args:
            text: The already-decoded DBC document.

        Returns:
            A canonical database built only from :mod:`canx.dbc.model` types.

        Raises:
            DbcParseError: If the text is not a well-formed DBC document. The
                ``details`` carry a source position and never a slice of the
                document.
            DbcModelError: If the document is well-formed DBC but cannot satisfy
                a CAN-X canonical invariant.
        """
        if not isinstance(text, str):
            raise DbcParseError(
                "The DBC source must be text.",
                details={"received_type": type(text).__name__},
            )
        try:
            # `sort_signals=None` keeps the document's own signal order instead
            # of the engine's start-bit sort, and `strict=True` keeps the
            # engine's own DBC layout validation switched on.
            database: Any = cantools.database.load_string(
                text,
                database_format=_DBC_FORMAT,
                strict=True,
                sort_signals=None,
            )
        except UnsupportedDatabaseFormatError as error:
            raise _parse_failure(error) from error
        return _to_database(database)


def _parse_failure(error: UnsupportedDatabaseFormatError) -> DbcParseError:
    """Translate a third-party parse failure into a typed, leak-free error."""
    return DbcParseError(
        "The DBC source could not be parsed as a DBC document.",
        details=_parser_diagnostics(error.e_dbc),
    )


def _parser_diagnostics(inner: object) -> dict[str, object]:
    """Return only the safe, structured part of a third-party failure.

    A syntax error carries a source position, which is exactly the diagnostic an
    operator needs and contains no document content. Any other failure is
    summarised as a bounded, single-line reason.

    The engine's own ``str()`` is deliberately *not* used for a positioned
    syntax error: it embeds the offending source line verbatim, which would turn
    an error response into an echo of the caller's upload.
    """
    details: dict[str, object] = {"parser_error": type(inner).__name__}
    line = getattr(inner, "line", None)
    column = getattr(inner, "column", None)
    if _is_index(line) and _is_index(column):
        details["line"] = line
        details["column"] = column
        return details
    reason = _bounded_reason(inner)
    if reason is not None:
        details["reason"] = reason
    return details


def _is_index(value: object) -> bool:
    """Return whether ``value`` can be reported as a 1-based source position."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _bounded_reason(inner: object) -> str | None:
    """Return a sanitized, length-bounded one-line summary, or ``None``."""
    text = " ".join(str(inner).split())
    if not text:
        return None
    return text[:_MAX_REASON_LENGTH]


def _to_database(database: Any) -> DbcDatabase:
    """Convert the engine's database into the canonical one."""
    try:
        return DbcDatabase(
            messages=tuple(_to_message(message) for message in database.messages),
            nodes=tuple(_to_node(node) for node in database.nodes),
            version=_optional_str(database.version),
        )
    except ValueError as error:
        raise DbcModelError(
            "The imported DBC document contradicts a CAN-X domain invariant.",
            details={"violation": str(error)},
        ) from error


def _to_message(message: Any) -> DbcMessage:
    """Convert one engine message into a canonical message."""
    try:
        return DbcMessage(
            frame_id=int(message.frame_id),
            name=str(message.name),
            length=int(message.length),
            is_extended=bool(message.is_extended_frame),
            is_fd=bool(message.is_fd),
            senders=tuple(str(sender) for sender in message.senders),
            signals=tuple(_to_signal(signal) for signal in message.signals),
            comment=_optional_str(message.comment),
            cycle_time=_optional_int(message.cycle_time),
        )
    except ValueError as error:
        raise _invalid_model(
            f"message {message.name!r} cannot become a CAN-X message", error
        ) from error


def _to_signal(signal: Any) -> DbcSignal:
    """Convert one engine signal into a canonical signal."""
    byte_order = _BYTE_ORDER.get(signal.byte_order)
    if byte_order is None:
        raise DbcModelError(
            "The DBC engine reported a byte order CAN-X does not define.",
            details={"signal": str(signal.name), "byte_order": str(signal.byte_order)},
        )
    try:
        return DbcSignal(
            name=str(signal.name),
            start_bit=int(signal.start),
            length=int(signal.length),
            byte_order=byte_order,
            is_signed=bool(signal.is_signed),
            factor=signal.scale,
            offset=signal.offset,
            minimum=signal.minimum,
            maximum=signal.maximum,
            unit=_optional_str(signal.unit),
            receivers=tuple(str(receiver) for receiver in signal.receivers),
            choices=_to_choices(signal.choices),
            is_multiplexer=bool(signal.is_multiplexer),
            multiplexer_signal=_optional_str(signal.multiplexer_signal),
            multiplexer_ids=_optional_int_tuple(signal.multiplexer_ids),
            comment=_optional_str(signal.comment),
        )
    except ValueError as error:
        raise _invalid_model(
            f"signal {signal.name!r} cannot become a CAN-X signal", error
        ) from error


def _to_node(node: Any) -> DbcNode:
    """Convert one engine node into a canonical node."""
    try:
        return DbcNode(name=str(node.name), comment=_optional_str(node.comment))
    except ValueError as error:
        raise _invalid_model(f"node {node.name!r} cannot become a CAN-X node", error) from error


def _to_choices(choices: Any) -> tuple[DbcChoice, ...]:
    """Convert the engine's choice mapping into ordered canonical pairs.

    The engine hands back an insertion-ordered mapping keyed by raw value, so the
    document's own ``VAL_`` order is preserved rather than re-sorted.
    """
    if not choices:
        return ()
    return tuple(
        DbcChoice(value=int(value), label=str(named.name)) for value, named in choices.items()
    )


def _invalid_model(what: str, error: ValueError) -> DbcModelError:
    """Wrap a canonical invariant violation as a typed, engine-free error."""
    return DbcModelError(
        f"The imported DBC document has an invalid {what}.",
        details={"violation": str(error)},
    )


def _optional_str(value: object) -> str | None:
    """Return ``None`` or ``value`` rendered as a string."""
    return None if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    """Return ``None`` or ``value`` rendered as an integer."""
    return None if value is None else int(value)


def _optional_int_tuple(value: Any) -> tuple[int, ...] | None:
    """Return ``None`` or ``value`` rendered as a tuple of integers."""
    if value is None:
        return None
    return tuple(int(item) for item in value)
