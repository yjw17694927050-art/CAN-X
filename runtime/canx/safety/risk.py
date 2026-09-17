"""Canonical risk taxonomy, capability vocabulary and deterministic classification.

This module is the **single** risk vocabulary for CAN-X. ``ToolRisk`` in
:mod:`canx.agent.tools` is an alias of :class:`RiskLevel` defined here, not a
parallel enum — the Agent tool registry and the safety kernel cannot drift into
two disagreeing notions of "how dangerous is this" (AGENTS.md §17, §21).

Two axes live here and they are deliberately separate:

``RiskLevel``
    **What an operation does.** Not which protocol it speaks. Reading a
    diagnostic identifier and reading a captured frame can share a level;
    clearing diagnostic information cannot share one with either, because what
    it does to the vehicle is different in kind. A taxonomy keyed on protocol
    would have to decide "is UDS dangerous?", and the honest answer is that the
    protocol is not the question (SAFETY-01 §7).

``Capability``
    **What authority is needed.** Capability-based and least-privilege
    (invariant S6). There is no ``can_tx: bool``: a grant to transmit frames is
    not a grant to clear fault memory, and neither is a grant to actuate.

The public surface is immutable and pure:

* :func:`classify_operation` is a total function over a closed table — the same
  operation class always yields the same risk level, and an unknown class raises
  rather than guessing;
* :func:`required_capability` maps a risk level to the one capability that
  authorises it.

Nothing here imports a device, a bus, an adapter or a UI shape. The safety
domain must stay unit-testable and free of vendor coupling.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Final

from canx.safety.errors import SafetyUnknownOperationError


class RiskLevel(IntEnum):
    """Runtime-enforced operation risk, ordered from least to most consequential.

    The ordering is the contract: policy rules such as "anything above
    ``WRITE_PROJECT`` needs the safety kernel" are expressed as comparisons, so
    a new level must be inserted where its consequence places it, never appended
    for convenience.

    ``TX``, ``DIAGNOSTIC_MUTATION``, ``ACTUATION``, ``ECU_MUTATION`` and
    ``CRITICAL`` are the five dangerous levels; ``READ``, ``COMPUTE`` and
    ``WRITE_PROJECT`` are the three the Agent Runtime may execute without an
    approval flow (AGENTS.md §17).
    """

    READ = 1
    COMPUTE = 2
    WRITE_PROJECT = 3
    TX = 4
    DIAGNOSTIC_MUTATION = 5
    ACTUATION = 6
    ECU_MUTATION = 7
    CRITICAL = 8

    @property
    def is_dangerous(self) -> bool:
        """Whether this level can change the vehicle rather than observe it.

        This single predicate is the boundary the whole kernel hangs from: it
        decides whether ARM state, an approval and an audit trail are required.
        """
        return self > RiskLevel.WRITE_PROJECT


class Capability(StrEnum):
    """A capability-based authority token (invariant S6).

    Deliberately fine-grained enough that "can transmit" and "can mutate an ECU"
    are different grants. A caller holding one must never be able to reach an
    operation protected by another.

    Every member here is *reachable*: :func:`required_capability` names each one
    for exactly one risk level, and a capability no risk level requires would be
    a grant an operator could hand out that changes nothing. That is worse than
    a missing name — it looks like a control. Reading diagnostic data is
    therefore authorised by :attr:`READ` rather than by a separate
    ``DIAGNOSTIC_READ`` token: the *authority* to read is the same authority,
    even though the risk of a transmit is not.
    """

    READ = "READ"
    COMPUTE = "COMPUTE"
    WRITE_PROJECT = "WRITE_PROJECT"
    CAN_TX = "CAN_TX"
    DIAGNOSTIC_MUTATION = "DIAGNOSTIC_MUTATION"
    ACTUATION = "ACTUATION"
    ECU_MUTATION = "ECU_MUTATION"
    CRITICAL_OPERATION = "CRITICAL_OPERATION"


class OperationClass(StrEnum):
    """What an operation does, in effect terms rather than protocol terms.

    The names describe consequences, not SIDs. ``diagnostic.mutation`` covers
    "this changes stored diagnostic state" without this module having to know
    which service implements it, so SAFETY-01 freezes the *risk* boundary while
    leaving the protocol layer free to arrive later (SAFETY-01 §7, §14).

    Note the deliberate split inside diagnostics: ``diagnostic.read`` is a read
    of the same functional area as ``diagnostic.mutation``, and they land on
    opposite sides of the dangerous boundary. Treating "UDS" as one risk would
    lose exactly that distinction.
    """

    ENGINEERING_READ = "engineering.read"
    ENGINEERING_COMPUTE = "engineering.compute"
    PROJECT_WRITE = "project.write"

    BUS_TRANSMIT = "bus.transmit"
    BUS_REPLAY = "bus.replay"
    BUS_INJECTION = "bus.injection"

    DIAGNOSTIC_READ = "diagnostic.read"
    DIAGNOSTIC_MUTATION = "diagnostic.mutation"

    ACTUATION = "actuation"
    ECU_MUTATION = "ecu.mutation"
    CRITICAL_OPERATION = "critical"


#: The closed classification table. Every operation class maps to exactly one
#: risk level, and the mapping is total over :class:`OperationClass` — the
#: taxonomy has no "unclassified" hole a caller could fall through.
_OPERATION_RISK: Final[MappingProxyType[OperationClass, RiskLevel]] = MappingProxyType(
    {
        OperationClass.ENGINEERING_READ: RiskLevel.READ,
        OperationClass.ENGINEERING_COMPUTE: RiskLevel.COMPUTE,
        OperationClass.PROJECT_WRITE: RiskLevel.WRITE_PROJECT,
        OperationClass.BUS_TRANSMIT: RiskLevel.TX,
        OperationClass.BUS_REPLAY: RiskLevel.TX,
        OperationClass.BUS_INJECTION: RiskLevel.TX,
        OperationClass.DIAGNOSTIC_READ: RiskLevel.READ,
        OperationClass.DIAGNOSTIC_MUTATION: RiskLevel.DIAGNOSTIC_MUTATION,
        OperationClass.ACTUATION: RiskLevel.ACTUATION,
        OperationClass.ECU_MUTATION: RiskLevel.ECU_MUTATION,
        OperationClass.CRITICAL_OPERATION: RiskLevel.CRITICAL,
    }
)

#: The one capability that authorises each risk level. One-to-one on purpose: a
#: risk level with two possible capabilities would let a caller shop for the
#: cheaper one, and a capability reachable from two levels would make the levels
#: indistinguishable at the permission boundary.
_REQUIRED_CAPABILITY: Final[MappingProxyType[RiskLevel, Capability]] = MappingProxyType(
    {
        RiskLevel.READ: Capability.READ,
        RiskLevel.COMPUTE: Capability.COMPUTE,
        RiskLevel.WRITE_PROJECT: Capability.WRITE_PROJECT,
        RiskLevel.TX: Capability.CAN_TX,
        RiskLevel.DIAGNOSTIC_MUTATION: Capability.DIAGNOSTIC_MUTATION,
        RiskLevel.ACTUATION: Capability.ACTUATION,
        RiskLevel.ECU_MUTATION: Capability.ECU_MUTATION,
        RiskLevel.CRITICAL: Capability.CRITICAL_OPERATION,
    }
)


def classify_operation(operation_class: OperationClass | str) -> RiskLevel:
    """Return the risk level of an operation class.

    Args:
        operation_class: A member of :class:`OperationClass`, or its string
            value. Anything else — an unknown label, a ``None``, a number, an
            object from another layer — is not classified.

    Returns:
        The risk level the closed table assigns to that operation class.

    Raises:
        SafetyUnknownOperationError: The operation class is not in the
            vocabulary. Callers inside the safety kernel translate this into a
            ``DENY`` with reason ``safety.unknown_operation``; it must never be
            read as "no particular risk".

    The function is pure and total over its input domain: the same argument
    always produces the same level, and no registry, configuration or clock can
    change the answer between two calls.
    """
    try:
        normalized = OperationClass(operation_class)
    except ValueError as error:
        raise SafetyUnknownOperationError(
            "The operation class is not part of the CAN-X risk taxonomy.",
            details={"operation_class": _described(operation_class)},
        ) from error
    except TypeError as error:  # pragma: no cover - defensive, enum() is typed
        raise SafetyUnknownOperationError(
            "The operation class is not part of the CAN-X risk taxonomy.",
            details={"operation_class": _described(operation_class)},
        ) from error
    try:
        return _OPERATION_RISK[normalized]
    except KeyError as error:  # pragma: no cover - the table is total by construction
        raise SafetyUnknownOperationError(
            "The operation class has no risk classification.",
            details={"operation_class": normalized.value},
        ) from error


def required_capability(risk: RiskLevel) -> Capability:
    """Return the capability that authorises a risk level.

    Unknown risk levels raise rather than falling back to a permissive default:
    an unrecognised level is one the kernel has no rule for, and the only honest
    answer to "may this run?" is that it may not.
    """
    try:
        return _REQUIRED_CAPABILITY[risk]
    except KeyError as error:
        raise SafetyUnknownOperationError(
            "The risk level has no required capability.",
            details={"risk_level": repr(risk)},
        ) from error


def _described(value: object) -> str:
    """Describe a rejected value without echoing arbitrary content into a report."""
    if isinstance(value, str):
        return value
    return type(value).__name__
