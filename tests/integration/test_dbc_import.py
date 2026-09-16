"""End-to-end DBC import across the real filesystem boundary.

This is the increment's honest integration surface: a real ``.dbc`` file on
disk, read by the real import service, parsed by the real cantools adapter, and
returned as a real CAN-X canonical document — with the source, and any project
next to it, provably unchanged afterwards.

V0.3-02 is a read-only foundation. A test that only checked the returned object
would not notice the day an import started writing a DBC registry.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from canx.dbc.errors import DbcDecodeError, DbcFileNotFoundError, DbcParseError
from canx.dbc.model import DbcByteOrder
from canx.dbc.service import DEFAULT_DBC_ENCODING, DbcImportService
from canx.project.service import ProjectService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "dbc"

VALID_FIXTURES = (
    "basic_standard.dbc",
    "extended.dbc",
    "endian_signed_scale.dbc",
    "choices.dbc",
    "multiplexed.dbc",
    "metadata.dbc",
    "can_fd.dbc",
    "non_ascii.dbc",
)


def snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    """Fingerprint every entry under ``root``.

    Directories are recorded as well as files, so an import that merely creates
    a directory — or a project ``dbc/`` registry — is caught too.
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


# --- the happy path ---------------------------------------------------------


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_every_fixture_imports_through_the_real_filesystem_chain(name: str) -> None:
    path = FIXTURES / name
    raw = path.read_bytes()

    document = DbcImportService().import_file(path)

    assert document.source.name == name
    assert document.source.size_bytes == len(raw)
    assert document.source.sha256 == hashlib.sha256(raw).hexdigest()
    assert document.source.encoding == DEFAULT_DBC_ENCODING
    assert document.database.messages


def test_the_full_chain_keeps_the_standard_message_intact() -> None:
    document = DbcImportService().import_file(FIXTURES / "basic_standard.dbc")

    message = document.database.messages[0]

    assert document.database.version == "1.0"
    assert [node.name for node in document.database.nodes] == ["Engine", "ECU"]
    assert message.name == "EngineData"
    assert message.frame_id == 0x123
    assert message.is_extended is False
    assert message.is_fd is False
    assert message.length == 8
    assert message.senders == ("Engine",)
    assert message.comment == "Primary engine broadcast frame"
    assert [item.name for item in message.signals] == [
        "EngineSpeed",
        "CoolantTemp",
        "ThrottlePosition",
    ]
    assert message.signals[0].factor == 0.25
    assert message.signals[0].unit == "rpm"


def test_the_full_chain_keeps_extended_can_identifiers_distinct() -> None:
    standard = DbcImportService().import_file(FIXTURES / "basic_standard.dbc")
    extended = DbcImportService().import_file(FIXTURES / "extended.dbc")

    standard_message = standard.database.messages[0]
    extended_message = extended.database.messages[0]

    assert standard_message.frame_id == extended_message.frame_id == 0x123
    assert (standard_message.is_extended, extended_message.is_extended) == (False, True)


def test_the_full_chain_keeps_can_fd_and_both_byte_orders() -> None:
    fd = DbcImportService().import_file(FIXTURES / "can_fd.dbc").database.messages[0]
    assert fd.is_fd is True
    assert fd.length == 64

    mixed = DbcImportService().import_file(FIXTURES / "endian_signed_scale.dbc")
    little, big = mixed.database.messages[0].signals

    assert little.byte_order is DbcByteOrder.LITTLE_ENDIAN
    assert little.is_signed is False
    assert big.byte_order is DbcByteOrder.BIG_ENDIAN
    assert big.is_signed is True


def test_the_full_chain_keeps_choices_and_multiplexing() -> None:
    choices = DbcImportService().import_file(FIXTURES / "choices.dbc")
    assert [choice.label for choice in choices.database.messages[0].signals[0].choices] == [
        "Closed",
        "Open",
        "Error",
    ]

    mux = DbcImportService().import_file(FIXTURES / "multiplexed.dbc")
    signals = mux.database.messages[0].signals
    assert signals[0].is_multiplexer is True
    assert [(item.multiplexer_signal, item.multiplexer_ids) for item in signals[1:]] == [
        ("ModeSwitch", (0,)),
        ("ModeSwitch", (1,)),
        ("ModeSwitch", (2,)),
    ]


