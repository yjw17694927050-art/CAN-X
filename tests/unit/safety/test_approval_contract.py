"""The approval contract: narrow, expiring, consumable, and impossible to self-issue.

SAFETY-01 §14, §15 and invariant S11. The anti-escalation claims are tested here
as behaviours, not as documentation:

```text
an approval for READ      cannot authorise ECU_MUTATION
an approval for target A  cannot authorise target B
an expired approval       cannot authorise anything
a single-use approval     cannot be spent twice
a machine caller          cannot issue one at all
```
"""

from __future__ import annotations

import threading

import pytest
from canx.safety.approval import Approval, ApprovalIssuer, ApprovalStore
from canx.safety.errors import (
    SafetyApprovalError,
    SafetyApprovalReusedError,
    SafetyCallerError,
)
from canx.safety.risk import Capability
from canx.safety.scope import OperationTarget
from safety_builders import AGENT, HOST, MACHINE_CALLERS, OPERATOR, SCRIPT, approval, target

CAN1 = OperationTarget(channel="can1")
CAN2 = OperationTarget(channel="can2")


# -- The approval value ------------------------------------------------------


def test_an_approval_authorises_its_own_capability_and_target() -> None:
    granted = approval(capability=Capability.CAN_TX, target_=CAN1)
    assert granted.covers(Capability.CAN_TX, CAN1) is True
    assert granted.covers(Capability.CAN_TX, CAN2) is False
    assert granted.covers(Capability.ECU_MUTATION, CAN1) is False


def test_a_lower_risk_approval_cannot_authorise_a_higher_risk_operation() -> None:
    read_approval = approval(capability=Capability.READ)
    assert read_approval.covers(Capability.ECU_MUTATION, OperationTarget()) is False
    assert read_approval.covers(Capability.CAN_TX, OperationTarget()) is False


def test_a_higher_risk_approval_is_not_downgraded_into_a_licence_for_less() -> None:
    """Capability matching is exact in both directions."""
    strong = approval(capability=Capability.ECU_MUTATION)
    assert strong.covers(Capability.READ, OperationTarget()) is False


def test_exact_matching_refuses_an_approval_that_names_more_than_the_request() -> None:
    scoped = approval(capability=Capability.CAN_TX, target_=target(channel="can1"))
    assert scoped.exactly_matches(Capability.CAN_TX, target(channel="can1")) is True
    # The stored target states a channel the request never mentioned: not the
    # same authority, so high-assurance approvals refuse it.
    assert scoped.exactly_matches(Capability.CAN_TX, OperationTarget()) is False


def test_an_approval_must_be_bounded_by_finite_timestamps() -> None:
    with pytest.raises(SafetyApprovalError):
        approval(issued_at=10.0, expires_at=10.0)
    with pytest.raises(SafetyApprovalError):
        approval(issued_at=float("nan"), expires_at=float("nan"))


def test_an_approval_cannot_be_constructed_with_an_unknown_issuer() -> None:
    with pytest.raises(SafetyApprovalError):
        Approval(
            approval_id="appr-x",
            capability=Capability.CAN_TX,
            target=OperationTarget(),
            issued_at=0.0,
            expires_at=1.0,
            issuer="agent",  # type: ignore[arg-type]
        )


def test_an_approval_carries_no_free_text_field_for_a_secret() -> None:
    """SAFETY-01 §20: the shape must make a key leak structurally impossible."""
    fields = set(Approval.__dataclass_fields__)
    assert fields == {
        "approval_id",
        "capability",
        "target",
        "issued_at",
        "expires_at",
        "issuer",
        "single_use",
    }
    assert not any("secret" in name or "key" in name or "token" in name for name in fields)


# -- The store ---------------------------------------------------------------


