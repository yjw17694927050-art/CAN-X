"""Canonical risk taxonomy: effect risk, execution authority and classification.

This module is the **single** risk vocabulary for CAN-X. ``ToolRisk`` in
:mod:`canx.agent.tools` is an alias of :class:`RiskLevel` defined here, not a
parallel enum — the Agent tool registry and the safety kernel cannot drift into
two disagreeing notions of "how dangerous is this" (AGENTS.md §17, §21).

**Two axes, deliberately independent** (SAFETY-01-FIX-1, P0-1):

``RiskLevel`` — the effect risk
    **What an operation does to the vehicle.** Not which protocol it speaks, and
    not what it costs to execute. Reading stored data and reading live diagnostic
    data can share a level, because both only observe; clearing fault memory
    cannot share one with either, because what it does is different in kind.

``Capability`` — the execution authority
    **What must be granted before it can run at all.** A set, not a single
    value, because an operation can need more than one authority at once.

Collapsing those two into a chain of ``operation → risk → exactly one
capability`` is the defect this module was rebuilt to remove. Almost every
operation survives the collapse; ``diagnostic.read`` does not:

```text
diagnostic.read
  effect risk         READ      it only observes the vehicle
  execution authority CAN_TX    it still puts frames on a live bus
```

Under the one-axis model it landed on ``Capability.READ`` alone, so a future
would have been free to let a diagnostic read ride the low-risk automatic path —
straight past the ARM state, the CAN_TX grant and the audit trail that every
other real transmission has to cross.

The resolution is **not** to relabel it ``TX``: that would throw away the
read-only effect risk, which is real and which decides whether an approval is
needed. It is to keep both answers and require both.

Frozen by this model (invariant S15):

> Any operation that transmits to a live vehicle requires ``CAN_TX`` authority
> regardless of its semantic effect risk.

The public surface is immutable and pure. Nothing here imports a device, a bus,
an adapter or a UI shape: the safety domain must stay unit-testable and free of
vendor coupling.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Final

from canx.safety.errors import SafetyUnknownOperationError


class RiskLevel(IntEnum):
    """The **effect** risk of an operation, ordered from least to most consequential.

    The ordering is the contract: policy rules such as "anything above
    ``WRITE_PROJECT`` needs an approval" are expressed as comparisons, so a new
    level must be inserted where its consequence places it, never appended for
    convenience.

    ``TX``, ``DIAGNOSTIC_MUTATION``, ``ACTUATION``, ``ECU_MUTATION`` and
    ``CRITICAL`` are the five dangerous effects; ``READ``, ``COMPUTE`` and
    ``WRITE_PROJECT`` are the three that need no approval (AGENTS.md §17).

    This is the *effect*, not the authority. ``diagnostic.read`` has
    ``RiskLevel.READ`` and still requires ``CAN_TX`` — see
    :class:`OperationPolicy`.
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
        """Whether this effect can change the vehicle rather than observe it.

        This predicate decides whether an **approval** is required. It does *not*
        decide whether the ARM state is required: that answer comes from
        :attr:`OperationPolicy.requires_arm`, which also accounts for operations
        that observe the vehicle but must still transmit to do so.
        """
        return self > RiskLevel.WRITE_PROJECT


class Capability(StrEnum):
    """A capability-based authority token (invariant S6).

    Deliberately fine-grained enough that "can transmit" and "can mutate an ECU"
    are different grants. A caller holding one must never be able to reach an
    operation protected by another.

    ``CAN_TX`` is the **vehicle transmission authority**, and it is required by
    every operation that frames a live bus — including diagnostic reads. It is
    separate from :attr:`READ` because "may read" and "may put frames on the
    vehicle's bus" are different permissions even when the frames in question
    only ask a question.

    Every member here is reachable from the operation table, so no grant exists
    that an operator could hand out which changes nothing.
    """

    READ = "READ"
    COMPUTE = "COMPUTE"
    WRITE_PROJECT = "WRITE_PROJECT"
    CAN_TX = "CAN_TX"
    DIAGNOSTIC_MUTATION = "DIAGNOSTIC_MUTATION"
    ACTUATION = "ACTUATION"
    ECU_MUTATION = "ECU_MUTATION"
    CRITICAL_OPERATION = "CRITICAL_OPERATION"


