"""Bounded historical query service for one CAN-X project.

The service is the public entry point of the query domain. It owns exactly three
responsibilities and delegates the rest:

1. take a **metadata snapshot** of one session's committed segments;
2. **validate and prune** the candidate files — resolve each stored path inside
   the project root, confirm it exists, and confirm the file agrees with every
   claim its registered row makes (canonical path, session, stream, index, column
   layout, row count, byte size) — without materializing a row. That agreement is
   not re-defined here: it is the same gate the DataSession read path calls;
3. ask the engine for a **bounded** result and return it as a typed model.

It never exposes SQL, never materializes a whole session, and never lets a
``duckdb``, ``sqlite3`` or ``pyarrow`` object escape.

Query scope is deliberately one data session. A session already spans many
Parquet segments, which is the cross-file query SPEC asks for; widening it to the
whole project is a later increment.
"""

from __future__ import annotations

from pathlib import Path

from canx.data import repository
from canx.data.errors import DataError, DataSessionError, DataStorageError, ParquetReadError
from canx.data.model import DataSegment, DataSession
from canx.data.session import require_registered_segment, resolve_within_root
from canx.query import planning
from canx.query.engine import QueryEngine
from canx.query.errors import (
    QueryDataUnavailableError,
    QueryIntegrityError,
    QuerySessionError,
    QueryValidationError,
)
from canx.query.model import (
    DEFAULT_ARBITRATION_ID_TOP_N,
    MAX_ARBITRATION_ID_TOP_N,
    ArbitrationIdCount,
    FrameFilter,
    FrameQuery,
    FrameQueryPage,
    FrameQuerySummary,
    QueryPlan,
)


