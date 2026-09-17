"""Authority and its audit record move together, or the authority does not move.

The defect this file pins down (SAFETY-01-FIX-1, P0-3) is an ordering:

```text
mutate authority  →  write audit
```

If the write fails, the caller gets a ``SafetyAuditError`` — and the authority
has already changed. The operation *looks* failed while its effect survives, so
the runtime ends up holding authority that no trail accounts for. That is the
worst of both worlds: the operator believes nothing happened, and the runtime is
armed, or holding an approval, or no longer stopped.

The rule (invariant S17):

> Audit failure may remove authority, but must never create, retain or restore
> unaudited authority.

Which is why the two directions are treated differently and the difference is
tested rather than assumed:

```text
authority-increasing / restoring   arm · confirm arm · grant approval · release e-stop
    → the audit must land first; if it cannot, the authority is rolled back

authority-reducing                 disarm · engage e-stop · revoke approval
    → the reduction stands even when the audit fails. Rolling a safety action
      back to restore dangerous authority would be the failure, not the fix.
```

The sink used here records normally until it is told to fail, so a test can
reach a real armed state and *then* break the trail — which is the only way to
observe whether the authority survived its own audit.
"""

from __future__ import annotations

import pytest
from canx.safety.arm import ArmState
from canx.safety.audit import SafetyAuditEvent
from canx.safety.errors import SafetyApprovalError, SafetyAuditError, SafetyStateError
from canx.safety.risk import Capability
from safety_builders import (
    AGENT,
    OPERATOR,
    MovableClock,
    armed_kernel,
    kernel,
    permissions,
    scope,
    spec,
)


class StoppableSink:
    """An audit trail that works until it is told not to."""

    def __init__(self) -> None:
        self.failing = False
        self.events: list[SafetyAuditEvent] = []

    def record(self, event: SafetyAuditEvent) -> None:
        if self.failing:
            raise OSError("audit storage is unavailable")
        self.events.append(event)


def test_a_failed_arm_request_leaves_the_runtime_disarmed() -> None:
    """The arm state must not survive an audit that never happened."""
    clock = MovableClock()
    sink = StoppableSink()
    safety = kernel(clock=clock, audit_sink=sink)
    sink.failing = True
    with pytest.raises(SafetyAuditError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
            caller=OPERATOR,
        )
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None


def test_a_failed_arm_confirmation_does_not_leave_the_runtime_armed() -> None:
    """The sharpest case: an unaudited ARM is the state everything else trusts."""
    clock = MovableClock()
    sink = StoppableSink()
    safety = kernel(clock=clock, audit_sink=sink)
    safety.arm(
        scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
        caller=OPERATOR,
    )
    assert safety.arm_state is ArmState.ARMING
    sink.failing = True
    with pytest.raises(SafetyAuditError):
        safety.confirm_arm(caller=OPERATOR)
    assert safety.arm_state is not ArmState.ARMED
    # DISARMED rather than back to ARMING: a confirmation that could not be
    # recorded must not leave a state the next caller could confirm without
    # anyone having seen it happen.
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None


def test_a_failed_approval_grant_leaves_no_usable_approval() -> None:
    clock = MovableClock()
    sink = StoppableSink()
    safety = kernel(clock=clock, audit_sink=sink)
    sink.failing = True
    with pytest.raises(SafetyAuditError):
        safety.grant_approval(
            spec(approval_id="appr-1", issued_at=clock(), expires_at=clock() + 60),
            granted_by=OPERATOR,
        )
    assert safety.approvals.outstanding() == ()
    with pytest.raises(SafetyApprovalError):
        safety.approvals.consume("appr-1")


def test_a_failed_emergency_stop_release_leaves_the_stop_engaged() -> None:
    """Releasing restores the *possibility* of dangerous work — it must be audited."""
    sink = StoppableSink()
    safety = kernel(audit_sink=sink)
    safety.engage_emergency_stop(caller=AGENT, reason="detected")
    assert safety.emergency_stop_engaged is True
    sink.failing = True
    with pytest.raises(SafetyAuditError):
        safety.release_emergency_stop(caller=OPERATOR)
    assert safety.emergency_stop_engaged is True


