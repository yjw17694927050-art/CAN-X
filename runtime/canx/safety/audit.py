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

**"No free-text slot" is a property of the contract, not of the field list**
(SAFETY-01-FIX-2, invariant S21). SAFETY-01-FIX-1 removed the three obvious
free-text slots and left five reference fields that only had to be non-empty —
``caller_name``, ``operation_id``, ``approval_id``, ``device_id`` and ``channel``.
A field that only has to be non-empty *is* a free-text slot. Each of them is now a
validated identifier (:mod:`canx.safety.identifiers`), and the event re-checks
every one of them at construction so the boundary that persists does not depend on
a constructor above it having been careful.

The claim this supports is deliberately not "no secret can ever enter a log".
That is not provable and not what the contract says. What IS frozen is narrower
and checkable (see ``docs/architecture/SAFETY_ARCHITECTURE.md`` §12):

> Safety Audit accepts only typed identifiers, enumerated vocabulary values,
> bounded structured coordinates and cryptographic digests. Arbitrary
> caller-controlled text and raw operation parameters have no field in the Safety
> Audit contract.

The sink is a protocol with an in-memory implementation. SAFETY-01 deliberately
does **not** introduce a database table or a migration: `docs/PROJECT_STATE.md`
§3 keeps SQLite for project metadata, and a safety trail has no natural home
there yet. When persistence does arrive it attaches to this protocol without any
decision above it changing — and the hardening it will need (durability across
restart, tamper evidence) is named here so it is not rediscovered as a surprise.
"""

from __future__ import annotations

import hashlib
import math
import threading
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final, Protocol

from canx.safety.errors import SafetyIdentifierError
from canx.safety.identifiers import (
    PARAMETERS_DIGEST_ROLE,
    REASON_DIGEST_ROLE,
    ApprovalId,
    AuditEventId,
    AuditIdentifier,
    CallerId,
    ChannelId,
    DeviceId,
    OperationId,
    validate_audit_identifier,
    validate_sha256_digest,
)

#: Bounded by default. An in-memory trail that grows without limit is a slow
#: memory leak on a runtime that is expected to run for hours; a bounded one is
#: honest about being a session-local record rather than a durable log.
DEFAULT_AUDIT_CAPACITY = 4096

#: The vocabulary fields of an event. The kernel fills every one of them from an
#: enumeration, and every enumeration value is a bounded identifier — so "no free
#: text may occupy a vocabulary field" is one rule applied to six fields rather
#: than six branches somebody has to keep in step (invariant S21).
_EVENT_VOCABULARY_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("caller_kind", "caller kind"),
    ("operation_class", "operation class"),
    ("decision", "decision"),
    ("reason_code", "reason code"),
    ("arm_state", "arm state"),
    ("outcome", "outcome"),
)

#: The always-present reference fields, and the identifier type each must be.
_EVENT_IDENTIFIER_FIELDS: Final[tuple[tuple[str, type[AuditIdentifier]], ...]] = (
    ("event_id", AuditEventId),
    ("caller_id", CallerId),
    ("operation_id", OperationId),
)

#: The reference fields that are absent when the event does not concern them.
_EVENT_OPTIONAL_IDENTIFIER_FIELDS: Final[tuple[tuple[str, type[AuditIdentifier]], ...]] = (
    ("approval_id", ApprovalId),
    ("device_id", DeviceId),
    ("channel", ChannelId),
)


def digest_reason(reason: str) -> str:
    """Return the digest a safety reason is recorded as.

    The reason itself never enters the trail (invariant S19). A reason field is
    where a token, a key or an unlock payload ends up once someone is describing
    *why* they stopped the runtime, and the trail is the one place that must
    never be able to hold one.

    The digest keeps what the reason was actually for: two records of the same
    reason can be matched against each other, and a reason can be confirmed after
    the fact by whoever already knows it — without the trail ever holding it.
    """
    return hashlib.sha256(reason.encode("utf-8")).hexdigest()


def digest_reason_best_effort(reason: str) -> str | None:
    """Return the reason's digest, or ``None`` when it cannot be computed.

    **Only** for the emergency-stop path, where the digest is attribution
    metadata attached to an authority *reduction* (invariant S25). A reason that
    cannot be encoded — a lone surrogate such as ``"\\ud800"`` is the realistic
    case, since ``str`` admits it and UTF-8 does not — must cost the attribution
    and never the stop. Better an unattributed stop than an attributed non-stop.

    Deliberately **not** a global replacement for :func:`digest_reason`. On an
    authority-*increasing* path a reason that cannot be digested is a caller bug,
    and knowing about it is worth more than proceeding; the asymmetry is the same
    one S17 makes between the two directions.

    ``None`` rather than a substituted encoding, and the distinction is the whole
    point:

    ```text
    errors="ignore"   two different reasons would hash to the same digest
    errors="replace"  the digest would be of text nobody ever supplied
    None              attribution unavailable — an honest, visible absence
    ```

    A fabricated digest would be worse than no digest: it would claim a reason
    was recorded when the one recorded is not the one given, and a trail that
    lies about attribution is a trail nobody can use to answer "did the operator
    say X or Y?". ``None`` shows up as ``reason_digest: null`` beside
    ``engaged: true`` — diagnosable, and not a lie.
    """
    try:
        return digest_reason(reason)
    except Exception:  # recorded as an absent digest, never as a veto
        return None


@dataclass(frozen=True, slots=True)
class SafetyAuditEvent:
    """One safety decision or control action, recorded.

    Every field is a declared scalar, an enum value, a **validated identifier**, a
    digest, or kernel-owned text. No field holds arbitrary caller-supplied text
    (invariants S19, S21):