# --- determinism and read-only guarantees -----------------------------------


def test_repeated_imports_across_service_instances_are_equal() -> None:
    first = DbcImportService().import_file(FIXTURES / "metadata.dbc")
    second = DbcImportService().import_file(FIXTURES / "metadata.dbc")

    assert first == second
    assert first.source == second.source


def test_importing_never_mutates_any_fixture(tmp_path: Path) -> None:
    before = snapshot(FIXTURES)

    for name in VALID_FIXTURES:
        DbcImportService().import_file(FIXTURES / name)

    assert snapshot(FIXTURES) == before


def test_importing_a_dbc_inside_a_real_project_writes_nothing(tmp_path: Path) -> None:
    """The strongest read-only claim: a real project directory is untouched."""
    project_root = tmp_path / "vehicle.canx"

    with ProjectService().create(project_root, display_name="V0.3-02 read-only probe"):
        pass

    inbox = project_root / "inbox"
    inbox.mkdir()
    source = inbox / "basic.dbc"
    source.write_bytes((FIXTURES / "basic_standard.dbc").read_bytes())

    before = snapshot(project_root)

    document = DbcImportService().import_file(source)

    assert snapshot(project_root) == before
    assert document.database.messages[0].name == "EngineData"
    # V0.2-01 already declares an empty ``dbc/`` directory in a project. This
    # increment must leave it empty: no registry, no copy of the source, no index.
    assert list((project_root / "dbc").iterdir()) == []


def test_importing_twice_into_a_project_still_writes_nothing(tmp_path: Path) -> None:
    project_root = tmp_path / "vehicle.canx"

    with ProjectService().create(project_root, display_name="V0.3-02 read-only probe"):
        pass

    source = tmp_path / "outside.dbc"
    source.write_bytes((FIXTURES / "choices.dbc").read_bytes())
    before = snapshot(project_root)

    DbcImportService().import_file(source)
    DbcImportService().import_file(source)

    assert snapshot(project_root) == before


# --- failure paths ----------------------------------------------------------


def test_a_missing_source_is_a_typed_failure() -> None:
    with pytest.raises(DbcFileNotFoundError) as captured:
        DbcImportService().import_file(FIXTURES / "does_not_exist.dbc")

    assert captured.value.code == "dbc.file_not_found"


def test_the_malformed_fixture_is_a_typed_parse_failure() -> None:
    with pytest.raises(DbcParseError) as captured:
        DbcImportService().import_file(FIXTURES / "malformed.dbc")

    error = captured.value
    assert error.code == "dbc.parse_failed"
    assert error.details["source_name"] == "malformed.dbc"
    assert error.details["line"] == 9


def test_a_non_utf8_source_is_a_typed_decode_failure(tmp_path: Path) -> None:
    source = tmp_path / "legacy.dbc"
    legacy = (
        'VERSION "1.0"\n'
        "\n"
        "BU_: N1\n"
        "\n"
        "BO_ 256 Demo: 8 N1\n"
        ' SG_ Temp : 0|8@1+ (1,-40) [-40|215] "\xb0C" N1\n'
    )
    # ``°`` is 0xB0 in cp1252 and is not valid UTF-8 on its own.
    source.write_bytes(legacy.encode("cp1252"))

    with pytest.raises(DbcDecodeError) as captured:
        DbcImportService().import_file(source)

    error = captured.value
    assert error.code == "dbc.decode_failed"
    assert error.details["source_name"] == "legacy.dbc"


def test_a_failed_import_leaves_the_fixture_directory_untouched() -> None:
    before = snapshot(FIXTURES)

    with pytest.raises(DbcParseError):
        DbcImportService().import_file(FIXTURES / "malformed.dbc")

    assert snapshot(FIXTURES) == before
