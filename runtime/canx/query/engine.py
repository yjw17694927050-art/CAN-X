"""Bounded, structured DuckDB execution (internal).

This module is the only place in CAN-X that speaks SQL and the only place that
touches ``duckdb``. Three properties are deliberate and load-bearing:

* **No arbitrary SQL.** Every column name, operator and ordering keyword is a
  code-owned constant. Values are bound parameters, and a repeated ``?`` group
  only ever repeats the same code-owned placeholder, so no caller-supplied
  string can become SQL.
* **Bounded results.** Every frame query filters, orders and limits *inside*
  DuckDB and fetches at most ``limit + 1`` rows. Nothing materializes a whole
  dataset, and no ``pandas``/``pyarrow`` table is built from a result.
* **Short-lived connections.** Each operation opens its own in-memory DuckDB
  connection and closes it when done, so querying never leaves a Windows file
  handle behind and never creates a persistent query database.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any

import duckdb

from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.query.errors import QueryExecutionError, QueryIntegrityError
from canx.query.model import (
    ArbitrationIdCount,
    FrameFilter,
    FrameQueryPage,
    FrameQuerySummary,
)

#: The canonical frame projection, in the persisted column order. This is the
#: whitelist: a caller can never ask for a column outside it.
_SELECTED_COLUMNS: tuple[str, ...] = (
    "sequence",
    "channel_id",
    "arbitration_id",
    "is_extended",
    "is_fd",
    "bitrate_switch",
    "error_state_indicator",
    "dlc",
    "data",
    "direction",
    "hardware_timestamp",
    "host_timestamp",
    "normalized_timestamp",
    "clock_domain",
    "timestamp_quality",
    "flags",
)

_FRAME_PROJECTION = ", ".join(_SELECTED_COLUMNS)

#: The single fixed ordering for the first version of the query engine.
_ORDER_BY = " ORDER BY sequence ASC"

#: The only allowed table function, always bound to a validated path list.
_PARQUET_SOURCE = "read_parquet(?)"

#: DuckDB resolves a bound path as a glob pattern. Translated in a single pass so
#: the bracket inserted for one metacharacter is never re-escaped by the next.
_PATH_GLOB_ESCAPE = str.maketrans({"[": "[[]", "*": "[*]", "?": "[?]"})


class QueryEngine:
    """Executes bounded structured queries over already-validated segments.

    The engine trusts its caller for two things only: the paths it is handed are
    real, project-relative segment files that already passed footer validation,
    and the filter/cursor are valid models. Everything else — SQL, ordering,
    bounds — is owned here.
    """

    def query_frames(
        self,
        *,
        paths: tuple[Path, ...],
        frame_filter: FrameFilter,
        after_sequence: int | None,
        limit: int,
    ) -> FrameQueryPage:
        """Return one deterministic page of canonical frames.

        Fetches ``limit + 1`` rows to decide ``has_more`` without an ``OFFSET``
        scan, then keeps at most ``limit`` of them.

        Raises:
            QueryExecutionError: If the engine could not execute the query.
            QueryIntegrityError: If a stored row no longer describes a frame.
        """
        if not paths:
            return _empty_page(frame_filter.session_id)
        clauses, parameters = _frame_predicates(frame_filter)
        if after_sequence is not None:
            clauses.append("sequence > ?")
            parameters.append(after_sequence)
        statement = (
            f"SELECT {_FRAME_PROJECTION} FROM {_PARQUET_SOURCE}{_where(clauses)}"
            f"{_ORDER_BY} LIMIT ?"
        )
        rows = self._run(
            statement, [_path_list(paths), *parameters, limit + 1]
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit] if has_more else rows
        frames = tuple(_row_to_frame(row) for row in page_rows)
        return FrameQueryPage(
            session_id=frame_filter.session_id,
            frames=frames,
            has_more=has_more,
            next_after_sequence=frames[-1].sequence if has_more and frames else None,
        )

    def summarize(self, *, paths: tuple[Path, ...], frame_filter: FrameFilter) -> FrameQuerySummary:
        """Return a bounded aggregate over the filter, computed inside DuckDB.

        Raises:
            QueryExecutionError: If the engine could not execute the query.
        """
        if not paths:
            return _empty_summary(frame_filter.session_id)
        clauses, parameters = _frame_predicates(frame_filter)
        statement = (
            "SELECT count(*), min(sequence), max(sequence),"
            " min(normalized_timestamp), max(normalized_timestamp)"
            f" FROM {_PARQUET_SOURCE}{_where(clauses)}"
        )
        rows = self._run(statement, [_path_list(paths), *parameters])
        if not rows:
            return _empty_summary(frame_filter.session_id)
        count, first_sequence, last_sequence, first_timestamp, last_timestamp = rows[0]
        count_value = int(count)
        if count_value == 0:
            return _empty_summary(frame_filter.session_id)
        return FrameQuerySummary(
            session_id=frame_filter.session_id,
            matching_frame_count=count_value,
            first_sequence=int(first_sequence),
            last_sequence=int(last_sequence),
            first_normalized_timestamp=float(first_timestamp),
            last_normalized_timestamp=float(last_timestamp),
        )

    def count_by_arbitration_id(
        self,
        *,
        paths: tuple[Path, ...],
        frame_filter: FrameFilter,
        top_n: int,
    ) -> tuple[ArbitrationIdCount, ...]:
        """Return a bounded, deterministically ordered arbitration-id histogram.

        The grouping happens in DuckDB and the result is capped at ``top_n``, so
        the aggregate can never grow a Python list proportional to the dataset.

        Raises:
            QueryExecutionError: If the engine could not execute the query.
        """
        if not paths:
            return ()
        clauses, parameters = _frame_predicates(frame_filter)
        statement = (
            "SELECT arbitration_id, is_extended, count(*) AS frame_count"
            f" FROM {_PARQUET_SOURCE}{_where(clauses)}"
            " GROUP BY arbitration_id, is_extended"
            " ORDER BY frame_count DESC, arbitration_id ASC LIMIT ?"
        )
        rows = self._run(statement, [_path_list(paths), *parameters, top_n])
        return tuple(_row_to_arbitration_count(row) for row in rows)

    def _run(self, statement: str, parameters: list[object]) -> list[Any]:
        """Execute one bounded statement on a fresh connection and close it.

        Raises:
            QueryExecutionError: If DuckDB refused to execute the statement. No
                ``duckdb.Error`` ever crosses this boundary.
        """
        connection = _connect()
        try:
            try:
                return list(connection.execute(statement, parameters).fetchall())
            except duckdb.Error as error:
                raise QueryExecutionError(
                    "The query engine could not execute the bounded query.",
                    code="query.execution_failed",
                    details={"error": str(error), "engine": "duckdb"},
                ) from error
        finally:
            _close_quietly(connection)


def _connect() -> duckdb.DuckDBPyConnection:
    """Open a short-lived in-memory connection (a seam for failure tests)."""
    return duckdb.connect(":memory:")


def _close_quietly(connection: duckdb.DuckDBPyConnection) -> None:
    """Best-effort close so the original failure always wins."""
    with suppress(duckdb.Error):
        connection.close()


def _path_list(paths: tuple[Path, ...]) -> list[str]:
    """Return the bound path list for ``read_parquet``, escaped to literals.

    Paths are passed as a value, never interpolated into SQL, and use POSIX
    separators so the same statement is portable. DuckDB then resolves each value
    as a glob pattern, and ``[`` is legal in a Windows file name: an unescaped
    project path could silently resolve to a *different* file (observed during
    development: ``a[1]/s.parquet`` returned ``a1/s.parquet``). Escaping every
    glob metacharacter to a literal keeps the query bound to the exact file the
    header validation already approved.
    """
    return [path.as_posix().translate(_PATH_GLOB_ESCAPE) for path in paths]


def _where(clauses: list[str]) -> str:
    return "" if not clauses else " WHERE " + " AND ".join(clauses)


def _frame_predicates(frame_filter: FrameFilter) -> tuple[list[str], list[object]]:
    """Translate a typed filter into whitelisted clauses plus bound parameters.

    Every clause is a constant string; every caller value travels as a bound
    parameter. The repeated ``?`` groups are sized by an already-validated tuple,
    not by caller text.

    The CAN id axes stay three independent predicates — a set membership test, an
    inclusive range and a masked equality — joined with ``AND`` exactly like every
    other axis. The masked predicate's right-hand side is not computed here: the
    domain model owns the reduction, so ``(id & mask) == (value & mask)`` has one
    definition in one place.
    """
    clauses: list[str] = []
    parameters: list[object] = []
    if frame_filter.sequence_start is not None:
        clauses.append("sequence >= ?")
        parameters.append(frame_filter.sequence_start)
    if frame_filter.sequence_end is not None:
        clauses.append("sequence <= ?")
        parameters.append(frame_filter.sequence_end)
    if frame_filter.normalized_timestamp_start is not None:
        clauses.append("normalized_timestamp >= ?")
        parameters.append(float(frame_filter.normalized_timestamp_start))
    if frame_filter.normalized_timestamp_end is not None:
        clauses.append("normalized_timestamp <= ?")
        parameters.append(float(frame_filter.normalized_timestamp_end))
    if frame_filter.channel_ids is not None:
        clauses.append(f"channel_id IN ({_placeholders(len(frame_filter.channel_ids))})")
        parameters.extend(frame_filter.channel_ids)
    if frame_filter.arbitration_ids is not None:
        clauses.append(f"arbitration_id IN ({_placeholders(len(frame_filter.arbitration_ids))})")
        parameters.extend(frame_filter.arbitration_ids)
    if frame_filter.arbitration_id_start is not None:
        clauses.append("arbitration_id >= ?")
        parameters.append(frame_filter.arbitration_id_start)
    if frame_filter.arbitration_id_end is not None:
        clauses.append("arbitration_id <= ?")
        parameters.append(frame_filter.arbitration_id_end)
    # The masked equality test is `(id & mask) = target`, where the target is the
    # mask value already reduced through the mask by the domain model. Both the
    # bitwise-and operator and the column are code-owned; both operands are bound.
    mask = frame_filter.arbitration_id_mask
    mask_target = frame_filter.arbitration_id_mask_target
    if mask is not None and mask_target is not None:
        clauses.append("(arbitration_id & ?) = ?")
        parameters.extend((mask, mask_target))
    if frame_filter.directions is not None:
        clauses.append(f"direction IN ({_placeholders(len(frame_filter.directions))})")
        parameters.extend(direction.value for direction in frame_filter.directions)
    if frame_filter.is_extended is not None:
        clauses.append("is_extended = ?")
        parameters.append(frame_filter.is_extended)
    if frame_filter.is_fd is not None:
        clauses.append("is_fd = ?")
        parameters.append(frame_filter.is_fd)
    return clauses, parameters


def _placeholders(count: int) -> str:
    return ", ".join("?" for _ in range(count))


def _row_to_frame(row: Any) -> Frame:
    """Rebuild a canonical frame from one engine row.

    Raises:
        QueryIntegrityError: If the row's values no longer describe a frame.
    """
    values = dict(zip(_SELECTED_COLUMNS, row, strict=True))
    try:
        return Frame(
            sequence=int(values["sequence"]),
            channel_id=str(values["channel_id"]),
            arbitration_id=int(values["arbitration_id"]),
            is_extended=bool(values["is_extended"]),
            is_fd=bool(values["is_fd"]),
            bitrate_switch=bool(values["bitrate_switch"]),
            error_state_indicator=bool(values["error_state_indicator"]),
            dlc=int(values["dlc"]),
            data=bytes(values["data"]),
            direction=Direction(str(values["direction"])),
            hardware_timestamp=(
                None
                if values["hardware_timestamp"] is None
                else float(values["hardware_timestamp"])
            ),
            host_timestamp=float(values["host_timestamp"]),
            normalized_timestamp=float(values["normalized_timestamp"]),
            clock_domain=str(values["clock_domain"]),
            timestamp_quality=TimestampQuality(str(values["timestamp_quality"])),
            flags=int(values["flags"]),
        )
    except (ValueError, TypeError, KeyError) as error:
        raise QueryIntegrityError(
            "A stored frame row does not describe a valid canonical frame.",
            code="query.frame_invalid",
            details={"error": str(error)},
        ) from error


def _row_to_arbitration_count(row: Any) -> ArbitrationIdCount:
    """Rebuild one histogram row.

    Raises:
        QueryIntegrityError: If the row's values no longer describe a count.
    """
    try:
        return ArbitrationIdCount(
            arbitration_id=int(row[0]),
            is_extended=bool(row[1]),
            frame_count=int(row[2]),
        )
    except (ValueError, TypeError, IndexError) as error:
        raise QueryIntegrityError(
            "A stored arbitration-id count does not describe a valid row.",
            code="query.frame_invalid",
            details={"error": str(error)},
        ) from error


def _empty_page(session_id: str) -> FrameQueryPage:
    return FrameQueryPage(
        session_id=session_id,
        frames=(),
        has_more=False,
        next_after_sequence=None,
    )


def _empty_summary(session_id: str) -> FrameQuerySummary:
    return FrameQuerySummary(
        session_id=session_id,
        matching_frame_count=0,
        first_sequence=None,
        last_sequence=None,
        first_normalized_timestamp=None,
        last_normalized_timestamp=None,
    )
