"""Typed failure contract for the CAN-X data domain.

Callers never see a bare ``OSError``, ``sqlite3.Error``, ``ValueError`` or an
``pyarrow`` error. Every failure that crosses the data boundary is a
:class:`DataError` carrying the structured fields required by SPEC §38:
``code``, the human ``message``, ``details``, ``recoverable`` and ``source``.

The hierarchy mirrors :mod:`canx.project.errors` so both domains read the same
way at a call site.
"""

from __future__ import annotations


class DataError(Exception):
    """Base class for every diagnosable data-domain failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "data.error",
        details: dict[str, object] | None = None,
        recoverable: bool = False,
        source: str = "data",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details: dict[str, object] = {} if details is None else dict(details)
        self.recoverable = recoverable
        self.source = source


class DataValidationError(DataError):
    """Raised when a public data argument cannot produce a valid model."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "data.validation_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DataSessionError(DataError):
    """Raised for a data-session level failure that is not a state violation."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "data.session_failed",
        details: dict[str, object] | None = None,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=recoverable)


class DataSessionStateError(DataSessionError):
    """Raised when a lifecycle operation is invalid for the current state."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "data.session.invalid_state",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)


class DataStreamMismatchError(DataSessionError):
    """Raised when a batch belongs to a stream the session does not own."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "data.session.stream_mismatch",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)


class DataStorageError(DataSessionError):
    """Raised when durable metadata could not be written or read."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "data.storage_failed",
        details: dict[str, object] | None = None,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=recoverable)


class ParquetWriteError(DataError):
    """Raised when a Parquet segment could not be durably committed."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "data.parquet_write_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class ParquetReadError(DataError):
    """Raised when a Parquet segment file cannot be read at all."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "data.parquet_read_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class DataIntegrityError(DataError):
    """Raised when persisted data contradicts the session/segment contract.

    Also used for a readable Parquet file that is not a CAN-X segment of the
    expected session, or whose stored row count does not match its metadata.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "data.integrity_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)
