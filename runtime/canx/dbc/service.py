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

The service has two kinds of entry point, and the difference between them is what
they are allowed to assume about the caller:

* :meth:`DbcImportService.import_file` and
  :meth:`DbcImportService.import_bytes` are the **external import** boundary. A
  caller is making a claim about content CAN-X has never seen, so the *identity*
  of that content — the extension of a file, the shape of a submitted file name —
  is validated here. Neither reads anything it was not handed.
* :meth:`DbcImportService.load_bytes` serves bytes whose identity a durable record
  already established: a project-owned asset, re-read and hash-verified against
  its registry row. It reports the name that record holds and re-litigates
  nothing.

Keeping the second path permissive is deliberate. A registry row written by an
older release, or by a caller that reached the domain directly, must still load;
tightening ``load_bytes`` to the external-import rule would turn a bookkeeping
choice into a broken project.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

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

#: Characters that may never appear in the name an external import claims for its
#: content: path separators, the Windows drive separator, and control characters.
#: A name carrying one of them is describing a location, not a file.
FORBIDDEN_SOURCE_NAME_CHARACTERS = frozenset("/\\:") | frozenset(
    chr(code) for code in (*range(0x20), 0x7F)
)


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
        return self._parse_bytes(
            raw, codec=codec, source_name=path.name, path=_resolved_path(path)
        )

    def load_bytes(
        self, raw: bytes, *, source_name: str, encoding: str | None = None
    ) -> DbcDocument:
        """Parse DBC bytes a caller already holds into a canonical document.

        The sibling of :meth:`import_file`, for a caller that owns the bytes and
        has already established they are the right ones — a project asset whose
        file was just verified against its registry hash. The encoding policy and
        the parser are exactly the same; only the filesystem step is missing, and
        the returned provenance therefore carries **no path**: a project-owned
        copy is not bound to the external location it was imported from.

        Args:
            raw: The exact DBC bytes to parse.
            source_name: The name this document is reported under.
            encoding: Codec to decode the bytes with. ``None`` selects
                :data:`DEFAULT_DBC_ENCODING`.

        Raises:
            DbcDecodeError: If the bytes are not text under the declared
                encoding, or the encoding is not a known codec.
            DbcParseError: If the text is not a well-formed DBC document.
            DbcModelError: If the document cannot satisfy a CAN-X invariant.
        """
        codec = _resolve_encoding(encoding)
        return self._parse_bytes(raw, codec=codec, source_name=source_name, path=None)

    def import_bytes(
        self, raw: bytes, *, source_name: str, encoding: str | None = None
    ) -> DbcDocument:
        """Import DBC bytes a trusted caller already holds into a canonical document.

        The external-import sibling of :meth:`load_bytes`. Both parse the exact
        bytes they are handed and neither opens a file; the difference is what they
        may assume about the caller. This one **is** the boundary, so the name the
        caller claims for the content is validated as a provenance label — a plain
        ``.dbc`` file name — rather than trusted the way a registry row is.

        The bytes are never re-rendered: the canonical document is a *view* of
        them, so a caller that persists the same ``raw`` it passed here keeps the
        digest, the size and the byte-for-byte content it submitted.

        Args:
            raw: The exact DBC bytes to import.
            source_name: The file name being claimed for this content. Not a path.
            encoding: Codec to decode the bytes with. ``None`` selects
                :data:`DEFAULT_DBC_ENCODING`.

        Returns:
            A canonical document whose provenance carries **no path**: the content
            came from the caller, not from a location CAN-X could name.

        Raises:
            DbcUnsupportedFormatError: If ``source_name`` is not a plain,
                non-blank ``.dbc`` file name.
            DbcDecodeError: If the bytes are not text under the declared encoding,
                or the encoding is not a known codec.
            DbcParseError: If the text is not a well-formed DBC document.
            DbcModelError: If the document cannot satisfy a CAN-X invariant.
        """
        _require_import_source_name(source_name)
        codec = _resolve_encoding(encoding)
        return self._parse_bytes(raw, codec=codec, source_name=source_name, path=None)

    def _parse_bytes(
        self, raw: bytes, *, codec: str, source_name: str, path: str | None
    ) -> DbcDocument:
        """Decode and parse already-read bytes: the single import body."""
        text = _decode(raw, codec=codec, source_name=source_name)
        try:
            database = self._parser.parse_text(text)
        except DbcError as error:
            # The parser speaks about text, not about files. A caller importing a
            # batch of DBCs needs to know which one failed, so the source name is
            # attached here, where it is known, rather than guessed downstream.
            raise _with_source(error, source_name=source_name) from error
        return DbcDocument(
            database=database,
            source=_source(name=source_name, path=path, raw=raw, encoding=codec),
        )


def _require_import_source_name(source_name: object) -> str:
    """Return ``source_name`` when it is a plain ``.dbc`` file name, else raise.

    An external import is handed a name the caller chose, so that name is untrusted
    input rather than a fact read off a directory listing. Three properties are
    required, and each failure is a different fact:

    * it is text — anything else cannot be a file name at all;
    * it is a *base name*: no directory separator, no Windows drive separator, no
      control character, and no leading or trailing whitespace a later reader
      would silently disagree about;
    * it ends in ``.dbc``, case-insensitively, so the recorded provenance names a
      document CAN-X actually imports.

    The rejected value is deliberately **not** echoed into ``details``. This layer
    cannot know whether the caller's string was a path or a secret, and an error
    response must not become a rendering of the payload that was refused — so the
    diagnostic names the rule that failed, not the text that failed it.

    Raises:
        DbcUnsupportedFormatError: If the name cannot describe an imported DBC.
    """
    if not isinstance(source_name, str):
        raise _unsupported_source_name(reason="source_name_not_text")
    if not source_name or source_name != source_name.strip():
        raise _unsupported_source_name(reason="source_name_blank_or_padded")
    if any(character in FORBIDDEN_SOURCE_NAME_CHARACTERS for character in source_name):
        raise _unsupported_source_name(reason="source_name_is_a_path")
    if PurePosixPath(source_name).suffix.lower() not in SUPPORTED_DBC_EXTENSIONS:
        raise _unsupported_source_name(reason="source_name_is_not_a_dbc_file")
    return source_name


def _unsupported_source_name(*, reason: str) -> DbcUnsupportedFormatError:
    """Build the typed refusal for a name an external import cannot claim."""
    return DbcUnsupportedFormatError(
        "An imported DBC source must be named as a plain file name ending in '.dbc'.",
        details={"reason": reason},
    )


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


def _source(*, name: str, path: str | None, raw: bytes, encoding: str) -> DbcSource:
    """Build the provenance record for an import that already succeeded.

    ``path`` is ``None`` for bytes that were not read from a file the caller can
    name — a project-owned copy, for instance, which is deliberately not bound to
    the external location it was imported from.
    """
    return DbcSource(
        name=name,
        path=path,
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

    The service's own ``source_name`` wins over one carried by the inner failure:
    this is the layer that actually opened a file, so its answer is the
    authoritative one rather than a detail that happened to survive translation.
    """
    details: dict[str, object] = dict(error.details)
    details["source_name"] = source_name
    return type(error)(error.message, code=error.code, details=details)
