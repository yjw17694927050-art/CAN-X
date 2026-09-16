"""Project-owned DBC assets across the real filesystem, database and reopen.

This is the increment's end-to-end proof. The unit tests check each layer; these
tests check that the layers together produce the one thing V0.3-03 promises: a
DBC that becomes a durable, verifiable project asset which is still exactly the
same after the project is closed, reopened, or moved.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from canx.dbc.asset import DbcAsset
from canx.dbc.errors import DbcAssetIntegrityError, DbcAssetNotFoundError, DbcParseError
from canx.dbc.project_service import ProjectDbcService
from canx.dbc.service import DbcImportService
from canx.project.service import ProjectService
from canx.project.storage import DATABASE_FILENAME

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "dbc"

BASIC_FIXTURE = FIXTURES / "basic_standard.dbc"
EXTENDED_FIXTURE = FIXTURES / "extended.dbc"
MULTIPLEXED_FIXTURE = FIXTURES / "multiplexed.dbc"
MALFORMED_FIXTURE = FIXTURES / "malformed.dbc"


def import_fixture(root: Path, fixture: Path, *, name: str | None = None) -> DbcAsset:
    """Copy one fixture into an inbox under ``root`` and import it from there."""
    inbox = root.parent / f"inbox-{fixture.stem}"
    inbox.mkdir(parents=True, exist_ok=True)
    source = inbox / (fixture.name if name is None else name)
    source.write_bytes(fixture.read_bytes())
    return ProjectDbcService(root).import_asset(source)


def created_project(tmp_path: Path, name: str = "vehicle.canx") -> Path:
    root = tmp_path / name
    with ProjectService().create(root, display_name="DBC assets"):
        pass
    return root


def snapshot(root: Path) -> dict[str, tuple[object, ...]]:
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


def rewrite_registry(root: Path, **columns: str) -> None:
    """Tamper with the registry row directly, the way a user with an editor would."""
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        for column, value in columns.items():
            connection.execute(f"UPDATE dbc_assets SET {column} = ?", (value,))
        connection.commit()
    finally:
        connection.close()


# --- the end-to-end proof ---------------------------------------------------


def test_an_imported_asset_is_identical_after_a_close_and_reopen(tmp_path: Path) -> None:
    """The increment's core promise: same id, same metadata, same document."""
    root = tmp_path / "vehicle.canx"
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source = inbox / "network.dbc"
    source.write_bytes(BASIC_FIXTURE.read_bytes())

    with ProjectService().create(root, display_name="Assets") as handle:
        created = ProjectDbcService(handle.root).import_asset(source)
        listed_before = ProjectDbcService(handle.root).list_assets()

    with ProjectService().open(root) as reopened:
        service = ProjectDbcService(reopened.root)
        listed_after = service.list_assets()
        fetched = service.get_asset(created.asset_id)
        document = service.load_asset(created.asset_id)

    assert listed_after == (created,)
    assert listed_before == listed_after
    assert fetched == created
    assert document.database == DbcImportService().import_file(source).database
    assert document.source.name == "network.dbc"
    assert document.source.sha256 == created.sha256
    assert document.source.size_bytes == created.size_bytes
    assert document.source.encoding == created.encoding
    assert document.source.path is None


def test_a_moved_project_still_finds_its_assets(tmp_path: Path) -> None:
    """Nothing durable is absolute: the registry stores a project-relative path."""
    before = tmp_path / "before" / "vehicle.canx"
    before.parent.mkdir()
    with ProjectService().create(before, display_name="Assets") as handle:
        asset = import_fixture(handle.root, BASIC_FIXTURE)

    after = tmp_path / "after" / "vehicle.canx"
    after.parent.mkdir()
    before.rename(after)

    with ProjectService().open(after) as reopened:
        service = ProjectDbcService(reopened.root)

        assert service.get_asset(asset.asset_id) == asset
        assert service.load_asset(asset.asset_id).database.messages[0].name == "EngineData"


