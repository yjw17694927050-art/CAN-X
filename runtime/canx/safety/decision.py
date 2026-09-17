"""The policy verdict: a value, not an exception.

A refusal is a **normal domain outcome**. When the kernel declines to authorise
an operation it returns a :class:`PolicyDecision` carrying a stable reason code
and a human message, exactly as :mod:`canx.query` returns a typed query failure
rather than raising an opaque error. Callers need to branch on "denied because
the runtime is not armed" differently from "denied because the approval
expired", and an exception type per reason would make that branch a
``try``/``except`` tree instead of a comparison.

Faults are the other half of the contract and they stay exceptions: a transition
the state machine does not have, or a policy engine that could not run at all,
is not a verdict — it is the machinery failing. Faults fail closed and surface
as :class:`canx.safety.errors.SafetyError`.

Every reason code is prefixed ``safety.`` and is part of the published safety
vocabulary; adding one is a contract change, renaming one is a breaking change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from canx.safety.risk import RiskLevel


class DecisionOutcome(StrEnum):
    """The two verdicts the kernel can return."""

    ALLOW = "allow"
    DENY = "deny"


class SafetyReason(StrEnum):
    """Stable reason codes for a safety decision.

    The set is deliberately small and *diagnostic*: each code names the one
    condition that failed, so an operator reading an audit trail can tell which
    of the required authorities was missing.
    """

    ALLOWED = "safety.allowed"

    NOT_ARMED = "safety.not_armed"
    SCOPE_VIOLATION = "safety.scope_violation"
    PERMISSION_DENIED = "safety.permission_denied"
    CALLER_NOT_PERMITTED = "safety.caller_not_permitted"

    APPROVAL_REQUIRED = "safety.approval_required"
    APPROVAL_INVALID = "safety.approval_invalid"
    APPROVAL_EXPIRED = "safety.approval_expired"
    APPROVAL_REUSED = "safety.approval_reused"

    UNKNOWN_OPERATION = "safety.unknown_operation"
    INVALID_TRANSITION = "safety.invalid_transition"
    POLICY_FAILURE = "safety.policy_failure"
    EMERGENCY_STOP = "safety.emergency_stop_active"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """One verdict, with everything needed to explain it later.

    ``required_conditions`` lists the authorities a refusal was waiting for, in
    the order the decision chain checks them. It is not a to-do list for the
    caller — several conditions cannot be satisfied by a caller at all (an Agent
    cannot arm) — it is a diagnosis for whoever reads the audit trail next.
    """

    outcome: DecisionOutcome
    reason_code: SafetyReason
    message: str
    risk_level: RiskLevel | None = None
    required_conditions: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        """Whether this decision authorises the operation."""
        return self.outcome is DecisionOutcome.ALLOW

    @property
    def denied(self) -> bool:
        """Whether this decision refuses the operation."""
        return self.outcome is DecisionOutcome.DENY

    def describe(self) -> dict[str, object]:
        """Return the audit-safe shape of this decision."""
        return {
            "outcome": self.outcome.value,
            "reason_code": self.reason_code.value,
            "message": self.message,
            "risk_level": None if self.risk_level is None else int(self.risk_level),
            "required_conditions": list(self.required_conditions),
        }


def allow(
    *,
    risk_level: RiskLevel,
    message: str = "The operation is authorised.",
    required_conditions: tuple[str, ...] = (),
) -> PolicyDecision:
    """Build the single well-formed ``ALLOW`` decision.

    A factory rather than a bare constructor call at each site, so that "what an
    allow looks like" is defined once and cannot drift into a permissive default
    with a missing risk level.
    """
    return PolicyDecision(
        outcome=DecisionOutcome.ALLOW,
        reason_code=SafetyReason.ALLOWED,
        message=message,
        risk_level=risk_level,
        required_conditions=required_conditions,
    )


def deny(
    reason_code: SafetyReason,
    message: str,
    *,
    risk_level: RiskLevel | None = None,
    required_conditions: tuple[str, ...] = (),
) -> PolicyDecision:
    """Build a well-formed ``DENY`` decision.

    ``risk_level`` may be ``None``: an operation that could not be classified at
    all is still denied, and the honest record of that refusal names no level.
    """
    return PolicyDecision(
        outcome=DecisionOutcome.DENY,
        reason_code=reason_code,
        message=message,
        risk_level=risk_level,
        required_conditions=required_conditions,
    )
