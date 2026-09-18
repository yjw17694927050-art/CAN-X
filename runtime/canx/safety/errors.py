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


class SafetyEmergencyStopError(SafetyError):
    """Raised when dangerous authority is requested while the stop is engaged.

    The emergency stop is a **safety epoch boundary**, not a pause (invariants
    S22, S23). While it is engaged no caller — operator, host, Agent, script,
    automation or the UI — may establish or pre-stage dangerous vehicle
    authority, and ARM state and approval are exactly that authority.

    Deliberately its own type rather than a :class:`SafetyStateError`, and
    deliberately a *fault* rather than a ``PolicyDecision.DENY``. ``arm``,
    ``confirm_arm`` and ``grant_approval`` are not operation requests — they are
    control-plane authority mutations that never reach ``evaluate`` — so there is
    no decision for a ``DENY`` to be the verdict of. Refusing them as a typed
    fault is the honest shape, and it lets a caller tell "the stop is engaged,
    wait for the operator" apart from "the arm machine has no such transition".

    The refusal is raised **before** any mutation, so there is nothing to roll
    back: this is a gate, not a rollback.
    """

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="safety.emergency_stop_active", details=details)


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


class SafetyApprovalProvenanceError(SafetyApprovalError):
    """Raised when an approval's declared issuer is not its source's provenance.

    An approval carries a two-member issuer vocabulary so the kernel can ask "did
    a human authorise this, or the host runtime?". If the label could be chosen
    by whoever hands the approval over, that question has no answer and
    ``human_issuer_required`` protects nothing.

    A mismatch is therefore not a clerical error to be corrected — it is refused,
    because a label that does not match its source is the exact shape of an
    attempt to speak as someone else (invariant S16).
    """

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="safety.approval_provenance", details=details)


class SafetyAuditError(SafetyError):
    """Raised when a decision could not be recorded.

    Invariant S10 says safety decisions are auditable. A decision that cannot be
    written down is not handed back at all: the caller gets this fault instead of
    an ``ALLOW`` it could act on (invariant S14 — no silent swallow).

    Since SAFETY-01-FIX-2 this is also the single outward type for a failure
    anywhere in the **audit transaction** — event preparation, event construction
    and the sink write — not only the sink write (invariant S20). A clock that
    cannot be read, a UUID provider that raises and a serialisation fault all
    arrive at a caller as this fault, with the original exception preserved as
    ``__cause__``.
    """

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="safety.audit_failure", details=details)


class SafetyIdentifierError(SafetyError):
    """Raised when a safety reference is not a well-formed identifier.

    Safety Audit records identifiers, enumerated vocabulary values, bounded
    structured coordinates and cryptographic digests. It has no field for
    arbitrary caller-controlled text (invariant S21), and the reason is not
    tidiness: a reference field is exactly where a token, a security-access key
    or an unlock payload ends up once a caller is free to write prose into it.

    The contract is enforced where the value is constructed rather than by
    inspecting what the value looks like. "It does not look like a secret" is not
    a security boundary — ``abc123`` may be a password — so the rule is the
    alphabet and the length, applied to every reference field without exception
    (invariants S19, S21).
    """

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, code="safety.invalid_identifier", details=details)


class SafetyRollbackError(SafetyError):
    """Raised when an authority change could be neither audited nor rolled back.

    This is the strongest fault the kernel can raise, and it exists so that the
    worst case is loud rather than quiet. The kernel's authority-increasing
    operations commit first and roll back if the audit cannot be written
    (invariant S17). If the rollback *also* fails, then authority was created and
    no record accounts for it, and the runtime's safety state can no longer be
    trusted.

    Deliberately **not** a :class:`SafetyAuditError`: a caller that caught the
    ordinary audit fault and moved on would be treating "the rollback worked" as
    true when it is unknown. Catching a ``SafetyAuditError`` must never
    accidentally swallow this one.

    It carries the original audit failure, the rollback failure and the action
    being attempted. It does **not** carry any payload — only the fault type and
    the ``safety.*`` code of each failure.

    With no real device attached this is a contract, not an actuator: the runtime
    fails loudly and hands the fault up. When a real transmit path exists,
    reaching this state must additionally trigger the global fail-safe /
    emergency semantics (see ``docs/architecture/SAFETY_ARCHITECTURE.md`` §17.1).
    """

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, object] | None = None,
        audit_failure: BaseException | None = None,
        rollback_failure: BaseException | None = None,
    ) -> None:
        super().__init__(message, code="safety.rollback_failure", details=details)
        self.audit_failure = audit_failure
        self.rollback_failure = rollback_failure
