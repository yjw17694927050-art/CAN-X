"""The Agent tool registry and the safety kernel share one risk vocabulary.

SAFETY-01 §21. CAN-X already had an Agent-side risk enum
(:class:`canx.agent.tools.ToolRisk`) before this stage, with the six levels
AGENTS.md §17 names. This stage adds two more consequences to the taxonomy —
``DIAGNOSTIC_MUTATION`` and ``ACTUATION`` — and the temptation at that point is
to leave the Agent's enum alone and translate between the two.

That translation is the defect these tests exist to prevent. Two enums that mean
the same thing drift the first time one is edited, and the failure mode is a tool
whose ``risk_level`` reads "safe" to the executor and "dangerous" to the policy.
The Agent's name is now an alias of the canonical level, and these tests fail if
that stops being true.
"""

from __future__ import annotations

import pytest
from canx.agent.tools import (
    PermissionDeniedError,
    ToolDefinition,
    ToolExecutor,
    ToolRegistry,
    ToolRisk,
)
from canx.safety.risk import Capability, RiskLevel
from pydantic import BaseModel


class Input(BaseModel):
    value: int


class Output(BaseModel):
    value: int


def test_the_agent_risk_enum_is_the_canonical_risk_level() -> None:
    assert ToolRisk is RiskLevel


def test_every_agent_risk_member_matches_its_canonical_level() -> None:
    for name, level in (
        ("READ", RiskLevel.READ),
        ("COMPUTE", RiskLevel.COMPUTE),
        ("WRITE_PROJECT", RiskLevel.WRITE_PROJECT),
        ("TX", RiskLevel.TX),
        ("ECU_MUTATION", RiskLevel.ECU_MUTATION),
        ("CRITICAL", RiskLevel.CRITICAL),
    ):
        assert getattr(ToolRisk, name) is level


def test_the_dangerous_boundary_is_the_same_on_both_sides() -> None:
    assert ToolRisk.WRITE_PROJECT.is_dangerous is False
    assert ToolRisk.TX.is_dangerous is True


def test_the_new_levels_are_available_to_the_agent_registry() -> None:
    assert ToolRisk.DIAGNOSTIC_MUTATION.is_dangerous is True
    assert ToolRisk.ACTUATION.is_dangerous is True
    assert RiskLevel.DIAGNOSTIC_MUTATION < RiskLevel.ACTUATION < RiskLevel.ECU_MUTATION


async def test_the_tool_executor_still_refuses_above_write_project() -> None:
    registry = ToolRegistry()

    async def handler(value: Input) -> Output:
        return Output(value=value.value)

    registry.register(
        ToolDefinition(
            name="test.transmit",
            description="Not a real transmit.",
            input_model=Input,
            output_model=Output,
            risk_level=ToolRisk.TX,
            required_capabilities=frozenset({Capability.CAN_TX}),
            timeout_seconds=0.1,
            idempotency="idempotent",
        ),
        handler,
    )
    with pytest.raises(PermissionDeniedError):
        await ToolExecutor(registry).execute(
            "test.transmit", {"value": 1}, frozenset({Capability.CAN_TX})
        )


@pytest.mark.parametrize("level", list(RiskLevel))
def test_every_risk_level_keeps_its_ordering_relative_to_write_project(
    level: RiskLevel,
) -> None:
    """The comparison the executor uses must stay meaningful for every level."""
    assert (level.is_dangerous) == (level > RiskLevel.WRITE_PROJECT)
