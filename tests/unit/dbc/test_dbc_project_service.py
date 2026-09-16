"""Project DBC import: exact bytes, one registry row, and honest failure paths.

The import is the one operation that touches the external filesystem, the project
filesystem and the project database in sequence, so most of these tests are about
what must be true when one of those three steps fails: no registry row without a
file, no file without a registry row, no partial asset, and never a modified
source.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import canx.dbc.project_service as project_service_module
import pytest
from canx.dbc.decode import DbcDecoder
from canx.dbc.errors import (
    DbcAssetIntegrityError,
    DbcAssetNotFoundError,
    DbcAssetRegistryError,
    DbcAssetStorageError,
    DbcDecodeError,
    DbcFileNotFoundError,
    DbcModelError,
    DbcParseError,
    DbcSourceChangedError,
    DbcUnsupportedFormatError,
)
from canx.dbc.model import DbcDatabase
from canx.dbc.project_service import ProjectDbcService
from canx.dbc.service import DbcImportService
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.errors import InvalidProjectError
from canx.project.service import ProjectService
from canx.project.storage import DATABASE_FILENAME

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "dbc"

BASIC_DBC = (
    'VERSION "1.0"\n'
    "\n"
    "BU_: N1 N2\n"
    "\n"
    "BO_ 256 Demo: 8 N1\n"
    ' SG_ Speed : 0|16@1+ (0.5,-10) [-10|1000] "km/h" N2\n'
)
OTHER_DBC = (
    'VERSION "1.0"\n'
    "\n"
    "BU_: N1\n"
    "\n"
    "BO_ 512 Other: 8 N1\n"
    ' SG_ Level : 0|8@1+ (1,0) [0|255] "" N1\n'
)


def make_project(tmp_path: Path) -> tuple[Path, str]:
    """Create a real project and return its root and identity."""
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="DBC assets")
    try:
        return root, handle.project_id
    finally:
        handle.close()


def write_source(directory: Path, name: str, text: str = BASIC_DBC) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(text.encode("utf-8"))
    return path


def snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    """Fingerprint every entry under ``root``."""
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


def asset_rows(root: Path) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        return [tuple(row) for row in connection.execute("SELECT * FROM dbc_assets")]
    finally:
        connection.close()


def stored_files(root: Path) -> list[str]:
    return sorted(path.name for path in (root / "dbc").iterdir())


# --- the happy path ---------------------------------------------------------


def test_importing_a_source_registers_one_asset(tmp_path: Path) -> None:
    root, project_id = make_project(tmp_path)
    source = write_source(tmp_path / "inbox", "vehicle.dbc")
    raw = source.read_bytes()

    asset = ProjectDbcService(root).import_asset(source)

    assert asset.project_id == project_id
    assert asset.source_name == "vehicle.dbc"
    assert asset.relative_path == f"dbc/{asset.asset_id}.dbc"
    assert asset.sha256 == hashlib.sha256(raw).hexdigest()
    assert asset.size_bytes == len(raw)
    assert asset.encoding == "utf-8-sig"
    assert asset.imported_at.tzinfo is not None
    assert asset.imported_at <= datetime.now(UTC)


def test_the_project_owned_copy_is_the_exact_source_bytes(tmp_path: Path) -> None:
    """Not a re-rendered DBC: the copy is byte-for-byte what was validated."""
    root, _ = make_project(tmp_path)
    source = write_source(tmp_path / "inbox", "vehicle.dbc")
    raw = source.read_bytes()

    asset = ProjectDbcService(root).import_asset(source)
    owned = root / "dbc" / f"{asset.asset_id}.dbc"

    assert owned.read_bytes() == raw
    assert hashlib.sha256(owned.read_bytes()).hexdigest() == asset.sha256


def test_the_owned_file_is_named_after_the_asset_not_the_source(tmp_path: Path) -> None:
    """The user's file name never becomes a project path segment."""
    root, _ = make_project(tmp_path)
    source = write_source(tmp_path / "inbox", "customer network.dbc")

    asset = ProjectDbcService(root).import_asset(source)

    assert stored_files(root) == [f"{asset.asset_id}.dbc"]
    assert "customer network.dbc" not in stored_files(root)
    assert asset.source_name == "customer network.dbc"


