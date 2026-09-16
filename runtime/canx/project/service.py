"""Project lifecycle: create, open, close, and reopen a CAN-X ``.canx`` root."""

import shutil
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self
from uuid import uuid4

from canx.project.errors import (
    InvalidProjectError,
    ProjectAlreadyExistsError,
    ProjectClosedError,
    ProjectError,
    ProjectIdentityMismatchError,
)
from canx.project.manifest import MANIFEST_FILENAME, read_manifest, write_manifest
from canx.project.model import (
    MANIFEST_FORMAT,
    MANIFEST_SCHEMA_VERSION,
    ProjectManifest,
    ProjectMetadata,
    utc_now,
)
from canx.project.storage import (
    DATABASE_FILENAME,
    close_quietly,
    create_database,
    open_database,
    read_metadata,
)

PROJECT_DIRECTORY_NAMES: tuple[str, ...] = (
    "dbc",
    "data",
    "logs",
    "scripts",
    "agent",
    "exports",
    "cache",
)


class ProjectHandle:
    """An open project together with the database connection it owns.

    The handle is the only owner of the SQLite connection. Identity is read-only
    and remains available after closing, but database work is refused once the
    handle is closed.
    """

    def __init__(
        self,
        *,
        root: Path,
        manifest: ProjectManifest,
        metadata: ProjectMetadata,
        connection: sqlite3.Connection,
    ) -> None:
        self._root = root
        self._manifest = manifest
        self._metadata = metadata
        self._connection: sqlite3.Connection | None = connection

    @property
    def root(self) -> Path:
        """Return the project directory this handle was opened from."""
        return self._root

    @property
    def project_id(self) -> str:
        """Return the stable identity shared by the manifest and the database."""
        return self._manifest.project_id

    @property
    def display_name(self) -> str:
        """Return the human-readable name stored in project metadata."""
        return self._metadata.display_name

    @property
    def schema_version(self) -> int:
        """Return the manifest schema version of the open project."""
        return self._manifest.schema_version

    @property
    def manifest(self) -> ProjectManifest:
        """Return the manifest this project was opened with."""
        return self._manifest

    @property
    def metadata(self) -> ProjectMetadata:
        """Return the metadata snapshot read while opening the project."""
        return self._metadata

    @property
    def closed(self) -> bool:
        """Return whether this handle has released its database connection."""
        return self._connection is None

    def read_metadata(self) -> ProjectMetadata:
        """Read the current metadata row through the owned connection.

        Raises:
            ProjectClosedError: If the handle has already been closed.
        """
        return read_metadata(self._require_connection())

    def close(self) -> None:
        """Release the owned database connection; safe to call repeatedly.

        Raises:
            ProjectError: If the connection cannot be closed cleanly.
        """
        connection = self._connection
        if connection is None:
            return
        self._connection = None
        try:
            connection.close()
        except sqlite3.Error as error:
            raise ProjectError(
                "The project database could not be closed cleanly.",
                code="project.close_failed",
                details={"path": str(self._root), "error": str(error)},
            ) from error

    def __enter__(self) -> Self:
        if self._connection is None:
            raise ProjectClosedError(
                "The project handle is closed and owns no database connection.",
                details={"path": str(self._root)},
            )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise ProjectClosedError(
                "The project handle is closed and owns no database connection.",
                details={"path": str(self._root)},
            )
        return self._connection


class ProjectService:
    """Stateless factory and loader for CAN-X projects.

    The service keeps no connection between calls: :meth:`create` and
    :meth:`open` each return a :class:`ProjectHandle` that the caller owns and
    closes. The domain depends only on the standard library, so a future
    FastAPI layer is an adapter over this API rather than a requirement of it.
    """

    def create(self, root: Path, *, display_name: str) -> ProjectHandle:
        """Create a project directory, refusing to touch an existing path.

        Args:
            root: Directory to create. It must not already exist.
            display_name: Human-readable project name.

        Raises:
            ValueError: If ``display_name`` is empty.
            ProjectAlreadyExistsError: If ``root`` already exists.
            ProjectError: If the directory layout cannot be created.
        """
        if not display_name:
            raise ValueError("display_name must be a non-empty string")
        try:
            root.mkdir(parents=True, exist_ok=False)
        except FileExistsError as error:
            raise ProjectAlreadyExistsError(
                "A project cannot be created over an existing path;"
                " existing user data is never replaced.",
                details={"path": str(root)},
            ) from error
        except OSError as error:
            raise ProjectError(
                "The project directory could not be created.",
                code="project.create_failed",
                details={"path": str(root)},
            ) from error

        project_id = str(uuid4())
        created_at = utc_now()
        manifest = ProjectManifest(
            format=MANIFEST_FORMAT,
            schema_version=MANIFEST_SCHEMA_VERSION,
            project_id=project_id,
        )
        metadata = ProjectMetadata(
            project_id=project_id,
            display_name=display_name,
            created_at=created_at,
            updated_at=created_at,
        )
        connection: sqlite3.Connection | None = None
        try:
            for name in PROJECT_DIRECTORY_NAMES:
                (root / name).mkdir()
            connection = create_database(root / DATABASE_FILENAME, metadata)
            write_manifest(root / MANIFEST_FILENAME, manifest)
        except BaseException as error:
            # Best-effort rollback. Only the directory this call just created is
            # removed, so pre-existing user data is never touched.
            close_quietly(connection)
            shutil.rmtree(root, ignore_errors=True)
            if isinstance(error, OSError):
                raise ProjectError(
                    "The project directory layout could not be created.",
                    code="project.create_failed",
                    details={"path": str(root), "error": str(error)},
                ) from error
            raise
        return ProjectHandle(
            root=root,
            manifest=manifest,
            metadata=metadata,
            connection=connection,
        )

    def open(self, root: Path) -> ProjectHandle:
        """Open an existing project after validating it end to end.

        Validation order: path, manifest presence, manifest validity, database
        presence, database schema, then manifest/database identity agreement.
        A project that fails any step is rejected rather than repaired.

        Raises:
            InvalidProjectError: If ``root`` is not a readable CAN-X project.
            UnsupportedProjectVersionError: If the manifest or database schema
                is newer than this runtime.
            ProjectIdentityMismatchError: If the manifest and database
                disagree on ``project_id``.
        """
        if not root.exists():
            raise InvalidProjectError(
                "The project path does not exist.",
                code="project.not_found",
                details={"path": str(root)},
            )
        if not root.is_dir():
            raise InvalidProjectError(
                "The project path is not a directory.",
                code="project.not_a_directory",
                details={"path": str(root)},
            )
        manifest_path = root / MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise InvalidProjectError(
                "The project manifest is missing.",
                code="project.manifest_missing",
                details={"path": str(manifest_path)},
            )
        manifest = read_manifest(manifest_path)
        database_path = root / DATABASE_FILENAME
        if not database_path.is_file():
            raise InvalidProjectError(
                "The project database is missing.",
                code="project.database_missing",
                details={"path": str(database_path)},
            )
        connection: sqlite3.Connection | None = None
        try:
            connection = open_database(database_path)
            metadata = read_metadata(connection)
            if metadata.project_id != manifest.project_id:
                raise ProjectIdentityMismatchError(
                    "The project manifest and database disagree on project identity.",
                    details={
                        "path": str(root),
                        "manifest_project_id": manifest.project_id,
                        "database_project_id": metadata.project_id,
                    },
                )
        except BaseException:
            close_quietly(connection)
            raise
        return ProjectHandle(
            root=root,
            manifest=manifest,
            metadata=metadata,
            connection=connection,
        )
