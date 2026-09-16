"""Shared HTTP error envelope for the CAN-X runtime control plane.

SPEC §38 requires every diagnosable failure that crosses an API boundary to carry
``code``, a human ``message``, ``details``, ``recoverable`` and ``source``. That
envelope is defined once, here, so the capture endpoints and the Trace endpoints
cannot drift into two subtly different shapes.

The status mapping lives here for the same reason. It is deliberately one-to-one
between diagnoses: an unregistered session, a missing segment file and an
unreadable segment are three different facts, and collapsing them into a single
status would hide which one a caller has to fix. In particular a missing segment
is **not** reported as a missing session.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

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
DomainError = ProjectError | QueryError


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
