"""Audit-safe identifiers — the only vocabulary a safety reference may use.

The defect this module exists to close (SAFETY-01-FIX-2, P1-1) was a contract the
audit event *described* and the code did not *hold*:

```text
SafetyAuditEvent
  caller_name       bounded label — enforced since FIX-1
  operation_id      non-empty string, and nothing else
  approval_id       non-empty string, and nothing else
  device_id         non-empty string, and nothing else
  channel           non-empty string, and nothing else
```

Four of the five reference fields were "whatever the caller passed, as long as it
was not empty". That is not a boundary: ``operation_id="rotate the key
hunter2-please"`` was a perfectly acceptable audit reference, and the trail stored
it. The event's no-secret property therefore rested on a caller's restraint.

**What this module freezes** (invariant S21):

> Safety Audit reference fields are identifiers, not arbitrary caller text. The
> vocabulary is enforced at construction of every domain type that can reach the
> trail, and again by the audit event itself.

**Why the rule is an alphabet and not a detector.** The obvious shortcut is to
reject values that "look like secrets" — containing ``SECRET``, or ``+``/``/``/``=``,
or matching a base64 shape. That is not a security boundary, because a secret can
be *any* string: ``abc123`` may be a password, and ``runtime.host`` may be a token
that happens to look like a hostname. A rule that admits a value because it does
not resemble the secret it is, is a rule that fails on the one input that matters.
So the rule is the shape: bounded ASCII identifiers, or nothing.

**What an identifier is.**

```text
alphabet   A-Z a-z 0-9 . _ : -
length     1 .. 64
```

The alphabet is wide enough for every identifier this runtime and a future device
manager will construct — ``ui.main``, ``agent.session-7``, ``op-...``,
``approval-...``, ``pcan-usb-1``, ``can1``, ``virtual-0``, ``0x7e0`` — and narrow
enough that free-form text (a sentence, a URL, a filesystem path, a JSON blob, a
shell command, a PEM block, a multi-line prompt) cannot be one. When a real
vendor identifier legitimately contains another character, the fix is to extend
*this* contract deliberately, in the open, not to widen a validator at a call site.

**Semantic distinction.** The typed identifiers below are the sanctioned way to
name a reference, so a field's role is visible at the point of construction. They
subclass :class:`str` on purpose: the audit event stores a plain string for
serialisation, and a value object that had to be unwrapped at every boundary would
be ceremony rather than safety. Being a ``str`` also means construction (and
therefore validation) is the only gate — there is no path that produces a
``CallerId`` without the contract having been applied.

**Serialization rule.** An identifier serialises as itself: a bare, bounded ASCII
string. There is no encoding, no escaping and no normalisation — a normalisation
step would be a second place for two spellings of one reference to diverge, and a
digest that must be confirmed after the fact cannot afford that.

**Runtime-generated references.** ``operation_id``, ``approval_id`` and
``event_id`` are minted by the runtime (see the ``new_*`` helpers) rather than
dictated by a caller. A caller-supplied reference is accepted only when it already
satisfies the identifier contract, and it is validated on the way in — never
stored first and inspected later.
"""

from __future__ import annotations

import re
import uuid
from typing import ClassVar, Final

from canx.safety.errors import SafetyIdentifierError

#: The single length budget for every audit reference. Defined once so a caller
#: cannot pick 64 for a caller id and 255 for a channel and call both "bounded".
MAX_AUDIT_IDENTIFIER_LENGTH: Final[int] = 64

#: The single alphabet for every audit reference — see the module docstring.
AUDIT_IDENTIFIER_ALPHABET: Final[str] = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "._:-"
)

_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"[{re.escape(AUDIT_IDENTIFIER_ALPHABET)}]{{1,{MAX_AUDIT_IDENTIFIER_LENGTH}}}"
)

#: A digest is the one reference that is not an identifier: it is fixed-width
#: lowercase hex, produced by ``hashlib``. Validated rather than trusted so a
#: caller cannot put a free-text field's contents into the digest slot and have
#: the event shape call it "one-way".
SHA256_HEX_LENGTH: Final[int] = 64

_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


def validate_audit_identifier(value: object, *, role: str) -> str:
    """Return ``value`` when it is a well-formed audit identifier.

    Args:
        value: The candidate reference.
        role: What kind of reference this is, for the fault's diagnosis —
            ``"caller identifier"``, ``"operation identifier"``, and so on. The
            role is the only thing that distinguishes two references that share a
            grammar, which is why the grammar lives here and the roles live at the
            fields.

    Returns:
        The unchanged identifier.

    Raises:
        SafetyIdentifierError: The value is not a string, is empty, is longer
            than :data:`MAX_AUDIT_IDENTIFIER_LENGTH`, or uses a character outside
            :data:`AUDIT_IDENTIFIER_ALPHABET`.

    The rejected text is deliberately **not** echoed into the fault's details —
    repeating a payload in an error message is the same leak through a different
    channel, and an error string is a log line waiting to happen. Only the role,
    the length and the limit are reported. The original value remains reachable
    as the exception's own argument for an in-process debugger.
    """
    if not isinstance(value, str) or not value:
        raise SafetyIdentifierError(
            f"A {role} must be a non-empty string.",
            details={
                "role": role,
                "received": "empty" if isinstance(value, str) else type(value).__name__,
            },
        )
    if _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise SafetyIdentifierError(
            f"A {role} must be a bounded identifier, not free-form text.",
            details={
                "role": role,
                "length": len(value),
                "limit": MAX_AUDIT_IDENTIFIER_LENGTH,
            },
        )
    return value


