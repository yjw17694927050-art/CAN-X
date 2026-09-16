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
from canx.dbc.errors import (
    DbcAssetIntegrityError,
    DbcAssetNotFoundError,
    DbcAssetRegistryError,
    DbcAssetStorageError,
    DbcFileNotFoundError,
    DbcSourceChangedError,
)
from canx.dbc.model import DbcDatabase
from canx.dbc.project_service import ProjectDbcService
from canx.dbc.service import DbcImportService
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