def test_two_sources_with_the_same_file_name_do_not_collide(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)
    service = ProjectDbcService(root)

    first = service.import_asset(write_source(tmp_path / "a", "network.dbc", BASIC_DBC))
    second = service.import_asset(write_source(tmp_path / "b", "network.dbc", OTHER_DBC))

    assert first.asset_id != second.asset_id
    assert first.source_name == second.source_name == "network.dbc"
    assert len(stored_files(root)) == 2
    assert {asset.asset_id for asset in service.list_assets()} == {first.asset_id, second.asset_id}


def test_importing_leaves_no_staging_file_behind(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)

    asset = ProjectDbcService(root).import_asset(write_source(tmp_path / "inbox", "vehicle.dbc"))

    assert stored_files(root) == [f"{asset.asset_id}.dbc"]


def test_importing_never_modifies_the_source_file(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)
    inbox = tmp_path / "inbox"
    source = write_source(inbox, "vehicle.dbc")
    before = snapshot(inbox)

    ProjectDbcService(root).import_asset(source)

    assert snapshot(inbox) == before


def test_a_caller_can_declare_a_legacy_encoding(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)
    legacy = (
        'VERSION "1.0"\n'
        "\n"
        "BU_: N1\n"
        "\n"
        "BO_ 256 Demo: 8 N1\n"
        ' SG_ Temp : 0|8@1+ (1,-40) [-40|215] "\xb0C" N1\n'
    )
    source = tmp_path / "legacy.dbc"
    source.write_bytes(legacy.encode("cp1252"))

    asset = ProjectDbcService(root).import_asset(source, encoding="cp1252")

    assert asset.encoding == "cp1252"
    assert ProjectDbcService(root).load_asset(asset.asset_id).database.messages[0].signals[
        0
    ].unit == "°C"


def test_importing_nothing_leaves_an_empty_registry(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)

    assert ProjectDbcService(root).list_assets() == ()
    assert stored_files(root) == []


def test_assets_are_listed_in_import_order(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)
    service = ProjectDbcService(root)

    first = service.import_asset(write_source(tmp_path / "a", "a.dbc", BASIC_DBC))
    second = service.import_asset(write_source(tmp_path / "b", "b.dbc", OTHER_DBC))

    assert [asset.asset_id for asset in service.list_assets()] == [first.asset_id, second.asset_id]


# --- loading ----------------------------------------------------------------


def test_loading_an_asset_returns_the_canonical_document(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)
    source = write_source(tmp_path / "inbox", "vehicle.dbc")
    service = ProjectDbcService(root)
    asset = service.import_asset(source)

    document = service.load_asset(asset.asset_id)
    expected: DbcDatabase = DbcImportService().import_file(source).database

    assert document.database == expected
    assert document.database.messages[0].signals[0].unit == "km/h"


def test_the_loaded_provenance_names_the_import_and_holds_no_path(tmp_path: Path) -> None:
    """The durable source is the project copy, not the machine it came from."""
    root, _ = make_project(tmp_path)
    source = write_source(tmp_path / "inbox", "vehicle.dbc")
    service = ProjectDbcService(root)
    asset = service.import_asset(source)

    document = service.load_asset(asset.asset_id)

    assert document.source.name == "vehicle.dbc"
    assert document.source.path is None
    assert document.source.sha256 == asset.sha256
    assert document.source.size_bytes == asset.size_bytes
    assert document.source.encoding == asset.encoding


def test_getting_an_unknown_asset_is_a_typed_not_found(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)

    with pytest.raises(DbcAssetNotFoundError) as info:
        ProjectDbcService(root).get_asset("11111111-1111-4111-8111-111111111111")

    assert info.value.code == "dbc.asset_not_found"
    assert info.value.source == "dbc"