#: The capabilities that can change the vehicle, or that put frames on its bus.
#:
#: ``CAN_TX`` is in this set even though a diagnostic read has a harmless effect
#: risk: transmitting is the act that has to be bounded, and a grant to do it
#: without an expiry would outlive the reason it was issued (invariant S18).
DANGEROUS_CAPABILITIES: Final[frozenset[Capability]] = frozenset(
    {
        Capability.CAN_TX,
        Capability.DIAGNOSTIC_MUTATION,
        Capability.ACTUATION,
        Capability.ECU_MUTATION,
        Capability.CRITICAL_OPERATION,
    }
)


class OperationClass(StrEnum):
    """What an operation does, in effect terms rather than protocol terms.

    The names describe consequences, not SIDs. ``diagnostic.mutation`` covers
    "this changes stored diagnostic state" without this module having to know
    which service implements it, so SAFETY-01 freezes the *risk* boundary while
    leaving the protocol layer free to arrive later (SAFETY-01 §7, §14).

    Note the deliberate split inside diagnostics: ``diagnostic.read`` is a read
    of the same functional area as ``diagnostic.mutation``, and they land on
    opposite sides of the *effect* boundary. They also differ in authority:
    reading needs ``CAN_TX`` as well as ``READ``, mutating needs ``CAN_TX`` as
    well as ``DIAGNOSTIC_MUTATION``. Two axes, two answers, both checked.
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


#: The capability that a risk level's *effect* implies. Used to build the
#: operation table and to decide which capability an approval must name.
#:
#: Distinct from the operation's full authority: the effect capability says
#: "changing the vehicle this way is what you are being trusted with", while the
#: operation's required capabilities say "and here is everything executing it
#: costs". ``diagnostic.read``'s effect capability is ``READ``; its authority
#: also includes ``CAN_TX``.
_EFFECT_CAPABILITY: Final[MappingProxyType[RiskLevel, Capability]] = MappingProxyType(
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


@dataclass(frozen=True, slots=True)
class OperationPolicy:
    """What an operation is: its effect, and everything executing it requires.

    Both answers are carried because both are checked, at different points in
    the decision chain:

