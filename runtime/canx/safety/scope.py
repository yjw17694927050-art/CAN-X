"""Scope — what an authority is limited to, and how two scopes are compared.

An ARM state is not a global boolean (invariant S7). Arming CAN-X on one channel
of one adapter does not open every channel of every adapter, and an approval for
one target address does not travel to another. That property is not enforced by
convention; it is enforced by every authority in this domain carrying a
:class:`OperationTarget` and by the kernel comparing them before it allows
anything.

The comparison is intentionally asymmetric and fail-closed. ``covers`` asks
"does this grant authorise that request?", and a coordinate the grant does not
name is a coordinate it can cover only when the request names it too:

```text
grant:   channel="can1"            request: channel="can1"  → covered
grant:   channel="can1"            request: channel=None    → not covered
grant:   channel=None (unstated)   request: channel="can1"  → covered
grant:   channel="can1"            request: channel="can2"  → not covered
```

The second row is the one that matters. A request that does not say which
channel it means cannot be proven to be the channel the grant names, and "cannot
be proven" is a refusal — not an invitation to read the blank as a wildcard.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from canx.safety.errors import SafetyIdentifierError, SafetyScopeError
from canx.safety.identifiers import (
    MAX_AUDIT_IDENTIFIER_LENGTH,
    ChannelId,
    DeviceId,
)
from canx.safety.risk import Capability


def device_identifier(value: str) -> DeviceId:
    """Return ``value`` as a device identifier, or refuse it as a scope fault.

    ``device_id`` and ``channel`` reach the audit trail, so they are identifiers
    like every other reference (invariant S21). The refusal is reported as a
    :class:`SafetyScopeError` rather than the identifier fault because a bad
    coordinate is a scope-shape problem, and the existing typed contract for that
    is this one; the identifier fault is preserved as ``__cause__``.

    No vendor grammar is frozen here — CAN-X has no device manager yet, and
    inventing one would be fiction. What *is* frozen is that this is an
    identifier and not a place to describe something.
    """
    try:
        return DeviceId(value)
    except SafetyIdentifierError as error:
        raise SafetyScopeError(
            "A device identity, when stated, must be a bounded identifier, not free-form text.",
            details={
                "field": "device_id",
                "role": DeviceId.role,
                "limit": MAX_AUDIT_IDENTIFIER_LENGTH,
            },
        ) from error


def channel_identifier(value: str) -> ChannelId:
    """Return ``value`` as a channel identifier, or refuse it as a scope fault."""
    try:
        return ChannelId(value)
    except SafetyIdentifierError as error:
        raise SafetyScopeError(
            "A channel, when stated, must be a bounded identifier, not free-form text.",
            details={
                "field": "channel",
                "role": ChannelId.role,
                "limit": MAX_AUDIT_IDENTIFIER_LENGTH,
            },
        ) from error


def has_lapsed(now: float, expires_at: float) -> bool:
    """Whether ``expires_at`` has passed at ``now`` — safe against a broken clock.

    Written as ``not (now < expires_at)`` rather than ``now >= expires_at``
    because the two differ on exactly one input, and it is the input that
    matters. IEEE-754 makes every comparison with ``NaN`` false, so
    ``NaN >= expires_at`` answers "not expired" — a corrupted or unset clock
    reading would *extend* an authority, which is the one direction expiry must
    never fail in. The negated form answers "expired" instead.

    Every expiry check in this package routes through here so that the property
    is a single fact rather than a convention repeated in four places.
    """
    return not now < expires_at


@dataclass(frozen=True, slots=True)
class OperationTarget:
    """The coordinates an operation acts on.

    Every field is optional because CAN-X does not yet have a device manager
    that could name a real adapter, and inventing identifiers here would be
    fiction. The shape is what this stage must freeze: a device identity, a
    channel and a target address are three independent coordinates, and a future
    integration fills them in without changing any comparison in this module.

    ``device_id`` and ``channel`` are **identifiers**, not descriptions
    (invariant S21): ``pcan-usb-1``, ``can1`` and ``virtual-0`` are the shape, and
    "my device password is …" is not. ``target_address`` is already a bounded
    integer rather than text, so it needs no such rule.

    An all-``None`` target is *unstated*, not universal. It is what a request
    means when it does not say where it is going, and it is covered only by an
    equally unstated grant.
    """

    device_id: str | None = None
    channel: str | None = None
    target_address: int | None = None

    def __post_init__(self) -> None:
        # ``object.__setattr__`` because the dataclass is frozen: a stated
        # coordinate is normalised to its typed identifier, so an ``OperationTarget``
        # that exists cannot hold an unvalidated one.
        if self.device_id is not None:
            object.__setattr__(self, "device_id", device_identifier(self.device_id))
        if self.channel is not None:
            object.__setattr__(self, "channel", channel_identifier(self.channel))
        if self.target_address is not None and not 0 <= self.target_address <= 0x1FFFFFFF:
            raise SafetyScopeError(
                "A target address must be a CAN identifier.",
                details={"field": "target_address"},
            )

    @property
    def is_unstated(self) -> bool:
        """Whether the target names no coordinate at all."""
        return (
            self.device_id is None
            and self.channel is None
            and self.target_address is None
        )

    def covers(self, other: OperationTarget) -> bool:
        """Whether this target authorises every coordinate ``other`` names.

        See the module docstring for the truth table. The rule in one sentence:
        a coordinate this target leaves unstated imposes no constraint, and a
        coordinate it states must match exactly.
        """
        if self.device_id is not None and self.device_id != other.device_id:
            return False
        if self.channel is not None and self.channel != other.channel:
            return False
        return self.target_address is None or self.target_address == other.target_address

    def describe(self) -> dict[str, object]:
        """Return the auditable coordinates of this target.

        Explicitly a projection, not ``asdict``: an audit event records the
        coordinates that were compared and nothing else, so adding a field to
        this dataclass can never silently widen what the trail collects.
        """
        return {
            "device_id": self.device_id,
            "channel": self.channel,
            "target_address": self.target_address,
        }


@dataclass(frozen=True, slots=True)
class ArmScope:
    """The bounded authority an ARMED runtime holds.

    Arming is a claim with an expiry, not a switch. A scope names which
    capabilities it opens, where those capabilities may be used, and when the
    claim lapses; a runtime that has been armed past its expiry is treated as
    disarmed by every check that reads it (invariant S7).

    A scope with no capabilities cannot be constructed at all: an arm that opens
    nothing is a state with no purpose, and allowing it would create an
    "armed-looking" runtime that refuses everything — a state that invites
    misreading by whoever looks at it next.
    """

    capabilities: frozenset[Capability]
    target: OperationTarget
    granted_at: float
    expires_at: float

    def __post_init__(self) -> None:
        if not self.capabilities:
            raise SafetyScopeError(
                "An arm scope must open at least one capability.",
                details={},
            )
        if not isinstance(self.capabilities, frozenset):
            raise SafetyScopeError(
                "An arm scope's capabilities must be an immutable set.",
                details={"type": type(self.capabilities).__name__},
            )
        if not (math.isfinite(self.granted_at) and math.isfinite(self.expires_at)):
            raise SafetyScopeError(
                "An arm scope must be bounded by finite timestamps.",
                details={"granted_at": self.granted_at, "expires_at": self.expires_at},
            )
        if self.expires_at <= self.granted_at:
            raise SafetyScopeError(
                "An arm scope must expire after the moment it was granted.",
                details={"granted_at": self.granted_at, "expires_at": self.expires_at},
            )

    def is_expired(self, now: float) -> bool:
        """Whether this scope has lapsed at ``now``.

        The boundary is closed: a scope expiring exactly at ``now`` has expired.
        Expiry is never extended by observation, and a non-finite ``now`` reads
        as expired rather than as "not yet" (see :func:`has_lapsed`).
        """
        return has_lapsed(now, self.expires_at)

    def covers(self, capability: Capability, target: OperationTarget, now: float) -> bool:
        """Whether this scope authorises ``capability`` at ``target`` right now.

        All three questions are asked together because all three can independently
        answer no: the scope may not open that capability, may not reach that
        target, or may have lapsed. A caller that had to check them separately
        would eventually check two of the three.
        """
        if self.is_expired(now):
            return False
        return capability in self.capabilities and self.target.covers(target)

    def describe(self) -> dict[str, object]:
        """Return the auditable shape of this scope."""
        return {
            "capabilities": sorted(capability.value for capability in self.capabilities),
            "target": self.target.describe(),
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
        }
