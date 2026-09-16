"""The read-only DBC import service: boundary, encoding policy and side effects.

The service is the only component in this increment that touches a filesystem.
These tests therefore spend as much effort on what it must *not* do — mutate the
source, create a file, write into a project — as on what it must return.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from canx.dbc.errors import (
    DbcDecodeError,
    DbcFileNotFoundError,
    DbcModelError,
    DbcParseError,
    DbcReadError,
    DbcUnsupportedFormatError,
)
from canx.dbc.model import DbcDocument
from canx.dbc.parser import CantoolsDbcParser
from canx.dbc.service import DEFAULT_DBC_ENCODING, DbcImportService

VALID_DBC = (
    'VERSION "1.0"\n'
    "\n"
    "BU_: N1 N2\n"
    "\n"
    "BO_ 256 Demo: 8 N1\n"
    ' SG_ Speed : 0|16@1+ (0.5,-10) [-10|1000] "km/h" N2\n'
)

#: Declared with ``°`` (0xB0 in cp1252), which is not valid UTF-8 on its own.
LEGACY_CP1252_DBC = (
    'VERSION "1.0"\n'
    "\n"
    "BU_: N1\n"
    "\n"
    "BO_ 256 Demo: 8 N1\n"
    ' SG_ Temp : 0|8@1+ (1,-40) [-40|215] "°C" N1\n'
)

REVERSED_RANGE_DBC = (
    'VERSION "1.0"\n'
    "\n"
    "BU_: N1\n"
    "\n"
    "BO_ 256 Demo: 8 N1\n"
    ' SG_ Reversed : 0|8@1+ (1,0) [100|0] "" N1\n'
)


def write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    """Write DBC text and return the path."""
    path.write_bytes(text.encode(encoding))
    return path


def snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    """Fingerprint every entry under ``root``.

    Directories are recorded as well as files, so an import that merely creates
    a directory is caught too.
    """
    entries: dict[str, tuple[object, ...]] = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_dir():
            entries[key] = ("directory",)
        else:
            stat = path.stat()
            entries[key] = (
                "file",
                stat.st_size,
                stat.st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    return entries


# --- happy path -------------------------------------------------------------


def test_importing_a_file_returns_a_canonical_document(tmp_path: Path) -> None:
    path = write(tmp_path / "basic.dbc", VALID_DBC)

    document = DbcImportService().import_file(path)

    assert isinstance(document, DbcDocument)
    assert document.database.version == "1.0"
    assert [message.name for message in document.database.messages] == ["Demo"]
    assert document.database.messages[0].frame_id == 256
    assert document.database.messages[0].signals[0].unit == "km/h"


def test_importing_records_stable_provenance(tmp_path: Path) -> None:
    path = write(tmp_path / "basic.dbc", VALID_DBC)
    raw = path.read_bytes()

    source = DbcImportService().import_file(path).source

    assert source.name == "basic.dbc"
    assert source.path == str(path.resolve())
    assert source.size_bytes == len(raw)
    assert source.sha256 == hashlib.sha256(raw).hexdigest()
    assert source.encoding == DEFAULT_DBC_ENCODING


def test_a_string_path_is_accepted(tmp_path: Path) -> None:
    path = write(tmp_path / "basic.dbc", VALID_DBC)

    document = DbcImportService().import_file(str(path))

    assert document.source.name == "basic.dbc"


def test_the_service_returns_what_the_parser_returns(tmp_path: Path) -> None:
    """The service adds provenance; it must not alter the canonical content."""
    path = write(tmp_path / "basic.dbc", VALID_DBC)

    document = DbcImportService().import_file(path)
    parsed = CantoolsDbcParser().parse_text(VALID_DBC)

    assert document.database == parsed


def test_repeated_imports_of_one_file_are_equal(tmp_path: Path) -> None:
    path = write(tmp_path / "basic.dbc", VALID_DBC)
    service = DbcImportService()

    assert service.import_file(path) == service.import_file(path)


def test_the_extension_check_is_case_insensitive(tmp_path: Path) -> None:
    path = write(tmp_path / "BASIC.DBC", VALID_DBC)

    document = DbcImportService().import_file(path)

    assert document.source.name == "BASIC.DBC"
    assert document.database.messages[0].name == "Demo"


# --- read-only guarantee ----------------------------------------------------


def test_importing_never_mutates_the_source_file(tmp_path: Path) -> None:
    path = write(tmp_path / "basic.dbc", VALID_DBC)
    before = snapshot(tmp_path)
    original = path.read_bytes()

    DbcImportService().import_file(path)

    assert snapshot(tmp_path) == before
    assert path.read_bytes() == original


def test_importing_creates_nothing_next_to_the_source(tmp_path: Path) -> None:
    path = write(tmp_path / "basic.dbc", VALID_DBC)
    before = set(tmp_path.iterdir())

    DbcImportService().import_file(path)

    assert set(tmp_path.iterdir()) == before


def test_importing_writes_nothing_into_a_sibling_project_directory(tmp_path: Path) -> None:
    """V0.3-02 is read-only: no project DBC registry, no ``dbc/`` copy, no index."""
    source_dir = tmp_path / "inbox"
    source_dir.mkdir()
    path = write(source_dir / "basic.dbc", VALID_DBC)

    project = tmp_path / "vehicle.canx"
    (project / "dbc").mkdir(parents=True)
    (project / "project.json").write_text('{"display_name": "A"}', encoding="utf-8")
    (project / "project.db").write_bytes(b"not really sqlite")
    before = snapshot(tmp_path)

    DbcImportService().import_file(path)

    assert snapshot(tmp_path) == before


# --- source failures --------------------------------------------------------


def test_a_missing_file_is_a_typed_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(DbcFileNotFoundError) as captured:
        DbcImportService().import_file(tmp_path / "absent.dbc")

    error = captured.value
    assert error.code == "dbc.file_not_found"
    assert error.source == "dbc"
    assert error.recoverable is False
    assert error.details["source_name"] == "absent.dbc"


def test_a_directory_is_not_a_dbc_source(tmp_path: Path) -> None:
    directory = tmp_path / "folder.dbc"
    directory.mkdir()

    with pytest.raises(DbcFileNotFoundError):
        DbcImportService().import_file(directory)


def test_an_unsupported_extension_is_a_typed_unsupported_format(tmp_path: Path) -> None:
    path = write(tmp_path / "basic.txt", VALID_DBC)

    with pytest.raises(DbcUnsupportedFormatError) as captured:
        DbcImportService().import_file(path)

    error = captured.value
    assert error.code == "dbc.unsupported_format"
    assert error.recoverable is False
    assert error.details["extension"] == ".txt"


def test_an_unreadable_file_is_a_typed_recoverable_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(tmp_path / "basic.dbc", VALID_DBC)

    def refuse(self: Path) -> bytes:
        raise PermissionError("in use by another process")

    monkeypatch.setattr(Path, "read_bytes", refuse)

    with pytest.raises(DbcReadError) as captured:
        DbcImportService().import_file(path)

    error = captured.value
    assert error.code == "dbc.read_failed"
    assert error.recoverable is True
    assert error.details["source_name"] == "basic.dbc"


# --- encoding policy --------------------------------------------------------


def test_the_default_encoding_is_declared_explicitly() -> None:
    assert DEFAULT_DBC_ENCODING == "utf-8-sig"


def test_a_utf8_bom_is_tolerated_under_the_default_policy(tmp_path: Path) -> None:
    path = tmp_path / "bom.dbc"
    path.write_bytes(b"\xef\xbb\xbf" + VALID_DBC.encode("utf-8"))

    document = DbcImportService().import_file(path)

    assert document.source.encoding == DEFAULT_DBC_ENCODING
    assert document.database.messages[0].name == "Demo"


def test_non_utf8_bytes_are_a_typed_decode_failure_not_a_silent_mangle(
    tmp_path: Path,
) -> None:
    """The engine's own file path would decode cp1252 with ``errors='replace'``."""
    path = tmp_path / "legacy.dbc"
    path.write_bytes(LEGACY_CP1252_DBC.encode("cp1252"))

    with pytest.raises(DbcDecodeError) as captured:
        DbcImportService().import_file(path)

    error = captured.value
    assert error.code == "dbc.decode_failed"
    assert error.recoverable is False
    assert error.details["encoding"] == DEFAULT_DBC_ENCODING
    assert error.details["source_name"] == "legacy.dbc"
    assert isinstance(error.details["byte_offset"], int)


