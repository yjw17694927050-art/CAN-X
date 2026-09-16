"""Typed failure contract for the CAN-X DBC domain.

Callers never see a bare ``FileNotFoundError``, ``UnicodeDecodeError``, an
``OSError``, a ``ValueError``, a ``textparser`` error or a ``cantools``
exception. Every failure that crosses the DBC boundary is a :class:`DbcError`
carrying the structured fields required by SPEC §38: ``code``, the human
``message``, ``details``, ``recoverable`` and ``source``.

The hierarchy mirrors :mod:`canx.project.errors`, :mod:`canx.data.errors` and
:mod:`canx.query.errors`, so all four domains read the same way at a call site.

Which failures are recoverable is a deliberate judgement, not a default:

* a *read* failure is recoverable — a locked or temporarily unreadable file may
  become readable, and the identical import can then succeed;
* a *decode*, *parse* or *model* failure is not — the bytes will not become
  different bytes by trying again.

The project-asset failures follow the same rule. A *storage* or *registry*
failure is recoverable, because the environment may settle. A *validation* or
*integrity* failure is not: a registry row that contradicts the file it points
at will not become consistent by retrying, and CAN-X never repairs it silently.
A *source changed* failure is recoverable, because re-running the import
validates the file's current content from scratch.
"""

from __future__ import annotations


class DbcError(Exception):
    """Base class for every diagnosable DBC-domain failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.error",
        details: dict[str, object] | None = None,
        recoverable: bool = False,
        source: str = "dbc",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details: dict[str, object] = {} if details is None else dict(details)
        self.recoverable = recoverable
        self.source = source


class DbcFileNotFoundError(DbcError):
    """Raised when the requested DBC source is not a readable regular file."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.file_not_found",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DbcReadError(DbcError):
    """Raised when a DBC source exists but could not be read.

    Recoverable on purpose: the cause is the environment (a lock, a transient
    device error), not the request, so the same import may succeed later.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.read_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=True)


class DbcUnsupportedFormatError(DbcError):
    """Raised when a source is not a DBC document CAN-X can import."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.unsupported_format",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DbcDecodeError(DbcError):
    """Raised when DBC bytes could not be decoded under the declared encoding."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.decode_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DbcParseError(DbcError):
    """Raised when DBC text does not describe a well-formed DBC document."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.parse_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DbcModelError(DbcError):
    """Raised when a parsed document cannot become a canonical CAN-X database.

    The DBC grammar accepted it, but the result contradicts a CAN-X domain
    invariant — a reversed signal range, a repeated message name, a repeated
    ``(frame_id, is_extended)`` pair. CAN-X never hands such a document on.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.invalid_model",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DbcAssetValidationError(DbcError):
    """Raised when a record cannot describe a valid project-owned DBC asset.

    Raised by the asset model itself, so a hand-edited registry row or a
    malformed identifier is refused at the boundary rather than turning into a
    confusing path or hash failure later.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.invalid_asset",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DbcAssetNotFoundError(DbcError):
    """Raised when an asset id is not registered in this project."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.asset_not_found",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DbcAssetStorageError(DbcError):
    """Raised when the project-owned copy of a DBC file could not be written.

    Recoverable on purpose: the cause is the environment (a full disk, a locked
    or read-only directory), not the request, so the identical import may succeed
    once the environment settles.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.asset_storage_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=True)


class DbcAssetRegistryError(DbcError):
    """Raised when a registered asset row could not be committed or read.

    Recoverable on purpose, for the same reason as
    :class:`DbcAssetStorageError`: a locked database is an environment problem,
    and the same operation may succeed later.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.asset_registry_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=True)


class DbcAssetIntegrityError(DbcError):
    """Raised when a registered project asset contradicts its registry row.

    Never repaired automatically: CAN-X does not re-hash, re-register or
    substitute another file for an asset whose bytes, size, path or owning
    project no longer match what was registered.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.asset_integrity_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DbcSourceChangedError(DbcError):
    """Raised when the source file changed between validation and persistence.

    The import validated one set of bytes and would otherwise persist another.
    Recoverable on purpose: re-running the import validates the file's current
    content from scratch, so the caller has a real path forward.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.source_changed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=True)


# --- frame decoding ----------------------------------------------------------
#
# ``DbcDecodeError`` above already means one specific thing — DBC *text* could
# not be decoded under the declared encoding — and it keeps that meaning. The
# failures below are a different axis entirely: they are raised when a canonical
# Frame is decoded against a canonical DbcDatabase. Reusing the import code for
# them would make "the file's bytes are not valid text" and "this frame is not
# in this database" indistinguishable at a call site.
#
# Recoverability is judged the same way as everywhere else in this domain: a
# decode failure is deterministic. The same Frame against the same DbcDatabase
# produces the same outcome every time, so retrying changes nothing and every
# failure here is ``recoverable = False``.

class DbcMessageNotFoundError(DbcError):
    """Raised when a frame's identifier pair is not defined in the database.

    The strict per-frame entry point reports this instead of returning an empty
    result: a caller that asked "what does this frame mean?" must be told that
    the question has no answer, not handed a plausible empty answer.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.message_not_found",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DbcFrameTypeMismatchError(DbcError):
    """Raised when a frame and its message disagree about being CAN FD.

    Matching is strict on purpose: a Classic definition is never silently
    applied to an FD frame (or the reverse), because the two say different
    things about the payload that follows the identifier.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.frame_type_mismatch",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DbcPayloadTooShortError(DbcError):
    """Raised when a frame carries fewer payload bytes than its message defines.

    Truncated decoding is not supported in V0.3-04: a short payload is reported,
    never partially interpreted. A payload *longer* than the definition is
    accepted, because a CAN FD payload bucket may legitimately exceed the
    engineering length.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.payload_too_short",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DbcDecodeUnsupportedError(DbcError):
    """Raised when a canonical definition cannot be decoded unambiguously.

    Two shapes reach this: a definition whose signals cannot be extracted
    without reading past the message payload, and a multiplexing topology the
    canonical model cannot express without guessing (more than one multiplexer
    switch, or a multiplexed signal whose parent cannot be resolved). CAN-X
    refuses such a definition rather than returning half-correct signals.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.decode_unsupported",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DbcSignalDecodeError(DbcError):
    """Raised when one signal's bits could not become a decoded value.

    A last-resort translation at the decoder boundary: a definition that passed
    compilation should never reach it, so reaching it means the arithmetic or
    the bit extraction genuinely failed for this payload (for example a scaling
    step that overflowed to a non-finite number).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "dbc.signal_decode_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)
