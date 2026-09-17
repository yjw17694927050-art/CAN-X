"""Effect risk and execution authority are two different questions.

The defect this file pins down (SAFETY-01-FIX-1, P0-1) is that the model answered
only one of them.

```text
operation class → effect risk → exactly one capability
```

That collapses "what does this do to the vehicle?" (its **effect risk**) and "what
authority does executing it require?" (its **required capabilities**) into a
single axis. Almost every operation survives that collapse. ``diagnostic.read``
does not:

```text
diagnostic.read
  effect risk            READ      — it only observes, and that must stay true
  execution authority    CAN_TX    — it still puts frames on a live bus
```

Under the old model it landed on ``Capability.READ`` alone, which means a future
may have let a diagnostic read ride the low-risk automatic path — straight past
the ARM state, the CAN_TX grant and the audit trail that every other real
transmission has to cross. The fix is *not* to relabel it ``TX`` (that would lose
the read-only effect risk, which is real); it is to make required capabilities a
set that any vehicle-transmitting operation has to include ``CAN_TX`` in.

These tests are written against the public surface, so they express the defect as
a wrong verdict rather than a missing symbol: each one below returns ``ALLOW``
before the fix and ``DENY`` after it.
"""

from __future__ import annotations

import pytest
from canx.safety.decision import SafetyReason
from canx.safety.risk import Capability, OperationClass
from safety_builders import (
    OPERATOR,
    MovableClock,
    armed_kernel,
    kernel,
    permissions,
    request,
    target,
)

DIAGNOSTIC_READ = OperationClass.DIAGNOSTIC_READ
ENGINEERING_READ = OperationClass.ENGINEERING_READ


# -- The defect, expressed as a wrong verdict ---------------------------------


def test_a_diagnostic_read_with_only_a_read_grant_is_denied() -> None:
    """The whole point: READ authority is not enough to put frames on a bus.

    The runtime is armed for transmission here, so the arm state is not what
    refuses this — the missing ``CAN_TX`` grant is.
    """
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset({Capability.CAN_TX}),
        permission_set=permissions(Capability.READ),
    )
    decision = safety.evaluate(request(DIAGNOSTIC_READ, caller=OPERATOR))
    assert decision.denied
    assert decision.reason_code is SafetyReason.PERMISSION_DENIED


def test_a_diagnostic_read_is_denied_while_the_runtime_is_disarmed() -> None:
    """A live bus request is a vehicle operation even when it only observes."""
    safety = kernel(permission_set=permissions(Capability.READ, Capability.CAN_TX))
    decision = safety.evaluate(request(DIAGNOSTIC_READ, caller=OPERATOR))
    assert decision.denied
    assert decision.reason_code is SafetyReason.NOT_ARMED


def test_a_diagnostic_read_outside_the_arm_scope_is_denied() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset({Capability.READ, Capability.CAN_TX}),
        target_=target(channel="can1"),
        permission_set=permissions(
            Capability.READ, Capability.CAN_TX, target_=target(channel="can1")
        ),
    )
    decision = safety.evaluate(
        request(DIAGNOSTIC_READ, caller=OPERATOR, target_=target(channel="can2"))
    )
    assert decision.denied
    assert decision.reason_code is SafetyReason.SCOPE_VIOLATION


# -- The correct behaviour, locked in -----------------------------------------


def test_a_diagnostic_read_with_full_authority_is_allowed() -> None:
    """Armed, granted and in scope: a read is still a read, and it proceeds."""
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset({Capability.CAN_TX}),
        permission_set=permissions(Capability.READ, Capability.CAN_TX),
    )
    decision = safety.evaluate(request(DIAGNOSTIC_READ, caller=OPERATOR))
    assert decision.allowed


def test_a_diagnostic_read_does_not_need_an_approval() -> None:
    """Requiring vehicle authority must not silently make reads dangerous work."""
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset({Capability.CAN_TX}),
        permission_set=permissions(Capability.READ, Capability.CAN_TX),
    )
    decision = safety.evaluate(request(DIAGNOSTIC_READ, caller=OPERATOR))
    assert decision.allowed
    assert safety.approvals.outstanding() == ()


def test_an_engineering_read_still_needs_only_a_read_grant() -> None:
    """Captured data is already on disk: no bus, no grant beyond READ."""
    safety = kernel(permission_set=permissions(Capability.READ))
    decision = safety.evaluate(request(ENGINEERING_READ, caller=OPERATOR))
    assert decision.allowed


def test_an_engineering_read_is_not_gated_by_the_arm_state() -> None:
    safety = kernel(permission_set=permissions(Capability.READ))
    assert safety.arm_state.value == "disarmed"
    assert safety.evaluate(request(ENGINEERING_READ, caller=OPERATOR)).allowed


def test_an_engineering_compute_is_not_gated_by_the_arm_state() -> None:
    safety = kernel(permission_set=permissions(Capability.COMPUTE))
    decision = safety.evaluate(request(OperationClass.ENGINEERING_COMPUTE, caller=OPERATOR))
    assert decision.allowed


def test_a_project_write_is_not_gated_by_the_arm_state() -> None:
    safety = kernel(permission_set=permissions(Capability.WRITE_PROJECT))
    decision = safety.evaluate(request(OperationClass.PROJECT_WRITE, caller=OPERATOR))
    assert decision.allowed


# -- Every vehicle-transmitting class, not only the read ----------------------


@pytest.mark.parametrize(
    "operation_class",
    [
        OperationClass.DIAGNOSTIC_READ,
        OperationClass.BUS_TRANSMIT,
        OperationClass.BUS_REPLAY,
        OperationClass.BUS_INJECTION,
        OperationClass.DIAGNOSTIC_MUTATION,
        OperationClass.ACTUATION,
        OperationClass.ECU_MUTATION,
        OperationClass.CRITICAL_OPERATION,
    ],
)
def test_every_vehicle_transmitting_operation_requires_can_tx(
    operation_class: OperationClass,
) -> None:
    """No class that frames a live bus may be reachable without CAN_TX."""
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset(Capability),
        duration=600.0,
        permission_set=permissions(
            Capability.READ,
            Capability.COMPUTE,
            Capability.WRITE_PROJECT,
        ),
    )
    decision = safety.evaluate(request(operation_class, caller=OPERATOR))
    assert decision.denied, operation_class
    assert decision.reason_code is SafetyReason.PERMISSION_DENIED


@pytest.mark.parametrize(
    "operation_class",
    [
        OperationClass.DIAGNOSTIC_READ,
        OperationClass.BUS_TRANSMIT,
        OperationClass.DIAGNOSTIC_MUTATION,
        OperationClass.ECU_MUTATION,
    ],
)
def test_every_vehicle_transmitting_operation_requires_the_arm_state(
    operation_class: OperationClass,
) -> None:
    safety = kernel(permission_set=permissions(*Capability))
    decision = safety.evaluate(request(operation_class, caller=OPERATOR))
    assert decision.denied, operation_class
    assert decision.reason_code is SafetyReason.NOT_ARMED


@pytest.mark.parametrize(
    "operation_class",
    [
        OperationClass.ENGINEERING_READ,
        OperationClass.ENGINEERING_COMPUTE,
        OperationClass.PROJECT_WRITE,
    ],
)
def test_no_off_vehicle_operation_is_armed_gated(operation_class: OperationClass) -> None:
    """The three levels below the boundary stay usable without arming."""
    safety = kernel(permission_set=permissions(*Capability))
    assert safety.evaluate(request(operation_class, caller=OPERATOR)).allowed, operation_class
