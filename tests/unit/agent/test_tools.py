import asyncio

import pytest
from canx.agent.tools import (
    PermissionDeniedError,
    ToolDefinition,
    ToolExecutor,
    ToolRegistry,
    ToolRisk,
    UnknownToolError,
)
from canx.safety.risk import Capability
from pydantic import BaseModel, ValidationError


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


READ_GRANT = frozenset({Capability.READ})
COMPUTE_GRANT = frozenset({Capability.COMPUTE})


async def test_registry_validates_input_and_permission() -> None:
    registry = ToolRegistry()

    async def handler(value: Input) -> Output:
        return Output(doubled=value.value * 2)

    registry.register(
        ToolDefinition(
            name="test.double",
            description="Double a number.",
            input_model=Input,
            output_model=Output,
            risk_level=ToolRisk.READ,
            required_capabilities=frozenset({Capability.READ}),
            timeout_seconds=0.1,
            idempotency="idempotent",
        ),
        handler,
    )
    executor = ToolExecutor(registry)
    assert await executor.execute("test.double", {"value": 3}, READ_GRANT) == {"doubled": 6}
    with pytest.raises(PermissionDeniedError):
        await executor.execute("test.double", {"value": 3}, frozenset())
    with pytest.raises(UnknownToolError):
        await executor.execute("unknown", {}, READ_GRANT)
    with pytest.raises(ValidationError):
        await executor.execute("test.double", {"value": "not-an-integer"}, READ_GRANT)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(registry.definition("test.double"), handler)


async def test_executor_enforces_timeout() -> None:
    registry = ToolRegistry()

    async def handler(value: Input) -> Output:
        await asyncio.sleep(0.05)
        return Output(doubled=value.value)

    registry.register(
        ToolDefinition(
            "test.slow",
            "Slow.",
            Input,
            Output,
            ToolRisk.COMPUTE,
            frozenset({Capability.COMPUTE}),
            0.001,
            "idempotent",
        ),
        handler,
    )
    with pytest.raises(TimeoutError):
        await ToolExecutor(registry).execute("test.slow", {"value": 1}, COMPUTE_GRANT)


async def test_a_low_risk_tool_that_transmits_is_refused_outright() -> None:
    """The P0-1 bypass, closed at the executor.

    A diagnostic read has ``READ`` effect risk and still puts frames on the bus.
    If the executor decided from the effect risk alone, this tool would run
    automatically — past the arm state, the ``CAN_TX`` grant and the audit trail
    that every other real transmission has to cross.
    """
    registry = ToolRegistry()

    async def handler(value: Input) -> Output:
        return Output(doubled=value.value)

    registry.register(
        ToolDefinition(
            name="test.diagnostic.read",
            description="Read a diagnostic value. Transmits; only observes.",
            input_model=Input,
            output_model=Output,
            risk_level=ToolRisk.READ,
            required_capabilities=frozenset({Capability.READ, Capability.CAN_TX}),
            timeout_seconds=0.1,
            idempotency="idempotent",
        ),
        handler,
    )
    with pytest.raises(PermissionDeniedError, match="vehicle transmission authority"):
        await ToolExecutor(registry).execute(
            "test.diagnostic.read",
            {"value": 1},
            frozenset({Capability.READ, Capability.CAN_TX}),
        )


async def test_a_tool_that_needs_more_than_one_capability_needs_all_of_them() -> None:
    registry = ToolRegistry()

    async def handler(value: Input) -> Output:
        return Output(doubled=value.value)

    registry.register(
        ToolDefinition(
            name="test.pair",
            description="Needs two capabilities.",
            input_model=Input,
            output_model=Output,
            risk_level=ToolRisk.READ,
            required_capabilities=frozenset({Capability.READ, Capability.COMPUTE}),
            timeout_seconds=0.1,
            idempotency="idempotent",
        ),
        handler,
    )
    executor = ToolExecutor(registry)
    with pytest.raises(PermissionDeniedError):
        await executor.execute("test.pair", {"value": 1}, READ_GRANT)
    assert await executor.execute(
        "test.pair", {"value": 2}, frozenset({Capability.READ, Capability.COMPUTE})
    ) == {"doubled": 2}


def test_the_definition_reports_its_vehicle_transmission_requirement() -> None:
    transmit = ToolDefinition(
        "test.tx",
        "x",
        Input,
        Output,
        ToolRisk.TX,
        frozenset({Capability.CAN_TX}),
        0.1,
        "idempotent",
    )
    reads = ToolDefinition(
        "test.read",
        "x",
        Input,
        Output,
        ToolRisk.READ,
        frozenset({Capability.READ}),
        0.1,
        "idempotent",
    )
    assert transmit.requires_vehicle_transmission is True
    assert reads.requires_vehicle_transmission is False
