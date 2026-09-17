"""Audit — both verdicts, and nothing that could be a secret.

Invariant S10: safety decisions are auditable. That means *decisions*, not
actions — a ``DENY`` is recorded with the same care as an ``ALLOW``, because the
refusals are the part of the trail that shows the kernel was working. A trail
that only recorded successes would answer "what did CAN-X do?" and never "what
did CAN-X refuse, and on what authority?", which is the question an incident
review actually asks.

The event shape is a flat set of pre-declared fields with no free-text slot and
no ``dict[str, Any]`` catch-all. That is a security decision rather than a
tidiness one (SAFETY-01 §20): operation parameters are the position a
security-access key, seed, token or unlock payload would occupy, and this stage
freezes the rule before any such operation exists. Parameters reach the trail
only as a digest (see :meth:`canx.safety.operation.OperationRequest.digest_parameters`).

The sink is a protocol with an in-memory implementation. SAFETY-01 deliberately
does **not** introduce a database table or a migration: `docs/PROJECT_STATE.md`
§3 keeps SQLite for project metadata, and a safety trail has no natural home
there yet. When persistence does arrive it attaches to this protocol without any
decision above it changing — and the hardening it will need (durability across
restart, tamper evidence) is named here so it is not rediscovered as a surprise.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

#: Bounded by default. An in-memory trail that grows without limit is a slow
#: memory leak on a runtime that is expected to run for hours; a bounded one is
#: honest about being a session-local record rather than a durable log.
DEFAULT_AUDIT_CAPACITY = 4096


@dataclass(frozen=True, slots=True)
class SafetyAuditEvent:
    """One safety decision, recorded.

    Every field is a declared scalar, an enum value or a digest. There is
    deliberately no field that could hold an arbitrary payload, so no caller can
    put a secret into the trail by way of a parameter bag.
    """

    event_id: str
    recorded_at: float

    caller_kind: str
    caller_name: str

    operation_id: str
    operation_class: str
    risk_level: int | None

    device_id: str | None
    channel: str | None
    target_address: int | None

    decision: str
    reason_code: str
    message: str
    detail: str | None

    approval_id: str | None
    parameters_digest: str | None

    arm_state: str
    arm_scope_expired: bool | None
    emergency_stop_engaged: bool

    outcome: str

    def describe(self) -> dict[str, object]:
        """Return this event as a plain mapping for a sink or a report."""
        return {
            "event_id": self.event_id,
            "recorded_at": self.recorded_at,
            "caller_kind": self.caller_kind,
            "caller_name": self.caller_name,
            "operation_id": self.operation_id,
            "operation_class": self.operation_class,
            "risk_level": self.risk_level,
            "device_id": self.device_id,
            "channel": self.channel,
            "target_address": self.target_address,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "message": self.message,
            "detail": self.detail,
            "approval_id": self.approval_id,
            "parameters_digest": self.parameters_digest,
            "arm_state": self.arm_state,
            "arm_scope_expired": self.arm_scope_expired,
            "emergency_stop_engaged": self.emergency_stop_engaged,
            "outcome": self.outcome,
        }


class SafetyAuditSink(Protocol):
    """Where safety decisions are recorded.

    ``record`` is synchronous and must not raise for an ordinary decision: a
    sink that cannot record is a broken sink, and the kernel treats its failure
    as a fault (see :class:`canx.safety.errors.SafetyAuditError`) rather than as
    a decision it can proceed past.
    """

    def record(self, event: SafetyAuditEvent) -> None:
        """Append one event to the trail."""
        ...


class InMemoryAuditSink:
    """A bounded, session-local audit trail. Thread-safe.

    Bounded because it is a session record, not a log: when it is full the
    oldest event is dropped, and the sink reports that it has done so rather
    than pretending the trail is complete.
    """

    __slots__ = ("_capacity", "_dropped", "_events", "_lock")

    def __init__(self, *, capacity: int = DEFAULT_AUDIT_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError("An audit trail capacity must be positive.")
        self._lock = threading.Lock()
        self._capacity = capacity
        self._events: deque[SafetyAuditEvent] = deque(maxlen=capacity)
        self._dropped = 0

    def record(self, event: SafetyAuditEvent) -> None:
        """Append one event, dropping the oldest when the trail is full."""
        with self._lock:
            if len(self._events) == self._capacity:
                self._dropped += 1
            self._events.append(event)

    def events(self) -> tuple[SafetyAuditEvent, ...]:
        """Return every retained event, oldest first."""
        with self._lock:
            return tuple(self._events)

    @property
    def dropped(self) -> int:
        """How many events have been evicted from the front of the trail."""
        with self._lock:
            return self._dropped

    def __iter__(self) -> Iterator[SafetyAuditEvent]:
        return iter(self.events())

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)
