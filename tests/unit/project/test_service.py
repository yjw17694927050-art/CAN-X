"""Behavior tests for CAN-X project create/open/close ownership."""

from pathlib import Path

import canx.project.service as service_module
import pytest
from canx.project.errors import (
    InvalidProjectError,
    ProjectAlreadyExistsError,
    ProjectClosedError,
    ProjectError,
)
from canx.project.manifest import MANIFEST_FILENAME
from canx.project.model import MANIFEST_FORMAT, MANIFEST_SCHEMA_VERSION
from canx.project.service import PROJECT_DIRECTORY_NAMES, ProjectService
from canx.project.storage import DATABASE_FILENAME


def test_create_lays_out_the_full_project_directory(tmp_path: Path) -> None:
    """A created project owns every documented ``.canx`` entry."""
    root = tmp_path / "vehicle.canx"

    handle = ProjectService().create(root, display_name="Vehicle A")
    try:
        assert sorted(entry.name for entry in root.iterdir()) == sorted(
            [MANIFEST_FILENAME, DATABASE_FILENAME, *PROJECT_DIRECTORY_NAMES]
        )
        assert all((root / name).is_dir() for name in PROJECT_DIRECTORY_NAMES)
    finally:
        handle.close()


def test_handle_exposes_read_only_identity(tmp_path: Path) -> None:
    """Identity is available without handing SQLite details to the caller."""
    root = tmp_path / "vehicle.canx"

    handle = ProjectService().create(root, display_name="Vehicle A")
    try:
        assert handle.root == root
        assert handle.display_name == "Vehicle A"
        assert handle.schema_version == MANIFEST_SCHEMA_VERSION
        assert handle.manifest.format == MANIFEST_FORMAT
        assert handle.manifest.project_id == handle.project_id
        assert handle.metadata.project_id == handle.project_id
        assert handle.closed is False
        assert handle.read_metadata() == handle.metadata
    finally:
        handle.close()


def test_create_refuses_an_existing_path(tmp_path: Path) -> None:
    """An existing directory may hold user data and is never replaced."""
    root = tmp_path / "existing.canx"
    root.mkdir()
    marker = root / "user-notes.txt"
    marker.write_text("keep me", encoding="utf-8")

    with pytest.raises(ProjectAlreadyExistsError) as info:
        ProjectService().create(root, display_name="Clash")

    assert info.value.code == "project.already_exists"
    assert marker.read_text(encoding="utf-8") == "keep me"
    assert not (root / MANIFEST_FILENAME).exists()
    assert not (root / DATABASE_FILENAME).exists()


def test_create_requires_a_display_name(tmp_path: Path) -> None:
    """An unnamed project is a programming error, not a project failure."""
    root = tmp_path / "vehicle.canx"

    with pytest.raises(ValueError):
        ProjectService().create(root, display_name="")

    assert not root.exists()


def test_close_is_idempotent_and_blocks_further_database_use(tmp_path: Path) -> None:
    """Closing twice is safe, and a closed handle refuses database work."""
    handle = ProjectService().create(tmp_path / "vehicle.canx", display_name="Vehicle A")

    handle.close()
    handle.close()

    assert handle.closed is True
    with pytest.raises(ProjectClosedError) as info:
        handle.read_metadata()
    assert info.value.code == "project.closed"


def test_handle_is_a_context_manager(tmp_path: Path) -> None:
    """The context manager releases the owned connection on exit."""
    service = ProjectService()
    root = tmp_path / "vehicle.canx"

    with service.create(root, display_name="Vehicle A") as created:
        assert created.closed is False
        project_id = created.project_id
    assert created.closed is True

    with service.open(root) as reopened:
        assert reopened.project_id == project_id
    assert reopened.closed is True


def test_failed_create_leaves_no_partial_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manifest write failure must not leave a valid-looking project behind."""
    root = tmp_path / "broken.canx"

    def fail_write(path: Path, manifest: object) -> None:
        raise ProjectError("manifest write refused")

    monkeypatch.setattr(service_module, "write_manifest", fail_write)

    with pytest.raises(ProjectError):
        ProjectService().create(root, display_name="Broken")

    assert not root.exists()

    monkeypatch.undo()
    handle = ProjectService().create(root, display_name="Retry")
    try:
        assert handle.display_name == "Retry"
    finally:
        handle.close()


def test_failed_database_initialization_leaves_no_partial_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SQLite initialization failure rolls the newly created directory back."""
    root = tmp_path / "broken.canx"

    def fail_create_database(path: Path, metadata: object) -> None:
        raise InvalidProjectError("database refused", code="project.database_init_failed")

    monkeypatch.setattr(service_module, "create_database", fail_create_database)

    with pytest.raises(InvalidProjectError) as info:
        ProjectService().create(root, display_name="Broken")

    assert info.value.code == "project.database_init_failed"
    assert not root.exists()
