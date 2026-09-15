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
from pydantic import BaseModel, ValidationError


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


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
            permissions=frozenset({"READ"}),
            timeout_seconds=0.1,
            idempotency="idempotent",
        ),
        handler,
    )
    executor = ToolExecutor(registry)
    assert await executor.execute("test.double", {"value": 3}, {"READ"}) == {"doubled": 6}
    with pytest.raises(PermissionDeniedError):
        await executor.execute("test.double", {"value": 3}, set())
    with pytest.raises(UnknownToolError):
        await executor.execute("unknown", {}, {"READ"})
    with pytest.raises(ValidationError):
        await executor.execute("test.double", {"value": "not-an-integer"}, {"READ"})
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
            frozenset({"COMPUTE"}),
            0.001,
            "idempotent",
        ),
        handler,
    )
    with pytest.raises(TimeoutError):
        await ToolExecutor(registry).execute("test.slow", {"value": 1}, {"COMPUTE"})
