"""Scope comparison and capability-based permission — least privilege, fail closed.

SAFETY-01 §12, §13 and invariant S6. The claims tested here are the ones that
decide whether "armed for channel 1" can leak into "armed for channel 2", and
whether a blank in a request can be read as a wildcard. Both are refused, and
both refusals are structural rather than remembered.
"""

from __future__ import annotations

import math
from typing import cast

import pytest
from canx.safety.errors import SafetyScopeError
from canx.safety.permission import PermissionGrant, PermissionSet
from canx.safety.risk import Capability
from canx.safety.scope import ArmScope, OperationTarget, has_lapsed
from safety_builders import permissions, scope, target

CAN1 = OperationTarget(channel="can1")
CAN2 = OperationTarget(channel="can2")
UNSTATED = OperationTarget()


# -- OperationTarget.covers --------------------------------------------------


def test_a_stated_coordinate_must_match_exactly() -> None:
    assert CAN1.covers(CAN1) is True
    assert CAN1.covers(CAN2) is False


def test_an_unstated_coordinate_in_the_grant_imposes_no_constraint() -> None:
    assert UNSTATED.covers(CAN1) is True


def test_a_blank_request_coordinate_is_not_a_wildcard() -> None:
    """The row that matters: a request that does not say *where* is not covered.

    Reading the blank as "any channel" would turn every request that forgot to
    name its channel into a request for every channel.
    """
    assert CAN1.covers(UNSTATED) is False


def test_a_target_address_is_compared_the_same_way() -> None:
    granted = OperationTarget(target_address=0x7E0)
    assert granted.covers(OperationTarget(target_address=0x7E0)) is True
    assert granted.covers(OperationTarget(target_address=0x7E8)) is False
    assert granted.covers(OperationTarget()) is False


def test_a_malformed_target_is_refused_at_construction() -> None:
    with pytest.raises(SafetyScopeError):
        OperationTarget(channel="")
    with pytest.raises(SafetyScopeError):
        OperationTarget(device_id="")
    with pytest.raises(SafetyScopeError):
        OperationTarget(target_address=-1)
    with pytest.raises(SafetyScopeError):
        OperationTarget(target_address=0x2000_0000)


# -- ArmScope ----------------------------------------------------------------


def test_an_arm_scope_must_open_something() -> None:
    with pytest.raises(SafetyScopeError):
        ArmScope(capabilities=frozenset(), target=UNSTATED, granted_at=0.0, expires_at=1.0)


def test_an_arm_scope_must_be_finite_and_bounded() -> None:
    with pytest.raises(SafetyScopeError):
        ArmScope(
            capabilities=frozenset({Capability.CAN_TX}),
            target=UNSTATED,
            granted_at=0.0,
            expires_at=0.0,
        )
    with pytest.raises(SafetyScopeError):
        ArmScope(
            capabilities=frozenset({Capability.CAN_TX}),
            target=UNSTATED,
            granted_at=math.nan,
            expires_at=math.nan,
        )


def test_a_scope_expiring_exactly_now_has_expired() -> None:
    bounded = scope(Capability.CAN_TX, granted_at=0.0, expires_at=10.0)
    assert bounded.is_expired(9.999_999) is False
    assert bounded.is_expired(10.0) is True


def test_a_broken_clock_reading_expires_rather_than_extends() -> None:
    """NaN compares false against everything, so ``now >= expiry`` would say "valid".

    That is the one direction expiry must never fail in, which is why the check
    is written as ``not (now < expiry)``.
    """
    assert has_lapsed(math.nan, 10.0) is True
    assert has_lapsed(math.inf, 10.0) is True
    assert scope(Capability.CAN_TX, granted_at=0.0, expires_at=10.0).is_expired(math.nan) is True


def test_a_scope_covers_a_capability_and_a_target_together() -> None:
    granted = scope(Capability.CAN_TX, target_=CAN1, granted_at=0.0, expires_at=10.0)
    assert granted.covers(Capability.CAN_TX, CAN1, 5.0) is True
    assert granted.covers(Capability.CAN_TX, CAN2, 5.0) is False
    assert granted.covers(Capability.CAN_TX, CAN1, 10.0) is False
    assert granted.covers(Capability.DIAGNOSTIC_MUTATION, CAN1, 5.0) is False


# -- PermissionSet -----------------------------------------------------------


def test_an_empty_permission_set_authorises_nothing() -> None:
    empty = PermissionSet()
    assert empty.covers(Capability.READ, UNSTATED, 0.0) is False
    assert empty.covers(Capability.CAN_TX, UNSTATED, 0.0) is False
    assert empty.granted_capabilities() == frozenset()


def test_a_capability_grant_does_not_leak_into_another_capability() -> None:
    """A grant to read is not a grant to clear fault memory."""
    held = permissions(Capability.READ)
    assert held.covers(Capability.READ, UNSTATED, 0.0) is True
    assert held.covers(Capability.ECU_MUTATION, UNSTATED, 0.0) is False
    assert held.covers(Capability.CAN_TX, UNSTATED, 0.0) is False


def test_a_grant_is_scoped_to_its_target() -> None:
    held = permissions(Capability.CAN_TX, target_=CAN1)
    assert held.covers(Capability.CAN_TX, CAN1, 0.0) is True
    assert held.covers(Capability.CAN_TX, CAN2, 0.0) is False


def test_an_expired_grant_authorises_nothing() -> None:
    held = permissions(Capability.CAN_TX, expires_at=10.0)
    assert held.covers(Capability.CAN_TX, UNSTATED, 9.0) is True
    assert held.covers(Capability.CAN_TX, UNSTATED, 10.0) is False


def test_an_empty_grant_cannot_be_constructed() -> None:
    with pytest.raises(SafetyScopeError):
        PermissionGrant(capabilities=frozenset())


def test_a_permission_set_cannot_be_widened_through_its_own_api() -> None:
    """There is no mutator, and this test exists to notice if one is added."""
    held = permissions(Capability.READ)
    assert not any(
        name in {"add", "grant", "widen", "update", "extend"} for name in dir(held)
    )


def test_a_forged_capability_cannot_authorise_a_real_operation() -> None:
    """An unknown capability name is not a skeleton key for a known one."""
    held = PermissionSet(
        [PermissionGrant(capabilities=frozenset({cast(Capability, "ROOT")}))]
    )
    assert held.covers(Capability.CAN_TX, UNSTATED, 0.0) is False
    assert held.covers(Capability.READ, UNSTATED, 0.0) is False


def test_a_target_that_states_more_than_the_grant_does_is_still_covered() -> None:
    """The grant names the channel; the request adds an address it did not limit."""
    granted = permissions(Capability.CAN_TX, target_=CAN1)
    assert (
        granted.covers(Capability.CAN_TX, target(channel="can1", target_address=0x7E0), 0.0)
        is True
    )