def test_a_failed_arm_request_does_not_disturb_a_previous_arm() -> None:
    """Rollback returns to the last *audited* state, not to some third one."""
    clock = MovableClock()
    sink = StoppableSink()
    safety = armed_kernel(clock=clock, audit_sink=sink)
    assert safety.arm_state is ArmState.ARMED
    sink.failing = True
    with pytest.raises(SafetyStateError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
            caller=OPERATOR,
        )
    # Re-arming an armed runtime is an invalid transition and never started, so
    # the live authority is untouched — but it is still the audited one.
    assert safety.arm_state is ArmState.ARMED
    assert safety.active_scope is not None


# -- The reducing direction: authority is lost, never silently restored --------


def test_a_failed_audit_during_disarm_still_leaves_the_runtime_disarmed() -> None:
    clock = MovableClock()
    sink = StoppableSink()
    safety = armed_kernel(clock=clock, audit_sink=sink)
    sink.failing = True
    with pytest.raises(SafetyAuditError):
        safety.disarm(caller=OPERATOR, reason="operator released control")
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None


def test_a_failed_audit_during_emergency_stop_still_leaves_it_engaged() -> None:
    clock = MovableClock()
    sink = StoppableSink()
    safety = armed_kernel(clock=clock, audit_sink=sink)
    sink.failing = True
    with pytest.raises(SafetyAuditError):
        safety.engage_emergency_stop(caller=OPERATOR, reason="bench smoke")
    assert safety.emergency_stop_engaged is True
    assert safety.arm_state is ArmState.DISARMED


def test_revocation_is_not_gated_by_the_trail() -> None:
    """Revoking takes no authority check and writes no audit record.

    It is the one authority mutation that cannot make a runtime more dangerous,
    so a broken trail has nothing to roll back — and the approval is gone either
    way.
    """
    clock = MovableClock()
    sink = StoppableSink()
    safety = kernel(clock=clock, audit_sink=sink)
    safety.grant_approval(
        spec(approval_id="appr-1", issued_at=clock(), expires_at=clock() + 60),
        granted_by=OPERATOR,
    )
    sink.failing = True
    assert safety.revoke_approval("appr-1") is True
    assert safety.approvals.outstanding() == ()


# -- The invariant, stated once -----------------------------------------------


def test_an_audit_fault_never_moves_the_runtime_towards_more_authority() -> None:
    """The property behind every test above, checked on the authority surface."""
    clock = MovableClock()
    sink = StoppableSink()
    safety = kernel(
        clock=clock, permission_set=permissions(Capability.CAN_TX), audit_sink=sink
    )
    sink.failing = True

    # Every authority-increasing action is attempted against a broken trail.
    with pytest.raises(SafetyAuditError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
            caller=OPERATOR,
        )
    # The arm was rolled back, so there is no ARMING state left to confirm —
    # which is itself the point: a failed arm must not leave a confirmable
    # half-state behind for the next caller to complete.
    with pytest.raises(SafetyStateError):
        safety.confirm_arm(caller=OPERATOR)
    with pytest.raises(SafetyAuditError):
        safety.grant_approval(
            spec(approval_id="appr-x", issued_at=clock(), expires_at=clock() + 60),
            granted_by=OPERATOR,
        )

    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None
    assert safety.approvals.outstanding() == ()
    assert safety.emergency_stop_engaged is False


def test_a_failed_grant_rollback_does_not_remove_an_earlier_approval() -> None:
    """Rollback removes what this call added, and nothing else."""
    clock = MovableClock()
    sink = StoppableSink()
    safety = kernel(clock=clock, audit_sink=sink)
    safety.grant_approval(
        spec(approval_id="appr-first", issued_at=clock(), expires_at=clock() + 60),
        granted_by=OPERATOR,
    )
    sink.failing = True
    with pytest.raises(SafetyAuditError):
        safety.grant_approval(
            spec(approval_id="appr-second", issued_at=clock(), expires_at=clock() + 60),
            granted_by=OPERATOR,
        )
    remaining = {candidate.approval_id for candidate in safety.approvals.outstanding()}
    assert remaining == {"appr-first"}
