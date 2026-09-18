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
from canx.safety.errors import (
    SafetyApprovalError,
    SafetyAuditError,
    SafetyRollbackError,
    SafetyScopeError,
    SafetyStateError,
)
from canx.safety.risk import Capability
from safety_builders import (
    AGENT,
    OPERATOR,
    SAFE,
    FaultingClock,
    MovableClock,
    armed_kernel,
    kernel,
    permissions,
    request,
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


# -- SAFETY-01-FIX-2: the audit *transaction*, not only the sink write ---------
#
# The first remediation treated "the audit failed" as "``audit_sink.record()``
# raised". It is broader than that. Before the sink is reached, an audit event is
# prepared: an event id is generated, the clock is read, the event is
# constructed, and kernel coordinates are rendered into ``detail``. An exception
# anywhere in that sequence is an audit that never happened — and because
# ``_record_or_rollback`` only caught ``SafetyAuditError``, such an exception used
# to skip the rollback entirely:
#
#     mutate authority → audit preparation raises → no rollback → authority survives
#
# Each test below breaks one preparation step and asserts the same outcome: the
# authority is gone and the caller gets a typed safety fault, never the raw
# ``RuntimeError`` that the injected fault actually was.
#
# The clock is the fault injector of choice because it is already an injected
# dependency, it is read by the real preparation path, and breaking it is a
# realistic fault (a monotonic source that has gone away) rather than a synthetic
# one. No production API was widened to make these reachable.


def test_confirm_arm_rolls_back_when_audit_clock_fails_before_sink_write() -> None:
    """The headline case: an unaudited ``ARMED`` is the state everything trusts.

    ``confirm()`` reads the clock to check the scope, then the audit preparation
    reads it again for ``recorded_at``. The second read is the one that fails, so
    the authority mutation has already happened when the fault arrives.
    """
    clock = FaultingClock()
    safety = kernel(clock=clock)
    safety.arm(
        scope(Capability.CAN_TX, granted_at=1_000.0, expires_at=1_060.0),
        caller=OPERATOR,
    )
    assert safety.arm_state is ArmState.ARMING
    clock.fail_on_read(2)
    with pytest.raises(SafetyAuditError):
        safety.confirm_arm(caller=OPERATOR)
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None


def test_arm_request_rolls_back_when_audit_clock_fails_before_sink_write() -> None:
    """``ARMING`` is authority too: it is the state the confirmation completes."""
    clock = FaultingClock()
    safety = kernel(clock=clock)
    clock.fail_on_read(2)
    with pytest.raises(SafetyAuditError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=1_000.0, expires_at=1_060.0),
            caller=OPERATOR,
        )
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None


def test_grant_approval_rolls_back_when_audit_clock_fails_before_sink_write() -> None:
    """A new approval is authority; an unaudited one must not be spendable."""
    clock = FaultingClock()
    safety = kernel(clock=clock)
    clock.fail_on_read(1)
    with pytest.raises(SafetyAuditError):
        safety.grant_approval(
            spec(approval_id="appr-clock", issued_at=1_000.0, expires_at=1_060.0),
            granted_by=OPERATOR,
        )
    assert safety.approvals.outstanding() == ()
    with pytest.raises(SafetyApprovalError):
        safety.approvals.consume("appr-clock")


def test_releasing_the_emergency_stop_rolls_back_when_the_audit_clock_fails() -> None:
    """Releasing restores the *possibility* of dangerous work, so it must be audited."""
    clock = FaultingClock()
    safety = kernel(clock=clock)
    safety.engage_emergency_stop(caller=AGENT, reason="detected")
    assert safety.emergency_stop_engaged is True
    clock.fail_on_read(1)
    with pytest.raises(SafetyAuditError):
        safety.release_emergency_stop(caller=OPERATOR)
    assert safety.emergency_stop_engaged is True


def test_a_non_finite_clock_reading_rolls_the_authority_back() -> None:
    """``NaN`` never enters the trail: the event refuses it and the guard rolls back.

    Belt and braces with the injected exception above. A clock that *returns*
    ``NaN`` rather than raising would otherwise put a non-finite timestamp into
    ``recorded_at`` — a malformed record rather than a missing one, and one that
    no expiry comparison can be trusted against.

    ``grant_approval`` is the operation used here because it is the one
    authority-increasing path that reads the clock *only* during audit
    preparation, so the mutation genuinely happens before the fault. The arm paths
    refuse earlier and differently: ``has_lapsed`` reads a non-finite ``now`` as
    "expired", so a broken clock cannot start an arm at all (see
    ``test_a_non_finite_clock_reading_cannot_arm_the_runtime``).
    """
    clock = MovableClock()
    safety = kernel(clock=clock)
    clock.set(float("nan"))
    with pytest.raises(SafetyAuditError):
        safety.grant_approval(
            spec(approval_id="appr-nan", issued_at=1_000.0, expires_at=1_060.0),
            granted_by=OPERATOR,
        )
    assert safety.approvals.outstanding() == ()


