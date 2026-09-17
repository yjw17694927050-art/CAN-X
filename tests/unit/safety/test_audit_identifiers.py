"""Safety Audit records identifiers, not prose.

The defect this file pins down (SAFETY-01-FIX-2, P1-1) is the last place
caller-controlled text could still reach the trail. SAFETY-01-FIX-1 removed the
*obvious* free-text slots — ``reason`` became ``reason_digest``, a control event's
``message`` became kernel text, and ``CallerIdentity``'s name got a bounded
alphabet. What it left behind was four reference fields that only had to be
non-empty:

```text
operation_id      non-empty string
approval_id       non-empty string
device_id         non-empty string
channel           non-empty string
```

``operation_id="rotate the key hunter2-please"`` was therefore a legitimate audit
reference, and the trail stored it verbatim. The event's no-secret property rested
on the caller's restraint rather than on the contract.

The rule (invariant S21):

> Safety Audit reference fields are identifiers, not arbitrary caller text.

**Why this file does not test for "secrets".** A test that asserts "this token
does not appear" only proves that *this* token does not appear. A secret can be
any string — ``abc123`` may be a password — so the assertions here are about the
**grammar**: a value with a space, a newline, a slash, a quote or a tab is refused
because it is not an identifier, never because it resembles a credential. The
invalid corpus below is free-form text of the kind a caller would actually write,
which is the shape the boundary has to reject.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re

import pytest
from canx.safety.approval import ApprovalSpec
from canx.safety.audit import SafetyAuditEvent
from canx.safety.caller import CallerIdentity, CallerKind
from canx.safety.errors import SafetyError
from canx.safety.identifiers import (
    AUDIT_IDENTIFIER_ALPHABET,
    MAX_AUDIT_IDENTIFIER_LENGTH,
    SHA256_HEX_LENGTH,
    ChannelId,
    DeviceId,
    new_approval_id,
    new_audit_event_id,
    new_operation_id,
)
from canx.safety.operation import OperationRequest
from canx.safety.risk import Capability, OperationClass
from canx.safety.scope import OperationTarget
from safety_builders import AGENT, OPERATOR, kernel, permissions, request

#: Deliberately written out rather than imported from the implementation: a test
#: that reuses the validator it is checking agrees with itself by construction.
#: This is the *frozen* contract as a reader of `SAFETY_ARCHITECTURE.md` §12 would
#: state it — 1..64 characters of ``A-Z a-z 0-9 . _ : -`` and nothing else.
_FROZEN_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z")

#: Free-form text a caller could plausibly supply. None of it is "secret-like" by
#: construction — several entries are entirely innocuous — because the boundary
#: must reject the *shape*, not the suspicion.
FREE_TEXT = (
    "operator entered emergency because the rig was smoking",
    "rotate\tthe\tkey",
    "line one\nline two",
    "\r",
    "  padded  ",
    '{"token": "abc"}',
    "rm -rf /",
    "https://example.invalid/oauth?access_token=abc",
    "C:\\Users\\operator\\secrets.env",
    "/etc/passwd",
    "-----BEGIN PRIVATE KEY-----",
    "[notice] disarm requested by the bench operator",
    "a" * (MAX_AUDIT_IDENTIFIER_LENGTH + 1),
    "",
)


def assert_frozen_identifier(value: object) -> None:
    """Assert ``value`` satisfies the frozen contract, independently of the code."""
    assert isinstance(value, str)
    assert _FROZEN_IDENTIFIER.fullmatch(value) is not None, repr(value)


# -- The grammar, as a property of its own ------------------------------------


def test_the_frozen_grammar_agrees_with_the_shipped_alphabet() -> None:
    """The two spellings of the contract must not drift apart silently."""
    assert set(AUDIT_IDENTIFIER_ALPHABET) == set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
    )
    assert MAX_AUDIT_IDENTIFIER_LENGTH == 64
    assert SHA256_HEX_LENGTH == 64


@pytest.mark.parametrize(
    "value",
    ["ui.main", "agent.session-7", "runtime.host", "pcan-usb-1", "can1", "virtual-0", "0x7e0"],
)
def test_identifiers_a_real_runtime_would_construct_are_accepted(value: str) -> None:
    """The grammar must not be so tight that ordinary identifiers stop working."""
    assert ChannelId(value) == value
    assert DeviceId(value) == value
    assert CallerIdentity(CallerKind.AGENT, value).caller_id == value


@pytest.mark.parametrize("text", FREE_TEXT)
def test_free_form_text_is_not_a_valid_identifier(text: str) -> None:
    """The core claim: prose cannot masquerade as a reference."""
    with pytest.raises(SafetyError):
        ChannelId(text)


# -- Runtime-generated references ---------------------------------------------


def test_runtime_generated_references_satisfy_the_contract() -> None:
    for generated in (new_operation_id(), new_approval_id(), new_audit_event_id()):
        assert_frozen_identifier(generated)


def test_runtime_generated_references_are_distinct() -> None:
    assert len({new_audit_event_id() for _ in range(16)}) == 16


# -- Each reference field, one at a time --------------------------------------


@pytest.mark.parametrize("text", FREE_TEXT)
def test_a_free_form_caller_identifier_is_refused(text: str) -> None:
    with pytest.raises(SafetyError):
        CallerIdentity(CallerKind.AGENT, text)


@pytest.mark.parametrize("text", FREE_TEXT)
def test_a_free_form_operation_identifier_is_refused(text: str) -> None:
    with pytest.raises(SafetyError):
        request(operation_id=text)


@pytest.mark.parametrize("text", FREE_TEXT)
def test_a_free_form_approval_identifier_is_refused(text: str) -> None:
    with pytest.raises(SafetyError):
        ApprovalSpec(
            approval_id=text,
            capability=Capability.CAN_TX,
            target=OperationTarget(),
            issued_at=0.0,
            expires_at=10.0,
        )
    with pytest.raises(SafetyError):
        request(approval_id=text)


@pytest.mark.parametrize("text", FREE_TEXT)
def test_a_free_form_device_identifier_is_refused(text: str) -> None:
    with pytest.raises(SafetyError):
        OperationTarget(device_id=text)


@pytest.mark.parametrize("text", FREE_TEXT)
def test_a_free_form_channel_identifier_is_refused(text: str) -> None:
    with pytest.raises(SafetyError):
        OperationTarget(channel=text)


def test_none_still_means_unstated_not_invalid() -> None:
    """A blank coordinate is a semantic value, and the identifier contract keeps it.

    ``None`` is how the scope truth table says "the grant / request does not say
    where this is going" — it is not text, so the identifier rule has nothing to
    say about it, and the fail-closed comparison in ``OperationTarget.covers`` is
    untouched: an unstated *grant* imposes no constraint, while a stated grant does
    not cover an unstated *request*.
    """
    unstated = OperationTarget(device_id=None, channel=None)
    assert unstated.is_unstated is True
    assert unstated.covers(OperationTarget(channel="can1")) is True
    assert OperationTarget(channel="can1").covers(unstated) is False


# -- Timestamps and digests ---------------------------------------------------


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_requested_at_is_refused(value: float) -> None:
    """A malformed request timestamp has no place in the ordering of the trail."""
    with pytest.raises(SafetyError):
        request(requested_at=value)


def test_a_finite_requested_at_is_accepted() -> None:
    assert request(requested_at=1_700_000_000.25).requested_at == 1_700_000_000.25


@pytest.mark.parametrize("text", (*FREE_TEXT, "A" * 64, "0" * 63, "0" * 65, "not-a-digest"))
def test_a_malformed_parameters_digest_is_refused(text: str) -> None:
    """The digest slot is the one reference that is not an identifier — so it is
    validated as what it claims to be rather than trusted for being non-empty."""
    with pytest.raises(SafetyError):
        request(parameters_digest=text)


def test_a_real_parameters_digest_is_accepted() -> None:
    digest = request().digest_parameters({"service": 0x27})
    assert len(digest) == SHA256_HEX_LENGTH
    assert request(parameters_digest=digest).parameters_digest == digest


# -- The audit event refuses a malformed reference however it was built --------


def event(**overrides: object) -> SafetyAuditEvent:
    """A minimal, well-formed decision event, with named fields overridable."""
    fields: dict[str, object] = {
        "event_id": new_audit_event_id(),
        "recorded_at": 1_000.0,
        "caller_kind": "agent",
        "caller_id": "agent.session-1",
        "operation_id": "op-bus-transmit-1",
        "operation_class": "bus.transmit",
        "risk_level": 4,
        "device_id": None,
        "channel": "can1",
        "target_address": 0x7E0,
        "decision": "deny",
        "reason_code": "safety.not_armed",
        "message": "The runtime is not armed for this operation.",
        "detail": None,
        "approval_id": None,
        "parameters_digest": None,
        "reason_digest": None,
        "arm_state": "disarmed",
        "arm_scope_expired": None,
        "emergency_stop_engaged": False,
        "outcome": "decision",
    }
    fields.update(overrides)
    return SafetyAuditEvent(**fields)  # type: ignore[arg-type]


def test_the_reference_event_is_valid() -> None:
    assert_frozen_identifier(event().event_id)


@pytest.mark.parametrize(
    "field",
    ["event_id", "caller_id", "operation_id", "approval_id", "device_id", "channel"],
)
@pytest.mark.parametrize("text", FREE_TEXT)
def test_the_event_refuses_free_form_text_in_every_reference_field(
    field: str, text: str
) -> None:
    """The audit boundary is the backstop, not only the domain types above it."""
    with pytest.raises(SafetyError):
        event(**{field: text})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_the_event_refuses_a_non_finite_timestamp(value: float) -> None:
    with pytest.raises(SafetyError):
        event(recorded_at=value)


@pytest.mark.parametrize("field", ["parameters_digest", "reason_digest"])
@pytest.mark.parametrize("text", ["", "0" * 63, "0" * 65, "A" * 64, "ZZ"])
def test_the_event_refuses_a_malformed_digest(field: str, text: str) -> None:
    with pytest.raises(SafetyError):
        event(**{field: text})


def test_the_event_accepts_a_real_digest() -> None:
    digest = hashlib.sha256(b"bench smoke").hexdigest()
    assert event(reason_digest=digest).reason_digest == digest


# -- The contract, as the callers see it --------------------------------------


def test_a_caller_identity_has_an_identifier_and_no_display_name() -> None:
    """``name`` implied display text; the field is an audit identity, and says so.

    A UI that wants a human-readable label keeps it in the UI. A label is display
    text, it is not authority, and the safety domain has no field for it — which
    is why the field was renamed rather than re-documented.
    """
    fields = {field.name for field in dataclasses.fields(CallerIdentity)}
    assert fields == {"kind", "caller_id"}
    assert CallerIdentity(CallerKind.SYSTEM, "runtime.host").caller_id == "runtime.host"


def test_a_caller_identity_cannot_be_built_from_a_display_label() -> None:
    with pytest.raises(SafetyError):
        CallerIdentity(CallerKind.HUMAN_UI, "YJW (bench operator)")


# -- End to end: what the trail actually holds --------------------------------


def test_the_trail_holds_identifiers_and_no_caller_prose() -> None:
    """The property the whole file is about, asserted on a real evaluation."""
    safety = kernel(permission_set=permissions(Capability.READ))
    safety.evaluate(request(OperationClass.ENGINEERING_READ, caller=OPERATOR))
    events = safety.audit_events()
    assert events

    for recorded in events:
        assert_frozen_identifier(recorded.event_id)
        assert_frozen_identifier(recorded.caller_id)
        assert_frozen_identifier(recorded.operation_id)
        for reference in (recorded.approval_id, recorded.device_id, recorded.channel):
            if reference is not None:
                assert_frozen_identifier(reference)
        # Every vocabulary field is an enum value, never free text.
        for field in (
            "caller_kind",
            "operation_class",
            "decision",
            "reason_code",
            "arm_state",
            "outcome",
        ):
            assert_frozen_identifier(getattr(recorded, field))


def test_prose_a_caller_meant_as_an_operation_reference_never_reaches_the_trail() -> None:
    """The leak path that existed until FIX-2, closed end to end.

    Before the fix this request was constructed happily, evaluated, and the prose
    was recorded verbatim as ``operation_id``. Now the request cannot be built at
    all, so there is nothing for the trail to hold.
    """
    prose = "operator entered emergency because the rig was smoking"
    with pytest.raises(SafetyError):
        request(operation_id=prose)

    safety = kernel(permission_set=permissions(Capability.READ))
    safety.evaluate(request(OperationClass.ENGINEERING_READ, caller=OPERATOR))
    trail = " | ".join(str(recorded.describe()) for recorded in safety.audit_events())
    assert prose not in trail


def test_the_parameters_digest_still_replaces_the_parameters() -> None:
    """FIX-2 must not have re-opened the route FIX-1 closed."""
    secret = "0xDEADBEEF-SECURITY-ACCESS-KEY"
    digest = request().digest_parameters({"key": secret})
    assert secret not in digest
    safety = kernel(permission_set=permissions(Capability.READ))
    built = OperationRequest(
        operation_id="op-diagnostic-read-1",
        operation_class=OperationClass.ENGINEERING_READ,
        caller=AGENT,
        target=OperationTarget(),
        requested_at=1_000.0,
        parameters_digest=digest,
    )
    safety.evaluate(built)
    trail = " | ".join(str(recorded.describe()) for recorded in safety.audit_events())
    assert secret not in trail
    assert digest in trail
