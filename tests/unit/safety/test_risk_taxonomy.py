"""The risk taxonomy is a deterministic, closed, effect-based classification.

SAFETY-01 §7 and §28, extended by SAFETY-01-FIX-1 §4-§7. Three claims are tested
here and they are the foundation everything else stands on:

* classification is **deterministic** — the same operation always lands on the
  same level, and no registry, clock or configuration can change that between
  two calls;
* classification is **effect-based** — the two halves of diagnostics land on
  opposite sides of the dangerous boundary, which is exactly what a
  protocol-keyed taxonomy ("is UDS dangerous?") could not express;
* classification is **closed** — an unclassifiable operation raises instead of
  falling through to a permissive default.

And the claim that was added when the one-axis model was removed: an operation's
**required capabilities** are a separate, larger answer than its effect risk, and
every operation that frames a live bus has to include ``CAN_TX``.
"""

from __future__ import annotations

from typing import cast

import pytest
from canx.safety.errors import SafetyUnknownOperationError
from canx.safety.risk import (
    DANGEROUS_CAPABILITIES,
    Capability,
    OperationClass,
    OperationPolicy,
    RiskLevel,
    approval_capability_for,
    classify_operation,
    is_dangerous_capability,
    operation_policy,
)

#: The five levels that may change a vehicle rather than observe it.
DANGEROUS_LEVELS = frozenset(
    {
        RiskLevel.TX,
        RiskLevel.DIAGNOSTIC_MUTATION,
        RiskLevel.ACTUATION,
        RiskLevel.ECU_MUTATION,
        RiskLevel.CRITICAL,
    }
)

SAFE_LEVELS = frozenset({RiskLevel.READ, RiskLevel.COMPUTE, RiskLevel.WRITE_PROJECT})


# -- Effect risk --------------------------------------------------------------


def test_the_three_safe_levels_are_exactly_the_ones_below_the_boundary() -> None:
    for level in SAFE_LEVELS:
        assert not level.is_dangerous
    for level in DANGEROUS_LEVELS:
        assert level.is_dangerous


def test_the_dangerous_boundary_is_above_write_project() -> None:
    assert RiskLevel.WRITE_PROJECT.is_dangerous is False
    assert RiskLevel.TX.is_dangerous is True


def test_every_operation_class_has_exactly_one_policy() -> None:
    policies = {operation: operation_policy(operation) for operation in OperationClass}
    assert set(policies) == set(OperationClass)


def test_classification_is_deterministic_across_repeated_calls() -> None:
    first = {operation: classify_operation(operation) for operation in OperationClass}
    second = {operation: classify_operation(operation) for operation in OperationClass}
    assert first == second


def test_an_operation_class_is_accepted_as_its_string_value() -> None:
    assert classify_operation("bus.transmit") is RiskLevel.TX
    assert classify_operation(OperationClass.BUS_TRANSMIT) is RiskLevel.TX


def test_diagnostics_are_split_by_effect_not_by_protocol() -> None:
    """Reading diagnostics and mutating diagnostics are not the same risk.

    This is the concrete case SAFETY-01 §7 names: a taxonomy that asked "is this
    UDS?" would have to answer the same way for both, and would be wrong for one
    of them.
    """
    assert classify_operation(OperationClass.DIAGNOSTIC_READ) is RiskLevel.READ
    assert classify_operation(OperationClass.DIAGNOSTIC_MUTATION) is RiskLevel.DIAGNOSTIC_MUTATION
    assert classify_operation(OperationClass.DIAGNOSTIC_READ).is_dangerous is False
    assert classify_operation(OperationClass.DIAGNOSTIC_MUTATION).is_dangerous is True


def test_every_transmit_family_is_dangerous() -> None:
    for operation in (
        OperationClass.BUS_TRANSMIT,
        OperationClass.BUS_REPLAY,
        OperationClass.BUS_INJECTION,
    ):
        assert classify_operation(operation).is_dangerous


def test_an_unknown_operation_class_is_refused_rather_than_guessed() -> None:
    with pytest.raises(SafetyUnknownOperationError) as raised:
        operation_policy("bus.teleport")
    assert raised.value.code == "safety.unknown_operation"


def test_classification_refuses_a_non_string_argument() -> None:
    with pytest.raises(SafetyUnknownOperationError):
        operation_policy(cast(OperationClass, None))
    with pytest.raises(SafetyUnknownOperationError):
        operation_policy(cast(OperationClass, 3))


# -- Required capabilities: the second axis -----------------------------------


def test_effect_capability_is_one_to_one_with_effect_risk() -> None:
    capabilities = [approval_capability_for(level) for level in RiskLevel]
    assert len(set(capabilities)) == len(RiskLevel)


