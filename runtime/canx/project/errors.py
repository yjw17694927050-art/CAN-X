"""Typed failure contract for the CAN-X project domain.

Callers never see a bare ``OSError``, ``sqlite3.Error`` or
``json.JSONDecodeError``. Every failure that crosses the project boundary is a
:class:`ProjectError` carrying the structured fields required by SPEC §38:
``code``, the human ``message``, ``details``, ``recoverable`` and ``source``.
"""


class ProjectError(Exception):
    """Base class for every diagnosable project-domain failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "project.error",
        details: dict[str, object] | None = None,
        recoverable: bool = False,
        source: str = "project",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details: dict[str, object] = {} if details is None else dict(details)
        self.recoverable = recoverable
        self.source = source


class ProjectValidationError(ProjectError):
    """Raised when a public project argument cannot produce a valid project.

    The default ``code`` is deliberately generic so a caller can raise this for
    any argument, while the concrete ``code`` names the offending field.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "project.validation_failed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class ProjectAlreadyExistsError(ProjectError):
    """Raised when a create target already exists and must not be replaced."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(
            message,
            code="project.already_exists",
            details=details,
            recoverable=False,
        )


class InvalidProjectError(ProjectError):
    """Raised when a path does not describe a readable CAN-X project."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "project.invalid",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, recoverable=False)


class UnsupportedProjectVersionError(InvalidProjectError):
    """Raised when a manifest or database was written by an unsupported schema."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="project.unsupported_schema_version", details=details)


class ProjectIdentityMismatchError(InvalidProjectError):
    """Raised when the manifest and the database disagree on project identity."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="project.identity_mismatch", details=details)


class ProjectClosedError(ProjectError):
    """Raised when a closed project handle is asked to do database work."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(
            message,
            code="project.closed",
            details=details,
            recoverable=False,
        )
