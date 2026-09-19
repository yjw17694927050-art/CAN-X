"""Typed failure contract for the CAN-X historical Plot domain.

Callers never see a bare ``duckdb`` error, ``sqlite3.Error``, ``OSError``, a
``pyarrow`` error or a raw ``cantools`` exception. Every failure that crosses the
plot boundary is a :class:`PlotError` carrying the structured fields required by
SPEC §38: ``code``, the human ``message``, ``details``, ``recoverable`` and
``source``.

The hierarchy mirrors :mod:`canx.query.errors`, :mod:`canx.data.errors` and
:mod:`canx.dbc.errors`, so all four domains read the same way at a call site. The
plot domain sits *above* those three: it owns no file, no SQL and no bit
arithmetic, and it translates their typed failures into its own without ever
hiding the original ``code`` in ``details["cause"]``.
"""

from __future__ import annotations


class PlotError(Exception):
    """Base class for every diagnosable historical-plot failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "plot.error",
        details: dict[str, object] | None = None,
        recoverable: bool = False,
        source: str = "plot",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details: dict[str, object] = {} if details is None else dict(details)
        self.recoverable = recoverable
        self.source = source


class PlotValidationError(PlotError):
    """Raised when a public plot argument cannot express a valid query.

    A caller that supplies an unusable signal identity, time window or sample
    budget gets this error before any file is opened, so an invalid request never
    costs a scan.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "plot.validation_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class PlotSessionError(PlotError):
    """Raised when the requested data session is not registered in the project.

    ``recoverable`` stays ``False`` on purpose: the session either is or is not
    part of this project, and no retry changes that.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "plot.session_not_found",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class PlotAssetError(PlotError):
    """Raised when the project-owned DBC asset behind a signal cannot be trusted.

    This is the fail-closed gate for the decode half of the source-of-truth
    chain: a missing, unregistered, wrong-project, resized or tampered DBC asset
    never yields a plausible series. ``recoverable`` is ``True`` because the file
    may be restored (re-imported) and the exact same query can then succeed —
    except that a genuine registry mismatch is not papered over either.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "plot.asset_unavailable",
        details: dict[str, object] | None = None,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=recoverable)


class PlotSignalNotFoundError(PlotError):
    """Raised when the DBC asset does not define the requested signal identity.

    The requested message or signal name is not part of the verified document, so
    no series can be produced. This is reported rather than silently returning an
    empty series, because "the document does not declare this signal" and "the
    signal was captured zero times" are different facts.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "plot.signal_not_found",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class PlotQueryError(PlotError):
    """Raised when the bounded frame query underneath the series failed.

    The cause is always a :class:`~canx.query.errors.QueryError` — a tampered
    segment, a missing segment, a path escaping the project or an engine failure.
    It is translated rather than re-raised so the plot boundary speaks one failure
    language, while ``details["cause"]`` keeps the original query ``code``.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "plot.query_failed",
        details: dict[str, object] | None = None,
        recoverable: bool = True,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=recoverable)
