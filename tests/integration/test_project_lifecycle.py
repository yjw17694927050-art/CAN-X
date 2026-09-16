"""End-to-end CAN-X project lifecycle across a runtime restart."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from canx.project.errors import (
    InvalidProjectError,
    ProjectAlreadyExistsError,
    ProjectIdentityMismatchError,
    UnsupportedProjectVersionError,
)
from canx.project.manifest import MANIFEST_FILENAME
from canx.project.model import MANIFEST_FORMAT, MANIFEST_SCHEMA_VERSION
from canx.project.service import ProjectService
from canx.project.storage import DATABASE_FILENAME

OTHER_PROJECT_ID = "8b7a6c5d-4e3f-4a2b-9c8d-7e6f5a4b3c2d"


def _stored_identity(root: Path) -> object:
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        row = connection.execute(
            "SELECT project_id FROM project_metadata WHERE id = 1"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return row[0]


def test_create_close_reopen_preserves_project_identity(tmp_path: Path) -> None:
    """Identity survives the runtime closing and the project being reopened."""
    service = ProjectService()
    root = tmp_path / "vehicle.canx"

    created = service.create(root, display_name="Vehicle A")
    project_id = created.project_id
    assert created.closed is False
    created.close()

    reopened = service.open(root)
    try:
        assert reopened.project_id == project_id
        assert reopened.display_name == "Vehicle A"
        assert reopened.schema_version == MANIFEST_SCHEMA_VERSION
        assert reopened.root == root
        assert reopened.manifest.project_id == reopened.metadata.project_id
    finally:
        reopened.close()

    third = service.open(root)
    try:
        assert third.project_id == project_id
    finally:
        third.close()


def test_manifest_and_database_agree_on_identity(tmp_path: Path) -> None:
    """Both identity documents record the same project_id as the handle reports."""
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    try:
        project_id = handle.project_id
        payload = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        assert payload == {
            "format": MANIFEST_FORMAT,
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "project_id": project_id,
        }
        assert handle.metadata.project_id == project_id
        assert handle.metadata.display_name == "Vehicle A"
    finally:
        handle.close()

    assert _stored_identity(root) == project_id


def test_close_releases_the_database_file_handle(tmp_path: Path) -> None:
    """A leaked SQLite handle would keep the project directory locked."""
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")

    handle.close()

    assert handle.closed is True
    shutil.rmtree(root)
    assert not root.exists()


def test_reopening_repeatedly_does_not_leak_handles(tmp_path: Path) -> None:
    """Each open must release its own connection before the next one starts."""
    service = ProjectService()
    root = tmp_path / "vehicle.canx"
    with service.create(root, display_name="Vehicle A") as created:
        project_id = created.project_id

    for _ in range(5):
        with service.open(root) as reopened:
            assert reopened.project_id == project_id

    shutil.rmtree(root)
    assert not root.exists()


def test_create_refuses_an_existing_project(tmp_path: Path) -> None:
    """Re-creating over a live project must never destroy its data."""
    service = ProjectService()
    root = tmp_path / "vehicle.canx"
    handle = service.create(root, display_name="Vehicle A")
    project_id = handle.project_id
    handle.close()

    with pytest.raises(ProjectAlreadyExistsError):
        service.create(root, display_name="Replacement")

    with service.open(root) as reopened:
        assert reopened.project_id == project_id
        assert reopened.display_name == "Vehicle A"


def test_open_rejects_a_missing_manifest(tmp_path: Path) -> None:
    """A project without its manifest has no identity to trust."""
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Vehicle A"):
        pass
    (root / MANIFEST_FILENAME).unlink()

    with pytest.raises(InvalidProjectError) as info:
        ProjectService().open(root)

    assert info.value.code == "project.manifest_missing"


def test_open_rejects_a_missing_database(tmp_path: Path) -> None:
    """A project without its database cannot be opened."""
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Vehicle A"):
        pass
    (root / DATABASE_FILENAME).unlink()

    with pytest.raises(InvalidProjectError) as info:
        ProjectService().open(root)

    assert info.value.code == "project.database_missing"


def test_open_rejects_a_malformed_manifest(tmp_path: Path) -> None:
    """A corrupt manifest is reported instead of regenerating a new identity."""
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Vehicle A"):
        pass
    manifest_path = root / MANIFEST_FILENAME
    manifest_path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(InvalidProjectError) as info:
        ProjectService().open(root)

    assert info.value.code == "project.manifest_malformed"
    assert manifest_path.read_text(encoding="utf-8") == "{ not json"


def test_open_rejects_an_unsupported_schema_version(tmp_path: Path) -> None:
    """A future manifest schema is refused, not downgraded."""
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Vehicle A"):
        pass
    manifest_path = root / MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["schema_version"] = MANIFEST_SCHEMA_VERSION + 1
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UnsupportedProjectVersionError) as info:
        ProjectService().open(root)

    assert info.value.details["schema_version"] == MANIFEST_SCHEMA_VERSION + 1


def test_open_rejects_an_identity_mismatch_without_repairing_it(tmp_path: Path) -> None:
    """A manifest that disagrees with the database is never silently rewritten."""
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    project_id = handle.project_id
    handle.close()

    manifest_path = root / MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["project_id"] = OTHER_PROJECT_ID
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProjectIdentityMismatchError) as info:
        ProjectService().open(root)

    assert info.value.details["manifest_project_id"] == OTHER_PROJECT_ID
    assert info.value.details["database_project_id"] == project_id
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["project_id"] == OTHER_PROJECT_ID
    assert _stored_identity(root) == project_id


@pytest.mark.parametrize("kind", ["missing", "file", "empty_directory"])
def test_open_rejects_a_path_that_is_not_a_project(tmp_path: Path, kind: str) -> None:
    """Only a readable project directory is accepted."""
    root = tmp_path / "not-a-project.canx"
    if kind == "file":
        root.write_text("just a file", encoding="utf-8")
    elif kind == "empty_directory":
        root.mkdir()

    with pytest.raises(InvalidProjectError):
        ProjectService().open(root)