@pytest.mark.parametrize("asset_id", ["", "not-a-uuid", "vehicle.dbc", "../../etc/passwd"])
def test_getting_an_asset_with_an_impossible_id_is_a_typed_not_found(
    tmp_path: Path, asset_id: str
) -> None:
    root, _ = make_project(tmp_path)

    with pytest.raises(DbcAssetNotFoundError):
        ProjectDbcService(root).get_asset(asset_id)


def test_loading_an_unknown_asset_is_a_typed_not_found(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)

    with pytest.raises(DbcAssetNotFoundError):
        ProjectDbcService(root).load_asset("11111111-1111-4111-8111-111111111111")


# --- privacy and provenance -------------------------------------------------


def test_the_external_source_directory_is_never_persisted(tmp_path: Path) -> None:
    """A project must not carry a customer path out of the machine it came from."""
    inbox = tmp_path / "customer-secret-project"
    source = write_source(inbox, "vehicle.dbc")
    root, _ = make_project(tmp_path)

    ProjectDbcService(root).import_asset(source)

    stored = " ".join(str(value) for row in asset_rows(root) for value in row)

    assert "customer-secret-project" not in stored
    assert str(tmp_path) not in stored
    assert str(source) not in stored


def test_the_registry_stores_a_project_relative_posix_path(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)

    asset = ProjectDbcService(root).import_asset(write_source(tmp_path / "inbox", "vehicle.dbc"))
    (relative_path,) = [row[3] for row in asset_rows(root)]

    assert relative_path == f"dbc/{asset.asset_id}.dbc"
    assert "\\" not in relative_path
    assert not Path(relative_path).is_absolute()


# --- failure paths ----------------------------------------------------------


def test_a_source_that_changes_after_validation_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The TOCTOU gate: persisted bytes must be the bytes that were validated."""
    root, _ = make_project(tmp_path)
    source = write_source(tmp_path / "inbox", "vehicle.dbc")
    original_read_bytes = Path.read_bytes
    reads = {"count": 0}

    def mutating_read_bytes(self: Path) -> bytes:
        if self == source:
            reads["count"] += 1
            if reads["count"] == 2:
                return BASIC_DBC.encode("utf-8") + b"\n// changed after validation\n"
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", mutating_read_bytes)

    with pytest.raises(DbcSourceChangedError) as info:
        ProjectDbcService(root).import_asset(source)

    assert info.value.code == "dbc.source_changed"
    assert info.value.recoverable is True
    assert reads["count"] == 2

    monkeypatch.undo()

    assert stored_files(root) == []
    assert asset_rows(root) == []
    assert ProjectDbcService(root).list_assets() == ()


def test_a_written_copy_that_cannot_be_promoted_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = make_project(tmp_path)
    source = write_source(tmp_path / "inbox", "vehicle.dbc")

    def refuse_promotion(source_path: object, destination: object) -> None:
        raise OSError("the destination is locked by another process")

    monkeypatch.setattr(project_service_module.os, "replace", refuse_promotion)

    with pytest.raises(DbcAssetStorageError) as info:
        ProjectDbcService(root).import_asset(source)

    assert info.value.code == "dbc.asset_storage_failed"
    assert info.value.recoverable is True

    monkeypatch.undo()

    assert stored_files(root) == []
    assert asset_rows(root) == []


def test_a_staging_write_that_fails_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = make_project(tmp_path)
    source = write_source(tmp_path / "inbox", "vehicle.dbc")
    original_open = Path.open

    def refuse_staging_open(self: Path, *args: object, **kwargs: object) -> object:
        if self.name.startswith(".tmp-"):
            raise OSError("no space left on device")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_staging_open)

    with pytest.raises(DbcAssetStorageError):
        ProjectDbcService(root).import_asset(source)

    monkeypatch.undo()

    assert stored_files(root) == []
    assert asset_rows(root) == []


def test_a_registry_failure_removes_the_file_it_just_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unregistered file is not an asset, so it must not be left behind."""

    def refuse_insert(connection: object, asset: object) -> None:
        raise DbcAssetRegistryError("the registry is locked")

    monkeypatch.setattr(project_service_module.repository, "insert_asset", refuse_insert)
    root, _ = make_project(tmp_path)
    source = write_source(tmp_path / "inbox", "vehicle.dbc")

    with pytest.raises(DbcAssetRegistryError):
        ProjectDbcService(root).import_asset(source)

    monkeypatch.undo()

    assert stored_files(root) == []
    assert asset_rows(root) == []
    assert ProjectDbcService(root).list_assets() == ()


