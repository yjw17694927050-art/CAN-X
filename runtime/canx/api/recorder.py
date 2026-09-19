"""Recorder control plane and persisted-session HTTP surface.

One headless, UI-independent router answers two questions and nothing else:

```text
GET /recorder/status                       what the runtime's recorder is doing
GET /recorder/sessions?project_path=…       which sessions a validated project holds
GET /recorder/sessions/{session_id}?…       one session's durable metadata
```

Four properties are load-bearing:

* **Observability is not authority.** ``/recorder/status`` projects the runtime's
  recorder seam and never steers it: there is no start, stop, flush or cleanup
  route here, and the surface does not own a CAN device, a recorder or a data
  session. Recording ingress stays with the Capture Runtime, and the recorder
  backends stay with their own modules.
* **The durable row is the only verdict.** A session's state is read through
  :class:`~canx.data.session.DataSessionService` — the storage authority that
  wrote it — and never inferred by scanning Parquet data or by re-deriving a
  lifecycle from in-memory counters. ``ACTIVE`` / ``COMPLETED`` / ``INTERRUPTED`` /
  ``FAILED`` are reported exactly as the database holds them, so
  ``PENDING`` is never guessed into ``COMPLETED`` or ``FAILED``.
* **Nothing blocking runs on the event loop.** Opening a project and reading its
  SQLite rows happens in a worker thread, so a control-plane request can never
  stall the realtime frame WebSocket served by the same loop.
* **The response is bounded and not a filesystem authority.** The session list is
  ordered by the data domain's canonical ``(started_at, session_id)`` and bounded
  by ``limit`` with ``has_more`` reporting truncation; the projection carries
  identity and metadata only, never a path the caller did not supply.

Failures are not translated into a second taxonomy here. A ``ProjectError``
propagates to the application boundary's project handler; a typed ``DataError`` —
an unknown session, a malformed id — is mapped by :func:`data_error_status` and
:func:`data_error_envelope`, which the application factory registers.
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from canx.api.errors import ErrorResponse
from canx.data.errors import (
    DataError,
    DataIntegrityError,
    DataSessionError,
    DataStorageError,
    DataValidationError,
)
from canx.data.model import DataSession, DataSessionState
from canx.data.session import DataSessionService
from canx.project.service import ProjectHandle, ProjectService
from canx.recorder.control import FinalizationState, RecorderStatus
from canx.recorder.msgpack_recorder import RecorderState
from canx.runtime.service import RuntimeService

#: Default and hard ceiling for the session list. The list is metadata rows, not
#: frames, but a control-plane response is still bounded rather than proportional
#: to how long a project has been used.
DEFAULT_SESSION_LIST_LIMIT = 100
MAX_SESSION_LIST_LIMIT = 1_000


class RecorderFailureResponse(BaseModel):
    """The recorder's first retained diagnostic, projected for the wire."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    recoverable: bool
    context: dict[str, object]


class RecorderStatusResponse(BaseModel):
    """The recorder control plane's live, durable state.

    ``durable_session_state`` is deliberately nullable and never defaulted: a
    capture with no project target has no durable session, and one whose row
    cannot be read is unknown — neither may be rendered as a verdict.
    """

    model_config = ConfigDict(frozen=True)

    active: bool
    stream_id: str | None
    data_session_id: str | None
    lifecycle_state: RecorderState
    recorded_frame_count: int
    failure: RecorderFailureResponse | None
    finalization_state: FinalizationState
    durable_session_state: DataSessionState | None


class RecorderSessionView(BaseModel):
    """One data session's durable metadata, and nothing else.

    Every field is read from the project database. The session directory, the
    segment files and every absolute path inside the project are absent: the caller
    supplied the project, and a session read must not become a way to discover the
    runtime's layout.
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    project_id: str
    stream_id: str
    state: DataSessionState
    started_at: str
    ended_at: str | None
    frame_count: int
    segment_count: int
    first_sequence: int | None
    last_sequence: int | None
    first_timestamp: float | None
    last_timestamp: float | None
    updated_at: str


class RecorderSessionListResponse(BaseModel):
    """A bounded, deterministically ordered page of a project's sessions."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    sessions: list[RecorderSessionView]
    limit: int
    has_more: bool