def test_only_an_authority_bearing_caller_may_issue_an_approval() -> None:
    store = ApprovalStore()
    for machine in MACHINE_CALLERS:
        with pytest.raises(SafetyCallerError):
            store.grant(approval(), granted_by=machine)
    store.grant(approval(), granted_by=OPERATOR)
    # The host issues host approvals: its provenance is its own, not the
    # operator's (invariant S16).
    store.grant(approval(approval_id="appr-2", issuer=ApprovalIssuer.HOST_SYSTEM), granted_by=HOST)
    assert len(store.outstanding()) == 2


def test_an_unknown_approval_reference_fails_closed() -> None:
    store = ApprovalStore()
    with pytest.raises(SafetyApprovalError):
        store.consume("never-issued")
    with pytest.raises(SafetyApprovalError):
        store.consume("")
    with pytest.raises(SafetyApprovalError):
        store.lookup("never-issued")


def test_an_expired_approval_is_refused_and_left_unspent() -> None:
    store = ApprovalStore()
    store.grant(approval(issued_at=0.0, expires_at=10.0), granted_by=OPERATOR)

    def check(candidate: Approval) -> None:
        if candidate.is_expired(20.0):
            raise SafetyApprovalError("expired", code="safety.approval_expired")

    with pytest.raises(SafetyApprovalError):
        store.consume("appr-1", check=check)
    # A refusal is not a consumption: the approval is still there for the
    # operation it was actually issued for.
    assert len(store.outstanding()) == 1


def test_a_single_use_approval_cannot_be_reused() -> None:
    store = ApprovalStore()
    store.grant(approval(single_use=True), granted_by=OPERATOR)
    assert store.consume("appr-1").approval_id == "appr-1"
    with pytest.raises(SafetyApprovalReusedError) as raised:
        store.consume("appr-1")
    assert raised.value.code == "safety.approval_reused"


def test_a_reusable_approval_can_be_consumed_more_than_once() -> None:
    store = ApprovalStore()
    store.grant(approval(single_use=False), granted_by=OPERATOR)
    store.consume("appr-1")
    assert store.consume("appr-1").approval_id == "appr-1"


def test_a_single_use_approval_is_spent_exactly_once_under_concurrency() -> None:
    """Two callers racing for one approval must not both win (SAFETY-01 §25)."""
    store = ApprovalStore()
    store.grant(approval(single_use=True), granted_by=OPERATOR)
    winners: list[str] = []
    losses: list[Exception] = []
    start = threading.Barrier(8)

    def attempt() -> None:
        start.wait()
        try:
            winners.append(store.consume("appr-1").approval_id)
        except Exception as error:  # collected, then asserted on below
            losses.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert winners == ["appr-1"]
    assert len(losses) == 7
    assert all(isinstance(error, SafetyApprovalReusedError) for error in losses)


def test_revoking_an_approval_is_idempotent_and_never_raises() -> None:
    store = ApprovalStore()
    store.grant(approval(), granted_by=OPERATOR)
    assert store.revoke("appr-1") is True
    assert store.revoke("appr-1") is False
    with pytest.raises(SafetyApprovalError):
        store.consume("appr-1")


def test_clearing_the_store_removes_every_approval() -> None:
    store = ApprovalStore()
    store.grant(approval(), granted_by=OPERATOR)
    store.grant(approval(approval_id="appr-2"), granted_by=OPERATOR)
    store.clear()
    assert store.outstanding() == ()


def test_the_issuer_vocabulary_has_no_machine_member() -> None:
    """There is no value an Agent could occupy to become its own issuer."""
    assert {issuer.value for issuer in ApprovalIssuer} == {"human.operator", "host.system"}


def test_an_approval_description_is_secret_free() -> None:
    described = approval().describe()
    assert set(described) == {
        "approval_id",
        "capability",
        "target",
        "issued_at",
        "expires_at",
        "issuer",
        "single_use",
    }


def test_machine_callers_cannot_supply_an_approval_even_for_a_read() -> None:
    """Least privilege applies to the harmless direction too."""
    store = ApprovalStore()
    for machine in (AGENT, SCRIPT):
        with pytest.raises(SafetyCallerError):
            store.grant(approval(capability=Capability.READ), granted_by=machine)
