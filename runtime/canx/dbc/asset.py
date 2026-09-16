"""Project-owned DBC asset identity, and the paths that belong to it.

An asset is the durable record of *one* imported DBC file: a stable UUID, the
project that owns it, where its project-owned copy lives, and enough provenance
(the original file name, the encoding, the size, the SHA-256, the import instant)
to prove later that the copy is still exactly what was imported.

The module is deliberately free of SQLite and of the DBC engine. It describes what
an asset *is*; :mod:`canx.dbc.repository` owns the rows and
:mod:`canx.dbc.project_service` owns the orchestration.

Two deliberate choices live here:

* the project-owned file is named after the **asset id**, never after the name the
  user imported. Two files called ``network.dbc`` from different directories must
  be able to live in one project, and a source file name is untrusted input that
  must never become a path segment;
* ``source_name`` is a **base name only**. The external directory a DBC was
  imported from is never persisted, so a project never carries a customer path
  such as ``D:\\Customer\\SecretProject`` out of the machine it was imported on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import UUID

from canx.dbc.errors import DbcAssetIntegrityError, DbcAssetValidationError
from canx.dbc.model import SHA256_HEX_LENGTH

#: The project directory that holds imported DBC copies.
ASSET_DIRECTORY = "dbc"

#: The extension every project-owned DBC copy carries.
ASSET_SUFFIX = ".dbc"


@dataclass(frozen=True, slots=True)
class DbcAsset:
    """One immutable, project-owned imported DBC file.

    The record is metadata. The DBC itself stays in the ``.dbc`` file under
    ``relative_path``; nothing in it is relationalized into the project database.

    ``source_name`` is provenance, not a display name: it records what the user's
    file was called when it was imported, and it is never used to derive a path.
    """

    asset_id: str
    project_id: str
    source_name: str
    relative_path: str
    sha256: str
    size_bytes: int
    encoding: str
    imported_at: datetime

    def __post_init__(self) -> None:
        """Reject an asset record that could not describe a real project file."""
        object.__setattr__(self, "asset_id", _require_uuid(self.asset_id, field="asset_id"))
        object.__setattr__(self, "project_id", _require_uuid(self.project_id, field="project_id"))
        _require_source_name(self.source_name)
        _require_asset_relative_path(self.relative_path)
        _require_sha256(self.sha256)
        _require_size(self.size_bytes)
        _require_encoding(self.encoding)
        _require_aware(self.imported_at)


def asset_relative_path(asset_id: str) -> str:
    """Return the project-relative path of one asset's owned copy.

    Raises:
        DbcAssetValidationError: If ``asset_id`` is not a UUID.
    """
    normalized = _require_uuid(asset_id, field="asset_id")
    return f"{ASSET_DIRECTORY}/{normalized}{ASSET_SUFFIX}"


def resolve_asset_path(project_root: Path, relative_path: str) -> Path:
    """Resolve a stored asset path inside ``<project>/dbc``, refusing any escape.

    A persisted path is never trusted just because the database held it: the
    registry is a file a user can edit, and a row pointing at ``../outside.dbc``
    must not turn a load into a read outside the project. The check is made
    against the *resolved* DBC directory, so a ``dbc`` directory that is itself a
    link out of the project is refused as well.

    Args:
        project_root: The project the asset belongs to.
        relative_path: The project-relative path stored in the registry.

    Returns:
        The absolute path of the project-owned asset file.

    Raises:
        DbcAssetIntegrityError: If the path escapes ``<project>/dbc``.
    """
    root = Path(project_root).resolve()
    asset_root = (root / ASSET_DIRECTORY).resolve()
    candidate = (root / PurePosixPath(relative_path)).resolve()
    if not candidate.is_relative_to(asset_root):
        raise DbcAssetIntegrityError(
            "A stored DBC asset path points outside the project DBC directory.",
            details={"relative_path": relative_path, "project_root": str(root)},
        )
    return candidate


def _require_uuid(value: object, *, field: str) -> str:
    """Return the canonical string form of a UUID, else raise."""
    if not isinstance(value, str) or not value.strip():
        raise DbcAssetValidationError(
            f"{field} must be a canonical UUID string.",
            details={field: repr(value)},
        )
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError) as error:
        raise DbcAssetValidationError(
            f"{field} is not a UUID.",
            details={field: value},
        ) from error


def _require_source_name(value: object) -> str:
    """Return ``value`` when it is a plain file name, else raise."""
    if not isinstance(value, str) or not value.strip():
        raise DbcAssetValidationError(
            "source_name must be a non-blank file name.",
            details={"source_name": repr(value)},
        )
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise DbcAssetValidationError(
            "source_name must be a file name, not a path.",
            details={"source_name": value},
        )
    return value


def _require_asset_relative_path(value: object) -> str:
    """Return ``value`` when it is a project-relative path inside ``dbc/``, else raise."""
    if not isinstance(value, str) or not value.strip():
        raise DbcAssetValidationError(
            "relative_path must be a non-blank project-relative path.",
            details={"relative_path": repr(value)},
        )
    if "\\" in value:
        raise DbcAssetValidationError(
            "relative_path must use '/' separators.",
            details={"relative_path": value},
        )
    path = PurePosixPath(value)
    if path.is_absolute() or PureWindowsPath(value).is_absolute():
        raise DbcAssetValidationError(
            "relative_path must be relative to the project root.",
            details={"relative_path": value},
        )
    if ".." in path.parts:
        raise DbcAssetValidationError(
            "relative_path must not traverse the project directory.",
            details={"relative_path": value},
        )
    if path.parts[:1] != (ASSET_DIRECTORY,) or len(path.parts) != 2:
        raise DbcAssetValidationError(
            f"relative_path must name a file directly inside the project"
            f" '{ASSET_DIRECTORY}' directory.",
            details={"relative_path": value},
        )
    if path.suffix.lower() != ASSET_SUFFIX:
        raise DbcAssetValidationError(
            f"relative_path must name a '{ASSET_SUFFIX}' file.",
            details={"relative_path": value},
        )
    return value


def _require_sha256(value: object) -> str:
    """Return ``value`` when it is a lowercase hexadecimal SHA-256 digest, else raise."""
    if (
        not isinstance(value, str)
        or len(value) != SHA256_HEX_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise DbcAssetValidationError(
            "sha256 must be a lowercase hexadecimal SHA-256 digest.",
            details={"sha256": repr(value)},
        )
    return value


def _require_size(value: object) -> int:
    """Return ``value`` when it is a non-negative integer count of bytes, else raise."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DbcAssetValidationError(
            "size_bytes must be a non-negative integer.",
            details={"size_bytes": repr(value)},
        )
    return value


def _require_encoding(value: object) -> str:
    """Return ``value`` when it is a non-blank codec name, else raise."""
    if not isinstance(value, str) or not value.strip():
        raise DbcAssetValidationError(
            "encoding must be a non-blank codec name.",
            details={"encoding": repr(value)},
        )
    return value


def _require_aware(value: object) -> datetime:
    """Return ``value`` when it is a timezone-aware datetime, else raise."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DbcAssetValidationError(
            "imported_at must be a timezone-aware datetime.",
            details={"imported_at": repr(value)},
        )
    return value
