"""Schema-validated, permission-enforced Agent tool registry."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

# ``ToolRisk`` is not a second risk vocabulary — it is the canonical safety
# taxonomy under the name this module has always used. AGENTS.md §17 and the
# safety architecture both speak of ``READ``/``COMPUTE``/``WRITE_PROJECT`` and
# the dangerous levels above them, and there is exactly one enum that defines
# what those words mean. A local definition that happened to agree today would
# be a drift waiting to happen: the first edit to one side and the registry
# would be calling a tool safe while the policy called it dangerous.
from canx.safety.risk import RiskLevel as ToolRisk

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)

__all__ = [
    "PermissionDeniedError",
    "ToolDefinition",
    "ToolError",
    "ToolExecutor",
    "ToolRegistry",
    "ToolRisk",
    "UnknownToolError",
]


class ToolError(RuntimeError):
    """Base class for diagnosable tool boundary failures."""


class UnknownToolError(ToolError):
    """The requested name is not allow-listed."""


class PermissionDeniedError(ToolError):
    """The caller lacks permissions or exceeds the automatic risk ceiling."""


@dataclass(frozen=True, slots=True)
class ToolDefinition[DefinitionInputT: BaseModel, DefinitionOutputT: BaseModel]:
    """Immutable metadata required for every exposed Agent tool."""

    name: str
    description: str
    input_model: type[DefinitionInputT]
    output_model: type[DefinitionOutputT]
    risk_level: ToolRisk
    permissions: frozenset[str]
    timeout_seconds: float
    idempotency: str

    @property
    def input_schema(self) -> dict[str, object]:
        return self.input_model.model_json_schema()

    @property
    def output_schema(self) -> dict[str, object]:
        return self.output_model.model_json_schema()


@dataclass(frozen=True, slots=True)
class _RegisteredTool:
    definition: ToolDefinition[BaseModel, BaseModel]
    invoke: Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class ToolRegistry:
    """Register explicit tools; arbitrary functions are never exposed."""

    def __init__(self) -> None:
        self._tools: dict[str, _RegisteredTool] = {}

    def register(
        self,
        definition: ToolDefinition[InputT, OutputT],
        handler: Callable[[InputT], Awaitable[OutputT]],
    ) -> None:
        if not definition.name or definition.name in self._tools:
            raise ValueError(f"tool already registered or invalid: {definition.name}")

        async def invoke(payload: dict[str, object]) -> dict[str, object]:
            value = definition.input_model.model_validate(payload)
            result = await handler(value)
            return definition.output_model.model_validate(result).model_dump(mode="json")

        erased = ToolDefinition[BaseModel, BaseModel](
            definition.name,
            definition.description,
            definition.input_model,
            definition.output_model,
            definition.risk_level,
            definition.permissions,
            definition.timeout_seconds,
            definition.idempotency,
        )
        self._tools[definition.name] = _RegisteredTool(erased, invoke)

    def definition(self, name: str) -> ToolDefinition[BaseModel, BaseModel]:
        try:
            return self._tools[name].definition
        except KeyError as error:
            raise UnknownToolError(name) from error

    def registered(self, name: str) -> _RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as error:
            raise UnknownToolError(name) from error


class ToolExecutor:
    """Validate tool calls, permissions, risk ceiling, and timeout."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(
        self, name: str, payload: dict[str, object], permissions: set[str]
    ) -> dict[str, object]:
        tool = self._registry.registered(name)
        definition = tool.definition
        if definition.risk_level > ToolRisk.WRITE_PROJECT:
            # Everything above WRITE_PROJECT is a dangerous operation
            # (invariant S1). It is refused here unconditionally: authorising it
            # is the safety kernel's decision, and this executor has no arm
            # state, no approval and no audit trail to make that decision with.
            raise PermissionDeniedError(
                "tool risk exceeds the automatic execution ceiling; "
                "dangerous operations are authorised by the safety kernel"
            )
        if not definition.permissions.issubset(permissions):
            raise PermissionDeniedError("required tool permission is missing")
        async with asyncio.timeout(definition.timeout_seconds):
            return await tool.invoke(payload)