def test_a_caller_can_declare_a_legacy_encoding(tmp_path: Path) -> None:
    path = tmp_path / "legacy.dbc"
    path.write_bytes(LEGACY_CP1252_DBC.encode("cp1252"))

    document = DbcImportService().import_file(path, encoding="cp1252")

    assert document.source.encoding == "cp1252"
    assert document.database.messages[0].signals[0].unit == "°C"


def test_an_unknown_encoding_is_a_typed_decode_failure(tmp_path: Path) -> None:
    path = write(tmp_path / "basic.dbc", VALID_DBC)

    with pytest.raises(DbcDecodeError) as captured:
        DbcImportService().import_file(path, encoding="not-a-real-codec")

    assert captured.value.code == "dbc.decode_failed"
    assert captured.value.details["encoding"] == "not-a-real-codec"


# --- content failures -------------------------------------------------------


def test_a_malformed_file_is_a_typed_parse_failure(tmp_path: Path) -> None:
    path = write(tmp_path / "broken.dbc", 'VERSION "1.0"\n\nBO_ not_a_number M: 8 N1\n')

    with pytest.raises(DbcParseError) as captured:
        DbcImportService().import_file(path)

    error = captured.value
    assert error.code == "dbc.parse_failed"
    assert error.details["source_name"] == "broken.dbc"


def test_a_document_violating_a_canonical_invariant_is_a_typed_model_failure(
    tmp_path: Path,
) -> None:
    path = write(tmp_path / "reversed.dbc", REVERSED_RANGE_DBC)

    with pytest.raises(DbcModelError) as captured:
        DbcImportService().import_file(path)

    assert captured.value.code == "dbc.invalid_model"


def test_a_failed_import_still_leaves_the_source_untouched(tmp_path: Path) -> None:
    path = tmp_path / "legacy.dbc"
    path.write_bytes(LEGACY_CP1252_DBC.encode("cp1252"))
    before = snapshot(tmp_path)

    with pytest.raises(DbcDecodeError):
        DbcImportService().import_file(path)

    assert snapshot(tmp_path) == before


# --- plumbing ---------------------------------------------------------------


def test_an_explicitly_injected_parser_is_the_one_used(tmp_path: Path) -> None:
    path = write(tmp_path / "basic.dbc", VALID_DBC)
    parser = CantoolsDbcParser()

    document = DbcImportService(parser=parser).import_file(path)

    assert document.database == parser.parse_text(VALID_DBC)


def test_the_project_root_of_an_import_is_the_source_directory(tmp_path: Path) -> None:
    path = write(tmp_path / "basic.dbc", VALID_DBC)

    document = DbcImportService().import_file(path)

    assert Path(document.source.path or "").parent == tmp_path.resolve()
