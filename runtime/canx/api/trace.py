"""Bounded historical Trace HTTP surface — a thin adapter over the query domain.

Route handlers translate an HTTP request into canonical query models and hand
them to :class:`~canx.query.service.QueryService`. Nothing here speaks SQL,
touches ``duckdb``, reads a Parquet footer or parses a segment path: the query
domain owns all of that, and this module must not become a second query engine.

Three properties are load-bearing:

* **The project is validated by the project domain.** The target is opened with
  :meth:`~canx.project.service.ProjectService.open`, never handed straight to
  ``QueryService``. A directory that merely looks like a project is rejected, and
  a rejected target leaves nothing behind on disk.
* **Nothing blocking runs on the event loop.** A bounded query scans Parquet
  files and is bounded in *rows*, not in time, so it is executed in a worker
  thread. A Trace page request must never be able to stall the realtime WebSocket
  the runtime is serving on the same loop.
* **The domain decides what a valid filter is.** This module only maps names and
  converts JSON arrays into the tuples the model requires. An empty array reaches
  the model as an empty tuple and is rejected there, rather than silently
  degrading into "no restriction on this axis".

Failures are not translated here. ``QueryError`` and ``ProjectError`` propagate to
the application boundary, which maps them to the shared envelope and an honest
status code (see :mod:`canx.api.errors`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from canx.api.frame import FrameWire, frame_to_wire
from canx.domain.frame import Direction, Frame
from canx.project.service import ProjectHandle, ProjectService
from canx.query.errors import QueryValidationError
from canx.query.model import (
    DEFAULT_FRAME_QUERY_LIMIT,
    FrameFilter,
    FrameQuery,
    FrameQueryPage,
    FrameQuerySummary,
)
from canx.query.service import QueryService


class TraceFilterRequest(BaseModel):
    """The Trace filter axes, exactly as the query domain defines them.

    Field names and semantics mirror :class:`~canx.query.model.FrameFilter`.
    Types are deliberately permissive (``int``/``float``/``str``) so that an
    out-of-range or incoherent value is rejected by the query domain with its own
    typed error code, instead of being rejected here with a framework-level
    validation payload that says less.
    """

    model_config = ConfigDict(frozen=True)

    sequence_start: int | None = None
    sequence_end: int | None = None
    normalized_timestamp_start: float | None = None
    normalized_timestamp_end: float | None = None
    channel_ids: list[str] | None = None
    arbitration_ids: list[int] | None = None
    arbitration_id_start: int | None = None
    arbitration_id_end: int | None = None
    arbitration_id_mask: int | None = None
    arbitration_id_mask_value: int | None = None
    directions: list[str] | None = None
    is_extended: bool | None = None
    is_fd: bool | None = None


class TraceQueryRequest(BaseModel):
    """One bounded page request against a persisted CAN-X project."""

    model_config = ConfigDict(frozen=True)

    project_path: str
    session_id: str
    filters: TraceFilterRequest = Field(default_factory=TraceFilterRequest)
    after_sequence: int | None = None
    limit: int = DEFAULT_FRAME_QUERY_LIMIT


#: The Trace frame projection *is* the shared wire frame, not a second copy of
#: it. The identical shape is now also what the DBC decode surface accepts and
#: returns, and one definition is what keeps the two from drifting apart.
TraceFrameResponse = FrameWire


class TraceQueryResponse(BaseModel):
    """One deterministic page plus its exclusive continuation cursor."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    frames: list[TraceFrameResponse]
    has_more: bool
    next_after_sequence: int | None


class TraceSummaryRequest(BaseModel):
    """One bounded aggregate request against a persisted CAN-X project."""

    model_config = ConfigDict(frozen=True)

    project_path: str
    session_id: str
    filters: TraceFilterRequest = Field(default_factory=TraceFilterRequest)


