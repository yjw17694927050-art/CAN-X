"""Historical signal query: one session, one signal identity, one bounded series.

This service is the frozen V0.4-04 contract. It owns no storage, no SQL and no bit
arithmetic: it composes the three domains that already do, in the order the
source-of-truth chain demands, and it never lets a lower-level object escape.

```text
Project
  -> registered DataSession           (canx.data.session.DataSessionService)
  -> verified Parquet segments        (canx.query.service.QueryService:
                                       resolve-within-root + footer validation)
  -> canonical Frame                  (bounded, ordered pages; never a whole session)
  -> verified project-owned DBC asset (canx.dbc.project_service.ProjectDbcService:
                                       registry row + size + SHA-256, fail closed)
  -> DbcDecoder                       (canx.dbc.decode.DbcDecoder)
  -> bounded PlotSeries               (StreamingDownsampler: Runtime-side min/max)
```

Six properties are deliberate:

* **No second engine.** Frames come from the existing bounded query — the same
  ``QueryService``/``QueryEngine`` the rest of the Runtime uses — so segment
  integrity, path containment and ordering are not re-implemented here.
* **No whole-dataset load.** Reads are paged by an exclusive ``sequence`` cursor,
  so memory never grows with the session; only a bounded page is ever in hand.
* **The DBC is trusted only after it is verified.** The asset is loaded through
  the gate that proves the registry row, the size and the SHA-256 before a single
  bit is decoded, so a tampered or missing DBC fails closed instead of producing a
  plausible-looking curve.
* **Channel, asset, message and signal all agree.** The frame filter is built from
  the *identity's own* channel and the message's own ``(frame_id, is_extended,
  is_fd)``, so two same-named signals on different channels, or the same message in
  two assets, can never be merged into one series.
* **Downsampling is online and Runtime-side.** The
  :class:`~canx.plot.downsample.StreamingDownsampler` keeps at most
  ``sample_budget`` points while streaming, so the working set tracks the page size
  and the budget, not the dataset.
* **The result is deterministic.** Frames are read in the engine's fixed sequence
  order, the reducer's tie-breaks are total, and the final samples are ordered by
  timestamp — so two runs over the same data return an equal series.
"""

from __future__ import annotations

from pathlib import Path

from canx.data.errors import DataError, DataSessionError, DataStorageError
from canx.data.model import DataSession
from canx.data.session import DataSessionService
from canx.dbc.decode import DbcDecoder
from canx.dbc.errors import DbcAssetIntegrityError, DbcAssetNotFoundError, DbcError
from canx.dbc.model import DbcDatabase, DbcMessage
from canx.dbc.project_service import ProjectDbcService
from canx.domain.frame import Frame
from canx.plot.downsample import StreamingDownsampler
from canx.plot.errors import (
    PlotAssetError,
    PlotQueryError,
    PlotSessionError,
    PlotSignalNotFoundError,
    PlotValidationError,
)
from canx.plot.model import PlotQuery, PlotSample, PlotSeries, SignalIdentity
from canx.project.errors import ProjectError
from canx.query.errors import QueryError
from canx.query.model import FrameFilter, FrameQuery, FrameQueryPage
from canx.query.service import QueryService

#: The number of frames read per bounded page. Well under the engine's hard
#: ``MAX_QUERY_ROWS`` so a single page can never be an unbounded allocation.
FRAME_PAGE_SIZE = 5_000


