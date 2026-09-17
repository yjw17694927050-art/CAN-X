"""Fault injection: every broken input must produce a DENY, never an ALLOW.

SAFETY-01 §29. The tests here deliberately hand the kernel things it has no
reason to expect — a non-finite clock, a reference to an approval that was never
issued, an operation class that does not exist, a malformed scope, a caller kind
that is not in the vocabulary — and assert the same outcome every time.

The dangerous failure mode is not an exception. It is the *silent* one: a broken
input that a lenient check reads as "nothing wrong here" and lets through. Each
test below names the specific leniency it is guarding against.
"""

from __future__ import annotations

from typing import cast

import pytest
from canx.safety.caller import CallerIdentity, CallerKind
from canx.safety.decision import SafetyReason
from canx.safety.errors import SafetyCallerError, SafetyScopeError
from canx.safety.kernel import SafetyKernel
from canx.safety.risk import Capability, OperationClass
from canx.safety.scope import ArmScope, OperationTarget
from safety_builders import (
    DANGEROUS,
    OPERATOR,
    SAFE,
    MovableClock,
    approval,
    armed_kernel,
    kernel,
    permissions,
    request,
)


def test_a_non_finite_clock_reading_disarms_rather_than_preserving_the_arm() -> None:
    """NaN compares false against everything, so ``now >= expiry`` would say "valid"."""
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        approval(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    assert (
        safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1")).allowed
    )
    clock.set(float("nan"))
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1"))
    assert decision.denied
    assert decision.reason_code is SafetyReason.NOT_ARMED


def test_a_non_finite_clock_reading_expires_an_approval() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        approval(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    clock.set(float("inf"))
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1"))
    assert decision.denied


def test_an_unknown_caller_kind_cannot_be_constructed_at_all() -> None:
    """The refusal happens before the kernel ever sees the request."""
    with pytest.raises(SafetyCallerError):
        CallerIdentity(cast(CallerKind, "wizard"), "unknown")
    with pytest.raises(SafetyCallerError):
        CallerIdentity(CallerKind.AGENT, "")


def test_an_unexpected_operation_enum_is_denied() -> None:
    safety = kernel(permission_set=permissions(Capability.READ))
    decision = safety.evaluate(
        request(cast(OperationClass, "engineering.something-else"), caller=OPERATOR)
    )
    assert decision.denied
    assert decision.reason_code is SafetyReason.UNKNOWN_OPERATION


def test_a_corrupted_approval_reference_is_denied() -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    for corrupted in ("", "00000000-0000-0000-0000-000000000000", "\x00", "../../etc/passwd"):
        decision = safety.evaluate(
            request(DANGEROUS, caller=OPERATOR, approval_id=corrupted)
        )
        assert decision.denied, corrupted
        assert decision.reason_code is SafetyReason.APPROVAL_INVALID


def test_a_malformed_scope_cannot_be_constructed() -> None:
    with pytest.raises(SafetyScopeError):
        ArmScope(
            capabilities=frozenset(),
            target=OperationTarget(),
            granted_at=0.0,
            expires_at=10.0,
        )
    with pytest.raises(SafetyScopeError):
        ArmScope(
            capabilities=frozenset({Capability.CAN_TX}),
            target=OperationTarget(target_address=0x7FFF_FFFF),
            granted_at=0.0,
            expires_at=10.0,
        )


def test_a_malformed_permission_grant_cannot_be_constructed() -> None:
    from canx.safety.permission import PermissionGrant

    with pytest.raises(SafetyScopeError):
        PermissionGrant(capabilities=frozenset())


class _BrokenSink:
    """A sink that cannot record. The trail is the contract, so this is a fault."""

    def record(self, event: object) -> None:
        raise OSError("audit storage is unavailable")


def test_a_broken_audit_sink_is_a_fault_not_a_verdict() -> None:
    from canx.safety.errors import SafetyAuditError

    clock = MovableClock()
    safety = SafetyKernel(
        clock=clock,
        audit_sink=_BrokenSink(),  # type: ignore[arg-type]
        permissions=permissions(Capability.READ),
    )
    with pytest.raises(SafetyAuditError):
        safety.evaluate(request(SAFE, caller=OPERATOR))


def test_an_unrecordable_allow_is_never_handed_back() -> None:
    """The specific hazard: a caller acting on a verdict nobody can audit."""
    from canx.safety.errors import SafetyAuditError

    clock = MovableClock()
    safety = SafetyKernel(
        clock=clock,
        audit_sink=_BrokenSink(),  # type: ignore[arg-type]
        permissions=permissions(Capability.CAN_TX),
    )
    with pytest.raises(SafetyAuditError):
        safety.evaluate(request(SAFE, caller=OPERATOR))
