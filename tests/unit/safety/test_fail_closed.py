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

import math
from typing import cast

import pytest
from canx.safety.caller import CallerIdentity, CallerKind
from canx.safety.decision import SafetyReason
from canx.safety.errors import SafetyAuditError, SafetyCallerError, SafetyError, SafetyScopeError
from canx.safety.kernel import SafetyKernel
from canx.safety.risk import Capability, OperationClass
from canx.safety.scope import ArmScope, OperationTarget, has_lapsed
from safety_builders import (
    DANGEROUS,
    OPERATOR,
    SAFE,
    MovableClock,
    armed_kernel,
    kernel,
    permissions,
    request,
    spec,
)


def test_a_broken_clock_expires_authority_rather_than_extending_it() -> None:
    """The expiry rule, at the level where expiry is decided.

    ``NaN`` compares false against everything, so a check written
    ``now >= expires_at`` answers "not expired" — the one direction expiry must
    never fail in. Every expiry check routes through ``has_lapsed``, which answers
    "expired" instead.
    """
    assert has_lapsed(math.nan, 10.0) is True
    assert has_lapsed(math.inf, 10.0) is True


@pytest.mark.parametrize("broken", [math.nan, math.inf])
def test_a_broken_clock_never_produces_an_audited_verdict(broken: float) -> None:
    """And the second half: a non-finite reading cannot be *recorded* either.

    SAFETY-01-FIX-2 requires ``recorded_at`` to be finite (invariant S20), so once
    the clock goes bad the verdict is not handed back at all. FIX-1 pinned the
    outcome here as a ``DENY``; a fault is the narrower fail-closed result, and the
    expiry rule that assertion was about is unchanged and asserted directly above.
    """
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        spec(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    assert safety.evaluate(
        request(DANGEROUS, caller=OPERATOR, approval_id="appr-1")
    ).allowed
    clock.set(broken)
    with pytest.raises(SafetyAuditError):
        safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1"))


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


def test_a_reference_that_is_not_an_identifier_cannot_be_requested() -> None:
    """A reference reaches the trail, so free text is refused before it can.

    SAFETY-01-FIX-2 moved this refusal *earlier*: an ``approval_id`` that was not
    an identifier used to be accepted by the request and rejected later by the
    store, which meant the prose existed as a request field and was one audit
    write away from being persisted. Refusing it at construction is stronger, not
    weaker — the value never comes into existence (invariants S9, S21).
    """
    for corrupted in ("", "\x00", "../../etc/passwd", "not an approval"):
        with pytest.raises(SafetyError):
            request(DANGEROUS, caller=OPERATOR, approval_id=corrupted)


def test_an_unresolvable_approval_reference_is_denied() -> None:
    """A well-formed reference to an approval that does not exist fails closed."""
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    for unknown in ("00000000-0000-0000-0000-000000000000", "approval-never-issued"):
        decision = safety.evaluate(
            request(DANGEROUS, caller=OPERATOR, approval_id=unknown)
        )
        assert decision.denied, unknown
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
