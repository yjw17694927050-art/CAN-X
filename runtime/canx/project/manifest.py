"""Read and write the deterministic ``project.json`` identity manifest."""

import json
from pathlib import Path
from typing import Any

from canx.project.errors import InvalidProjectError, UnsupportedProjectVersionError
from canx.project.model import (
    MANIFEST_FORMAT,
    MANIFEST_SCHEMA_VERSION,
    ProjectManifest,
    normalize_project_id,
)

MANIFEST_FILENAME = "project.json"


def write_manifest(path: Path, manifest: ProjectManifest) -> None:
    """Write ``manifest`` as deterministic UTF-8 JSON with a trailing newline.

    Raises:
        InvalidProjectError: If the destination cannot be written.
    """
    payload = {
        "format": manifest.format,
        "schema_version": manifest.schema_version,
        "project_id": manifest.project_id,
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as error:
        raise InvalidProjectError(
            "The project manifest could not be written.",
            code="project.manifest_write_failed",
            details={"path": str(path)},
        ) from error


def read_manifest(path: Path) -> ProjectManifest:
    """Read and validate a manifest that the runtime is able to open.

    Raises:
        InvalidProjectError: If the manifest is unreadable, malformed, or of a
            foreign format.
        UnsupportedProjectVersionError: If the manifest declares a schema
            version this runtime does not implement.
    """
    payload = _load_object(path)
    format_value = payload.get("format")
    if format_value != MANIFEST_FORMAT:
        raise InvalidProjectError(
            "The project manifest format is not supported.",
            code="project.unsupported_format",
            details={"path": str(path), "format": repr(format_value)},
        )
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise InvalidProjectError(
            "The project manifest schema_version must be an integer.",
            code="project.manifest_malformed",
            details={"path": str(path), "schema_version": repr(schema_version)},
        )
    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise UnsupportedProjectVersionError(
            "The project schema version is not supported by this runtime.",
            details={
                "path": str(path),
                "source": "manifest",
                "schema_version": schema_version,
                "supported_schema_version": MANIFEST_SCHEMA_VERSION,
            },
        )
    raw_project_id = payload.get("project_id")
    if not isinstance(raw_project_id, str):
        raise InvalidProjectError(
            "The project manifest project_id must be a string.",
            code="project.manifest_malformed",
            details={"path": str(path), "project_id": repr(raw_project_id)},
        )
    try:
        project_id = normalize_project_id(raw_project_id)
    except ValueError as error:
        raise InvalidProjectError(
            "The project manifest project_id is not a UUID.",
            code="project.manifest_malformed",
            details={"path": str(path), "project_id": raw_project_id},
        ) from error
    return ProjectManifest(
        format=MANIFEST_FORMAT,
        schema_version=schema_version,
        project_id=project_id,
    )


def _load_object(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise InvalidProjectError(
            "The project manifest could not be read.",
            code="project.manifest_unreadable",
            details={"path": str(path)},
        ) from error
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise InvalidProjectError(
            "The project manifest is not valid JSON.",
            code="project.manifest_malformed",
            details={"path": str(path), "error": str(error)},
        ) from error
    if not isinstance(payload, dict):
        raise InvalidProjectError(
            "The project manifest must be a JSON object.",
            code="project.manifest_malformed",
            details={"path": str(path)},
        )
    return payload
