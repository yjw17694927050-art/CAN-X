"""The decision chain, attacked from the refusal side (SAFETY-01 §28).

Every test in this file except the explicitly-allowed ones asserts a **DENY**.
That is the point of the stage: a safety kernel is judged by what it refuses, and
a suite that only proved the happy path would prove that the gate opens, not that
it holds.

The chain under test:

```text
classify risk → caller → ARM → permission → approval → ALLOW / DENY
```
"""

from __future__ import annotations

import pytest
from canx.safety.approval import ApprovalIssuer
from canx.safety.arm import ArmState
from canx.safety.decision import SafetyReason
from canx.safety.kernel import SafetyKernel
from canx.safety.policy import SafetyPolicy, approval_requirement_for
from canx.safety.risk import Capability, OperationClass, RiskLevel
from safety_builders import (
    AGENT,
    DANGEROUS,
    OPERATOR,
    SAFE,
    MovableClock,
    approval,
    armed_kernel,
    kernel,
    permissions,
    request,
    target,
)

# -- The trust boundary ------------------------------------------------------


def test_a_fresh_kernel_is_disarmed() -> None:
    assert kernel().arm_state is ArmState.DISARMED
    assert kernel().active_scope is None


def test_a_dangerous_operation_while_disarmed_is_denied() -> None:
    """Invariant S1, the reason this stage exists.

    Nothing is missing except the arm state: the caller has the permission and
    there is no approval to be absent, and the operation is still refused.
    """
    safety = kernel(permission_set=permissions(Capability.CAN_TX))
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR))
    assert decision.denied
    assert decision.reason_code is SafetyReason.NOT_ARMED
    assert decision.risk_level is RiskLevel.TX


def test_an_operation_that_cannot_be_classified_is_denied() -> None:
    safety = kernel()
    decision = safety.evaluate(request("bus.teleport"))  # type: ignore[arg-type]
    assert decision.denied
    assert decision.reason_code is SafetyReason.UNKNOWN_OPERATION
    assert decision.risk_level is None


# -- Safe operations and the permission boundary -----------------------------


def test_a_read_without_permission_is_denied() -> None:
    decision = kernel().evaluate(request(SAFE, caller=OPERATOR))
    assert decision.denied
    assert decision.reason_code is SafetyReason.PERMISSION_DENIED


def test_a_read_with_permission_is_allowed() -> None:
    safety = kernel(permission_set=permissions(Capability.READ))
    decision = safety.evaluate(request(SAFE, caller=OPERATOR))
    assert decision.allowed
    assert decision.reason_code is SafetyReason.ALLOWED
    assert decision.risk_level is RiskLevel.READ


def test_a_project_write_does_not_need_the_arm_state() -> None:
    """``WRITE_PROJECT`` is the last level below the dangerous boundary."""
    safety = kernel(permission_set=permissions(Capability.WRITE_PROJECT))
    decision = safety.evaluate(request(OperationClass.PROJECT_WRITE, caller=AGENT))
    assert decision.allowed


def test_a_read_capability_does_not_authorise_transmit() -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.READ))
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR))
    assert decision.denied
    assert decision.reason_code is SafetyReason.PERMISSION_DENIED


# -- The ARM boundary --------------------------------------------------------


def test_armed_without_the_capability_is_denied() -> None:
    """Armed for diagnostics is not armed for transmit."""
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset({Capability.DIAGNOSTIC_MUTATION}),
        permission_set=permissions(Capability.CAN_TX),
    )
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR))
    assert decision.denied
    assert decision.reason_code is SafetyReason.SCOPE_VIOLATION


def test_armed_for_one_channel_does_not_authorise_another() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset({Capability.CAN_TX}),
        target_=target(channel="can1"),
        permission_set=permissions(Capability.CAN_TX, target_=target(channel="can1")),
    )
    safety.grant_approval(
        approval(
            capability=Capability.CAN_TX,
            target_=target(channel="can2"),
            issued_at=clock(),
            expires_at=clock() + 30,
        ),
        granted_by=OPERATOR,
    )
    decision = safety.evaluate(
        request(DANGEROUS, caller=OPERATOR, target_=target(channel="can2"), approval_id="appr-1")
    )
    assert decision.denied
    assert decision.reason_code is SafetyReason.SCOPE_VIOLATION


def test_disarming_invalidates_the_ability_to_run_dangerous_work() -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    safety.grant_approval(
        approval(issued_at=clock(), expires_at=clock() + 30), granted_by=OPERATOR
    )
    assert safety.evaluate(
        request(DANGEROUS, caller=OPERATOR, approval_id="appr-1")
    ).allowed
    safety.disarm(caller=OPERATOR, reason="operator released control")
    decision = safety.evaluate(
        request(DANGEROUS, caller=OPERATOR, operation_id="op-2", approval_id="appr-1")
    )
    assert decision.denied
    assert decision.reason_code is SafetyReason.NOT_ARMED


def test_a_lapsed_arm_scope_stops_authorising_work() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        duration=10.0,
        permission_set=permissions(Capability.CAN_TX),
    )
    safety.grant_approval(
        approval(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    assert safety.evaluate(
        request(DANGEROUS, caller=OPERATOR, approval_id="appr-1")
    ).allowed
    clock.advance(10)
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1"))
    assert decision.denied
    assert decision.reason_code is SafetyReason.NOT_ARMED


# -- The approval boundary ---------------------------------------------------


def test_armed_and_permitted_but_unapproved_is_denied() -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR))
    assert decision.denied
    assert decision.reason_code is SafetyReason.APPROVAL_REQUIRED


