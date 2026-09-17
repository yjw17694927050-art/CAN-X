"""No caller-controlled text reaches the safety trail.

The defect this file pins down (SAFETY-01-FIX-1, P1-1) is a claim the
architecture made and the code did not keep. The audit event was described as
having "no free-text slot", and it did have one — three of them:

```text
caller_name        supplied by whoever constructed the CallerIdentity
message            carried the operator's reason verbatim:
                     disarm(reason=…)
                     engage_emergency_stop(reason=…)
detail             rendered EmergencyStopState.describe(), which included
                   that same raw reason
```

A reason field is exactly where a token, a key or an unlock payload ends up once
someone is describing *why* they stopped the runtime, and the trail is the one
place that must never be able to hold one (SAFETY-01 §20).

The rule (invariant S19):

> No arbitrary caller-controlled text may be persisted in a safety audit event.
> A reason is recorded as a digest and a bounded label, never as its text.

A digest keeps the property the reason was there for — two records of the same
reason can be matched, and a reason can be confirmed after the fact by whoever
already knows it — without the trail ever holding the reason itself.
"""

from __future__ import annotations

import hashlib

import pytest
from canx.safety.audit import SafetyAuditEvent
from canx.safety.caller import CallerIdentity, CallerKind
from canx.safety.errors import SafetyError
from canx.safety.risk import Capability
from safety_builders import (
    AGENT,
    OPERATOR,
    MovableClock,
    armed_kernel,
    kernel,
    permissions,
    request,
)

#: Shaped like a real credential: high entropy, base64-ish alphabet, and long
#: enough that it cannot be mistaken for a label.
SECRET = "eyJhbGciOiJIUzI1NiJ9.SECRET-TOKEN-9f3a1c7be2d4"


def trail_text(safety: object) -> str:
    """Every audit field, flattened, so a leak anywhere shows up here."""
    events = safety.audit_events()  # type: ignore[attr-defined]
    return " | ".join(str(event.describe()) for event in events)


# -- The leak, closed ---------------------------------------------------------


def test_an_emergency_stop_reason_never_reaches_the_audit_trail() -> None:
    safety = kernel()
    safety.engage_emergency_stop(caller=OPERATOR, reason=SECRET)
    recorded = trail_text(safety)
    assert SECRET not in recorded
    assert "SECRET-TOKEN" not in recorded


def test_a_disarm_reason_never_reaches_the_audit_trail() -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock)
    safety.disarm(caller=OPERATOR, reason=SECRET)
    recorded = trail_text(safety)
    assert SECRET not in recorded


def test_the_emergency_state_description_carries_no_raw_reason() -> None:
    """The state object is rendered into the trail, so it must not hold the text."""
    safety = kernel()
    state = safety.engage_emergency_stop(caller=OPERATOR, reason=SECRET)
    assert SECRET not in str(state.describe())


def test_an_operation_parameter_secret_never_reaches_the_audit_trail() -> None:
    safety = kernel(permission_set=permissions(Capability.READ))
    digest = request().digest_parameters({"key": SECRET})
    safety.evaluate(request(parameters_digest=digest))
    recorded = trail_text(safety)
    assert SECRET not in recorded
    assert digest in recorded


# -- What replaces it ---------------------------------------------------------


def test_a_reason_is_recorded_as_a_digest() -> None:
    """The reason is still *attributable*, just not readable off the trail."""
    expected = hashlib.sha256(b"bench smoke").hexdigest()
    safety = kernel()
    state = safety.engage_emergency_stop(caller=OPERATOR, reason="bench smoke")
    assert state.reason_digest == expected
    assert expected in str(state.describe())


def test_a_disarm_reason_is_recorded_as_a_digest() -> None:
    expected = hashlib.sha256(b"operator released control").hexdigest()
    clock = MovableClock()
    safety = armed_kernel(clock=clock)
    safety.disarm(caller=OPERATOR, reason="operator released control")
    assert expected in trail_text(safety)


def test_the_same_reason_produces_the_same_digest() -> None:
    first = kernel()
    second = kernel()
    first.engage_emergency_stop(caller=OPERATOR, reason="bench smoke")
    second.engage_emergency_stop(caller=OPERATOR, reason="bench smoke")
    assert first.audit_events()[-1].reason_digest == second.audit_events()[-1].reason_digest


def test_different_reasons_produce_different_digests() -> None:
    first = kernel()
    second = kernel()
    first.engage_emergency_stop(caller=OPERATOR, reason="stop A")
    second.engage_emergency_stop(caller=OPERATOR, reason="stop B")
    assert first.audit_events()[-1].reason_digest != second.audit_events()[-1].reason_digest


def test_a_control_event_message_is_kernel_text_not_caller_text() -> None:
    safety = kernel()
    safety.engage_emergency_stop(caller=OPERATOR, reason=SECRET)
    control = [event for event in safety.audit_events() if event.outcome == "control"]
    assert control
    for event in control:
        assert SECRET not in event.message
        assert "emergency" in event.message.lower() or "stop" in event.message.lower()


# -- The remaining free-text field, bounded -----------------------------------


def test_a_caller_name_longer_than_the_label_budget_is_refused() -> None:
    with pytest.raises(SafetyError):
        CallerIdentity(CallerKind.AGENT, "a" * 200)


def test_a_caller_name_shaped_like_a_payload_is_refused() -> None:
    """A label alphabet, not an encoding: no ``+``, ``/`` or ``=``."""
    for payload in ("aGVsbG8+d29ybGQ=", "key:value/path+x", "  padded  "):
        with pytest.raises(SafetyError):
            CallerIdentity(CallerKind.AGENT, payload)


def test_a_normal_caller_name_is_still_accepted() -> None:
    for name in ("ui.main", "agent.session-1", "script.cleanup_v2", "runtime.host"):
        assert CallerIdentity(CallerKind.AGENT, name).name == name


# -- The shape, checked directly ----------------------------------------------


def test_the_event_has_a_digest_field_and_no_reason_field() -> None:
    fields = set(SafetyAuditEvent.__dataclass_fields__)
    assert "reason_digest" in fields
    assert "reason" not in fields


def test_an_approval_id_is_recorded_as_given() -> None:
    """An approval id is a runtime-chosen opaque handle, and the trail keeps it.

    That is deliberate and bounded: an id is a *reference* the trail must keep to
    be useful, and it is not unbounded text. What is forbidden is a field in
    which a caller can describe anything it likes — this test documents the
    boundary rather than pretending there is none.
    """
    safety = kernel(permission_set=permissions(Capability.READ))
    safety.evaluate(request(approval_id="appr-opaque-1"))
    assert any(event.approval_id == "appr-opaque-1" for event in safety.audit_events())


def test_a_machine_caller_engaging_the_stop_records_a_digest_not_the_text() -> None:
    """The rule holds for every caller kind, not only the operator."""
    safety = kernel()
    safety.engage_emergency_stop(caller=AGENT, reason=SECRET)
    assert SECRET not in trail_text(safety)
