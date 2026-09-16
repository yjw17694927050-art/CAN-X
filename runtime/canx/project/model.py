"""Identity and metadata models for a CAN-X project."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

MANIFEST_FORMAT = "can-x-project"
MANIFEST_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ProjectManifest:
    """Lightweight project identity persisted as ``project.json``.

    The manifest deliberately holds identity only. Dynamic project state
    belongs in ``project.db``, not in this document.
    """

    format: str
    schema_version: int
    project_id: str


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    """Project facts owned by the project database.

    Timestamps are always timezone-aware UTC values so a project opened on a
    different host clock domain still reports the same instant.
    """

    project_id: str
    display_name: str
    created_at: datetime
    updated_at: datetime


def normalize_project_id(value: str) -> str:
    """Return the canonical string form of a project UUID.

    Raises:
        ValueError: If ``value`` is not a UUID.
    """
    return str(UUID(value))


def utc_now() -> datetime:
    """Return the current instant as an explicit timezone-aware UTC value."""
    return datetime.now(UTC)