def test_a_non_finite_clock_reading_cannot_arm_the_runtime() -> None:
    """The other fail-closed route, kept explicit so it is not mistaken for a gap.

    Every expiry check routes through ``has_lapsed``, which answers "expired" for
    a non-finite reading. A broken clock therefore refuses the arm *before* any
    authority exists — a refusal rather than a rollback, which is why it lands on
    ``SafetyScopeError`` and the state never moves.
    """
    clock = MovableClock()
    safety = kernel(clock=clock)
    clock.set(float("inf"))
    with pytest.raises(SafetyScopeError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=1_000.0, expires_at=1_060.0),
            caller=OPERATOR,
        )
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None


def test_an_audit_render_failure_rolls_the_authority_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serialising kernel coordinates into ``detail`` is part of the transaction."""

    def boom(_payload: dict[str, object]) -> str:
        raise ValueError("detail rendering is unavailable")

    monkeypatch.setattr("canx.safety.kernel._render", boom)
    clock = MovableClock()
    safety = kernel(clock=clock)
    with pytest.raises(SafetyAuditError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=1_000.0, expires_at=1_060.0),
            caller=OPERATOR,
        )
    assert safety.arm_state is ArmState.DISARMED


def test_an_event_id_provider_failure_rolls_the_authority_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Event-id generation happens before the sink, so it is inside the transaction."""

    def boom() -> object:
        raise RuntimeError("uuid provider is unavailable")

    monkeypatch.setattr("canx.safety.identifiers.uuid.uuid4", boom)
    clock = MovableClock()
    safety = kernel(clock=clock)
    with pytest.raises(SafetyAuditError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=1_000.0, expires_at=1_060.0),
            caller=OPERATOR,
        )
    assert safety.arm_state is ArmState.DISARMED


def test_an_audit_preparation_fault_is_normalised_not_passed_through() -> None:
    """A caller must not have to tell an audit fault apart from a product exception."""
    clock = FaultingClock()
    safety = kernel(clock=clock)
    clock.fail_on_read(2)
    with pytest.raises(SafetyAuditError) as caught:
        safety.arm(
            scope(Capability.CAN_TX, granted_at=1_000.0, expires_at=1_060.0),
            caller=OPERATOR,
        )
    # The original fault is preserved for diagnosis rather than swallowed...
    assert isinstance(caught.value.__cause__, RuntimeError)
    # ...and it is not simply re-raised under its own type.
    assert "clock provider is unavailable" in str(caught.value.__cause__)


def test_a_decision_that_cannot_be_prepared_is_not_handed_back() -> None:
    """``evaluate`` increases no authority, but it must not return an unaudited verdict.

    The clock read for ``context.now`` succeeds; the one the audit preparation
    needs does not. The caller gets a fault instead of an ``ALLOW``.
    """
    clock = FaultingClock()
    safety = kernel(clock=clock, permission_set=permissions(Capability.READ))
    clock.fail_on_read(2)
    with pytest.raises(SafetyAuditError):
        safety.evaluate(request(SAFE, caller=OPERATOR))


# -- Rollback failure: loud, typed, and never dressed up as the ordinary fault --

def test_a_failed_rollback_is_a_typed_fault_and_not_an_ordinary_audit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new edge: the audit fails *and* the rollback fails.

    That combination means authority was created and no record accounts for it —
    the runtime's safety state can no longer be trusted. Reporting it as an
    ordinary ``SafetyAuditError`` would tell the caller the rollback worked when
    it is unknown, so it is a separate, stronger type.

    Only the *callable* is replaced; the kernel's real guard runs.
    """

    def boom(self: object) -> ArmState:
        raise RuntimeError("arm controller is unreachable")

    monkeypatch.setattr("canx.safety.arm.ArmController.disarm", boom)
    clock = MovableClock()
    sink = StoppableSink()
    safety = kernel(clock=clock, audit_sink=sink)
    sink.failing = True
    with pytest.raises(SafetyRollbackError) as caught:
        safety.arm(
            scope(Capability.CAN_TX, granted_at=1_000.0, expires_at=1_060.0),
            caller=OPERATOR,
        )

    fault = caught.value
    # Not an ordinary audit fault: catching SafetyAuditError must not swallow it.
    assert not isinstance(fault, SafetyAuditError)
    assert fault.code == "safety.rollback_failure"
    assert fault.details["action"] == "arm.requested"
    assert isinstance(fault.audit_failure, SafetyAuditError)
    assert isinstance(fault.rollback_failure, RuntimeError)
    # The authority is genuinely still there — which is exactly why this cannot
    # be reported as a clean failure. No FAULTED state was invented for it; the
    # loud fault is the contract (see SAFETY_ARCHITECTURE §17.1).
    assert safety.arm_state is ArmState.ARMING


def test_no_payload_from_the_faulted_operation_appears_in_the_rollback_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rollback fault is a safety record too, so it carries no caller text."""
    secret = "hunter2-rotate-the-key"

    def boom_caller(self: object) -> ArmState:
        raise RuntimeError(f"unreachable while holding {secret}")

    monkeypatch.setattr("canx.safety.arm.ArmController.disarm", boom_caller)
    clock = MovableClock()
    sink = StoppableSink()
    safety = kernel(clock=clock, audit_sink=sink)
    sink.failing = True
    with pytest.raises(SafetyRollbackError) as caught:
        safety.arm(
            scope(Capability.CAN_TX, granted_at=1_000.0, expires_at=1_060.0),
            caller=OPERATOR,
        )
    assert secret not in str(caught.value.details)
    assert secret not in str(caught.value)

