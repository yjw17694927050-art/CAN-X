"""The audit contract: both verdicts, no secrets, and no silent failure.

SAFETY-01 §19, §20 and invariants S10, S14. Three claims:

* a refusal is recorded with the same care as an approval — the trail answers
  "what did CAN-X refuse?", not only "what did it do?";
* the event shape cannot carry a secret, so the rule holds even before the
  security-access operation that would carry one exists;
* a decision that cannot be written down is not handed back.
"""

from __future__ import annotations

import hashlib

from canx.safety.audit import InMemoryAuditSink, SafetyAuditEvent
from canx.safety.decision import DecisionOutcome
from canx.safety.kernel import SafetyKernel
from canx.safety.operation import OperationRequest
from canx.safety.risk import Capability, OperationClass
from safety_builders import (
    AGENT,
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


def test_a_refusal_is_recorded() -> None:
    safety = kernel(permission_set=permissions(Capability.CAN_TX))
    safety.evaluate(request(DANGEROUS, caller=OPERATOR))
    events = safety.audit_events()
    assert len(events) == 1
    assert events[0].decision == DecisionOutcome.DENY.value
    assert events[0].reason_code == "safety.not_armed"
    assert events[0].outcome == "decision"


def test_an_approval_is_recorded() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.READ)
    )
    safety.evaluate(request(SAFE, caller=OPERATOR))
    decisions = [event for event in safety.audit_events() if event.outcome == "decision"]
    assert len(decisions) == 1
    assert decisions[0].decision == DecisionOutcome.ALLOW.value
    assert decisions[0].reason_code == "safety.allowed"


def test_the_control_actions_that_move_authority_are_recorded() -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    actions = [event.operation_id for event in safety.audit_events()]
    assert "kernel.arm.requested" in actions
    assert "kernel.arm.confirmed" in actions


def test_a_decision_event_carries_the_context_it_was_taken_in() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        spec(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    safety.evaluate(request(DANGEROUS, caller=AGENT, approval_id="appr-1"))
    decision = [event for event in safety.audit_events() if event.outcome == "decision"][-1]
    assert decision.caller_kind == "agent"
    assert decision.caller_name == "agent.session-1"
    assert decision.arm_state == "armed"
    assert decision.risk_level == 4
    assert decision.approval_id == "appr-1"
    assert decision.emergency_stop_engaged is False


def test_the_trail_cannot_carry_an_operation_secret() -> None:
    """The leak would be a parameter: this is where a security-access key lives."""
    secret = "0xDEADBEEF-SECURITY-ACCESS-KEY"
    digest = request().digest_parameters({"key": secret, "service": 0x27})
    assert secret not in digest
    assert len(digest) == hashlib.sha256(b"").hexdigest().__len__()

    rebuilt = OperationRequest(
        operation_id="op-secret",
        operation_class=OperationClass.DIAGNOSTIC_READ,
        caller=AGENT,
        target=request().target,
        requested_at=0.0,
        parameters_digest=digest,
    )
    safety = kernel(permission_set=permissions(Capability.READ))
    safety.evaluate(rebuilt)
    for event in safety.audit_events():
        assert secret not in str(event.describe())


def test_the_parameter_digest_is_stable_across_key_order() -> None:
    first = request().digest_parameters({"a": 1, "b": 2})
    second = request().digest_parameters({"b": 2, "a": 1})
    assert first == second


def test_the_event_shape_has_no_free_text_payload_field() -> None:
    fields = set(SafetyAuditEvent.__dataclass_fields__)
    for forbidden in ("parameters", "payload", "data", "secret", "token", "key"):
        assert forbidden not in fields


def test_the_trail_is_bounded_and_reports_what_it_dropped() -> None:
    sink = InMemoryAuditSink(capacity=2)
    for index in range(3):
        sink.record(
            SafetyAuditEvent(
                event_id=f"e{index}",
                recorded_at=0.0,
                caller_kind="human.ui",
                caller_name="ui.main",
                operation_id=f"op-{index}",
                operation_class="engineering.read",
                risk_level=1,
                device_id=None,
                channel=None,
                target_address=None,
                decision="allow",
                reason_code="safety.allowed",
                message="ok",
                detail=None,
                approval_id=None,
                parameters_digest=None,
                reason_digest=None,
                arm_state="disarmed",
                arm_scope_expired=None,
                emergency_stop_engaged=False,
                outcome="decision",
            )
        )
    assert len(sink) == 2
    assert sink.dropped == 1
    assert [event.event_id for event in sink.events()] == ["e1", "e2"]


def test_the_arm_scope_expiry_is_recorded_for_a_decision() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=10.0, permission_set=permissions(Capability.CAN_TX)
    )
    clock.advance(10)
    safety.evaluate(request(DANGEROUS, caller=OPERATOR))
    decision = [event for event in safety.audit_events() if event.outcome == "decision"][-1]
    assert decision.arm_scope_expired is True
    assert decision.arm_state == "armed"


def test_a_custom_sink_is_not_read_back_by_the_kernel() -> None:
    """The trail belongs to whoever supplied the sink; the kernel does not own it."""
    collected: list[SafetyAuditEvent] = []

    class _Collector:
        def record(self, event: SafetyAuditEvent) -> None:
            collected.append(event)

    safety = SafetyKernel(
        clock=MovableClock(),
        audit_sink=_Collector(),
        permissions=permissions(Capability.READ),
    )
    safety.evaluate(request(SAFE, caller=OPERATOR))
    assert safety.audit_events() == ()
    assert len(collected) == 1


def test_the_risk_level_recorded_for_a_read_is_the_read_level() -> None:
    safety = kernel(permission_set=permissions(Capability.READ))
    safety.evaluate(request(OperationClass.ENGINEERING_READ, caller=OPERATOR))
    assert safety.audit_events()[0].risk_level == 1
