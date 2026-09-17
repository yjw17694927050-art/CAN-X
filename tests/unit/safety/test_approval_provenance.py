"""Approval provenance comes from a trusted caller, never from the payload.

The defect this file pins down (SAFETY-01-FIX-1, P0-2) is a two-field
inconsistency that the store never noticed:

```text
Approval.issuer            carried by the approval object itself
ApprovalStore.grant(...)   checked granted_by.may_issue_approval, and nothing else
```

Nothing tied the two together. So a caller that may issue approvals — the host
system — could hand over an approval *labelled* ``HUMAN_OPERATOR`` and have it
stored that way:

```text
SYSTEM
  ↓ construct Approval(issuer=HUMAN_OPERATOR)
  ↓ grant()
  ↓ stored as a human approval
  ↓ satisfies human_issuer_required
```

The label was doing the work of a credential. ``human_issuer_required`` exists
precisely so that the most consequential operations — actuation, ECU mutation,
critical — cannot be authorised by the host runtime acting alone; a provenance
field the payload fills in for itself defeats that entirely.

The rule (invariant S16):

> Approval provenance is derived from trusted caller identity, and cannot be
> self-declared.

These tests express the defect as behaviour first — a forged label being
*accepted* — and as the API that removes the possibility second.
"""

from __future__ import annotations

import pytest
from canx.safety.approval import Approval, ApprovalIssuer, ApprovalSpec, issuer_for
from canx.safety.decision import SafetyReason
from canx.safety.errors import SafetyError
from canx.safety.risk import Capability, OperationClass
from canx.safety.scope import OperationTarget
from safety_builders import (
    AGENT,
    AUTOMATION,
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
)


def forged(
    *,
    issuer: ApprovalIssuer,
    capability: Capability = Capability.ECU_MUTATION,
    approval_id: str = "appr-forged",
    single_use: bool = False,
) -> Approval:
    """An approval whose provenance label was chosen by whoever built it."""
    return Approval(
        approval_id=approval_id,
        capability=capability,
        target=OperationTarget(),
        issued_at=0.0,
        expires_at=1_000_000.0,
        issuer=issuer,
        single_use=single_use,
    )


# -- The forgery, refused -----------------------------------------------------


def test_a_system_caller_cannot_grant_an_approval_that_claims_a_human_operator() -> None:
    """The host owns the runtime; it does not get to speak as the operator."""
    safety = kernel()
    with pytest.raises(SafetyError):
        safety.grant_approval(
            forged(issuer=ApprovalIssuer.HUMAN_OPERATOR), granted_by=HOST
        )
    assert safety.approvals.outstanding() == ()


def test_an_operator_cannot_grant_an_approval_that_claims_the_host() -> None:
    """The mismatch is refused in both directions, not only the dangerous one."""
    safety = kernel()
    with pytest.raises(SafetyError):
        safety.grant_approval(
            forged(issuer=ApprovalIssuer.HOST_SYSTEM), granted_by=OPERATOR
        )
    assert safety.approvals.outstanding() == ()


@pytest.mark.parametrize(
    "operation_class",
    [
        OperationClass.ACTUATION,
        OperationClass.ECU_MUTATION,
        OperationClass.CRITICAL_OPERATION,
    ],
)
def test_a_forged_human_label_does_not_unlock_human_issuer_operations(
    operation_class: OperationClass,
) -> None:
    """The consequence, end to end: forging the label must buy nothing."""
    clock = MovableClock()
    capability = {
        OperationClass.ACTUATION: Capability.ACTUATION,
        OperationClass.ECU_MUTATION: Capability.ECU_MUTATION,
        OperationClass.CRITICAL_OPERATION: Capability.CRITICAL_OPERATION,
    }[operation_class]
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset({Capability.CAN_TX, capability}),
        duration=600.0,
        permission_set=permissions(Capability.CAN_TX, capability),
    )
    with pytest.raises(SafetyError):
        safety.grant_approval(
            forged(issuer=ApprovalIssuer.HUMAN_OPERATOR, capability=capability),
            granted_by=HOST,
        )
    decision = safety.evaluate(
        request(operation_class, caller=OPERATOR, approval_id="appr-forged")
    )
    assert decision.denied


def test_a_host_issued_approval_cannot_satisfy_a_human_issuer_requirement() -> None:
    """Even a *legitimate* host approval is not a human one."""
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset({Capability.CAN_TX, Capability.ACTUATION}),
        duration=600.0,
        permission_set=permissions(Capability.CAN_TX, Capability.ACTUATION),
    )
    safety.grant_approval(
        ApprovalSpec(
            approval_id="appr-host",
            capability=Capability.ACTUATION,
            target=OperationTarget(),
            issued_at=clock(),
            expires_at=clock() + 600,
            single_use=True,
        ),
        granted_by=HOST,
    )
    decision = safety.evaluate(
        request(OperationClass.ACTUATION, caller=OPERATOR, approval_id="appr-host")
    )
    assert decision.denied
    assert decision.reason_code is SafetyReason.APPROVAL_INVALID


