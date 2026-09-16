"""Shared HTTP error envelope for the CAN-X runtime control plane.

SPEC §38 requires every diagnosable failure that crosses an API boundary to carry
``code``, a human ``message``, ``details``, ``recoverable`` and ``source``. That
envelope is defined once, here, so the capture, project, Trace and DBC boundaries
cannot drift into subtly different shapes.

The status mapping lives here for the same reason. It is deliberately one-to-one
between diagnoses: an unregistered session, a missing segment file and an
unreadable segment are three different facts, and collapsing them into a single
status would hide which one a caller has to fix. In particular a missing segment
is **not** reported as a missing session, and a project-owned DBC whose bytes no
longer match its registry row is a 409 rather than the 404 an unregistered asset
gets — one is a broken project, the other is a lookup that found nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from canx.dbc.errors import (
    DbcAssetIntegrityError,
    DbcAssetNotFoundError,
    DbcAssetRegistryError,
    DbcAssetStorageError,
    DbcAssetValidationError,
    DbcDecodeError,
    DbcDecodeUnsupportedError,
    DbcError,
    DbcFrameTypeMismatchError,
    DbcMessageNotFoundError,
    DbcModelError,
    DbcParseError,
    DbcPayloadTooShortError,
    DbcSignalDecodeError,
    DbcUnsupportedFormatError,
)
from canx.project.errors import ProjectError
from canx.query.errors import (
    QueryDataUnavailableError,
    QueryError,
    QueryExecutionError,
    QueryIntegrityError,
    QuerySessionError,
    QueryValidationError,
)

#: Every failure that a domain boundary may hand to the HTTP layer, already
#: carrying the five structured fields the envelope needs.
DomainError = ProjectError | QueryError | DbcError


class ErrorResponse(BaseModel):
    """Diagnostic error shared by control-plane boundaries."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    details: dict[str, object]
    recoverable: bool
    source: str


def status_for(error: DomainError) -> int:
    """Return the HTTP status a typed domain failure must be reported as.

    ``recoverable`` on the envelope carries the retry semantics; the status only
    has to be honest about which class of problem this is.
    """
    if isinstance(error, ProjectError):
        # The request named a target that is not a readable CAN-X project. That is
        # a bad request, not a server fault and not an unknown route.
        return 400
    if isinstance(error, DbcAssetValidationError):
        # A record that cannot describe a project-owned asset: the caller supplied
        # something that could never identify one.
        return 400
    if isinstance(error, DbcAssetNotFoundError):
        # A well-formed lookup for an asset this project does not have. An asset
        # registered in another project answers the same way: from here it is not
        # an integrity problem, it simply is not this project's asset.
        return 404
    if isinstance(error, DbcAssetIntegrityError):
        # The project owns a registry row that contradicts the file it points at.
        # Storage state, not the request, and CAN-X never repairs it silently.
        return 409
    if isinstance(error, DbcAssetRegistryError):
        # The registry could not be read or committed; the environment may settle.
        return 503
    if isinstance(error, DbcAssetStorageError):
        # The project-owned copy could not be written — a full disk, a locked or
        # read-only directory. The environment may settle, so this is a service
        # condition rather than a rejected request.
        return 503
    if isinstance(
        error,
        (
            DbcUnsupportedFormatError,
            DbcDecodeError,
            DbcParseError,
            DbcModelError,
        ),
    ):
        # The request was well formed and its *content* is the problem: a name that
        # is not a DBC file name, bytes that are not text under the declared
        # encoding, text that is not DBC, or a document that cannot satisfy a CAN-X
        # invariant. Distinct codes, one status — the fix is the same (submit a DBC
        # document the domain accepts) even though the reason is not.
        return 422
    if isinstance(
        error,
        (
            DbcMessageNotFoundError,
            DbcFrameTypeMismatchError,
            DbcPayloadTooShortError,
            DbcDecodeUnsupportedError,
            DbcSignalDecodeError,
        ),
    ):
        # The request was well formed and the frame is the problem: it is not
        # defined in this database, or the definition cannot decode it. Distinct
        # codes, one status — the fix is the same (send a frame the database
        # defines) even though the reason is not.
        return 422
    if isinstance(error, QueryValidationError):
        return 400
    if isinstance(error, QuerySessionError):
        return 404
    if isinstance(error, QueryDataUnavailableError):
        # Recoverable storage state: the request was fine, a stored artifact is
        # not where the metadata says it is. Explicitly not a missing session.
        return 409
    if isinstance(error, QueryIntegrityError):
        return 500
    if isinstance(error, QueryExecutionError):
        return 503
    return 500


def error_envelope(error: DomainError) -> ErrorResponse:
    """Return the wire envelope for a typed domain failure."""
    return ErrorResponse(
        code=error.code,
        message=error.message,
        details=error.details,
        recoverable=error.recoverable,
        source=error.source,
    )


#: Code, message and status for a request the *framework* rejected before any
#: route handler ran. Kept separate from every domain code on purpose: there is no
#: domain diagnosis behind this failure, because the domain was never reached.
#: The status stays the framework's own 422 — the envelope is unified, the status
#: semantics are not flattened.
REQUEST_VALIDATION_FAILED_CODE = "api.request_validation_failed"
REQUEST_VALIDATION_FAILED_MESSAGE = "The request payload does not match the API contract."
REQUEST_VALIDATION_FAILED_STATUS = 422


def request_validation_envelope(issues: Sequence[Mapping[str, object]]) -> ErrorResponse:
    """Return the shared envelope for a request that failed framework validation.

    Args:
        issues: The raw validation issues the framework produced, in its own
            ``loc`` / ``type`` / ``msg`` / ``input`` / ``ctx`` shape.

    Returns:
        An :class:`ErrorResponse` carrying only sanitized diagnostics: where the
        problem is, what kind of problem it is, and a human message. Pydantic's
        ``input`` and ``ctx`` are dropped deliberately — an error response must
        not echo the caller's payload back at them, and it must not leak the
        validator's internal context.
    """
    return ErrorResponse(
        code=REQUEST_VALIDATION_FAILED_CODE,
        message=REQUEST_VALIDATION_FAILED_MESSAGE,
        details={"errors": [_sanitized_issue(issue) for issue in issues]},
        recoverable=False,
        source="api",
    )


def _sanitized_issue(issue: Mapping[str, object]) -> dict[str, object]:
    """Reduce one raw framework validation issue to its diagnostic fields."""
    raw_location = issue.get("loc", ())
    parts = (
        raw_location
        if isinstance(raw_location, (list, tuple))
        else (raw_location,)
    )
    return {
        "location": [_location_part(part) for part in parts],
        "type": str(issue.get("type", "unknown")),
        "message": str(issue.get("msg", "The value is not valid.")),
    }


def _location_part(part: object) -> object:
    """Keep a path segment when it is a name or an index, else only its type.

    A validation location is built from field names and sequence indices, both of
    which are safe to report. Anything else is reported as its type name rather
    than as a rendering of its contents, so an unexpected object can never turn
    into a payload echo.
    """
    if isinstance(part, str):
        return part
    if isinstance(part, int) and not isinstance(part, bool):
        return part
    return type(part).__name__