def test_several_assets_keep_their_own_content_across_a_reopen(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Assets") as handle:
        service = ProjectDbcService(handle.root)
        basic = import_fixture(handle.root, BASIC_FIXTURE)
        extended = import_fixture(handle.root, EXTENDED_FIXTURE)
        multiplexed = import_fixture(handle.root, MULTIPLEXED_FIXTURE)

    with ProjectService().open(root) as reopened:
        service = ProjectDbcService(reopened.root)
        assert [asset.asset_id for asset in service.list_assets()] == [
            basic.asset_id,
            extended.asset_id,
            multiplexed.asset_id,
        ]
        first = service.load_asset(basic.asset_id).database
        second = service.load_asset(extended.asset_id).database
        third = service.load_asset(multiplexed.asset_id).database

    assert len({first, second, third}) == 3
    assert first.messages[0].is_extended is False
    assert second.messages[0].is_extended is True
    assert third.messages[0].signals[0].is_multiplexer is True


def test_two_sources_with_the_same_file_name_both_survive(tmp_path: Path) -> None:
    """Two ``network.dbc`` files must both be importable into one project."""
    root = tmp_path / "vehicle.canx"
    folder_a = tmp_path / "folderA"
    folder_b = tmp_path / "folderB"
    folder_a.mkdir()
    folder_b.mkdir()
    first_source = folder_a / "network.dbc"
    second_source = folder_b / "network.dbc"
    first_source.write_bytes(BASIC_FIXTURE.read_bytes())
    second_source.write_bytes(EXTENDED_FIXTURE.read_bytes())

    with ProjectService().create(root, display_name="Assets") as handle:
        service = ProjectDbcService(handle.root)
        first = service.import_asset(first_source)
        second = service.import_asset(second_source)

        assert first.asset_id != second.asset_id
        assert first.relative_path != second.relative_path
        assert sorted(path.name for path in (handle.root / "dbc").iterdir()) == sorted(
            [f"{first.asset_id}.dbc", f"{second.asset_id}.dbc"]
        )

    with ProjectService().open(root) as reopened:
        service = ProjectDbcService(reopened.root)
        listed = service.list_assets()
        assert [asset.source_name for asset in listed] == ["network.dbc", "network.dbc"]
        assert service.load_asset(first.asset_id).database != service.load_asset(
            second.asset_id
        ).database


def test_importing_leaves_the_source_and_the_project_directory_as_they_were_apart_from_the_copy(
    tmp_path: Path,
) -> None:
    root = created_project(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source = inbox / "network.dbc"
    source.write_bytes(BASIC_FIXTURE.read_bytes())
    source_before = snapshot(inbox)
    project_before = snapshot(root)

    asset = ProjectDbcService(root).import_asset(source)

    assert snapshot(inbox) == source_before
    added = set(snapshot(root)) - set(project_before)
    assert added == {str(Path("dbc") / f"{asset.asset_id}.dbc")}


def test_a_failed_import_leaves_no_trace_in_the_project(tmp_path: Path) -> None:
    root = created_project(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source = inbox / "broken.dbc"
    source.write_bytes(MALFORMED_FIXTURE.read_bytes())
    before = snapshot(root)

    with pytest.raises(DbcParseError) as info:
        ProjectDbcService(root).import_asset(source)

    assert info.value.code == "dbc.parse_failed"
    assert snapshot(root) == before
    assert ProjectDbcService(root).list_assets() == ()


# --- integrity of a registered asset ----------------------------------------


def test_a_tampered_project_copy_is_detected(tmp_path: Path) -> None:
    """One changed byte must be reported, never quietly accepted or re-hashed."""
    root = created_project(tmp_path)
    asset = import_fixture(root, BASIC_FIXTURE)
    owned = root / "dbc" / f"{asset.asset_id}.dbc"
    raw = bytearray(owned.read_bytes())
    raw[len(raw) // 2] = raw[len(raw) // 2] ^ 0x20
    owned.write_bytes(bytes(raw))

    with pytest.raises(DbcAssetIntegrityError) as info:
        ProjectDbcService(root).load_asset(asset.asset_id)

    assert info.value.code == "dbc.asset_integrity_failed"
    assert info.value.details["asset_id"] == asset.asset_id
    assert info.value.details["expected_sha256"] == asset.sha256
    assert info.value.details["actual_sha256"] != asset.sha256
    # The registry still holds the original digest: the file was not "fixed".
    with ProjectService().open(root) as handle:
        assert ProjectDbcService(handle.root).get_asset(asset.asset_id) == asset


def test_a_truncated_project_copy_is_detected_by_its_size(tmp_path: Path) -> None:
    root = created_project(tmp_path)
    asset = import_fixture(root, BASIC_FIXTURE)
    owned = root / "dbc" / f"{asset.asset_id}.dbc"
    owned.write_bytes(owned.read_bytes()[: len(owned.read_bytes()) // 2])

    with pytest.raises(DbcAssetIntegrityError) as info:
        ProjectDbcService(root).load_asset(asset.asset_id)

    assert info.value.details["expected_size_bytes"] == asset.size_bytes
    assert info.value.details["actual_size_bytes"] < asset.size_bytes


def test_a_deleted_project_copy_is_an_integrity_failure_not_a_missing_source(
    tmp_path: Path,
) -> None:
    root = created_project(tmp_path)
    asset = import_fixture(root, BASIC_FIXTURE)
    (root / "dbc" / f"{asset.asset_id}.dbc").unlink()

    with pytest.raises(DbcAssetIntegrityError) as info:
        ProjectDbcService(root).load_asset(asset.asset_id)

    assert info.value.code == "dbc.asset_integrity_failed"
    assert info.value.details["relative_path"] == asset.relative_path


def test_a_registry_path_pointing_outside_the_project_is_refused(tmp_path: Path) -> None:
    """Even a valid DBC outside the project must not be read through a tampered row."""
    root = created_project(tmp_path)
    asset = import_fixture(root, BASIC_FIXTURE)
    outside = tmp_path / "outside.dbc"
    outside.write_bytes(MULTIPLEXED_FIXTURE.read_bytes())

    rewrite_registry(root, relative_path="../outside.dbc")

    with pytest.raises(DbcAssetIntegrityError):
        ProjectDbcService(root).load_asset(asset.asset_id)


def test_a_registry_path_pointing_at_a_byte_identical_file_is_refused(tmp_path: Path) -> None:
    """A swapped path is refused by identity, not saved by the digest.

    The planted file holds the *same bytes* as the registered asset, so its size
    and SHA-256 both agree with the swap. Only the identity binding notices — which
    is exactly why the binding cannot be left to the digest check.
    """
    root = created_project(tmp_path)
    first = import_fixture(root, BASIC_FIXTURE)
    planted = root / "dbc" / "planted.dbc"
    planted.write_bytes((root / "dbc" / f"{first.asset_id}.dbc").read_bytes())

    assert hashlib.sha256(planted.read_bytes()).hexdigest() == first.sha256

    rewrite_registry(root, relative_path="dbc/planted.dbc")

    with pytest.raises(DbcAssetIntegrityError) as info:
        ProjectDbcService(root).load_asset(first.asset_id)

    assert info.value.code == "dbc.asset_integrity_failed"
    assert info.value.details["asset_id"] == first.asset_id
    # The row is refused while the record is being rebuilt, i.e. by identity —
    # not by the size or digest comparison that runs afterwards.
    assert info.value.details["cause"] == "dbc.invalid_asset"


def test_a_registry_digest_that_no_longer_matches_reads_as_a_broken_row(tmp_path: Path) -> None:
    root = created_project(tmp_path)
    asset = import_fixture(root, BASIC_FIXTURE)

    rewrite_registry(root, sha256="f" * 64)

    with pytest.raises(DbcAssetIntegrityError):
        ProjectDbcService(root).load_asset(asset.asset_id)


def test_an_asset_of_another_project_is_not_reachable(tmp_path: Path) -> None:
    """A row transplanted from another project must not be loaded here."""
    first_root = created_project(tmp_path, "first.canx")
    second_root = tmp_path / "second.canx"
    with ProjectService().create(second_root, display_name="Second") as handle:
        second_project_id = handle.project_id
    asset = import_fixture(first_root, BASIC_FIXTURE)

    rewrite_registry(first_root, project_id=second_project_id)

    with ProjectService().open(first_root) as handle:
        service = ProjectDbcService(handle.root)
        assert service.list_assets() == ()
        with pytest.raises(DbcAssetIntegrityError):
            service.get_asset(asset.asset_id)


# --- the registry is the only source of assets ------------------------------


def test_a_file_dropped_into_dbc_by_hand_is_not_an_asset(tmp_path: Path) -> None:
    """Registration is what makes a file an asset; nothing adopts a stray file."""
    root = created_project(tmp_path)
    planted = root / "dbc" / "planted.dbc"
    planted.write_bytes(BASIC_FIXTURE.read_bytes())

    with ProjectService().open(root) as handle:
        service = ProjectDbcService(handle.root)

        assert service.list_assets() == ()
        with pytest.raises(DbcAssetNotFoundError):
            service.get_asset("11111111-1111-4111-8111-111111111111")

    assert planted.is_file()


def test_an_unknown_asset_id_is_refused_across_a_reopen(tmp_path: Path) -> None:
    root = created_project(tmp_path)
    import_fixture(root, BASIC_FIXTURE)

    with (
        ProjectService().open(root) as handle,
        pytest.raises(DbcAssetNotFoundError) as info,
    ):
        ProjectDbcService(handle.root).load_asset("11111111-1111-4111-8111-111111111111")

    assert info.value.code == "dbc.asset_not_found"


# --- real directory links ---------------------------------------------------


def make_directory_link(link: Path, target: Path) -> bool:
    """Create a real directory link at ``link`` pointing at ``target``.

    A Windows junction needs no elevation, which is what makes the
    real-filesystem escape testable on an ordinary host; ``os.symlink`` requires a
    privilege most sessions do not hold. Returns ``False`` when the host refuses
    both, so the caller skips honestly instead of passing vacuously.
    """
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        return completed.returncode == 0 and link.is_dir()
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return link.is_dir()


def test_import_refuses_a_dbc_directory_linked_outside_the_project(tmp_path: Path) -> None:
    """Real filesystem evidence: the boundary moved, and nothing was written outside."""
    root = created_project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "dbc").rmdir()
    if not make_directory_link(root / "dbc", outside):
        pytest.skip("this host cannot create a directory link")

    source = tmp_path / "source.dbc"
    source.write_bytes(BASIC_FIXTURE.read_bytes())

    with pytest.raises(DbcAssetIntegrityError) as info:
        ProjectDbcService(root).import_asset(source)

    assert info.value.code == "dbc.asset_integrity_failed"
    assert info.value.details["dbc_directory"] == str(outside.resolve())
    assert ProjectDbcService(root).list_assets() == ()
    assert list(outside.iterdir()) == []
    assert source.read_bytes() == BASIC_FIXTURE.read_bytes()


def test_load_refuses_a_project_copy_reached_through_a_linked_dbc_directory(
    tmp_path: Path,
) -> None:
    """The same boundary applies to reading, even when the outside file matches."""
    root = created_project(tmp_path)
    asset = import_fixture(root, BASIC_FIXTURE)
    owned = root / "dbc" / f"{asset.asset_id}.dbc"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / owned.name).write_bytes(owned.read_bytes())
    owned.unlink()
    (root / "dbc").rmdir()
    if not make_directory_link(root / "dbc", outside):
        pytest.skip("this host cannot create a directory link")

    with pytest.raises(DbcAssetIntegrityError) as info:
        ProjectDbcService(root).load_asset(asset.asset_id)

    assert info.value.code == "dbc.asset_integrity_failed"
    assert info.value.details["dbc_directory"] == str(outside.resolve())