def test_an_invalid_project_is_reported_as_a_project_failure(tmp_path: Path) -> None:
    """The project domain decides what a project is; the fact stays a ProjectError."""
    empty = tmp_path / "not-a-project"
    empty.mkdir()
    source = write_source(tmp_path / "inbox", "vehicle.dbc")

    with pytest.raises(InvalidProjectError) as info:
        ProjectDbcService(empty).import_asset(source)

    assert info.value.code == "project.manifest_missing"
    assert info.value.source == "project"


def test_an_invalid_project_fails_before_the_source_is_touched(tmp_path: Path) -> None:
    """Validation order matters: a rejected project must not create files."""
    empty = tmp_path / "not-a-project"
    empty.mkdir()

    with pytest.raises(InvalidProjectError):
        ProjectDbcService(empty).list_assets()

    assert list(empty.iterdir()) == []


def test_a_missing_source_fails_before_anything_is_written(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)
    before = snapshot(root)

    with pytest.raises(DbcFileNotFoundError) as info:
        ProjectDbcService(root).import_asset(tmp_path / "inbox" / "absent.dbc")

    assert info.value.code == "dbc.file_not_found"
    assert snapshot(root) == before


def test_the_source_directory_is_not_required_to_be_inside_the_project(tmp_path: Path) -> None:
    """Import reads from anywhere; the project only owns the copy, never the source."""
    root, _ = make_project(tmp_path)
    source = write_source(tmp_path / "elsewhere", "vehicle.dbc")

    asset = ProjectDbcService(root).import_asset(source)

    assert (root / "dbc" / f"{asset.asset_id}.dbc").is_file()
    assert stored_files(root) == [f"{asset.asset_id}.dbc"]


def test_a_fixture_imports_end_to_end(tmp_path: Path) -> None:
    """One real fixture through the whole chain, not only synthetic text."""
    root, _ = make_project(tmp_path)
    service = ProjectDbcService(root)

    asset = service.import_asset(FIXTURES / "basic_standard.dbc")
    document = service.load_asset(asset.asset_id)

    assert document.database.messages[0].name == "EngineData"
    assert document.source.name == "basic_standard.dbc"
    assert document.source.path is None


def test_an_asset_cannot_be_loaded_once_its_owned_file_is_gone(tmp_path: Path) -> None:
    """Deleting a project copy is an integrity failure, not a missing-source error."""
    root, _ = make_project(tmp_path)
    service = ProjectDbcService(root)
    asset = service.import_asset(write_source(tmp_path / "inbox", "vehicle.dbc"))

    (root / "dbc" / f"{asset.asset_id}.dbc").unlink()

    with pytest.raises(DbcAssetIntegrityError) as info:
        service.load_asset(asset.asset_id)

    assert info.value.code == "dbc.asset_integrity_failed"


def test_importing_the_same_bytes_twice_creates_two_assets(tmp_path: Path) -> None:
    """No content de-duplication: an import is an explicit asset creation.

    Two assets that happen to hold identical bytes are two assets. Collapsing
    them on the digest would freeze a future decision — aliasing, revisions,
    replacement — into a constraint nobody asked for yet.
    """
    root, _ = make_project(tmp_path)
    service = ProjectDbcService(root)
    source = write_source(tmp_path / "inbox", "vehicle.dbc")

    first = service.import_asset(source)
    second = service.import_asset(source)

    assert first.asset_id != second.asset_id
    assert first.sha256 == second.sha256
    assert first.relative_path != second.relative_path
    assert len(service.list_assets()) == 2
    assert len(stored_files(root)) == 2


