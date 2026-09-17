"""Project runtime HTTP surface — a thin, read-only adapter over the project domain.

One endpoint, and nothing else:

```text
GET /project/inspect?project_path=…      validate a CAN-X project, read its identity
```

It answers exactly one question — *is this directory a CAN-X project, and what does
it say it is?* — and it answers it with the project domain's own authority.

```text
HTTP
 ↓
FastAPI adapter (this module)
 ↓
ProjectService.open
 ↓
validated CAN-X project
 ↓
ProjectHandle / ProjectMetadata
 ↓
typed, immutable read model
```

Four properties are load-bearing:

* **The project domain stays the authority.** A directory is a CAN-X project because
  :meth:`~canx.project.service.ProjectService.open` says so after validating the
  path, the manifest, the database schema and their agreement. This module never
  reads ``project.json``, never opens ``project.db``, never queries
  ``project_metadata`` and never decides for itself what a valid project is. A
  second parser here would be a second definition of "valid project", and the two
  would drift.
* **The request is stateless.** The caller supplies an explicit ``project_path`` on
  every call; the temporary handle is opened, read and closed inside the request.
  There is no global current project, no cached handle and no session state — the
  endpoint is a *read model*, not `set current project` / `activate` / `mount`. A
  handle kept between requests would be exactly the retained connection this
  increment does not have.
* **Nothing blocking runs on the event loop.** Opening a project walks a
  filesystem, reads and parses a manifest, opens SQLite, reads metadata and
  verifies the whole schema. All of it is offloaded with ``asyncio.to_thread`` so a
  project request can never stall the realtime WebSocket served by the same loop.
* **The answer is not a filesystem authority.** The response carries identity and
  metadata only. The path the caller supplied, and every absolute path inside the
  project (manifest, database, ``dbc/``, ``data/``, ``cache/``), are deliberately
  absent: the caller already knows what it asked for, and this projection must not
  become a way to discover the runtime's layout.

Failures are not translated here. Every typed ``ProjectError`` —
``project.not_found``, ``project.not_a_directory``, ``project.manifest_missing``,
``project.manifest_malformed``, ``project.unsupported_schema_version``,
``project.database_missing``, ``project.database_schema_invalid``,
``project.identity_mismatch`` — propagates to the application boundary, which maps
it to the shared envelope and an honest status (see :mod:`canx.api.errors`). No
second project error taxonomy is created here.

This module reads. It does not create, delete, rename, repair, upgrade or activate a
project, and it exposes no endpoint that does.
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from canx.project.model import ProjectMetadata
from canx.project.service import ProjectHandle, ProjectService


class ProjectInspectResponse(BaseModel):
    """The canonical, minimal read model of one validated CAN-X project.

    Identity and metadata only, and every field comes from the project domain:

    * ``project_id`` — the UUID the manifest and the database agree on, read
      through the validated handle. It is never inferred from a path or a name.
    * ``display_name`` — the metadata row the project database owns. It is
      deliberately *not* derived from the directory name, which may have been
      renamed a hundred times since the project was created.
    * ``schema_version`` — the canonical manifest schema version.
    * ``created_at`` / ``updated_at`` — the stored, timezone-aware UTC instants, as
      ISO-8601.

    The project path, the manifest file name, the database file name and every
    project-internal directory are absent on purpose. A read model must not become a
    filesystem authority.
    """

    model_config = ConfigDict(frozen=True)

    project_id: str
    display_name: str
    schema_version: int
    created_at: str
    updated_at: str


def create_project_router() -> APIRouter:
    """Build the project read-model router.

    A separate router keeps the project surface independent of the capture
    lifecycle and of the DBC surface, which it genuinely is: inspecting a project
    needs no running capture and no DBC asset, and a project's identity does not
    become more or less true because either exists.

    Mounting this under ``/project`` is what stops a future Desktop from asking a
    DBC endpoint whether a directory is a project. A project question deserves a
    project endpoint.
    """
    router = APIRouter(tags=["project"], prefix="/project")

    @router.get("/inspect", response_model=ProjectInspectResponse)
    async def inspect_project(project_path: str) -> ProjectInspectResponse:
        """Validate one CAN-X project and return its canonical read model.

        The route is ``async`` and the work is not: the whole open → read → close
        sequence runs in a worker thread, because ``ProjectService.open`` touches
        the filesystem and SQLite and the loop it would otherwise block is the one
        serving the realtime frame WebSocket.

        ``project_path`` arrives as the caller wrote it. It is not resolved,
        expanded, lowered or separator-normalized here — the project domain decides
        what the string means, and rewriting it would silently answer about a
        different directory than the one that was requested.
        """
        metadata, schema_version = await asyncio.to_thread(_inspect_project, project_path)
        return _inspect_payload(metadata, schema_version)

    return router


def _inspect_project(project_path: str) -> tuple[ProjectMetadata, int]:
    """Open the project, read its identity, close it. Blocking by design.

    The handle is taken as a context manager and not as a bare ``open`` call: it
    owns the SQLite connection, and the connection has to be released on the way
    out whether the read succeeded, failed, or was never reached. ``with`` is what
    makes "opened" and "closed" impossible to separate — a ``try``/``finally``
    around a stored handle would leave the same guarantee one early return away
    from being lost.

    The metadata is re-read through the owned connection rather than taken from the
    open-time snapshot, so the answer describes the project this request actually
    opened and not one that has since gone stale.

    Raises:
        ProjectError: If the path is not a readable CAN-X project, or if its
            metadata cannot be read.
    """
    with _open_project(project_path) as handle:
        return handle.read_metadata(), handle.schema_version


def _open_project(project_path: str) -> ProjectHandle:
    """Validate the target with the project domain, never with a private copy.

    A named function rather than an inline ``ProjectService().open(...)`` so the
    boundary is observable: the tests record which thread ran it, which is how "the
    project open does not run on the event loop" is asserted about the work instead
    of about the call that scheduled it.

    Raises:
        ProjectError: If the path is not a readable CAN-X project.
    """
    return ProjectService().open(Path(project_path))


def _inspect_payload(
    metadata: ProjectMetadata, schema_version: int
) -> ProjectInspectResponse:
    """Render one validated project's identity for the wire.

    Called *after* the owned scope has exited, so a failure while building the
    response has no connection left to leak.
    """
    return ProjectInspectResponse(
        project_id=metadata.project_id,
        display_name=metadata.display_name,
        schema_version=schema_version,
        created_at=metadata.created_at.astimezone(UTC).isoformat(),
        updated_at=metadata.updated_at.astimezone(UTC).isoformat(),
    )
