"""Typed failure contract for the CAN-X offline replay domain.

Replay never lets a bare ``OSError``, ``sqlite3.Error``, ``duckdb.Error`` or
``ValueError`` cross its boundary. Every failure a caller can observe is a
:class:`ReplayError` carrying the structured fields required by SPEC §38:
``code``, the human ``message``, ``details``, ``recoverable`` and ``source``.

The hierarchy mirrors :mod:`canx.query.errors` and :mod:`canx.data.errors`, so
every domain reads the same way at a call site. Each subclass maps to exactly one
arm of the offline-replay failure model:

``ReplayValidationError``   an unusable argument or configuration
``ReplayProjectError``      the project root is not a usable CAN-X project
``ReplaySessionError``      unknown session, unreplayable session state, empty session
``ReplaySourceError``       the source could not deliver the recorded frames
``ReplaySinkError``         the sink refused a frame
``ReplaySchedulerError``    the injected clock broke the scheduling contract
``ReplayStateError``        the replay lifecycle was driven in an invalid order

Cancellation is deliberately **not** an error: it is requested by the caller and
is reported as the terminal :attr:`~canx.replay.model.ReplayState.CANCELLED`
state of a normal :class:`~canx.replay.model.ReplayReport`.
"""

from __future__ import annotations


class ReplayError(Exception):
    """Base class for every diagnosable offline-replay failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "replay.error",
        details: dict[str, object] | None = None,
        recoverable: bool = False,
        source: str = "replay",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details: dict[str, object] = {} if details is None else dict(details)
        self.recoverable = recoverable
        self.source = source


class ReplayValidationError(ReplayError):
    """Raised when a public replay argument cannot describe a valid replay.

    An unusable configuration, sink, clock or session id is refused before any
    frame is read, so a bad request never costs a project read.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "replay.validation_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class ReplayProjectError(ReplayError):
    """Raised when the project root cannot serve as a replay source.

    Covers a root that is not a directory, a directory that is not a valid CAN-X
    project, and a project whose data store is missing or unusable — in every
    case nothing about the recording is claimed. The project domain's own stable
    cause is carried in ``details["cause"]`` (for example
    ``project.manifest_missing`` or ``project.identity_mismatch``); the project
    domain's details are never copied verbatim, because they carry absolute host
    paths that must not leave the project layer.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "replay.project_unavailable",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class ReplaySessionError(ReplayError):
    """Raised when the requested data session cannot be replayed as it stands.

    Three distinct codes share this class, because all three describe the same
    caller-facing situation ("this session is not replayable") while staying
    distinguishable: ``replay.session_not_found``, ``replay.session_not_replayable``
    (the session state is refused by the configured completion policy) and
    ``replay.empty_session`` (a session with no committed frame, refused by the
    configured empty-session policy).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "replay.session_not_found",
        details: dict[str, object] | None = None,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=recoverable)


class ReplaySourceError(ReplayError):
    """Raised when the recorded frames could not be delivered as they were recorded.

    Covers a query failure, missing or corrupt persisted segments, a source that
    contradicts the session's own metadata (non-monotonic recorded time, a
    pagination cursor that does not advance, a truncated page stream) and a
    frame count that does not match the session's durable count. Replay never
    reports a partial stream as complete: this error is raised instead.

    ``recoverable`` mirrors the wrapped query failure when there is one, so a
    segment that is merely unreachable stays distinguishable from a segment
    whose bytes contradict their metadata.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "replay.source_unavailable",
        details: dict[str, object] | None = None,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=recoverable)


class ReplaySinkError(ReplayError):
    """Raised when the sink refused a frame.

    The frame is then lost, so the replay cannot claim to have delivered the
    recording: the run ends ``FAILED`` and no further frame is emitted.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "replay.sink_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class ReplaySchedulerError(ReplayError):
    """Raised when the injected clock violates the scheduling contract.

    The contract is small and explicit: ``monotonic()`` returns a finite number,
    the reading never moves backwards, and ``sleep_until()`` reaches (or passes)
    the deadline it was given. A clock that breaks it makes the replay
    non-deterministic, so the replay stops instead of emitting a wrong schedule.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "replay.scheduler_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class ReplayStateError(ReplayError):
    """Raised when the replay lifecycle is driven in an invalid order.

    ``run()`` is a one-shot transition out of ``IDLE``; after it returned or
    raised, the object is terminal and refuses to run again. That refusal is what
    guarantees no orphan replay continues in the background.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "replay.invalid_run_state",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)