# --- a dbc directory that left the project (deterministic) -------------------


def redirect_dbc_tree(monkeypatch: pytest.MonkeyPatch, project: Path, outside: Path) -> None:
    """Make everything under ``<project>/dbc`` resolve under ``outside``.

    The deterministic stand-in for a junction: the directory *and* its contents are
    seen somewhere else, which is the shape a real junction produces — and the
    reason a check on the candidate alone cannot notice the move. Faking resolution
    keeps this evidence available on hosts that cannot create links at all.
    """
    source = project / "dbc"
    real_resolve = Path.resolve

    def fake_resolve(self: Path, strict: bool = False) -> Path:
        try:
            relative = self.relative_to(source)
        except ValueError:
            return real_resolve(self, strict=strict)
        return outside / relative

    monkeypatch.setattr(Path, "resolve", fake_resolve)


def test_import_refuses_a_dbc_directory_that_resolved_outside_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing may be written through a directory that is no longer the project's."""
    root, _ = make_project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    source = write_source(tmp_path / "inbox", "vehicle.dbc")
    before = snapshot(outside)
    redirect_dbc_tree(monkeypatch, root, outside)

    with pytest.raises(DbcAssetIntegrityError) as info:
        ProjectDbcService(root).import_asset(source)

    assert info.value.code == "dbc.asset_integrity_failed"
    assert info.value.details["dbc_directory"] == str(outside)
    assert snapshot(outside) == before
    assert list((root / "dbc").iterdir()) == []

    monkeypatch.undo()

    assert ProjectDbcService(root).list_assets() == ()
    assert stored_files(root) == []
    assert source.read_bytes() == BASIC_DBC.encode("utf-8")


def test_load_refuses_a_dbc_directory_that_resolved_outside_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading is gated by the same boundary, even when the outside file matches."""
    root, _ = make_project(tmp_path)
    service = ProjectDbcService(root)
    asset = service.import_asset(write_source(tmp_path / "inbox", "vehicle.dbc"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / f"{asset.asset_id}.dbc").write_bytes(BASIC_DBC.encode("utf-8"))
    (root / "dbc" / f"{asset.asset_id}.dbc").unlink()
    redirect_dbc_tree(monkeypatch, root, outside)

    with pytest.raises(DbcAssetIntegrityError) as info:
        service.load_asset(asset.asset_id)

    assert info.value.code == "dbc.asset_integrity_failed"
    assert Path(str(info.value.details["dbc_directory"])) == outside


def test_import_refuses_a_project_whose_dbc_directory_is_missing(tmp_path: Path) -> None:
    """A missing project directory is not silently recreated."""
    root, _ = make_project(tmp_path)
    (root / "dbc").rmdir()
    source = write_source(tmp_path / "inbox", "vehicle.dbc")

    with pytest.raises(DbcAssetIntegrityError) as info:
        ProjectDbcService(root).import_asset(source)

    assert info.value.code == "dbc.asset_integrity_failed"
    assert not (root / "dbc").exists()
    assert ProjectDbcService(root).list_assets() == ()
    assert source.read_bytes() == BASIC_DBC.encode("utf-8")


def test_import_does_not_recreate_a_dbc_path_that_is_a_file(tmp_path: Path) -> None:
    """A ``dbc`` that is a file is a tampered layout, not something to overwrite."""
    root, _ = make_project(tmp_path)
    (root / "dbc").rmdir()
    (root / "dbc").write_bytes(b"not a directory")
    source = write_source(tmp_path / "inbox", "vehicle.dbc")

    with pytest.raises(DbcAssetIntegrityError):
        ProjectDbcService(root).import_asset(source)

    assert (root / "dbc").read_bytes() == b"not a directory"
    assert ProjectDbcService(root).list_assets() == ()


# --- importing content a trusted caller already holds ------------------------
#
# V0.3-06's entry point. The Runtime is handed content, never a location, so the
# persistence body is shared with the path-based import above and the bytes that
# reach the project are exactly the bytes the caller submitted.

LEGACY_CP1252_DBC = (
    'VERSION "1.0"\n'
    "\n"
    "BU_: N1\n"
    "\n"
    "BO_ 256 Demo: 8 N1\n"
    ' SG_ Temp : 0|8@1+ (1,-40) [-40|215] "\xb0C" N1\n'
)

REVERSED_RANGE_DBC = (
    'VERSION "1.0"\n'
    "\n"
    "BU_: N1\n"
    "\n"
    "BO_ 256 Demo: 8 N1\n"
    ' SG_ Reversed : 0|8@1+ (1,0) [100|0] "" N1\n'
)


def demo_frame(data: bytes) -> Frame:
    """Build one canonical frame addressed to ``BASIC_DBC``'s ``Demo`` (256)."""
    return Frame(
        sequence=1,
        channel_id="can0",
        arbitration_id=256,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=len(data),
        data=data,
        direction=Direction.RX,
        hardware_timestamp=12.5,
        host_timestamp=100.25,
        normalized_timestamp=0.25,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HARDWARE,
        flags=0,
    )


