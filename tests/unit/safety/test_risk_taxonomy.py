"""The risk taxonomy is a deterministic, closed, effect-based classification.

SAFETY-01 §7 and §28. Three claims are tested here and they are the foundation
everything else stands on:

* classification is **deterministic** — the same operation always lands on the
  same level, and no registry, clock or configuration can change that between
  two calls;
* classification is **effect-based** — the two halves of diagnostics land on
  opposite sides of the dangerous boundary, which is exactly what a
  protocol-keyed taxonomy ("is UDS dangerous?") could not express;
* classification is **closed** — an unclassifiable operation raises instead of
  falling through to a permissive default.
"""

from __future__ import annotations

from typing import cast

import pytest
from canx.safety.errors import SafetyUnknownOperationError
from canx.safety.risk import (
    Capability,
    OperationClass,
    RiskLevel,
    classify_operation,
    required_capability,
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


def test_the_three_safe_levels_are_exactly_the_ones_below_the_boundary() -> None:
    for level in SAFE_LEVELS:
        assert not level.is_dangerous
    for level in DANGEROUS_LEVELS:
        assert level.is_dangerous


def test_the_dangerous_boundary_is_above_write_project() -> None:
    assert RiskLevel.WRITE_PROJECT.is_dangerous is False
    assert RiskLevel.TX.is_dangerous is True


def test_every_operation_class_has_exactly_one_risk_level() -> None:
    classified = {operation: classify_operation(operation) for operation in OperationClass}
    assert set(classified) == set(OperationClass)


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
        classify_operation("bus.teleport")
    assert raised.value.code == "safety.unknown_operation"


def test_classification_refuses_a_non_string_argument() -> None:
    with pytest.raises(SafetyUnknownOperationError):
        classify_operation(cast(OperationClass, None))
    with pytest.raises(SafetyUnknownOperationError):
        classify_operation(cast(OperationClass, 3))


def test_required_capability_is_one_to_one_with_risk_level() -> None:
    capabilities = [required_capability(level) for level in RiskLevel]
    assert len(set(capabilities)) == len(RiskLevel)
    assert set(capabilities) == set(Capability)


def test_a_risk_level_without_a_capability_is_refused() -> None:
    with pytest.raises(SafetyUnknownOperationError):
        required_capability(cast(RiskLevel, 99))
