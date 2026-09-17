"""Capability-based permission — least privilege, empty by default.

There is no ``can_tx: bool`` in CAN-X. A grant names the capabilities it opens
and the target those capabilities reach; anything it does not name it does not
open (invariant S6). The default is an empty set, which is the only safe default
for a runtime whose session might be started by an Agent:

```text
PermissionSet()                → refuses every operation
PermissionSet([read_grant])    → refuses everything except reads on that target
```

Grants are the *standing* authority of a session. They answer "may this caller
ever do this here?", which is a different question from the two other
authorities the kernel consults: :class:`canx.safety.scope.ArmScope` answers
"is the runtime armed for this right now?", and
:class:`canx.safety.approval.Approval` answers "has this specific operation been
authorised?". All three are required for a dangerous operation, and none of them
can be inferred from another.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from canx.safety.errors import SafetyScopeError
from canx.safety.risk import Capability
from canx.safety.scope import OperationTarget, has_lapsed


@dataclass(frozen=True, slots=True)
class PermissionGrant:
    """One bounded capability grant held for a runtime session.

    ``target`` scopes where the capabilities reach. ``expires_at`` is optional
    because a session-scoped read grant reasonably lasts as long as the session;
    it is deliberately *not* optional for the dangerous capabilities, where a
    grant without an expiry would outlive the reason it was issued — policy
    enforces that distinction rather than this dataclass, so the reason is
    visible where it is decided.
    """

    capabilities: frozenset[Capability]
    target: OperationTarget = field(default_factory=OperationTarget)
    expires_at: float | None = None

    def __post_init__(self) -> None:
        if not self.capabilities:
            raise SafetyScopeError(
                "A permission grant must open at least one capability.",
                details={},
            )
        if not isinstance(self.capabilities, frozenset):
            raise SafetyScopeError(
                "A permission grant's capabilities must be an immutable set.",
                details={"type": type(self.capabilities).__name__},
            )

    def covers(self, capability: Capability, target: OperationTarget, now: float) -> bool:
        """Whether this grant authorises ``capability`` at ``target`` right now."""
        if self.expires_at is not None and has_lapsed(now, self.expires_at):
            return False
        return capability in self.capabilities and self.target.covers(target)


class PermissionSet:
    """The standing authority held by a runtime session.

    Immutable after construction. A caller cannot add to its own permission set
    through any method on this class — the absence of a mutator is the point
    (invariant S3): widening authority is a host action that constructs a new
    set, not something an operation request can perform on itself.

    An empty set is a valid and meaningful state: it is the state a session
    starts in, and it means "nothing is permitted yet".
    """

    __slots__ = ("_grants",)

    def __init__(self, grants: Iterable[PermissionGrant] = ()) -> None:
        self._grants: tuple[PermissionGrant, ...] = tuple(grants)

    def covers(self, capability: Capability, target: OperationTarget, now: float) -> bool:
        """Whether any held grant authorises ``capability`` at ``target``.

        Disjunction over grants, conjunction within one: a grant must open the
        capability *and* reach the target. No grant can cover a coordinate
        another grant names — permissions add up, they do not combine into a
        target none of them stated.
        """
        return any(grant.covers(capability, target, now) for grant in self._grants)

    def granted_capabilities(self) -> frozenset[Capability]:
        """Return every capability this set mentions, ignoring targets and expiry.

        A reporting aid for diagnostics and for checking "was this capability
        ever granted at all?" without pretending it answers the authorisation
        question — it does not, and callers that need that answer must call
        :meth:`covers`.
        """
        return frozenset(
            capability for grant in self._grants for capability in grant.capabilities
        )

    def describe(self) -> list[dict[str, object]]:
        """Return the audit-safe shape of every grant."""
        return [
            {
                "capabilities": sorted(capability.value for capability in grant.capabilities),
                "target": grant.target.describe(),
                "expires_at": grant.expires_at,
            }
            for grant in self._grants
        ]
