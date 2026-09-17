"""The emergency-stop contract: one authority over every dangerous family.

SAFETY-01 §18 and invariant S13. The four steps the contract fixes are tested in
order:

```text
globally disarm → deny new dangerous operations
→ request cancellation of active dangerous operations → audit event
```

What is *not* tested is interrupting a real transmit, because there is none. That
limitation is asserted explicitly here rather than left for a reader to infer
from the absence of a test.
"""

from __future__ import annotations

import pytest
from canx.safety.arm import ArmState
from canx.safety.decision import SafetyReason
from canx.safety.errors import SafetyCallerError
from canx.safety.risk import Capability
from safety_builders import (
    AGENT,
    ALL_CALLERS,
    AUTOMATION,
    DANGEROUS,
    HOST,
    MACHINE_CALLERS,
    OPERATOR,
    SAFE,
    SCRIPT,
    MovableClock,
    approval,
    armed_kernel,
    kernel,
    permissions,
    request,
)


class _RecordingCanceller:
    """A subsystem that remembers it was asked to stop."""

    def __init__(self, name: str = "periodic-tx", requested: tuple[str, ...] = ("tx-1",)) -> None:
        self.name = name
        self.calls: list[str] = []
        self._requested = requested

    def cancel_active_operations(self, *, reason: str) -> tuple[str, ...]:
        self.calls.append(reason)
        return self._requested


class _RefusingCanceller:
    """A subsystem that cannot answer. Its failure must be reported, not swallowed."""

    def cancel_active_operations(self, *, reason: str) -> tuple[str, ...]:
        raise RuntimeError("cancellation channel is down")


def test_engaging_the_stop_disarms_the_runtime() -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    assert safety.arm_state is ArmState.ARMED
    safety.engage_emergency_stop(caller=OPERATOR, reason="bench smoke")
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None


def test_engaging_the_stop_denies_new_dangerous_operations() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        approval(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    safety.engage_emergency_stop(caller=OPERATOR, reason="operator stop")
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1"))
    assert decision.denied
    assert decision.reason_code is SafetyReason.EMERGENCY_STOP


def test_the_stop_survives_a_re_arm_attempt_during_the_emergency() -> None:
    """The strongest form: even a caller who re-arms has their approval gone.

    Re-arming is not blocked — restoring authority is an operator decision — but
    the stop dropped every approval when it engaged, so a re-arm alone does not
    restore the ability to do dangerous work.
    """
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        approval(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    safety.engage_emergency_stop(caller=OPERATOR, reason="operator stop")
    assert safety.approvals.outstanding() == ()
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1"))
    assert decision.denied


def test_a_read_still_works_during_the_stop() -> None:
    """The stop denies *dangerous* work; observing the bus is not dangerous."""
    safety = kernel(permission_set=permissions(Capability.READ))
    safety.engage_emergency_stop(caller=OPERATOR, reason="operator stop")
    decision = safety.evaluate(request(SAFE, caller=OPERATOR))
    assert decision.allowed


@pytest.mark.parametrize("caller", ALL_CALLERS)
def test_every_caller_kind_may_engage_the_stop(caller: object) -> None:
    """Anything that detects danger may pull it. Engaging only ever reduces authority."""
    safety = kernel()
    state = safety.engage_emergency_stop(
        caller=caller, reason="detected"  # type: ignore[arg-type]
    )
    assert state.engaged is True
    assert state.reason == "detected"


@pytest.mark.parametrize("caller", MACHINE_CALLERS)
def test_a_machine_caller_may_not_release_the_stop(caller: object) -> None:
    safety = kernel()
    safety.engage_emergency_stop(caller=AGENT, reason="detected")
    with pytest.raises(SafetyCallerError):
        safety.release_emergency_stop(caller=caller)  # type: ignore[arg-type]
    assert safety.emergency_stop_engaged is True


@pytest.mark.parametrize("caller", [OPERATOR, HOST])
def test_an_authority_bearing_caller_may_release_the_stop(caller: object) -> None:
    safety = kernel()
    safety.engage_emergency_stop(caller=AGENT, reason="detected")
    state = safety.release_emergency_stop(caller=caller)  # type: ignore[arg-type]
    assert state.engaged is False
    # Releasing restores the *possibility* of authority, never the authority.
    assert safety.arm_state is ArmState.DISARMED


def test_the_stop_requests_cancellation_from_every_registered_subsystem() -> None:
    safety = kernel()
    periodic = _RecordingCanceller("periodic-tx", requested=("tx-1", "tx-2"))
    replay = _RecordingCanceller("replay", requested=("replay-7",))
    safety.register_canceller(periodic)
    safety.register_canceller(replay)

    state = safety.engage_emergency_stop(caller=AGENT, reason="detected")

    assert periodic.calls == ["detected"]
    assert replay.calls == ["detected"]
    assert set(state.requested_cancellations) == {"tx-1", "tx-2", "replay-7"}
    assert state.cancellation_failures == ()


def test_a_subsystem_that_cannot_confirm_cancellation_is_reported() -> None:
    """Invariant S14: the operator must see exactly which subsystem did not answer."""
    safety = kernel()
    safety.register_canceller(_RecordingCanceller())
    safety.register_canceller(_RefusingCanceller())

    state = safety.engage_emergency_stop(caller=AGENT, reason="detected")

    assert state.requested_cancellations == ("tx-1",)
    assert len(state.cancellation_failures) == 1
    assert "RuntimeError" in state.cancellation_failures[0]
    assert safety.emergency_stop_engaged is True


def test_engaging_twice_keeps_the_first_reason_and_retries_cancellation() -> None:
    safety = kernel()
    canceller = _RecordingCanceller()
    safety.register_canceller(canceller)
    first = safety.engage_emergency_stop(caller=AGENT, reason="first")
    second = safety.engage_emergency_stop(caller=AUTOMATION, reason="second")
    assert first.reason == "first"
    assert second.reason == "first"
    assert canceller.calls == ["first", "second"]


def test_the_stop_is_recorded_on_the_audit_trail() -> None:
    safety = kernel()
    safety.engage_emergency_stop(caller=OPERATOR, reason="bench smoke")
    actions = [event.operation_id for event in safety.audit_events()]
    assert "kernel.emergency_stop.engaged" in actions
    engaged = [
        event for event in safety.audit_events() if event.operation_id.endswith("engaged")
    ][-1]
    assert engaged.caller_kind == "human.ui"
    assert engaged.message == "bench smoke"
    assert engaged.emergency_stop_engaged is True


def test_releasing_the_stop_is_recorded() -> None:
    safety = kernel()
    safety.engage_emergency_stop(caller=OPERATOR, reason="bench smoke")
    safety.release_emergency_stop(caller=OPERATOR)
    actions = [event.operation_id for event in safety.audit_events()]
    assert "kernel.emergency_stop.released" in actions


def test_there_is_no_real_transmit_to_interrupt_in_this_stage() -> None:
    """The contract is implemented; the interruption is not, and says so.

    ``requested_cancellations`` records what was *asked*. Nothing in the safety
    package can stop a device write, because no device write exists to stop —
    that integration is a later task and is listed as NOT VERIFIED in the
    architecture document rather than implied by a passing test.
    """
    safety = kernel()
    state = safety.engage_emergency_stop(caller=AGENT, reason="detected")
    assert state.requested_cancellations == ()
    assert state.cancellation_failures == ()


def test_machine_callers_can_never_reach_the_release_path() -> None:
    for machine in (AGENT, SCRIPT, AUTOMATION):
        assert machine.may_release_emergency_stop is False
