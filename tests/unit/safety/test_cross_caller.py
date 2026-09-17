"""Cross-caller: caller kind changes who may *supply* authority, nothing else.

SAFETY-01 §30 and invariants S3, S4. Two claims:

```text
the same conditions produce the same verdict for every caller kind
no machine caller can arm the runtime or issue an approval
```

The first is what stops "it came from an Agent" from becoming a reason to skip a
check. The second is what stops an Agent from manufacturing the conditions.
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
    SCRIPT,
    MovableClock,
    approval,
    armed_kernel,
    kernel,
    permissions,
    request,
    scope,
)


@pytest.mark.parametrize("caller", ALL_CALLERS)
def test_a_dangerous_operation_without_an_approval_is_denied_for_every_caller(
    caller: object,
) -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    decision = safety.evaluate(request(DANGEROUS, caller=caller))  # type: ignore[arg-type]
    assert decision.denied
    assert decision.reason_code is SafetyReason.APPROVAL_REQUIRED


@pytest.mark.parametrize("caller", ALL_CALLERS)
def test_a_dangerous_operation_with_every_authority_is_allowed_for_every_caller(
    caller: object,
) -> None:
    """No caller kind is privileged into *or out of* the ordinary chain.

    An Agent with a valid approval and a live arm scope is authorised exactly as
    an operator is: the machine-ness of the requester is not itself a denial
    reason, and treating it as one would make the real controls (arm, approval,
    scope) look decorative.
    """
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        approval(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    decision = safety.evaluate(
        request(DANGEROUS, caller=caller, approval_id="appr-1")  # type: ignore[arg-type]
    )
    assert decision.allowed


@pytest.mark.parametrize("caller", ALL_CALLERS)
def test_a_disarmed_runtime_denies_every_caller_equally(caller: object) -> None:
    safety = kernel(permission_set=permissions(Capability.CAN_TX))
    decision = safety.evaluate(request(DANGEROUS, caller=caller))  # type: ignore[arg-type]
    assert decision.denied
    assert decision.reason_code is SafetyReason.NOT_ARMED


@pytest.mark.parametrize("caller", ALL_CALLERS)
def test_a_missing_permission_denies_every_caller(caller: object) -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock)
    decision = safety.evaluate(request(DANGEROUS, caller=caller))  # type: ignore[arg-type]
    assert decision.denied
    assert decision.reason_code is SafetyReason.PERMISSION_DENIED


# -- Who may supply authority -------------------------------------------------


@pytest.mark.parametrize("caller", MACHINE_CALLERS)
def test_a_machine_caller_cannot_arm_the_runtime(caller: object) -> None:
    """Invariant S4: the runtime cannot be put into the dangerous state by a machine."""
    clock = MovableClock()
    safety = kernel(clock=clock)
    with pytest.raises(SafetyCallerError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
            caller=caller,  # type: ignore[arg-type]
        )
    assert safety.arm_state is ArmState.DISARMED


@pytest.mark.parametrize("caller", MACHINE_CALLERS)
def test_a_machine_caller_cannot_confirm_an_arm(caller: object) -> None:
    clock = MovableClock()
    safety = kernel(clock=clock)
    safety.arm(
        scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
        caller=OPERATOR,
    )
    with pytest.raises(SafetyCallerError):
        safety.confirm_arm(caller=caller)  # type: ignore[arg-type]
    assert safety.arm_state is ArmState.ARMING


@pytest.mark.parametrize("caller", MACHINE_CALLERS)
def test_a_machine_caller_cannot_issue_an_approval(caller: object) -> None:
    safety = kernel()
    with pytest.raises(SafetyCallerError):
        safety.grant_approval(approval(), granted_by=caller)  # type: ignore[arg-type]
    assert safety.approvals.outstanding() == ()


def test_an_agent_cannot_self_approve_then_spend_its_own_approval() -> None:
    """The composite attack: an Agent that mints an approval and presents it.

    Both halves are refused — the minting here, and the spending in the policy
    tests, where an approval the Agent could not have obtained is still just one
    input among three.
    """
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    with pytest.raises(SafetyCallerError):
        safety.grant_approval(approval(), granted_by=AGENT)
    decision = safety.evaluate(request(DANGEROUS, caller=AGENT, approval_id="appr-1"))
    assert decision.denied
    assert decision.reason_code is SafetyReason.APPROVAL_INVALID


def test_only_an_authority_bearing_caller_may_arm() -> None:
    for caller in (OPERATOR, HOST):
        clock = MovableClock()
        safety = kernel(clock=clock)
        safety.arm(
            scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
            caller=caller,
        )
        assert safety.confirm_arm(caller=caller) is ArmState.ARMED


def test_the_machine_caller_set_is_the_three_kinds_the_rules_name() -> None:
    assert {caller.kind.value for caller in MACHINE_CALLERS} == {
        "agent",
        "script",
        "automation",
    }
    assert SCRIPT.may_control_arm is False
    assert AUTOMATION.may_issue_approval is False