def test_a_bytes_import_registers_one_asset(tmp_path: Path) -> None:
    root, project_id = make_project(tmp_path)
    raw = BASIC_DBC.encode("utf-8")

    asset = ProjectDbcService(root).import_asset_bytes(raw, source_name="vehicle.dbc")

    assert asset.project_id == project_id
    assert asset.source_name == "vehicle.dbc"
    assert asset.relative_path == f"dbc/{asset.asset_id}.dbc"
    assert asset.sha256 == hashlib.sha256(raw).hexdigest()
    assert asset.size_bytes == len(raw)
    assert asset.encoding == "utf-8-sig"
    assert asset.imported_at.tzinfo is not None


def test_the_project_owned_bytes_are_exactly_the_submitted_bytes(tmp_path: Path) -> None:
    """Not a re-rendered DBC: the stored digest is the digest of the payload."""
    root, _ = make_project(tmp_path)
    raw = BASIC_DBC.encode("utf-8")

    asset = ProjectDbcService(root).import_asset_bytes(raw, source_name="vehicle.dbc")

    owned = root / "dbc" / f"{asset.asset_id}.dbc"
    assert owned.read_bytes() == raw
    assert hashlib.sha256(owned.read_bytes()).hexdigest() == asset.sha256


def test_a_bytes_import_preserves_a_byte_order_mark(tmp_path: Path) -> None:
    """The BOM is part of the bytes the caller submitted, so it is part of the copy."""
    root, _ = make_project(tmp_path)
    raw = b"\xef\xbb\xbf" + BASIC_DBC.encode("utf-8")

    asset = ProjectDbcService(root).import_asset_bytes(raw, source_name="bom.dbc")

    assert (root / "dbc" / f"{asset.asset_id}.dbc").read_bytes() == raw
    assert asset.size_bytes == len(raw)


def test_a_bytes_import_leaves_no_staging_file_behind(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)

    asset = ProjectDbcService(root).import_asset_bytes(
        BASIC_DBC.encode("utf-8"), source_name="vehicle.dbc"
    )

    assert stored_files(root) == [f"{asset.asset_id}.dbc"]


def test_a_reopened_bytes_import_lists_loads_and_decodes(tmp_path: Path) -> None:
    """The imported asset is immediately usable, not merely a row in a table."""
    root, _ = make_project(tmp_path)
    asset = ProjectDbcService(root).import_asset_bytes(
        BASIC_DBC.encode("utf-8"), source_name="vehicle.dbc"
    )

    with ProjectService().open(root) as reopened:
        service = ProjectDbcService(reopened.root)
        assert [listed.asset_id for listed in service.list_assets()] == [asset.asset_id]
        document = service.load_asset(asset.asset_id)

    assert document.source.name == "vehicle.dbc"
    assert document.source.path is None

    decoded = DbcDecoder(document.database).decode_frame(
        demo_frame(bytes([0x64, 0x00]) + bytes(6))
    )

    assert decoded.message_name == "Demo"
    assert [signal.name for signal in decoded.signals] == ["Speed"]
    assert decoded.signals[0].raw_value == 100
    assert decoded.signals[0].physical_value == pytest.approx(40.0)
    assert decoded.signals[0].unit == "km/h"