def test_every_authority_present_is_allowed() -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    safety.grant_approval(
        approval(issued_at=clock(), expires_at=clock() + 30), granted_by=OPERATOR
    )
    decision = safety.evaluate(request(DANGEROUS, caller=AGENT, approval_id="appr-1"))
    assert decision.allowed
    assert decision.risk_level is RiskLevel.TX


def test_an_expired_approval_is_denied() -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    safety.grant_approval(
        approval(issued_at=clock() - 120, expires_at=clock() - 60), granted_by=OPERATOR
    )
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1"))
    assert decision.denied
    assert decision.reason_code is SafetyReason.APPROVAL_EXPIRED


def test_an_approval_that_lapses_between_two_requests_stops_working() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        approval(single_use=False, issued_at=clock(), expires_at=clock() + 20),
        granted_by=OPERATOR,
    )
    assert safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1")).allowed
    clock.advance(20)
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1"))
    assert decision.denied
    assert decision.reason_code is SafetyReason.APPROVAL_EXPIRED


def test_a_reused_single_use_approval_is_denied() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        approval(single_use=True, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    assert safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1")).allowed
    decision = safety.evaluate(
        request(DANGEROUS, caller=OPERATOR, operation_id="op-2", approval_id="appr-1")
    )
    assert decision.denied
    assert decision.reason_code is SafetyReason.APPROVAL_REUSED


def test_a_lower_risk_approval_cannot_authorise_a_higher_risk_operation() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset({Capability.READ, Capability.CAN_TX}),
        duration=600.0,
        permission_set=permissions(Capability.READ, Capability.CAN_TX),
    )
    safety.grant_approval(
        approval(
            capability=Capability.READ,
            single_use=False,
            issued_at=clock(),
            expires_at=clock() + 600,
        ),
        granted_by=OPERATOR,
    )
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1"))
    assert decision.denied
    assert decision.reason_code is SafetyReason.APPROVAL_INVALID


def test_an_unknown_approval_reference_is_denied() -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    decision = safety.evaluate(
        request(DANGEROUS, caller=OPERATOR, approval_id="never-issued")
    )
    assert decision.denied
    assert decision.reason_code is SafetyReason.APPROVAL_INVALID


def test_an_approval_that_is_not_exact_enough_is_denied() -> None:
    """TX demands an approval that names the same coordinates the request does."""
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        approval(
            capability=Capability.CAN_TX,
            target_=target(channel="can1"),
            single_use=False,
            issued_at=clock(),
            expires_at=clock() + 600,
        ),
        granted_by=OPERATOR,
    )
    decision = safety.evaluate(
        request(DANGEROUS, caller=OPERATOR, approval_id="appr-1", target_=target(channel="can1"))
    )
    assert decision.allowed
    # The same approval, now presented to a request that never said which
    # channel it meant: not the same authority, so not the same approval.
    decision = safety.evaluate(
        request(DANGEROUS, caller=OPERATOR, operation_id="op-2", approval_id="appr-1")
    )
    assert decision.denied
    assert decision.reason_code is SafetyReason.SCOPE_VIOLATION


def test_an_actuation_approval_must_come_from_a_human() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset({Capability.ACTUATION}),
        duration=600.0,
        permission_set=permissions(Capability.ACTUATION),
    )
    safety.grant_approval(
        approval(
            capability=Capability.ACTUATION,
            issuer=ApprovalIssuer.HOST_SYSTEM,
            issued_at=clock(),
            expires_at=clock() + 30,
        ),
        granted_by=OPERATOR,
    )
    decision = safety.evaluate(
        request(OperationClass.ACTUATION, caller=OPERATOR, approval_id="appr-1")
    )
    assert decision.denied
    assert decision.reason_code is SafetyReason.APPROVAL_INVALID


def test_a_reusable_approval_is_denied_where_single_use_is_demanded() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset({Capability.DIAGNOSTIC_MUTATION}),
        duration=600.0,
        permission_set=permissions(Capability.DIAGNOSTIC_MUTATION),
    )
    safety.grant_approval(
        approval(
            capability=Capability.DIAGNOSTIC_MUTATION,
            single_use=False,
            issued_at=clock(),
            expires_at=clock() + 30,
        ),
        granted_by=OPERATOR,
    )
    decision = safety.evaluate(
        request(OperationClass.DIAGNOSTIC_MUTATION, caller=OPERATOR, approval_id="appr-1")
    )
    assert decision.denied
    assert decision.reason_code is SafetyReason.APPROVAL_INVALID


# -- Internal failure --------------------------------------------------------


class _ExplodingPolicy(SafetyPolicy):
    """A policy whose chain cannot finish. The kernel must not answer "yes"."""

    def _decide(self, request: object, context: object) -> object:
        raise RuntimeError("policy internals failed")


def test_a_policy_that_cannot_run_fails_closed() -> None:
    clock = MovableClock()
    safety = SafetyKernel(
        clock=clock,
        permissions=permissions(Capability.CAN_TX),
        policy=_ExplodingPolicy(),
    )
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR))
    assert decision.denied
    assert decision.reason_code is SafetyReason.POLICY_FAILURE
    assert decision.risk_level is None


@pytest.mark.parametrize("level", list(RiskLevel))
def test_the_default_approval_table_never_silently_permits_a_dangerous_level(
    level: RiskLevel,
) -> None:
    requirement = approval_requirement_for(level)
    if level.is_dangerous:
        assert requirement.required is True
        assert requirement.exact_target_required is True
    else:
        assert requirement.required is False