class HistoricalSignalQueryService:
    """Stateless historical signal-series entry point over one CAN-X project.

    Like the services it composes, it holds nothing between calls: every operation
    opens its own short-lived connections through ``DataSessionService``,
    ``QueryService`` and ``ProjectDbcService``. The three collaborators are
    injectable so a caller can substitute an instrumented one, but the defaults are
    the real, production services — there is no mock in the shipped path.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        data_sessions: DataSessionService | None = None,
        query: QueryService | None = None,
        dbc: ProjectDbcService | None = None,
    ) -> None:
        self._root = Path(project_root)
        self._data = DataSessionService(self._root) if data_sessions is None else data_sessions
        self._query = QueryService(self._root) if query is None else query
        self._dbc = ProjectDbcService(self._root) if dbc is None else dbc

    @property
    def project_root(self) -> Path:
        """Return the project root this service reads."""
        return self._root

    def query_signal_series(self, query: PlotQuery) -> PlotSeries:
        """Return the bounded, deterministic historical series for one signal.

        Args:
            query: A validated :class:`~canx.plot.model.PlotQuery`.

        Returns:
            The matching samples, reduced to at most ``query.sample_budget`` points
            and ordered by non-decreasing timestamp.

        Raises:
            PlotValidationError: If ``query`` is not a :class:`PlotQuery`.
            PlotSessionError: If the project is unusable or the session is not
                registered in it.
            PlotAssetError: If the identity's DBC asset is missing, unregistered,
                owned by another project, or no longer matches its registered size
                or digest (fail closed).
            PlotSignalNotFoundError: If the verified DBC asset does not declare the
                requested message or signal.
            PlotQueryError: If the bounded frame query failed (a missing or
                tampered segment, a path escaping the project, an engine failure).
        """
        if not isinstance(query, PlotQuery):
            raise PlotValidationError(
                "query_signal_series expects a PlotQuery.",
                code="plot.invalid_query",
                details={"type": type(query).__name__},
            )
        identity = query.identity
        # 1. Project + registered DataSession. This validates both before any
        #    segment or DBC file is touched.
        self._require_registered_session(query.session_id)
        # 2. Verified project-owned DBC asset -> canonical message + signal.
        database = self._verified_database(identity.asset_id)
        message = _require_message(database, identity)
        _require_signal(message, identity)
        # 3. Decode against the verified document and reduce online.
        decoder = DbcDecoder(database)
        frame_filter = _frame_filter(query, message)
        downsampler = self._collect(
            frame_filter=frame_filter,
            decoder=decoder,
            message_name=message.name,
            signal_name=identity.signal_name,
            sample_budget=query.sample_budget,
        )
        samples = tuple(
            sorted(downsampler.finish(), key=lambda sample: (sample.time, sample.sequence))
        )
        return PlotSeries(
            session_id=query.session_id,
            identity=identity,
            samples=samples,
            matched_frame_count=downsampler.added_count,
            sample_budget=query.sample_budget,
        )

    def _collect(
        self,
        *,
        frame_filter: FrameFilter,
        decoder: DbcDecoder,
        message_name: str,
        signal_name: str,
        sample_budget: int,
    ) -> StreamingDownsampler:
        """Stream bounded pages of frames into an online min/max reducer.

        The exclusive ``sequence`` cursor pages through the session without an
        ``OFFSET`` scan and without ever holding more than one page, so the working
        set is the page size plus the sample budget — never the dataset.

        Raises:
            PlotQueryError: If the bounded frame query failed.
        """
        downsampler = StreamingDownsampler(sample_budget=sample_budget)
        cursor: int | None = None
        while True:
            page = self._page(frame_filter, cursor)
            for frame in page.frames:
                sample = _sample_for(frame, decoder, message_name, signal_name)
                if sample is not None:
                    downsampler.add(sample)
            if not page.has_more:
                break
            cursor = page.next_after_sequence
        return downsampler

    def _page(self, frame_filter: FrameFilter, cursor: int | None) -> FrameQueryPage:
        try:
            return self._query.query_frames(
                FrameQuery(filter=frame_filter, after_sequence=cursor, limit=FRAME_PAGE_SIZE)
            )
        except QueryError as error:
            raise PlotQueryError(
                "The bounded frame query failed while reading persisted frames.",
                code="plot.query_failed",
                details={"cause": error.code, "message": error.message},
                recoverable=error.recoverable,
            ) from error

    def _require_registered_session(self, session_id: str) -> DataSession:
        """Prove the project is usable and the session is registered here.

        Raises:
            PlotSessionError: If the project database is unusable or the session is
                not registered.
        """
        try:
            return self._data.get_session(session_id)
        except DataStorageError as error:
            # `DataStorageError` is a `DataSessionError`, so it must be caught
            # first: an unusable project database is not a missing session.
            raise PlotSessionError(
                "The project data store is not available for a plot query.",
                code="plot.project_unavailable",
                details={"project_root": str(self._root), "cause": error.code},
            ) from error
        except DataSessionError as error:
            raise PlotSessionError(
                "The requested data session is not registered in this project.",
                code="plot.session_not_found",
                details={"session_id": session_id, "cause": error.code},
            ) from error
        except DataError as error:
            raise PlotSessionError(
                "The project data store is not available for a plot query.",
                code="plot.project_unavailable",
                details={"project_root": str(self._root), "cause": error.code},
            ) from error

    def _verified_database(self, asset_id: str) -> DbcDatabase:
        """Load the canonical database of one verified, project-owned asset.

        The load proves the registry row, the size and the SHA-256 *before* the
        document is parsed, so a missing, unregistered, cross-project or tampered
        DBC is reported as an asset failure and no series is produced.

        Raises:
            PlotAssetError: If the asset cannot be verified or is unreadable.
        """
        try:
            return self._dbc.load_asset(asset_id).database
        except DbcAssetIntegrityError as error:
            raise PlotAssetError(
                "The DBC asset no longer matches its registered size or digest.",
                code="plot.asset_integrity_failed",
                details={"asset_id": asset_id, "cause": error.code},
                recoverable=True,
            ) from error
        except DbcAssetNotFoundError as error:
            raise PlotAssetError(
                "The DBC asset is not registered in this project.",
                code="plot.asset_not_found",
                details={"asset_id": asset_id, "cause": error.code},
            ) from error
        except DbcError as error:
            raise PlotAssetError(
                "The DBC asset could not be read or parsed.",
                code="plot.asset_unavailable",
                details={"asset_id": asset_id, "cause": error.code},
            ) from error
        except ProjectError as error:
            raise PlotAssetError(
                "The project is not a readable CAN-X project for DBC resolution.",
                code="plot.project_unavailable",
                details={"project_root": str(self._root), "cause": error.code},
            ) from error


def _frame_filter(query: PlotQuery, message: DbcMessage) -> FrameFilter:
    """Translate a plot request into the exact frame predicate it means.

    Every axis comes from the request's own identity or the message's own
    definition — the channel, the arbitration id, the extended flag and the CAN FD
    flag — so the engine can only ever return frames that belong to *this* signal.
    Timestamp bounds stay a frame-level predicate inside the engine, exactly as the
    query domain requires.
    """
    identity = query.identity
    return FrameFilter(
        session_id=query.session_id,
        channel_ids=(identity.channel_id,),
        arbitration_ids=(message.frame_id,),
        is_extended=message.is_extended,
        is_fd=message.is_fd,
        normalized_timestamp_start=query.time_start,
        normalized_timestamp_end=query.time_end,
    )


def _require_message(database: DbcDatabase, identity: SignalIdentity) -> DbcMessage:
    for message in database.messages:
        if message.name == identity.message_name:
            return message
    raise PlotSignalNotFoundError(
        "The DBC asset does not define the requested message.",
        code="plot.message_not_found",
        details={
            "asset_id": identity.asset_id,
            "message_name": identity.message_name,
        },
    )


def _require_signal(message: DbcMessage, identity: SignalIdentity) -> None:
    if any(signal.name == identity.signal_name for signal in message.signals):
        return
    raise PlotSignalNotFoundError(
        "The DBC message does not define the requested signal.",
        code="plot.signal_not_found",
        details={
            "message_name": identity.message_name,
            "signal_name": identity.signal_name,
        },
    )


def _sample_for(
    frame: Frame, decoder: DbcDecoder, message_name: str, signal_name: str
) -> PlotSample | None:
    """Decode one frame and project the requested signal, or return ``None``.

    A frame that does not carry the signal contributes no point rather than failing
    the whole series: an unknown message, a payload shorter than the definition, an
    unsupported definition or an inactive multiplexed signal is data, not an error
    — the same way the realtime pipeline treats a failed decode outcome. A frame
    that *is* about the signal contributes its physical value at the frame's
    normalized timestamp.
    """
    try:
        decoded = decoder.decode_frame(frame)
    except DbcError:
        return None
    if decoded.message_name != message_name:
        return None
    signal = decoded.signal(signal_name)
    if signal is None:
        return None
    return PlotSample(
        time=frame.normalized_timestamp,
        value=signal.physical_value,
        sequence=frame.sequence,
    )