```text
event_id        runtime-generated identifier
recorded_at     a finite clock reading
caller_kind     an enum member (itself a bounded identifier)
caller_id       a validated CallerId — an audit identity, not a display name
operation_id    a validated OperationId
operation_class an enum value (or the kernel's own `safety.control`)
device_id       a validated DeviceId, or None when the event names no device
channel         a validated ChannelId, or None
target_address  an integer, or None
decision        an enum-derived value
reason_code     an enum-derived value
message         kernel text: a fixed sentence per action, never caller input
detail          kernel-rendered coordinates (enums, numbers, coordinates)
approval_id     a validated ApprovalId, or None
parameters_digest  sha256 of a parameter bag — the parameters are not stored
reason_digest   sha256 of an operator reason — the reason is not stored
arm_state       an enum-derived value
outcome         an enum-derived value
```

    ``reason_digest`` is what replaced the raw reason. It preserves attribution
    without preserving content: the same reason always produces the same digest.

    ``__post_init__`` re-checks every reference field even though the domain types
    above already validate them. That is deliberate defence in depth rather than
    duplication (invariant S21): the audit trail is the boundary that persists, so
    an event built by some future path that skipped the domain constructors must
    still be unstorable. The normalisation is a side effect — a stored event holds
    typed identifiers, not bare strings.
    """

    event_id: str
    recorded_at: float

    caller_kind: str
    caller_id: str

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
    reason_digest: str | None

    arm_state: str
    arm_scope_expired: bool | None
    emergency_stop_engaged: bool

    outcome: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.recorded_at):
            raise SafetyIdentifierError(
                "A safety audit event must carry a finite recorded_at timestamp.",
                details={"field": "recorded_at"},
            )
        for field, role in _EVENT_VOCABULARY_FIELDS:
            validate_audit_identifier(getattr(self, field), role=role)
        for field, identifier in _EVENT_IDENTIFIER_FIELDS:
            object.__setattr__(self, field, identifier(getattr(self, field)))
        for field, identifier in _EVENT_OPTIONAL_IDENTIFIER_FIELDS:
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, identifier(value))
        if self.parameters_digest is not None:
            validate_sha256_digest(self.parameters_digest, role=PARAMETERS_DIGEST_ROLE)
        if self.reason_digest is not None:
            validate_sha256_digest(self.reason_digest, role=REASON_DIGEST_ROLE)

    def describe(self) -> dict[str, object]:
        """Return this event as a plain mapping for a sink or a report."""
        return {
            "event_id": self.event_id,
            "recorded_at": self.recorded_at,
            "caller_kind": self.caller_kind,
            "caller_id": self.caller_id,
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
            "reason_digest": self.reason_digest,
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
