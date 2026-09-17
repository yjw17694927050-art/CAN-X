"""Typed failure contract for the CAN-X safety domain.

Callers never see a bare ``ValueError`` or ``KeyError`` for a safety question
they can act on. Every failure that crosses the safety boundary is a
:class:`SafetyError` carrying the structured fields required by SPEC §38 —
``code``, the human ``message``, ``details``, ``recoverable`` and ``source`` —
so the safety domain reads exactly like :mod:`canx.project.errors`,
:mod:`canx.query.errors`, :mod:`canx.dbc.errors`, :mod:`canx.data.errors` and
:mod:`canx.runtime.errors` at a call site. There is deliberately no second
exception vocabulary.

Two kinds of failure live here and they must not be confused:

* a **refusal** is a normal domain outcome — the policy said no. Refusals are
  reported as :class:`PolicyDecision` values (see :mod:`canx.safety.decision`),
  not as exceptions, because "denied" is not an incident;
* a **fault** is the safety machinery being unable to answer the question at
  all — an invalid state transition, an unresolvable approval reference, a
  corrupt scope. A fault always fails closed (invariant S9) and may be raised.

Every code in this module is prefixed ``safety.`` so a boundary can tell a
safety fault apart from a product-domain one without inspecting the type.
"""

from __future__ import annotations


class SafetyError(Exception):
    """Base class for every diagnosable safety-domain failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "safety.error",
        details: dict[str, object] | None = None,
        recoverable: bool = False,
        source: str = "safety",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details: dict[str, object] = {} if details is None else dict(details)
        self.recoverable = recoverable
        self.source = source


class SafetyStateError(SafetyError):
    """Raised when a safety state transition is not part of the contract.

    ``DISARMED → ARMED`` without passing through ``ARMING``, or ``ARMED →
    ARMING`` while still armed, are not "unusual": they are transitions the
    machine does not have. Refusing them keeps ARM from degenerating into the
    boolean the architecture forbids (invariant S7).
    """

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="safety.invalid_transition", details=details)


class SafetyScopeError(SafetyError):
    """Raised when a scope cannot describe the authority it claims to grant."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="safety.invalid_scope", details=details)


class SafetyUnknownOperationError(SafetyError):
    """Raised when an operation cannot be classified at all.

    An operation whose risk cannot be determined is not "probably safe": the
    whole decision chain starts from its risk, so an unclassifiable operation
    has no chain to run. This is raised inside the policy and turned into a
    ``DENY`` — it is never allowed to propagate as a decision.
    """

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="safety.unknown_operation", details=details)


class SafetyPolicyError(SafetyError):
    """Raised when the policy engine itself could not produce a decision.

    A policy that cannot answer must not answer "yes". This type is the
    explicit "the machinery broke" signal behind reason code
    ``safety.policy_failure``.
    """

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="safety.policy_failure", details=details)


class SafetyCallerError(SafetyError):
    """Raised when a caller asks for an authority it does not hold.

    Agent, script and automation callers are operation *requesters*: they may
    ask, and they may be refused, but they can neither arm the runtime nor issue
    an approval (invariants S3, S4).
    """

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="safety.caller_not_permitted", details=details)


class SafetyApprovalError(SafetyError):
    """Base class for every approval-contract failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "safety.approval_invalid",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)


class SafetyApprovalExpiredError(SafetyApprovalError):
    """Raised when an approval is presented after its own expiry."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="safety.approval_expired", details=details)


class SafetyApprovalReusedError(SafetyApprovalError):
    """Raised when a single-use approval is presented a second time.

    This is the domain expression of "an approval is consumed, not consulted":
    the second consumer loses, and the loss is diagnosable (invariant S11).
    """

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="safety.approval_reused", details=details)


class SafetyAuditError(SafetyError):
    """Raised when a decision could not be recorded.

    Invariant S10 says safety decisions are auditable. A decision that cannot be
    written down is not handed back at all: the caller gets this fault instead of
    an ``ALLOW`` it could act on (invariant S14 — no silent swallow).
    """

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="safety.audit_failure", details=details)