class TraceSummaryResponse(BaseModel):
    """Bounded aggregate over a filter; bounds are absent exactly when empty."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    matching_frame_count: int
    first_sequence: int | None
    last_sequence: int | None
    first_normalized_timestamp: float | None
    last_normalized_timestamp: float | None


def create_trace_router() -> APIRouter:
    """Build the historical Trace router.

    A separate router keeps the Trace surface independent of the capture
    lifecycle, which it genuinely is: querying persisted data does not require a
    running capture, and a running capture does not make a project queryable.
    """
    router = APIRouter(tags=["trace"])

    @router.post("/trace/query", response_model=TraceQueryResponse)
    async def trace_query(request: TraceQueryRequest) -> TraceQueryResponse:
        """Return one bounded page of canonical frames from a persisted session."""
        page = await asyncio.to_thread(_query_page, request)
        return TraceQueryResponse(
            session_id=page.session_id,
            frames=[_frame_payload(frame) for frame in page.frames],
            has_more=page.has_more,
            next_after_sequence=page.next_after_sequence,
        )

    @router.post("/trace/summary", response_model=TraceSummaryResponse)
    async def trace_summary(request: TraceSummaryRequest) -> TraceSummaryResponse:
        """Return the matching count and bounds, aggregated inside the engine."""
        summary = await asyncio.to_thread(_summarize, request)
        return TraceSummaryResponse(
            session_id=summary.session_id,
            matching_frame_count=summary.matching_frame_count,
            first_sequence=summary.first_sequence,
            last_sequence=summary.last_sequence,
            first_normalized_timestamp=summary.first_normalized_timestamp,
            last_normalized_timestamp=summary.last_normalized_timestamp,
        )

    return router


def _query_page(request: TraceQueryRequest) -> FrameQueryPage:
    """Open the project and run one bounded page. Blocking by design."""
    query = FrameQuery(
        filter=to_frame_filter(request.session_id, request.filters),
        after_sequence=request.after_sequence,
        limit=request.limit,
    )
    with _open_project(request.project_path) as handle:
        return QueryService(handle.root).query_frames(query)


def _summarize(request: TraceSummaryRequest) -> FrameQuerySummary:
    """Open the project and run one bounded aggregate. Blocking by design."""
    frame_filter = to_frame_filter(request.session_id, request.filters)
    with _open_project(request.project_path) as handle:
        return QueryService(handle.root).summarize_frames(frame_filter)


def _open_project(project_path: str) -> ProjectHandle:
    """Validate the query target with the project domain, never a private copy.

    ``QueryService(Path(project_path))`` would bypass project validation entirely:
    the project domain is what decides whether a directory is a CAN-X project at
    all, and it is what guarantees a query is never run against a path that merely
    looks like one. A rejected path raises ``ProjectError``, which the application
    boundary reports as a structured 400.

    Raises:
        ProjectError: If the path is not a readable CAN-X project.
    """
    return ProjectService().open(Path(project_path))


def to_frame_filter(session_id: str, request: TraceFilterRequest) -> FrameFilter:
    """Translate the HTTP filter into the canonical query filter.

    Only names are mapped and arrays become tuples; every semantic decision —
    inclusive bounds, an inverted range, a half-supplied mask, a blank channel, an
    id outside the 29-bit space — stays with :class:`FrameFilter`, so the HTTP
    layer can never accept something the domain would refuse.

    Raises:
        QueryValidationError: If the translated filter is not a valid one.
    """
    return FrameFilter(
        session_id=session_id,
        sequence_start=request.sequence_start,
        sequence_end=request.sequence_end,
        normalized_timestamp_start=request.normalized_timestamp_start,
        normalized_timestamp_end=request.normalized_timestamp_end,
        channel_ids=_as_tuple(request.channel_ids),
        arbitration_ids=_as_tuple(request.arbitration_ids),
        arbitration_id_start=request.arbitration_id_start,
        arbitration_id_end=request.arbitration_id_end,
        arbitration_id_mask=request.arbitration_id_mask,
        arbitration_id_mask_value=request.arbitration_id_mask_value,
        directions=_to_directions(request.directions),
        is_extended=request.is_extended,
        is_fd=request.is_fd,
    )


def _as_tuple[T](values: list[T] | None) -> tuple[T, ...] | None:
    """Convert a JSON array into the tuple the domain model requires.

    ``None`` stays ``None`` ("this axis does not restrict anything"), while an
    empty array becomes an empty tuple and is rejected by the model. The two are
    different requests and must not collapse into the same behaviour.
    """
    return None if values is None else tuple(values)


def _to_directions(values: list[str] | None) -> tuple[Direction, ...] | None:
    """Convert wire direction names into the canonical enum.

    Raises:
        QueryValidationError: If a supplied name is not a known direction.
    """
    if values is None:
        return None
    try:
        return tuple(Direction(value) for value in values)
    except ValueError as error:
        raise QueryValidationError(
            "each direction must be 'rx' or 'tx'.",
            code="query.invalid_direction",
            details={"directions": list(values)},
        ) from error


def _frame_payload(frame: Frame) -> TraceFrameResponse:
    """Render one canonical frame for the wire.

    The projection itself lives in :mod:`canx.api.frame`, because the DBC decode
    surface now speaks the same shape. A private copy here is precisely the drift
    that module exists to prevent.
    """
    return frame_to_wire(frame)
