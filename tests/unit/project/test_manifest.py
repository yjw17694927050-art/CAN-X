"""Behavior tests for the deterministic CAN-X project manifest."""

import json
from pathlib import Path

import pytest
from canx.project.errors import (
    InvalidProjectError,
    ProjectError,
    UnsupportedProjectVersionError,
)
from canx.project.manifest import MANIFEST_FILENAME, read_manifest, write_manifest
from canx.project.model import MANIFEST_FORMAT, MANIFEST_SCHEMA_VERSION, ProjectManifest

PROJECT_ID = "2f1c3d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"


def _manifest() -> ProjectManifest:
    return ProjectManifest(
        format=MANIFEST_FORMAT,
        schema_version=MANIFEST_SCHEMA_VERSION,
        project_id=PROJECT_ID,
    )


def _write_payload(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_written_manifest_is_deterministic_utf8_json(tmp_path: Path) -> None:
    """The identity document is byte-stable so identity never drifts on rewrite."""
    path = tmp_path / MANIFEST_FILENAME
    write_manifest(path, _manifest())
    first = path.read_bytes()

    write_manifest(path, _manifest())

    assert path.read_bytes() == first
    assert first.endswith(b"\n")
    assert json.loads(first.decode("utf-8")) == {
        "format": MANIFEST_FORMAT,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "project_id": PROJECT_ID,
    }


def test_manifest_round_trips_through_disk(tmp_path: Path) -> None:
    """Reading a written manifest returns exactly the identity that was written."""
    path = tmp_path / MANIFEST_FILENAME
    write_manifest(path, _manifest())

    assert read_manifest(path) == _manifest()


def test_missing_manifest_is_rejected(tmp_path: Path) -> None:
    """An absent manifest is not silently created on read."""
    path = tmp_path / "missing" / MANIFEST_FILENAME

    with pytest.raises(InvalidProjectError) as info:
        read_manifest(path)

    assert info.value.code == "project.manifest_unreadable"


def test_truncated_json_is_rejected(tmp_path: Path) -> None:
    """A half-written manifest must never be repaired into a new identity."""
    path = tmp_path / MANIFEST_FILENAME
    path.write_text('{"format": "can-x-project",', encoding="utf-8")

    with pytest.raises(InvalidProjectError) as info:
        read_manifest(path)

    assert info.value.code == "project.manifest_malformed"


def test_unsupported_format_is_rejected(tmp_path: Path) -> None:
    """A foreign JSON identity document is not a CAN-X project."""
    path = tmp_path / MANIFEST_FILENAME
    _write_payload(
        path,
        {"format": "canlab-project", "schema_version": 1, "project_id": PROJECT_ID},
    )

    with pytest.raises(InvalidProjectError) as info:
        read_manifest(path)

    assert info.value.code == "project.unsupported_format"


@pytest.mark.parametrize(
    "payload",
    [
        {"format": MANIFEST_FORMAT, "schema_version": 1, "project_id": "not-a-uuid"},
        {"format": MANIFEST_FORMAT, "schema_version": "1", "project_id": PROJECT_ID},
        {"format": MANIFEST_FORMAT, "project_id": PROJECT_ID},
        {"format": MANIFEST_FORMAT, "schema_version": 1},
        {"format": MANIFEST_FORMAT, "schema_version": True, "project_id": PROJECT_ID},
        [MANIFEST_FORMAT, 1, PROJECT_ID],
    ],
)
def test_malformed_manifest_shapes_are_rejected(tmp_path: Path, payload: object) -> None:
    """Wrong field types are rejected rather than coerced into a project."""
    path = tmp_path / MANIFEST_FILENAME
    _write_payload(path, payload)

    with pytest.raises(InvalidProjectError) as info:
        read_manifest(path)

    assert isinstance(info.value, ProjectError)
    assert info.value.code == "project.manifest_malformed"


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    """A newer schema version is refused instead of opened partially."""
    path = tmp_path / MANIFEST_FILENAME
    _write_payload(
        path,
        {
            "format": MANIFEST_FORMAT,
            "schema_version": MANIFEST_SCHEMA_VERSION + 1,
            "project_id": PROJECT_ID,
        },
    )

    with pytest.raises(UnsupportedProjectVersionError) as info:
        read_manifest(path)

    assert info.value.code == "project.unsupported_schema_version"
    assert info.value.details["schema_version"] == MANIFEST_SCHEMA_VERSION + 1
    assert info.value.details["source"] == "manifest"


def test_manifest_write_failure_is_typed(tmp_path: Path) -> None:
    """An unwritable destination surfaces as a project error, not a bare OSError."""
    path = tmp_path / "missing" / MANIFEST_FILENAME

    with pytest.raises(ProjectError) as info:
        write_manifest(path, _manifest())

    assert info.value.code == "project.manifest_write_failed"