def test_a_risk_level_without_an_effect_capability_is_refused() -> None:
    with pytest.raises(SafetyUnknownOperationError):
        approval_capability_for(cast(RiskLevel, 99))


def test_a_diagnostic_read_has_a_read_effect_and_a_transmit_authority() -> None:
    """The pair of answers a one-axis model could not carry at the same time."""
    policy = operation_policy(OperationClass.DIAGNOSTIC_READ)
    assert policy.effect_risk is RiskLevel.READ
    assert policy.effect_risk.is_dangerous is False
    assert policy.required_capabilities == frozenset({Capability.READ, Capability.CAN_TX})
    assert policy.transmits_to_vehicle is True
    assert policy.requires_arm is True


def test_an_engineering_read_needs_no_vehicle_authority() -> None:
    policy = operation_policy(OperationClass.ENGINEERING_READ)
    assert policy.required_capabilities == frozenset({Capability.READ})
    assert policy.transmits_to_vehicle is False
    assert policy.requires_arm is False


@pytest.mark.parametrize("operation_class", list(OperationClass))
def test_every_transmitting_operation_requires_can_tx(operation_class: OperationClass) -> None:
    policy = operation_policy(operation_class)
    if policy.transmits_to_vehicle and policy.effect_risk.is_dangerous:
        assert Capability.CAN_TX in policy.required_capabilities


def test_the_diagnostic_read_is_the_only_read_with_vehicle_authority() -> None:
    """Reads that do not touch the bus must not pay for the ARM state."""
    reads = [
        operation
        for operation in OperationClass
        if operation_policy(operation).effect_risk is RiskLevel.READ
    ]
    transmitting = [
        operation for operation in reads if operation_policy(operation).transmits_to_vehicle
    ]
    assert transmitting == [OperationClass.DIAGNOSTIC_READ]


def test_every_off_vehicle_operation_skips_the_arm_state() -> None:
    for operation in (
        OperationClass.ENGINEERING_READ,
        OperationClass.ENGINEERING_COMPUTE,
        OperationClass.PROJECT_WRITE,
    ):
        assert operation_policy(operation).requires_arm is False


def test_every_vehicle_operation_requires_the_arm_state() -> None:
    for operation in OperationClass:
        policy = operation_policy(operation)
        if policy.transmits_to_vehicle or policy.effect_risk.is_dangerous:
            assert policy.requires_arm is True


def test_a_policy_that_omits_its_effect_capability_is_refused() -> None:
    with pytest.raises(SafetyUnknownOperationError):
        OperationPolicy(
            operation_class=OperationClass.ECU_MUTATION,
            effect_risk=RiskLevel.ECU_MUTATION,
            required_capabilities=frozenset({Capability.READ}),
        )


def test_a_policy_with_no_capabilities_is_refused() -> None:
    with pytest.raises(SafetyUnknownOperationError):
        OperationPolicy(
            operation_class=OperationClass.ENGINEERING_READ,
            effect_risk=RiskLevel.READ,
            required_capabilities=frozenset(),
        )


def test_a_dangerous_effect_without_can_tx_is_refused() -> None:
    """A mutation that does not claim transmission authority is a table error."""
    with pytest.raises(SafetyUnknownOperationError):
        OperationPolicy(
            operation_class=OperationClass.ECU_MUTATION,
            effect_risk=RiskLevel.ECU_MUTATION,
            required_capabilities=frozenset({Capability.ECU_MUTATION}),
        )


def test_the_dangerous_capability_set_names_everything_that_must_expire() -> None:
    assert frozenset(
        {
            Capability.CAN_TX,
            Capability.DIAGNOSTIC_MUTATION,
            Capability.ACTUATION,
            Capability.ECU_MUTATION,
            Capability.CRITICAL_OPERATION,
        }
    ) == DANGEROUS_CAPABILITIES
    assert not is_dangerous_capability(Capability.READ)
    assert not is_dangerous_capability(Capability.COMPUTE)
    assert not is_dangerous_capability(Capability.WRITE_PROJECT)
    assert is_dangerous_capability(Capability.CAN_TX)


def test_every_dangerous_capability_is_reachable_from_some_operation() -> None:
    """No grant may exist that an operator could hand out and change nothing."""
    reachable = {
        capability
        for operation in OperationClass
        for capability in operation_policy(operation).required_capabilities
    }
    assert reachable >= DANGEROUS_CAPABILITIES


def test_the_policy_description_is_audit_safe() -> None:
    described = operation_policy(OperationClass.DIAGNOSTIC_READ).describe()
    assert described == {
        "operation_class": "diagnostic.read",
        "effect_risk": 1,
        "required_capabilities": ["CAN_TX", "READ"],
        "transmits_to_vehicle": True,
        "requires_arm": True,
    }
