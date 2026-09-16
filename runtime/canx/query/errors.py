"""Typed failure contract for the CAN-X query domain.

Callers never see a bare ``duckdb.Error``, ``sqlite3.Error``, ``OSError``,
``ValueError`` or ``pyarrow`` error. Every failure that crosses the query
boundary is a :class:`QueryError` carrying the structured fields required by
SPEC §38: ``code``, the human ``message``, ``details``, ``recoverable`` and
``source``.

The hierarchy mirrors :mod:`canx.project.errors` and :mod:`canx.data.errors`, so
all three domains read the same way at a call site.
"""

from __future__ import annotations


class QueryError(Exception):
    """Base class for every diagnosable query-domain failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "query.error",
        details: dict[str, object] | None = None,
        recoverable: bool = False,
        source: str = "query",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details: dict[str, object] = {} if details is None else dict(details)
        self.recoverable = recoverable
        self.source = source


class QueryValidationError(QueryError):
    """Raised when a public query argument cannot express a valid query.

    A caller that supplies an unusable filter, limit or cursor gets this error
    before any file is opened, so an invalid request never costs a scan.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "query.validation_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class QuerySessionError(QueryError):
    """Raised when the requested data session is not registered in the project."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "query.session_unavailable",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class QueryDataUnavailableError(QueryError):
    """Raised when a registered segment's data cannot be reached.

    Recoverable on purpose: the file may be restored (moved back, re-copied from
    a backup) and the exact same query can then succeed.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "query.segment_missing",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=True)


class QueryIntegrityError(QueryError):
    """Raised when a registered segment contradicts the CAN-X segment contract.

    Covers a foreign Parquet file, a segment belonging to another session or
    stream, an unsupported CAN-X schema, a corrupt file and a stored path that
    escapes the project root. Query never returns partial plausible results in
    these cases.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "query.integrity_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class QueryExecutionError(QueryError):
    """Raised when the query engine failed to execute an otherwise valid query.

    Recoverable on purpose: the engine failure is not caused by the request, so
    the same query may succeed when retried.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "query.execution_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=True)