def validate_sha256_digest(value: object, *, role: str) -> str:
    """Return ``value`` when it is a lowercase SHA-256 hex digest.

    Raises:
        SafetyIdentifierError: The value is not 64 lowercase hexadecimal
            characters.

    ``hashlib.sha256(...).hexdigest()`` is the only producer in this runtime and it
    emits lowercase, so lowercase is the contract rather than a case-insensitive
    "looks hexadecimal" check. Case-insensitive acceptance would let two spellings
    of one digest sit in the trail, which is the same class of defect as two
    spellings of an identifier.
    """
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise SafetyIdentifierError(
            f"A {role} must be a lowercase SHA-256 hex digest.",
            details={
                "role": role,
                "length": len(value) if isinstance(value, str) else None,
                "limit": SHA256_HEX_LENGTH,
            },
        )
    return value


class AuditIdentifier(str):
    """A validated audit-reference identifier.

    A :class:`str` subclass whose construction *is* the validation, so there is
    no way to hold one that does not satisfy the contract. Each concrete subclass
    names its role, which is what makes ``CallerId`` and ``ChannelId`` two
    different types even though they share a grammar — "this channel name is not
    a caller id" is then a type error a reader can see rather than a convention
    nobody re-checks.
    """

    __slots__ = ()

    #: The role reported by a refusal. Overridden by every concrete subclass.
    role: ClassVar[str] = "audit identifier"

    def __new__(cls, value: str) -> AuditIdentifier:
        validate_audit_identifier(value, role=cls.role)
        return super().__new__(cls, value)


class CallerId(AuditIdentifier):
    """The audit identity of the caller that made a request."""

    __slots__ = ()
    role: ClassVar[str] = "caller identifier"


class OperationId(AuditIdentifier):
    """The identifier an operation request is correlated by."""

    __slots__ = ()
    role: ClassVar[str] = "operation identifier"


class ApprovalId(AuditIdentifier):
    """The reference an approval is looked up and consumed by."""

    __slots__ = ()
    role: ClassVar[str] = "approval identifier"


class DeviceId(AuditIdentifier):
    """A device identity. Bounded grammar only — no vendor shape is frozen yet."""

    __slots__ = ()
    role: ClassVar[str] = "device identifier"


class ChannelId(AuditIdentifier):
    """A bus channel identity."""

    __slots__ = ()
    role: ClassVar[str] = "channel identifier"


class CancellerId(AuditIdentifier):
    """The registration identity of a subsystem the emergency stop must reach.

    Added by SAFETY-01-FIX-3 (invariant S24). A canceller's identity used to be
    ``type(canceller).__name__``, which is not a stable runtime identity: two
    instances of one class collide, a wrapper hides the subsystem underneath, and
    a dynamically created class can name itself free text. The id is supplied at
    registration and validated here, so a cancellation failure names a subsystem
    the runtime actually knows rather than a class that happened to be loaded.
    """

    __slots__ = ()
    role: ClassVar[str] = "canceller identifier"


class AuditEventId(AuditIdentifier):
    """The identifier of one recorded audit event. Runtime-generated."""

    __slots__ = ()
    role: ClassVar[str] = "audit event identifier"


#: The role names reported by :func:`validate_audit_identifier` for digest
#: fields. Not identifiers themselves, but part of the same reference vocabulary.
PARAMETERS_DIGEST_ROLE: Final[str] = "parameters digest"
REASON_DIGEST_ROLE: Final[str] = "reason digest"


def new_audit_event_id() -> AuditEventId:
    """Mint the identifier for a new audit event.

    Runtime-generated (invariant S21): the trail's own index is not something a
    caller should be able to choose. 32 hex characters, which is inside the
    identifier alphabet and well inside the length budget.
    """
    return AuditEventId(uuid.uuid4().hex)


def new_operation_id() -> OperationId:
    """Mint an operation identifier for a caller that has none of its own.

    Prefixed so a trail reader can tell at a glance that the runtime generated
    it. Callers that already hold a correlating reference from their own layer may
    use it instead — it is validated on the way in either way.
    """
    return OperationId(f"op-{uuid.uuid4().hex}")


def new_approval_id() -> ApprovalId:
    """Mint an approval reference, so a caller never hand-writes a UUID."""
    return ApprovalId(f"approval-{uuid.uuid4().hex}")