def create_recorder_router(runtime_service: RuntimeService) -> APIRouter:
    """Build the recorder control-plane router for one runtime service.

    The router is bound to the runtime whose recorder it observes; the persisted
    session reads need no runtime at all, because a project query is answered from
    the project and data domains alone.
    """
    router = APIRouter(tags=["recorder"])

    @router.get("/recorder/status", response_model=RecorderStatusResponse)
    async def recorder_status() -> RecorderStatusResponse:
        """Report the recorder's state without steering it.

        The runtime seam already resolves the one blocking read (the durable
        session row) on a worker thread, so this handler only projects it.
        """
        return _status_payload(await runtime_service.recorder_status())

    @router.get("/recorder/sessions", response_model=RecorderSessionListResponse)
    async def list_sessions(
        project_path: Annotated[str, Query(min_length=1)],
        limit: Annotated[int, Query(ge=1, le=MAX_SESSION_LIST_LIMIT)] = DEFAULT_SESSION_LIST_LIMIT,
    ) -> RecorderSessionListResponse:
        """List a validated project's sessions, oldest first, bounded by ``limit``.

        ``project_path`` is declared non-empty for the same reason the project
        read model declares it: ``Path("")`` is ``Path(".")``, so an empty value
        would silently mean *"whatever directory this runtime happens to be in"*
        rather than *"no project"*.
        """
        return await asyncio.to_thread(_list_sessions, project_path, limit)

    @router.get("/recorder/sessions/{session_id}", response_model=RecorderSessionView)
    async def read_session(
        session_id: str,
        project_path: Annotated[str, Query(min_length=1)],
    ) -> RecorderSessionView:
        """Return one session's durable metadata.

        The project is validated first, so a request that names a directory which
        is not a CAN-X project is refused before any session is looked up. A
        malformed or unknown session then answers with its own typed code.
        """
        return await asyncio.to_thread(_read_session, project_path, session_id)

    return router


def data_error_status(error: DataError) -> int:
    """Return the HTTP status a typed data-domain failure must be reported as.

    One-to-one with the diagnosis, exactly as the shared mapping is for the other
    domains: a malformed argument is a rejected request, an unknown session is a
    lookup that found nothing, an unreadable artifact is an environment condition,
    and data that contradicts its own metadata is a server-side integrity fault.
    """
    if isinstance(error, DataValidationError):
        return 400
    if isinstance(error, DataStorageError):
        return 503
    if isinstance(error, DataIntegrityError):
        return 500
    if isinstance(error, DataSessionError):
        return 404
    return 500


def data_error_envelope(error: DataError) -> ErrorResponse:
    """Return the shared five-field wire envelope for a typed data failure.

    The same shape every other domain failure uses; this module only picks the
    fields off a ``DataError`` because the shared helper is typed to the domains
    whose boundary already existed.
    """
    return ErrorResponse(
        code=error.code,
        message=error.message,
        details=error.details,
        recoverable=error.recoverable,
        source=error.source,
    )


def _status_payload(status: RecorderStatus) -> RecorderStatusResponse:
    """Project the domain snapshot onto the wire model, inventing nothing."""
    return RecorderStatusResponse(
        active=status.active,
        stream_id=status.stream_id,
        data_session_id=status.data_session_id,
        lifecycle_state=status.lifecycle_state,
        recorded_frame_count=status.recorded_frame_count,
        failure=(
            None
            if status.failure is None
            else RecorderFailureResponse(
                code=status.failure.code,
                message=status.failure.message,
                recoverable=status.failure.recoverable,
                context=dict(status.failure.context),
            )
        ),
        finalization_state=status.finalization_state,
        durable_session_state=status.durable_session_state,
    )


def _list_sessions(project_path: str, limit: int) -> RecorderSessionListResponse:
    """Open the project and read its session metadata. Blocking by design.

    Only the session rows are read — never a segment file — so the answer is the
    database's own, and the slice is taken after the domain has already ordered
    the whole list canonically. ``has_more`` reports that the slice truncated it.
    """
    with _open_project(project_path) as handle:
        sessions = DataSessionService(handle.root).list_sessions()
        project_id = handle.project_id
    return RecorderSessionListResponse(
        project_id=project_id,
        sessions=[_session_view(session) for session in sessions[:limit]],
        limit=limit,
        has_more=len(sessions) > limit,
    )


def _read_session(project_path: str, session_id: str) -> RecorderSessionView:
    """Open the project and read one session's durable metadata. Blocking by design.

    Raises:
        ProjectError: If the path is not a readable CAN-X project.
        DataValidationError: If ``session_id`` is not a UUID.
        DataSessionError: If no such session is registered in this project.
    """
    with _open_project(project_path) as handle:
        session = DataSessionService(handle.root).get_session(session_id)
    return _session_view(session)


def _open_project(project_path: str) -> ProjectHandle:
    """Validate the target with the project domain, never with a private copy.

    A named function rather than an inline ``ProjectService().open(...)`` so the
    boundary is observable: the tests record which thread ran it, which is how
    "the project open does not run on the event loop" is asserted about the work.

    Raises:
        ProjectError: If the path is not a readable CAN-X project.
    """
    return ProjectService().open(Path(project_path))


def _session_view(session: DataSession) -> RecorderSessionView:
    """Render one durable session for the wire.

    Timestamps are normalized to UTC ISO-8601 because the model stores them
    timezone-aware; nothing else is transformed, so the projection cannot change
    what the database said.
    """
    return RecorderSessionView(
        session_id=session.session_id,
        project_id=session.project_id,
        stream_id=session.stream_id,
        state=session.state,
        started_at=session.started_at.astimezone(UTC).isoformat(),
        ended_at=(
            None if session.ended_at is None else session.ended_at.astimezone(UTC).isoformat()
        ),
        frame_count=session.frame_count,
        segment_count=session.segment_count,
        first_sequence=session.first_sequence,
        last_sequence=session.last_sequence,
        first_timestamp=session.first_timestamp,
        last_timestamp=session.last_timestamp,
        updated_at=session.updated_at.astimezone(UTC).isoformat(),
    )