class QueryService:
    """Stateless bounded-query entry point over one project's data sessions.

    Like ``DataSessionService`` the service keeps nothing between calls: every
    operation opens its own short-lived SQLite connection for metadata and its
    own short-lived in-memory DuckDB connection for execution, so a query can
    never leave a Windows file handle behind.
    """

    def __init__(self, project_root: Path, *, engine: QueryEngine | None = None) -> None:
        self._root = Path(project_root)
        self._engine = QueryEngine() if engine is None else engine

    @property
    def project_root(self) -> Path:
        """Return the project root this service reads."""
        return self._root

    def query_frames(self, query: FrameQuery) -> FrameQueryPage:
        """Return one deterministic, bounded page of canonical frames.

        Args:
            query: A validated :class:`FrameQuery` (filter, cursor, limit).

        Raises:
            QueryValidationError: If ``query`` is not a :class:`FrameQuery`.
            QuerySessionError: If the session is not registered in this project.
            QueryDataUnavailableError: If a candidate segment file is missing.
            QueryIntegrityError: If a candidate segment is not a valid CAN-X
                segment of the expected session, or its path escapes the project.
            QueryExecutionError: If the engine could not execute the query.
        """
        _require_frame_query(query)
        session, segments = self._snapshot(query.session_id)
        paths = self._candidate_paths(
            session,
            segments,
            planning.plan_segments(
                segments, query.filter, after_sequence=query.after_sequence
            ),
        )
        return self._engine.query_frames(
            paths=paths,
            frame_filter=query.filter,
            after_sequence=query.after_sequence,
            limit=query.limit,
        )

    def summarize_frames(self, frame_filter: FrameFilter) -> FrameQuerySummary:
        """Return a bounded aggregate over ``frame_filter``.

        The count and the sequence/timestamp bounds are computed by the engine;
        no matched row is read into Python to be counted.

        Raises:
            QueryValidationError: If ``frame_filter`` is not a :class:`FrameFilter`.
            QuerySessionError: If the session is not registered in this project.
            QueryDataUnavailableError: If a candidate segment file is missing.
            QueryIntegrityError: If a candidate segment is not a valid CAN-X
                segment of the expected session, or its path escapes the project.
            QueryExecutionError: If the engine could not execute the query.
        """
        _require_frame_filter(frame_filter)
        session, segments = self._snapshot(frame_filter.session_id)
        paths = self._candidate_paths(
            session, segments, planning.plan_segments(segments, frame_filter)
        )
        return self._engine.summarize(paths=paths, frame_filter=frame_filter)

    def count_by_arbitration_id(
        self,
        frame_filter: FrameFilter,
        *,
        top_n: int = DEFAULT_ARBITRATION_ID_TOP_N,
    ) -> tuple[ArbitrationIdCount, ...]:
        """Return a bounded, deterministically ordered arbitration-id histogram.

        Raises:
            QueryValidationError: If ``frame_filter`` is not a :class:`FrameFilter`
                or ``top_n`` is not a positive integer within the hard maximum.
            QuerySessionError: If the session is not registered in this project.
            QueryDataUnavailableError: If a candidate segment file is missing.
            QueryIntegrityError: If a candidate segment is not a valid CAN-X
                segment of the expected session, or its path escapes the project.
            QueryExecutionError: If the engine could not execute the query.
        """
        _require_frame_filter(frame_filter)
        _require_top_n(top_n)
        session, segments = self._snapshot(frame_filter.session_id)
        paths = self._candidate_paths(
            session, segments, planning.plan_segments(segments, frame_filter)
        )
        return self._engine.count_by_arbitration_id(
            paths=paths, frame_filter=frame_filter, top_n=top_n
        )

    def plan_frames(
        self, frame_filter: FrameFilter, *, after_sequence: int | None = None
    ) -> QueryPlan:
        """Return which committed segments a query would scan, without reading data.

        Diagnostic surface: it takes a metadata snapshot and prunes it, so a
        caller can confirm that an unrelated segment is never handed to the
        engine. It does not open a single segment file.

        Raises:
            QueryValidationError: If ``frame_filter`` is not a :class:`FrameFilter`.
            QuerySessionError: If the session is not registered in this project.
        """
        _require_frame_filter(frame_filter)
        _session, segments = self._snapshot(frame_filter.session_id)
        return planning.plan_segments(
            segments, frame_filter, after_sequence=after_sequence
        )

    def _snapshot(self, session_id: str) -> tuple[DataSession, tuple[DataSegment, ...]]:
        """Freeze the committed-segment snapshot for one session.

        Reading the metadata once, before any file is touched, gives the whole
        operation a single snapshot boundary: frames still buffered in a live
        writer are invisible, and a segment committed after this call only ever
        appears to the *next* query.

        Raises:
            QuerySessionError: If the session is not registered or the project's
                data store is unusable.
        """
        try:
            with repository.data_connection(self._root) as connection:
                session = repository.require_registered_session(
                    repository.read_session(connection, session_id), session_id
                )
                segments = repository.list_segments(connection, session_id)
        except DataStorageError as error:
            # `DataStorageError` is a `DataSessionError`, so it is caught first:
            # an unusable project database is not a missing session.
            raise QuerySessionError(
                "The project data store is not available for query.",
                code="query.project_unavailable",
                details={"project_root": str(self._root), "cause": error.code},
            ) from error
        except DataSessionError as error:
            raise QuerySessionError(
                "The requested data session is not registered in this project.",
                code="query.session_not_found",
                details={"session_id": session_id, "cause": error.code},
            ) from error
        except DataError as error:
            raise QuerySessionError(
                "The project data store is not available for query.",
                code="query.project_unavailable",
                details={"project_root": str(self._root), "cause": error.code},
            ) from error
        return session, segments

    def _candidate_paths(
        self,
        session: DataSession,
        segments: tuple[DataSegment, ...],
        plan: QueryPlan,
    ) -> tuple[Path, ...]:
        """Resolve and footer-validate every segment the plan selected.

        Raises:
            QueryIntegrityError: If a stored path escapes the project root, is not
                the canonical path for its segment, or its footer is not a CAN-X
                segment of the expected session/stream/index.
            QueryDataUnavailableError: If a selected segment file is missing.
        """
        by_path = {segment.relative_path: segment for segment in segments}
        paths: list[Path] = []
        for relative_path in plan.relative_paths:
            segment = by_path[relative_path]
            path = self._resolve(session, segment)
            self._validate(session, segment, path)
            paths.append(path)
        return tuple(paths)

    def _resolve(self, session: DataSession, segment: DataSegment) -> Path:
        """Resolve one stored segment path, refusing anything outside the root.

        A persisted path is never trusted just because the database held it: the
        same ``resolve_within_root`` helper V0.2-02 established for segment IO is
        reused here, so a stored ``../../outside.parquet`` is rejected rather than
        handed to the engine.

        Raises:
            QueryIntegrityError: If the path escapes the project root.
        """
        try:
            return resolve_within_root(self._root, segment.relative_path)
        except DataError as error:
            raise QueryIntegrityError(
                "A registered segment path points outside the project root.",
                code="query.path_escape",
                details={
                    "session_id": session.session_id,
                    "segment_index": segment.segment_index,
                    "relative_path": segment.relative_path,
                    "cause": error.code,
                },
            ) from error

    def _validate(self, session: DataSession, segment: DataSegment, path: Path) -> None:
        """Confirm a candidate file agrees with everything its row claims.

        The agreement itself is not defined here: it is
        :func:`canx.data.session.require_registered_segment`, the one gate the
        :class:`~canx.data.session.DataSessionService` read path calls too. This
        method only translates that single data-domain outcome into the query
        domain's typed errors, keeping the data code visible as ``cause``.

        Raises:
            QueryDataUnavailableError: If the file does not exist.
            QueryIntegrityError: If the file cannot be read as Parquet, or is not
                the registered CAN-X segment of this session.
        """
        if not path.is_file():
            raise QueryDataUnavailableError(
                "A registered segment file is missing.",
                code="query.segment_missing",
                details={
                    "session_id": session.session_id,
                    "segment_index": segment.segment_index,
                    "relative_path": segment.relative_path,
                },
            )
        try:
            require_registered_segment(path, session=session, segment=segment)
        except ParquetReadError as error:
            raise QueryIntegrityError(
                "A registered segment file could not be read as a Parquet footer.",
                code="query.segment_unreadable",
                details={
                    "session_id": session.session_id,
                    "segment_index": segment.segment_index,
                    "relative_path": segment.relative_path,
                    "cause": error.code,
                },
            ) from error
        except DataError as error:
            raise QueryIntegrityError(
                "A registered segment file is not a CAN-X segment of this session.",
                code="query.segment_invalid",
                details={
                    "session_id": session.session_id,
                    "segment_index": segment.segment_index,
                    "relative_path": segment.relative_path,
                    "cause": error.code,
                },
            ) from error


def _require_frame_query(query: object) -> None:
    if not isinstance(query, FrameQuery):
        raise QueryValidationError(
            "query_frames expects a FrameQuery.",
            code="query.invalid_query",
            details={"type": type(query).__name__},
        )


def _require_frame_filter(frame_filter: object) -> None:
    if not isinstance(frame_filter, FrameFilter):
        raise QueryValidationError(
            "the query expects a FrameFilter.",
            code="query.invalid_filter",
            details={"type": type(frame_filter).__name__},
        )


def _require_top_n(top_n: object) -> None:
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n <= 0:
        raise QueryValidationError(
            "top_n must be a positive integer.",
            code="query.invalid_top_n",
            details={"top_n": repr(top_n)},
        )
    if top_n > MAX_ARBITRATION_ID_TOP_N:
        raise QueryValidationError(
            "top_n exceeds the maximum number of arbitration-id counts.",
            code="query.top_n_exceeded",
            details={"top_n": top_n, "max_top_n": MAX_ARBITRATION_ID_TOP_N},
        )