def test_a_bytes_import_honours_a_declared_legacy_encoding(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)
    raw = LEGACY_CP1252_DBC.encode("cp1252")

    asset = ProjectDbcService(root).import_asset_bytes(
        raw, source_name="legacy.dbc", encoding="cp1252"
    )

    assert asset.encoding == "cp1252"
    assert (root / "dbc" / f"{asset.asset_id}.dbc").read_bytes() == raw
    assert (
        ProjectDbcService(root)
        .load_asset(asset.asset_id)
        .database.messages[0]
        .signals[0]
        .unit
        == "°C"
    )


def test_two_bytes_imports_of_identical_content_create_two_assets(tmp_path: Path) -> None:
    """No content de-duplication: submitting the same bytes twice is two imports."""
    root, _ = make_project(tmp_path)
    service = ProjectDbcService(root)
    raw = BASIC_DBC.encode("utf-8")

    first = service.import_asset_bytes(raw, source_name="vehicle.dbc")
    second = service.import_asset_bytes(raw, source_name="vehicle.dbc")

    assert first.asset_id != second.asset_id
    assert first.sha256 == second.sha256
    assert len(stored_files(root)) == 2
    assert len(service.list_assets()) == 2


# --- what a bytes import must never do --------------------------------------


def test_a_bytes_import_never_reads_a_file_that_happens_to_share_its_name(
    tmp_path: Path,
) -> None:
    """The submitted bytes are the only content source, even when a decoy exists.

    A file called ``secret.dbc`` holding a perfectly importable DBC sits outside the
    project. The caller submits *different*, unparseable bytes under that same name.
    If anything on this path opened a location instead of consuming its argument,
    the decoy would be persisted and the import would succeed — so the failure is
    the proof.
    """
    decoy_directory = tmp_path / "elsewhere"
    decoy = write_source(decoy_directory, "secret.dbc", BASIC_DBC)
    root, _ = make_project(tmp_path)
    before = snapshot(decoy_directory)

    with pytest.raises(DbcParseError) as info:
        ProjectDbcService(root).import_asset_bytes(
            b"this is not a DBC document at all\n", source_name="secret.dbc"
        )

    assert info.value.code == "dbc.parse_failed"
    assert stored_files(root) == []
    assert asset_rows(root) == []
    assert ProjectDbcService(root).list_assets() == ()
    assert decoy.read_bytes() == BASIC_DBC.encode("utf-8")
    assert snapshot(decoy_directory) == before


@pytest.mark.parametrize(
    "source_name",
    [
        "",
        "   ",
        "\t",
        ".",
        "..",
        "../vehicle.dbc",
        "..\\vehicle.dbc",
        "folder/vehicle.dbc",
        "folder\\vehicle.dbc",
        "/tmp/vehicle.dbc",
        "\\tmp\\vehicle.dbc",
        "C:\\temp\\vehicle.dbc",
        "C:vehicle.dbc",
        "vehicle.txt",
        "vehicle",
        "vehicle.dbc.txt",
        " vehicle.dbc",
        "vehicle.dbc ",
    ],
)
def test_a_bytes_import_refuses_a_source_name_that_is_not_a_plain_dbc_name(
    tmp_path: Path, source_name: str
) -> None:
    """The domain protects its own invariant, not only the HTTP adapter."""
    root, _ = make_project(tmp_path)

    with pytest.raises(DbcUnsupportedFormatError) as info:
        ProjectDbcService(root).import_asset_bytes(
            BASIC_DBC.encode("utf-8"), source_name=source_name
        )

    assert info.value.code == "dbc.unsupported_format"
    assert info.value.source == "dbc"
    assert info.value.recoverable is False
    assert stored_files(root) == []
    assert asset_rows(root) == []
    assert ProjectDbcService(root).list_assets() == ()


