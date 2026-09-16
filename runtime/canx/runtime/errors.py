"""Typed failure contract for the CAN-X runtime control plane.

Callers never see a bare ``OSError``, ``sqlite3.Error`` or ``ValueError`` for a
request they can act on. Every failure that crosses the runtime boundary is a
:class:`CaptureConfigurationError` carrying the structured fields required by
SPEC §38: ``code``, the human ``message``, ``details``, ``recoverable`` and
``source``.

The hierarchy mirrors :mod:`canx.project.errors`, :mod:`canx.data.errors` and
:mod:`canx.query.errors`, so all four domains read the same way at a call site.
"""

from __future__ import annotations


class CaptureConfigurationError(Exception):
    """Raised when a capture cannot be started from the request as given.

    Reserved for problems the caller has to resolve before retrying: an unusable
    recording target, two conflicting targets, a path that is not a CAN-X
    project, or a project that cannot open a data session. It never carries a
    mid-capture adapter or storage fault, which are reported through the capture
    session's own failure state instead.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "capture.invalid_configuration",
        details: dict[str, object] | None = None,
        recoverable: bool = False,
        source: str = "runtime",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details: dict[str, object] = {} if details is None else dict(details)
        self.recoverable = recoverable
        self.source = source