```text
effect_risk            decides whether an approval is required
required_capabilities  decides whether the session may do it at all
requires_arm           either answer can demand the ARM state
```

    ``requires_arm`` is deliberately an **or**. A diagnostic read does not
    change the vehicle, so the effect-risk rule alone would not gate it — but it
    does put frames on a live bus, and an unarmed runtime has no business
    transmitting anything (invariant S7, S15).
    """

    operation_class: OperationClass
    effect_risk: RiskLevel
    required_capabilities: frozenset[Capability]

    def __post_init__(self) -> None:
        if not self.required_capabilities:
            raise SafetyUnknownOperationError(
                "An operation policy must name at least one required capability.",
                details={"operation_class": str(self.operation_class)},
            )
        if not isinstance(self.required_capabilities, frozenset):
            raise SafetyUnknownOperationError(
                "An operation policy's required capabilities must be an immutable set.",
                details={"type": type(self.required_capabilities).__name__},
            )
        # The effect must be reachable: an operation whose stated effect implies
        # a capability that is not in its own authority would be internally
        # contradictory — it would be describing a change it cannot be trusted
        # to make.
        effect_capability = _EFFECT_CAPABILITY[self.effect_risk]
        if effect_capability not in self.required_capabilities:
            raise SafetyUnknownOperationError(
                "An operation policy must require the capability its effect implies.",
                details={
                    "operation_class": str(self.operation_class),
                    "effect_capability": effect_capability.value,
                },
            )
        if Capability.CAN_TX not in self.required_capabilities and self.effect_risk in {
            RiskLevel.TX,
            RiskLevel.DIAGNOSTIC_MUTATION,
            RiskLevel.ACTUATION,
            RiskLevel.ECU_MUTATION,
            RiskLevel.CRITICAL,
        }:
            # Every dangerous effect in CAN-X is delivered over the bus today, so
            # a dangerous operation that does not claim CAN_TX is a table error —
            # and one that would let a mutation run without transmission
            # authority. If a future transport can deliver a dangerous effect
            # without CAN, this check is where that has to be reasoned about.
            raise SafetyUnknownOperationError(
                "A dangerous effect must require CAN_TX authority.",
                details={"operation_class": str(self.operation_class)},
            )

    @property
    def transmits_to_vehicle(self) -> bool:
        """Whether executing this operation puts frames on a live bus."""
        return Capability.CAN_TX in self.required_capabilities

    @property
    def requires_arm(self) -> bool:
        """Whether the runtime must be armed before this operation may run.

        True for anything dangerous, and true for anything that transmits —
        which is why ``diagnostic.read`` needs the arm state even though it only
        observes (invariant S15).
        """
        return self.effect_risk.is_dangerous or self.transmits_to_vehicle

    @property
    def effect_capability(self) -> Capability:
        """The capability that names this operation's effect."""
        return _EFFECT_CAPABILITY[self.effect_risk]

    def describe(self) -> dict[str, object]:
        """Return the audit-safe shape of this policy."""
        return {
            "operation_class": self.operation_class.value,
            "effect_risk": int(self.effect_risk),
            "required_capabilities": sorted(
                capability.value for capability in self.required_capabilities
            ),
            "transmits_to_vehicle": self.transmits_to_vehicle,
            "requires_arm": self.requires_arm,
        }


def _policy(
    operation_class: OperationClass,
    effect_risk: RiskLevel,
    *capabilities: Capability,
) -> OperationPolicy:
    """Build one table row. A helper so the table below reads as data."""
    return OperationPolicy(
        operation_class=operation_class,
        effect_risk=effect_risk,
        required_capabilities=frozenset(capabilities),
    )


#: The closed operation table. Every operation class appears exactly once, and
#: the mapping is total over :class:`OperationClass` — the taxonomy has no
#: "unclassified" hole a caller could fall through.
#:
#: Read the ``CAN_TX`` column as the answer to "does this frame a live bus?".
#: It is present on every diagnostic and bus operation and absent from the three
#: that work on data CAN-X already holds.
_OPERATION_POLICIES: Final[MappingProxyType[OperationClass, OperationPolicy]] = MappingProxyType(
    {
        OperationClass.ENGINEERING_READ: _policy(
            OperationClass.ENGINEERING_READ, RiskLevel.READ, Capability.READ
        ),
        OperationClass.ENGINEERING_COMPUTE: _policy(
            OperationClass.ENGINEERING_COMPUTE, RiskLevel.COMPUTE, Capability.COMPUTE
        ),
        OperationClass.PROJECT_WRITE: _policy(
            OperationClass.PROJECT_WRITE, RiskLevel.WRITE_PROJECT, Capability.WRITE_PROJECT
        ),
        OperationClass.BUS_TRANSMIT: _policy(
            OperationClass.BUS_TRANSMIT, RiskLevel.TX, Capability.CAN_TX
        ),
        OperationClass.BUS_REPLAY: _policy(
            OperationClass.BUS_REPLAY, RiskLevel.TX, Capability.CAN_TX
        ),
        OperationClass.BUS_INJECTION: _policy(
            OperationClass.BUS_INJECTION, RiskLevel.TX, Capability.CAN_TX
        ),
        # The one row that makes the two axes visible: observing effect, and it
        # still has to ask permission to transmit.
        OperationClass.DIAGNOSTIC_READ: _policy(
            OperationClass.DIAGNOSTIC_READ,
            RiskLevel.READ,
            Capability.READ,
            Capability.CAN_TX,
        ),
        OperationClass.DIAGNOSTIC_MUTATION: _policy(
            OperationClass.DIAGNOSTIC_MUTATION,
            RiskLevel.DIAGNOSTIC_MUTATION,
            Capability.CAN_TX,
            Capability.DIAGNOSTIC_MUTATION,
        ),
        OperationClass.ACTUATION: _policy(
            OperationClass.ACTUATION,
            RiskLevel.ACTUATION,
            Capability.CAN_TX,
            Capability.ACTUATION,
        ),
        OperationClass.ECU_MUTATION: _policy(
            OperationClass.ECU_MUTATION,
            RiskLevel.ECU_MUTATION,
            Capability.CAN_TX,
            Capability.ECU_MUTATION,
        ),
        OperationClass.CRITICAL_OPERATION: _policy(
            OperationClass.CRITICAL_OPERATION,
            RiskLevel.CRITICAL,
            Capability.CAN_TX,
            Capability.CRITICAL_OPERATION,
        ),
    }
)


