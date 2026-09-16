"""Behavior tests for CAN-X project create/open/close ownership."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import canx.project.service as service_module
import pytest
from canx.project.errors import (
    InvalidProjectError,
    ProjectAlreadyExistsError,
    ProjectClosedError,
    ProjectError,
    ProjectValidationError,
)
from canx.project.manifest import MANIFEST_FILENAME
from canx.project.model import (
    MANIFEST_FORMAT,
    MANIFEST_SCHEMA_VERSION,
    ProjectManifest,
    ProjectMetadata,
)
from canx.project.service import PROJECT_DIRECTORY_NAMES, ProjectHandle, ProjectService
from canx.project.storage import DATABASE_FILENAME

PROJECT_ID = "2f1c3d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"


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


@pytest.mark.parametrize(
    "display_name",
    ["", "   ", "\t\n"],
    ids=["empty", "spaces", "tabs-and-newlines"],
)
def test_create_rejects_an_invalid_display_name(tmp_path: Path, display_name: str) -> None:
    """An unusable name is a typed project failure, never a bare ValueError."""
    root = tmp_path / "vehicle.canx"

    with pytest.raises(ProjectValidationError) as info:
        ProjectService().create(root, display_name=display_name)

    assert info.value.code == "project.invalid_display_name"
    assert isinstance(info.value, ProjectError)
    assert not isinstance(info.value, ValueError)
    assert not root.exists()


@pytest.mark.parametrize("display_name", ["Vehicle A", "车辆项目A", "CAN Test 01"])
def test_create_accepts_a_valid_display_name(tmp_path: Path, display_name: str) -> None:
    """Ordinary names, including CJK text, are stored verbatim."""
    handle = ProjectService().create(tmp_path / "vehicle.canx", display_name=display_name)
    try:
        assert handle.display_name == display_name
        assert handle.metadata.display_name == display_name
    finally:
        handle.close()


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


class _FailingConnection:
    """Connection double whose ``close()`` fails a fixed number of times."""

    def __init__(self, *, failures: int) -> None:
        self.close_calls = 0
        self._failures = failures

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls <= self._failures:
            raise sqlite3.OperationalError("database is locked")


def _open_handle(connection: object, root: Path) -> ProjectHandle:
    created = datetime(2026, 9, 16, 8, 30, tzinfo=UTC)
    return ProjectHandle(
        root=root,
        manifest=ProjectManifest(
            format=MANIFEST_FORMAT,
            schema_version=MANIFEST_SCHEMA_VERSION,
            project_id=PROJECT_ID,
        ),
        metadata=ProjectMetadata(
            project_id=PROJECT_ID,
            display_name="Vehicle A",
            created_at=created,
            updated_at=created,
        ),
        connection=cast(sqlite3.Connection, connection),
    )


def test_failed_close_keeps_the_handle_open_and_the_connection_reachable(
    tmp_path: Path,
) -> None:
    """A close that SQLite rejects must not report a released handle."""
    failing = _FailingConnection(failures=1)
    handle = _open_handle(failing, tmp_path / "vehicle.canx")

    with pytest.raises(ProjectError) as info:
        handle.close()

    assert info.value.code == "project.close_failed"
    assert handle.closed is False
    assert failing.close_calls == 1

    # Ownership survived the failure, so cleanup can be attempted again.
    handle.close()

    assert handle.closed is True
    assert failing.close_calls == 2


def test_repeated_close_failure_never_falsely_reports_closed(tmp_path: Path) -> None:
    """The handle stays open for as long as SQLite keeps rejecting close."""
    failing = _FailingConnection(failures=2)
    handle = _open_handle(failing, tmp_path / "vehicle.canx")

    for expected_calls in (1, 2):
        with pytest.raises(ProjectError) as info:
            handle.close()
        assert info.value.code == "project.close_failed"
        assert handle.closed is False
        assert failing.close_calls == expected_calls

    handle.close()

    assert handle.closed is True
    assert failing.close_calls == 3
