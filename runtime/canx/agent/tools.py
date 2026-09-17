"""Schema-validated, permission-enforced Agent tool registry.

Two things are enforced here and they answer different questions:

``risk_level``
    The tool's **effect risk** — what running it does. Anything above
    ``WRITE_PROJECT`` is refused outright: authorising that is the safety
    kernel's decision, and this executor has no arm state, no approval and no
    audit trail to make the decision with.
``required_capabilities``
    The **authority** running it costs, as a set. Checked separately and against
    the caller's standing grants.

The two are checked independently, and that independence is the point
(SAFETY-01-FIX-1, P0-1). A tool that only reads — effect risk ``READ`` — and
still transmits to the vehicle carries ``CAN_TX``, and ``CAN_TX`` is refused here
*whatever* the effect risk says:

```text
tool effect risk       ≠   vehicle execution authority
```

Without that rule, an effect-based check would be a bypass: a diagnostic read
would classify as low risk, ride the automatic execution path, and never reach
the arm state, the transmission grant or the audit trail that every other real
transmission has to cross (invariant S15).
"""

import asyncio
from collections.abc import Awaitable, Callable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from canx.safety.risk import Capability
from canx.safety.risk import RiskLevel as ToolRisk

# ``ToolRisk`` is not a second risk vocabulary — it is the canonical safety
# taxonomy under the name this module has always used. AGENTS.md §17 and the
# safety architecture both speak of ``READ``/``COMPUTE``/``WRITE_PROJECT`` and
# the dangerous levels above them, and there is exactly one enum that defines
# what those words mean. A local definition that happened to agree today would
# be a drift waiting to happen: the first edit to one side and the registry
# would be calling a tool safe while the policy called it dangerous.
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
    """Immutable metadata required for every exposed Agent tool.

    ``required_capabilities`` replaces what used to be a set of permission
    strings. It is typed and it is a set rather than a single value, because an
    operation can need more than one authority at once — and a tool that needs
    ``CAN_TX`` is not executable from here at all, so the field has to be able to
    say so.
    """

    name: str
    description: str
    input_model: type[DefinitionInputT]
    output_model: type[DefinitionOutputT]
    risk_level: ToolRisk
    required_capabilities: frozenset[Capability]
    timeout_seconds: float
    idempotency: str

    @property
    def input_schema(self) -> dict[str, object]:
        return self.input_model.model_json_schema()

    @property
    def output_schema(self) -> dict[str, object]:
        return self.output_model.model_json_schema()

    @property
    def requires_vehicle_transmission(self) -> bool:
        """Whether executing this tool would put frames on a live bus.

        A property rather than a stored flag: a stored flag could disagree with
        the capability set, and the capability set is what the executor acts on.
        """
        return Capability.CAN_TX in self.required_capabilities


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
            definition.required_capabilities,
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
    """Validate tool calls, vehicle authority, permissions, risk ceiling, timeout.

    The order of the three refusals is deliberate. Vehicle transmission authority
    is asked first because it is the narrowest and most consequential question:
    a tool that needs it is refused here regardless of what its effect risk says,
    and reporting it as a generic risk refusal would hide the reason it exists.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(
        self, name: str, payload: dict[str, object], granted: AbstractSet[Capability]
    ) -> dict[str, object]:
        tool = self._registry.registered(name)
        definition = tool.definition
        if definition.requires_vehicle_transmission:
            # Not "needs an approval this executor cannot collect" — needs an
            # authority this executor must never hold. A tool that transmits is
            # authorised by the safety kernel or it is not authorised at all
            # (invariants S12, S15).
            raise PermissionDeniedError(
                "tool requires vehicle transmission authority; it is authorised by the "
                "safety kernel, never by the tool executor"
            )
        if definition.risk_level > ToolRisk.WRITE_PROJECT:
            # Everything above WRITE_PROJECT is a dangerous effect (invariant S1).
            raise PermissionDeniedError(
                "tool risk exceeds the automatic execution ceiling; "
                "dangerous operations are authorised by the safety kernel"
            )
        if not definition.required_capabilities.issubset(granted):
            raise PermissionDeniedError("required tool capability is missing")
        async with asyncio.timeout(definition.timeout_seconds):
            return await tool.invoke(payload)
