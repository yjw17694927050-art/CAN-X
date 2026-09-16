"""Read-only DBC import: filesystem bytes -> decoded text -> parser -> canonical document.

The service owns the three things the canonical model and the parser deliberately
do not:

1. **provenance** — which file this database came from, what it hashed to, and
   how many bytes it was, so a later project layer or audit can tell whether the
   source changed;
2. **the encoding policy** — a DBC file has no self-describing encoding, so
   "whatever the operating system defaults to" is not a policy, it is a coin
   flip that silently mangles non-ASCII identifiers and comments;
3. **the read-only guarantee** — this increment imports a DBC and nothing else.
   It does not copy the source into a project, does not write ``project.db``,
   and does not register anything. Persistence is a later increment with its own
   transaction and lifecycle design.

The default encoding is ``utf-8-sig``: UTF-8, with a leading byte-order mark
tolerated and stripped. Decoding is **strict** — unlike the third-party engine's
own file loader, which opens DBC as ``cp1252`` with ``errors='replace'`` and
would rather invent a replacement character than report a problem. A caller with
a genuinely legacy file declares it explicitly via ``encoding=``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from canx.dbc.errors import (
    DbcDecodeError,
    DbcError,
    DbcFileNotFoundError,
    DbcReadError,
    DbcUnsupportedFormatError,
)
from canx.dbc.model import DbcDocument, DbcSource
from canx.dbc.parser import CantoolsDbcParser

#: The deterministic default codec for DBC text. See the module docstring.
DEFAULT_DBC_ENCODING = "utf-8-sig"

#: The only source extensions this increment will import.
SUPPORTED_DBC_EXTENSIONS = (".dbc",)


class DbcImportService:
    """Stateless, read-only DBC import entry point.

    Like the other CAN-X services the import service keeps nothing between
    calls: it validates the source, reads it once, decodes it, hands the text to
    the parser and returns. It holds no open file handle once a call returns.
    """

    def __init__(self, *, parser: CantoolsDbcParser | None = None) -> None:
        self._parser = CantoolsDbcParser() if parser is None else parser

    @property
    def parser(self) -> CantoolsDbcParser:
        """Return the DBC parser this service delegates the conversion to."""
        return self._parser

    def import_file(
        self, source: str | Path, *, encoding: str | None = None
    ) -> DbcDocument:
        """Import one DBC file into a canonical document plus its provenance.

        Args:
            source: Path to the DBC file to import. The file is only ever read.
            encoding: Codec to decode the file with. ``None`` selects
                :data:`DEFAULT_DBC_ENCODING`.

        Returns:
            A canonical document: the parsed database and the exact source it
            came from (name, resolved path, SHA-256, size, encoding).

        Raises:
            DbcFileNotFoundError: If ``source`` is not a regular readable file.
            DbcUnsupportedFormatError: If the source is not a ``.dbc`` file.
            DbcReadError: If the file exists but cannot be read; recoverable,
                because the same import may succeed once the environment settles.
            DbcDecodeError: If the bytes are not text under the declared
                encoding, or the encoding is not a known codec.
            DbcParseError: If the text is not a well-formed DBC document.
            DbcModelError: If the document cannot satisfy a CAN-X invariant.
        """
        path = Path(source)
        codec = _resolve_encoding(encoding)
        _require_regular_file(path)
        _require_supported_extension(path)
        raw = _read_bytes(path)
        text = _decode(raw, codec=codec, source_name=path.name)
        try:
            database = self._parser.parse_text(text)
        except DbcError as error:
            # The parser speaks about text, not about files. A caller importing a
            # batch of DBCs needs to know which one failed, so the source name is
            # attached here, where it is known, rather than guessed downstream.
            raise _with_source(error, source_name=path.name) from error
        return DbcDocument(database=database, source=_source(path, raw, codec))


def _resolve_encoding(encoding: str | None) -> str:
    """Return the codec to use, defaulting to the declared policy."""
    if encoding is None:
        return DEFAULT_DBC_ENCODING
    if not isinstance(encoding, str) or not encoding.strip():
        raise DbcDecodeError(
            "The declared DBC encoding must be a non-blank codec name.",
            details={"encoding": repr(encoding)},
        )
    return encoding


def _require_regular_file(path: Path) -> None:
    """Raise unless ``path`` is an existing regular file."""
    if not path.is_file():
        raise DbcFileNotFoundError(
            "The DBC source is not a regular readable file.",
            details={"source_name": path.name},
        )


def _require_supported_extension(path: Path) -> None:
    """Raise unless ``path`` names a DBC document."""
    extension = path.suffix.lower()
    if extension not in SUPPORTED_DBC_EXTENSIONS:
        raise DbcUnsupportedFormatError(
            "The DBC source must be a .dbc file.",
            details={"source_name": path.name, "extension": extension},
        )


def _read_bytes(path: Path) -> bytes:
    """Read the source bytes, translating any I/O failure into a typed error."""
    try:
        return path.read_bytes()
    except OSError as error:
        raise DbcReadError(
            "The DBC source could not be read.",
            details={"source_name": path.name, "reason": type(error).__name__},
        ) from error


def _decode(raw: bytes, *, codec: str, source_name: str) -> str:
    """Decode the source strictly, translating any failure into a typed error.

    ``errors='replace'`` is deliberately absent: a DBC whose bytes are not valid
    text under the declared encoding is reported, never quietly repaired.
    """
    try:
        return raw.decode(codec)
    except UnicodeDecodeError as error:
        raise DbcDecodeError(
            "The DBC source is not valid text under the declared encoding.",
            details={
                "source_name": source_name,
                "encoding": codec,
                "byte_offset": error.start,
            },
        ) from error
    except LookupError as error:
        raise DbcDecodeError(
            "The declared DBC encoding is not a known codec.",
            details={"source_name": source_name, "encoding": codec},
        ) from error


def _source(path: Path, raw: bytes, encoding: str) -> DbcSource:
    """Build the provenance record for an import that already succeeded."""
    return DbcSource(
        name=path.name,
        path=_resolved_path(path),
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        encoding=encoding,
    )


def _resolved_path(path: Path) -> str:
    """Return an absolute path without failing on a path the OS dislikes."""
    try:
        return str(path.resolve())
    except OSError:
        return str(path.absolute())


def _with_source(error: DbcError, *, source_name: str) -> DbcError:
    """Return the same failure, re-raised with its source file named.

    The class, code, message and recoverability are preserved exactly; only the
    provenance field is added, and the original failure is kept as ``__cause__``
    by the caller so the engine-level diagnosis is not lost.
    """
    details: dict[str, object] = {"source_name": source_name}
    details.update(error.details)
    return type(error)(error.message, code=error.code, details=details)