# -- The correct behaviour, locked in -----------------------------------------


def test_the_kernel_derives_the_issuer_from_the_caller_kind() -> None:
    """The provenance on a stored approval is the caller's, not the payload's."""
    clock = MovableClock()
    safety = kernel(clock=clock)
    stored = safety.grant_approval(
        ApprovalSpec(
            approval_id="appr-1",
            capability=Capability.CAN_TX,
            target=OperationTarget(),
            issued_at=clock(),
            expires_at=clock() + 60,
        ),
        granted_by=OPERATOR,
    )
    assert stored.issuer is ApprovalIssuer.HUMAN_OPERATOR
    assert safety.approvals.lookup("appr-1").issuer is ApprovalIssuer.HUMAN_OPERATOR


def test_the_kernel_derives_the_host_issuer_from_a_system_caller() -> None:
    clock = MovableClock()
    safety = kernel(clock=clock)
    stored = safety.grant_approval(
        ApprovalSpec(
            approval_id="appr-2",
            capability=Capability.CAN_TX,
            target=OperationTarget(),
            issued_at=clock(),
            expires_at=clock() + 60,
        ),
        granted_by=HOST,
    )
    assert stored.issuer is ApprovalIssuer.HOST_SYSTEM


@pytest.mark.parametrize("caller", MACHINE_CALLERS)
def test_a_machine_caller_still_cannot_issue_an_approval(caller: object) -> None:
    clock = MovableClock()
    safety = kernel(clock=clock)
    with pytest.raises(SafetyError):
        safety.grant_approval(
            ApprovalSpec(
                approval_id="appr-3",
                capability=Capability.CAN_TX,
                target=OperationTarget(),
                issued_at=clock(),
                expires_at=clock() + 60,
            ),
            granted_by=caller,  # type: ignore[arg-type]
        )
    assert safety.approvals.outstanding() == ()


def test_a_spec_carries_no_provenance_field_at_all() -> None:
    """Nothing to forge: the shape has no issuer for a caller to choose."""
    fields = set(ApprovalSpec.__dataclass_fields__)
    assert "issuer" not in fields
    for forbidden in ("issuer", "granted_by", "caller", "provenance"):
        assert forbidden not in fields


def test_the_issuer_mapping_is_total_over_authority_bearing_callers() -> None:
    assert issuer_for(OPERATOR) is ApprovalIssuer.HUMAN_OPERATOR
    assert issuer_for(HOST) is ApprovalIssuer.HOST_SYSTEM
    for machine in (AGENT, SCRIPT, AUTOMATION):
        with pytest.raises(SafetyError):
            issuer_for(machine)


def test_a_human_operator_approval_still_works_end_to_end() -> None:
    """The fix must not break the legitimate path it protects."""
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset({Capability.CAN_TX, Capability.ACTUATION}),
        duration=600.0,
        permission_set=permissions(Capability.CAN_TX, Capability.ACTUATION),
    )
    safety.grant_approval(
        ApprovalSpec(
            approval_id="appr-human",
            capability=Capability.ACTUATION,
            target=OperationTarget(),
            issued_at=clock(),
            expires_at=clock() + 600,
            single_use=True,
        ),
        granted_by=OPERATOR,
    )
    decision = safety.evaluate(
        request(OperationClass.ACTUATION, caller=OPERATOR, approval_id="appr-human")
    )
    assert decision.allowed


def test_the_store_still_binds_provenance_for_a_hand_built_approval() -> None:
    """The kernel is not the only door, so the store checks the label it is given.

    ``SafetyKernel.grant_approval`` removes the possibility of choosing a
    provenance by construction. The store is reachable directly (a host may
    revoke through it), so it independently refuses a label that does not match
    the caller — defence in depth rather than a single choke point.
    """
    safety = kernel()
    safety.approvals.grant(
        approval(capability=Capability.CAN_TX, issuer=ApprovalIssuer.HUMAN_OPERATOR),
        granted_by=OPERATOR,
    )
    assert safety.approvals.outstanding() != ()


def test_the_store_refuses_a_hand_built_approval_with_the_wrong_label() -> None:
    safety = kernel()
    with pytest.raises(SafetyError):
        safety.approvals.grant(
            approval(capability=Capability.CAN_TX, issuer=ApprovalIssuer.HUMAN_OPERATOR),
            granted_by=HOST,
        )
    assert safety.approvals.outstanding() == ()
