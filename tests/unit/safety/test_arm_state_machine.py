"""The ARM state machine: explicit, scoped, expiring, and never a boolean.

SAFETY-01 §10 and §28. The claims tested here are the ones invariant S7 makes
load-bearing:

* the runtime starts ``DISARMED`` and there is no path from ``DISARMED``
  straight to ``ARMED``;
* ``ARMING`` is a real state in which nothing dangerous is authorised;
* a lapsed scope is no authority at all, not a stale authority;
* every transition outside the diagram is refused loudly rather than dropped.
"""

from __future__ import annotations

import pytest
from canx.safety.arm import ArmController, ArmState
from canx.safety.errors import SafetyScopeError, SafetyStateError
from canx.safety.risk import Capability
from safety_builders import MovableClock, scope, target


def controller(clock: MovableClock | None = None) -> tuple[ArmController, MovableClock]:
    resolved = clock if clock is not None else MovableClock()
    return ArmController(clock=resolved), resolved


def test_a_new_controller_is_disarmed_and_holds_no_scope() -> None:
    arm, _ = controller()
    assert arm.state is ArmState.DISARMED
    assert arm.scope is None
    assert arm.active_scope() is None


def test_disarmed_to_arming_to_armed_is_the_only_route_in() -> None:
    arm, clock = controller()
    bounded = scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60)
    assert arm.request(bounded) is ArmState.ARMING
    assert arm.state is ArmState.ARMING
    assert arm.confirm() is ArmState.ARMED
    assert arm.state is ArmState.ARMED


def test_arming_is_not_armed() -> None:
    """The gap between requesting and confirming is a state, and it is not armed."""
    arm, clock = controller()
    arm.request(scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60))
    assert arm.active_scope() is None


def test_armed_returns_to_disarmed() -> None:
    arm, clock = controller()
    arm.request(scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60))
    arm.confirm()
    assert arm.disarm() is ArmState.DISARMED
    assert arm.scope is None


def test_disarmed_cannot_jump_straight_to_armed() -> None:
    arm, clock = controller()
    arm.request(scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60))
    arm.disarm()
    with pytest.raises(SafetyStateError) as raised:
        arm.confirm()
    assert raised.value.code == "safety.invalid_transition"


def test_confirming_while_disarmed_is_refused() -> None:
    arm, _ = controller()
    with pytest.raises(SafetyStateError):
        arm.confirm()


def test_armed_cannot_be_re_requested_into_arming() -> None:
    arm, clock = controller()
    arm.request(scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60))
    arm.confirm()
    with pytest.raises(SafetyStateError):
        arm.request(scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60))


def test_arming_cannot_be_re_requested_with_a_second_scope() -> None:
    """A second request would silently overwrite the first scope's authority."""
    arm, clock = controller()
    arm.request(scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60))
    with pytest.raises(SafetyStateError):
        arm.request(scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 999))


def test_disarm_is_idempotent() -> None:
    """The safe direction must not raise — an emergency stop disarms unconditionally."""
    arm, _ = controller()
    assert arm.disarm() is ArmState.DISARMED
    assert arm.disarm() is ArmState.DISARMED


def test_an_already_expired_scope_cannot_be_presented() -> None:
    arm, clock = controller()
    with pytest.raises(SafetyScopeError):
        arm.request(scope(Capability.CAN_TX, granted_at=clock() - 120, expires_at=clock() - 60))


def test_a_scope_that_lapses_before_confirmation_is_refused_and_disarms() -> None:
    arm, clock = controller()
    arm.request(scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 5))
    clock.advance(6)
    with pytest.raises(SafetyScopeError):
        arm.confirm()
    # Not left in ARMING: a failed confirmation must not leave a state the next
    # caller could confirm.
    assert arm.state is ArmState.DISARMED
    assert arm.scope is None


def test_a_lapsed_scope_is_no_authority_even_while_armed() -> None:
    arm, clock = controller()
    arm.request(scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 5))
    arm.confirm()
    clock.advance(5)
    assert arm.active_scope() is None
    # The state field still reads ARMED, and says so honestly alongside an
    # explicit expiry flag rather than depending on when it was read.
    assert arm.describe()["scope_expired"] is True


def test_scope_covers_only_its_own_capability_and_target() -> None:
    arm, clock = controller()
    arm.request(
        scope(
            Capability.CAN_TX,
            target_=target(channel="can1"),
            granted_at=clock(),
            expires_at=clock() + 60,
        )
    )
    arm.confirm()
    active = arm.active_scope()
    assert active is not None
    assert active.covers(Capability.CAN_TX, target(channel="can1"), clock()) is True
    assert active.covers(Capability.ECU_MUTATION, target(channel="can1"), clock()) is False
    assert active.covers(Capability.CAN_TX, target(channel="can2"), clock()) is False