def test_invalid_bytes_leave_no_trace_in_the_project(tmp_path: Path) -> None:
    """Malformed grammar is refused before anything is staged or registered."""
    root, _ = make_project(tmp_path)
    before = snapshot(root)

    with pytest.raises(DbcParseError):
        ProjectDbcService(root).import_asset_bytes(
            b'VERSION "1.0"\n\nBO_ nope M: 8 N1\n', source_name="broken.dbc"
        )

    assert snapshot(root) == before
    assert stored_files(root) == []
    assert asset_rows(root) == []


def test_a_canonical_invariant_violation_leaves_no_trace(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)
    before = snapshot(root)

    with pytest.raises(DbcModelError) as info:
        ProjectDbcService(root).import_asset_bytes(
            REVERSED_RANGE_DBC.encode("utf-8"), source_name="reversed.dbc"
        )

    assert info.value.code == "dbc.invalid_model"
    assert snapshot(root) == before
    assert stored_files(root) == []
    assert asset_rows(root) == []


def test_an_unknown_encoding_leaves_no_trace(tmp_path: Path) -> None:
    root, _ = make_project(tmp_path)

    with pytest.raises(DbcDecodeError) as info:
        ProjectDbcService(root).import_asset_bytes(
            BASIC_DBC.encode("utf-8"),
            source_name="vehicle.dbc",
            encoding="not-a-real-codec",
        )

    assert info.value.code == "dbc.decode_failed"
    assert stored_files(root) == []
    assert asset_rows(root) == []


def test_bytes_that_are_not_text_under_the_declared_encoding_leave_no_trace(
    tmp_path: Path,
) -> None:
    root, _ = make_project(tmp_path)

    with pytest.raises(DbcDecodeError):
        ProjectDbcService(root).import_asset_bytes(
            LEGACY_CP1252_DBC.encode("cp1252"), source_name="legacy.dbc"
        )

    assert stored_files(root) == []
    assert asset_rows(root) == []


def test_a_bytes_import_into_an_invalid_project_is_a_project_failure(tmp_path: Path) -> None:
    """The project domain decides what a project is; a rejected root creates nothing."""
    empty = tmp_path / "not-a-project"
    empty.mkdir()

    with pytest.raises(InvalidProjectError) as info:
        ProjectDbcService(empty).import_asset_bytes(
            BASIC_DBC.encode("utf-8"), source_name="vehicle.dbc"
        )

    assert info.value.code == "project.manifest_missing"
    assert info.value.source == "project"
    assert list(empty.iterdir()) == []


def test_a_storage_failure_during_a_bytes_import_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = make_project(tmp_path)

    def refuse_promotion(source_path: object, destination: object) -> None:
        raise OSError("the destination is locked by another process")

    monkeypatch.setattr(project_service_module.os, "replace", refuse_promotion)

    with pytest.raises(DbcAssetStorageError) as info:
        ProjectDbcService(root).import_asset_bytes(
            BASIC_DBC.encode("utf-8"), source_name="vehicle.dbc"
        )

    assert info.value.code == "dbc.asset_storage_failed"
    assert info.value.recoverable is True

    monkeypatch.undo()

    assert stored_files(root) == []
    assert asset_rows(root) == []


def test_a_registry_failure_during_a_bytes_import_removes_the_file_it_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unregistered file is not an asset, exactly as on the path-based import."""

    def refuse_insert(connection: object, asset: object) -> None:
        raise DbcAssetRegistryError("the registry is locked")

    monkeypatch.setattr(project_service_module.repository, "insert_asset", refuse_insert)
    root, _ = make_project(tmp_path)

    with pytest.raises(DbcAssetRegistryError):
        ProjectDbcService(root).import_asset_bytes(
            BASIC_DBC.encode("utf-8"), source_name="vehicle.dbc"
        )

    monkeypatch.undo()

    assert stored_files(root) == []
    assert asset_rows(root) == []
    assert ProjectDbcService(root).list_assets() == ()