def operation_policy(operation_class: OperationClass | str) -> OperationPolicy:
    """Return the policy for an operation class.

    Args:
        operation_class: A member of :class:`OperationClass`, or its string
            value. Anything else is not classified.

    Returns:
        The :class:`OperationPolicy` the closed table assigns.

    Raises:
        SafetyUnknownOperationError: The operation class is not in the
            vocabulary. Callers inside the safety kernel translate this into a
            ``DENY`` with reason ``safety.unknown_operation``; it must never be
            read as "no particular risk".

    Pure and total over its input domain: the same argument always produces the
    same policy, and no registry, configuration or clock can change the answer.
    """
    try:
        normalized = OperationClass(operation_class)
    except (ValueError, TypeError) as error:
        raise SafetyUnknownOperationError(
            "The operation class is not part of the CAN-X risk taxonomy.",
            details={"operation_class": _described(operation_class)},
        ) from error
    try:
        return _OPERATION_POLICIES[normalized]
    except KeyError as error:  # pragma: no cover - the table is total by construction
        raise SafetyUnknownOperationError(
            "The operation class has no operation policy.",
            details={"operation_class": normalized.value},
        ) from error


def classify_operation(operation_class: OperationClass | str) -> RiskLevel:
    """Return the **effect** risk of an operation class.

    The effect risk alone. An operation's execution authority is
    :attr:`OperationPolicy.required_capabilities`, and it can be strictly larger
    than what this level implies — ``diagnostic.read`` is ``READ`` here and still
    requires ``CAN_TX`` to run.
    """
    return operation_policy(operation_class).effect_risk


def approval_capability_for(effect_risk: RiskLevel) -> Capability:
    """Return the capability an approval must name to authorise an effect.

    An approval is bought for *what will be done*, not for what executing it
    costs, so it names the effect capability — ``ECU_MUTATION`` for an ECU
    mutation, ``CAN_TX`` for a transmit. The execution authority is separately
    held as a standing grant, and both are checked before anything runs.

    Unknown risk levels raise rather than falling back to a permissive default:
    an unrecognised level is one the kernel has no rule for, and the only honest
    answer to "may this run?" is that it may not.
    """
    try:
        return _EFFECT_CAPABILITY[effect_risk]
    except KeyError as error:
        raise SafetyUnknownOperationError(
            "The risk level has no effect capability.",
            details={"risk_level": repr(effect_risk)},
        ) from error


def is_dangerous_capability(capability: Capability) -> bool:
    """Whether a capability is one that must be bounded by an expiry (invariant S18)."""
    return capability in DANGEROUS_CAPABILITIES


def _described(value: object) -> str:
    """Describe a rejected value without echoing arbitrary content into a report."""
    if isinstance(value, str):
        return value
    return type(value).__name__
