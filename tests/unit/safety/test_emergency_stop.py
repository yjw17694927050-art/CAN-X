"""The emergency-stop contract: one authority, and a safety epoch boundary.

SAFETY-01 §18 and invariants S13, S14, S22, S23, S24. The four steps the
contract fixes are tested in order:

```text
globally disarm → deny new dangerous operations
→ request cancellation of active dangerous operations → audit event
```

SAFETY-01-FIX-3 found that the contract was missing its fifth property, and it is
the one that makes the other four mean anything: a stop is an **epoch boundary**,
not a pause. Before the fix, authority could be *pre-staged* while the stop was
engaged — ``arm``, ``confirm_arm`` and ``grant_approval`` all kept working — so a
released runtime came back ``ARMED`` with a live approval, and dangerous work
resumed at the speed of one release call. The lifecycle the tests below pin is:

```text
E-stop engaged   → ARM DISARMED, every approval cleared
                 → no ARM may be requested and no approval granted
E-stop released  → still DISARMED, still no outstanding approval
                 → authority must be rebuilt explicitly, from scratch
```

What is *not* tested is interrupting a real transmit, because there is none. That
limitation is asserted explicitly here rather than left for a reader to infer from
the absence of a test.
"""

from __future__ import annotations

import pytest
from canx.safety.arm import ArmState
from canx.safety.audit import SafetyAuditEvent, digest_reason, digest_reason_best_effort
from canx.safety.decision import SafetyReason
from canx.safety.emergency import CancellationFailure, CancellationFailureCode
from canx.safety.errors import (
    SafetyAuditError,
    SafetyCallerError,
    SafetyEmergencyStopError,
    SafetyIdentifierError,
)
from canx.safety.identifiers import CancellerId, OperationId
from canx.safety.risk import Capability
from safety_builders import (
    AGENT,
    ALL_CALLERS,
    AUTOMATION,
    DANGEROUS,
    HOST,
    MACHINE_CALLERS,
    OPERATOR,
    SAFE,
    SCRIPT,
    MovableClock,
    armed_kernel,
    kernel,
    permissions,
    request,
    scope,
    spec,
)


class _RecordingCanceller:
    """A subsystem that remembers it was asked to stop."""

    def __init__(self, requested: tuple[str, ...] = ("tx-1",)) -> None:
        self.digests: list[str | None] = []
        self._requested = requested

    def cancel_active_operations(
        self, *, reason_digest: str | None
    ) -> tuple[OperationId, ...]:
        self.digests.append(reason_digest)
        return tuple(OperationId(value) for value in self._requested)


class _RefusingCanceller:
    """A subsystem that cannot answer. Its failure must be reported, not swallowed."""

    def cancel_active_operations(
        self, *, reason_digest: str | None
    ) -> tuple[OperationId, ...]:
        raise RuntimeError("cancellation channel is down")


class _MalformedCanceller:
    """A subsystem that violates its own contract and returns free text.

    The runtime type in the ``OperationCanceller`` protocol is not a security
    boundary — Python does not enforce it — so the controller revalidates every
    returned reference. This canceller exists to prove that revalidation, not to
    be believable.
    """

    def __init__(self, returned: tuple[object, ...]) -> None:
        self._returned = returned

    def cancel_active_operations(
        self, *, reason_digest: str | None
    ) -> tuple[OperationId, ...]:
        return self._returned  # type: ignore[return-value]


class _StoppableSink:
    """An audit trail that records normally until it is told not to."""

    def __init__(self) -> None:
        self.failing = False
        self.events: list[SafetyAuditEvent] = []

    def record(self, event: SafetyAuditEvent) -> None:
        if self.failing:
            raise OSError("audit storage is unavailable")
        self.events.append(event)


class _OneShotFailingClock(MovableClock):
    """A clock that fails exactly one read, then works again.

    Isolates "the ``engaged_at`` read failed" from "the audit read failed": the
    stop has to survive the first without being dragged down by the second, which
    is a different property from the audit-failure case.
    """

    def __init__(self, now: float = 1_000.0) -> None:
        super().__init__(now)
        self._fail_next = False

    def __call__(self) -> float:
        if self._fail_next:
            self._fail_next = False
            raise RuntimeError("clock provider is unavailable")
        return super().__call__()

    def fail_next_read(self) -> None:
        """Arm the next clock read to fail."""
        self._fail_next = True


# -- The stop removes authority -------------------------------------------------


def test_engaging_the_stop_disarms_the_runtime() -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    assert safety.arm_state is ArmState.ARMED
    safety.engage_emergency_stop(caller=OPERATOR, reason="bench smoke")
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None


def test_engaging_the_stop_denies_new_dangerous_operations() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        spec(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    safety.engage_emergency_stop(caller=OPERATOR, reason="operator stop")
    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1"))
    assert decision.denied
    assert decision.reason_code is SafetyReason.EMERGENCY_STOP


def test_a_read_still_works_during_the_stop() -> None:
    """The stop denies *dangerous* work; observing the bus is not dangerous."""
    safety = kernel(permission_set=permissions(Capability.READ))
    safety.engage_emergency_stop(caller=OPERATOR, reason="operator stop")
    decision = safety.evaluate(request(SAFE, caller=OPERATOR))
    assert decision.allowed


@pytest.mark.parametrize("caller", ALL_CALLERS)
def test_every_caller_kind_may_engage_the_stop(caller: object) -> None:
    """Anything that detects danger may pull it. Engaging only ever reduces authority."""
    safety = kernel()
    state = safety.engage_emergency_stop(
        caller=caller, reason="detected"  # type: ignore[arg-type]
    )
    assert state.engaged is True
    assert state.reason_digest == digest_reason("detected")


@pytest.mark.parametrize("caller", MACHINE_CALLERS)
def test_a_machine_caller_may_not_release_the_stop(caller: object) -> None:
    safety = kernel()
    safety.engage_emergency_stop(caller=AGENT, reason="detected")
    with pytest.raises(SafetyCallerError):
        safety.release_emergency_stop(caller=caller)  # type: ignore[arg-type]
    assert safety.emergency_stop_engaged is True
    # A refused release is not a partial one: the caller is judged before
    # anything is reduced, and nothing is recorded as if it had happened.
    assert safety.arm_state is ArmState.DISARMED
    assert safety.approvals.outstanding() == ()
    actions = [event.operation_id for event in safety.audit_events()]
    assert "kernel.emergency_stop.released" not in actions


@pytest.mark.parametrize("caller", [OPERATOR, HOST])
def test_an_authority_bearing_caller_may_release_the_stop(caller: object) -> None:
    safety = kernel()
    safety.engage_emergency_stop(caller=AGENT, reason="detected")
    state = safety.release_emergency_stop(caller=caller)  # type: ignore[arg-type]
    assert state.engaged is False
    # Releasing restores the *possibility* of authority, never the authority.
    assert safety.arm_state is ArmState.DISARMED


# -- SAFETY-01-FIX-3, P0: the stop blocks authority, it does not pause it -------
#
# The defect the third independent review found: while the stop was engaged,
# `arm`, `confirm_arm` and `grant_approval` all still worked. Authority could be
# pre-staged during the emergency and survived the release, so the stop behaved
# as pause/resume exactly where the architecture forbids it (invariant S22).
#
# Each gate is asserted separately on purpose. `arm` being blocked does not imply
# `confirm_arm` is: the `ARMING → stop → confirm` path reaches ARMED without ever
# calling `arm` again, and a gate on one entry point is not a gate on the other.


def test_rearming_is_forbidden_while_emergency_stop_is_engaged() -> None:
    """The stop removes authority *and* prevents it from being rebuilt."""
    clock = MovableClock()
    safety = kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    safety.engage_emergency_stop(caller=OPERATOR, reason="operator stop")
    with pytest.raises(SafetyEmergencyStopError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
            caller=OPERATOR,
        )
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None
    assert safety.emergency_stop_engaged is True


def test_confirming_an_arm_is_forbidden_while_emergency_stop_is_engaged() -> None:
    """``ARMING → stop → confirm`` must not be a bypass around the gate on ``arm``.

    The runtime reaches ``ARMING`` first, so the confirmation is the only call
    that could complete the arm. It has to be refused on its own.
    """
    clock = MovableClock()
    safety = kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    safety.arm(
        scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
        caller=OPERATOR,
    )
    assert safety.arm_state is ArmState.ARMING
    safety.engage_emergency_stop(caller=AGENT, reason="detected")
    assert safety.arm_state is ArmState.DISARMED
    with pytest.raises(SafetyEmergencyStopError):
        safety.confirm_arm(caller=OPERATOR)
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None


def test_granting_an_approval_is_forbidden_while_emergency_stop_is_engaged() -> None:
    """An approval is dangerous authority; pre-staging it shortens the resume chain."""
    clock = MovableClock()
    safety = kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    safety.engage_emergency_stop(caller=AGENT, reason="detected")
    with pytest.raises(SafetyEmergencyStopError):
        safety.grant_approval(
            spec(single_use=False, issued_at=clock(), expires_at=clock() + 600),
            granted_by=OPERATOR,
        )
    assert safety.approvals.outstanding() == ()


@pytest.mark.parametrize("caller", [OPERATOR, HOST])
def test_even_an_authority_bearing_caller_cannot_stage_authority_during_the_stop(
    caller: object,
) -> None:
    """The gate is not a caller-authority check — the host is refused just as firmly.

    Caller authority is judged *first*, so an Agent is refused for a different
    reason (``SafetyCallerError``, as it is at every other moment). This test
    pins the second gate for the two caller kinds that would otherwise pass the
    first one.
    """
    clock = MovableClock()
    safety = kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    safety.engage_emergency_stop(caller=AGENT, reason="detected")
    with pytest.raises(SafetyEmergencyStopError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
            caller=caller,  # type: ignore[arg-type]
        )
    assert safety.arm_state is ArmState.DISARMED


@pytest.mark.parametrize("caller", MACHINE_CALLERS)
def test_a_machine_caller_is_still_refused_by_caller_authority_first(caller: object) -> None:
    """Ordering, stated explicitly: caller authority, then the stop gate.

    A machine caller is refused for the reason it always was — it may not control
    the arm state at all — so the stop gate is not asked to carry that job.
    """
    clock = MovableClock()
    safety = kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    safety.engage_emergency_stop(caller=AGENT, reason="detected")
    with pytest.raises(SafetyCallerError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
            caller=caller,  # type: ignore[arg-type]
        )
    assert safety.arm_state is ArmState.DISARMED


def test_estop_cannot_pre_stage_authority_for_release() -> None:
    """The headline: authority staged during the stop must not survive the release."""
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        spec(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    safety.engage_emergency_stop(caller=OPERATOR, reason="operator stop")

    # Every route that used to pre-stage authority while the stop was engaged.
    with pytest.raises(SafetyEmergencyStopError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 600),
            caller=OPERATOR,
        )
    with pytest.raises(SafetyEmergencyStopError):
        safety.confirm_arm(caller=OPERATOR)
    with pytest.raises(SafetyEmergencyStopError):
        safety.grant_approval(
            spec(
                approval_id="appr-staged",
                single_use=False,
                issued_at=clock(),
                expires_at=clock() + 600,
            ),
            granted_by=OPERATOR,
        )

    safety.release_emergency_stop(caller=OPERATOR)
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None
    assert safety.approvals.outstanding() == ()
    assert safety.emergency_stop_engaged is False


# -- Releasing restores possibility, never authority ---------------------------


def test_a_successful_release_leaves_the_runtime_disarmed() -> None:
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    safety.engage_emergency_stop(caller=OPERATOR, reason="operator stop")
    safety.release_emergency_stop(caller=OPERATOR)
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None


def test_a_successful_release_leaves_no_outstanding_approval() -> None:
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        spec(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    safety.engage_emergency_stop(caller=OPERATOR, reason="operator stop")
    safety.release_emergency_stop(caller=OPERATOR)
    assert safety.approvals.outstanding() == ()


def test_release_is_not_a_resume_command() -> None:
    """After a release a dangerous request is still refused — authority is gone.

    This is the property the previous behaviour violated: an operator releasing
    the stop expected to be back at "nothing is armed", and the runtime handed
    them a live approval instead.
    """
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        spec(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    safety.engage_emergency_stop(caller=OPERATOR, reason="operator stop")
    safety.release_emergency_stop(caller=OPERATOR)

    decision = safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1"))
    assert decision.denied
    assert decision.reason_code is SafetyReason.NOT_ARMED


def test_the_emergency_stop_creates_a_new_safety_epoch() -> None:
    """The full lifecycle, end to end: stop → rebuild from nothing → dangerous work.

    The point is that the *same* runtime can reach ``ALLOW`` again only through an
    explicit re-arm and a **new** approval. If the stop were a pause, the second
    ``ALLOW`` would have needed nothing.
    """
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        spec(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    assert safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1")).allowed

    safety.engage_emergency_stop(caller=OPERATOR, reason="operator stop")
    assert safety.arm_state is ArmState.DISARMED
    assert safety.approvals.outstanding() == ()

    with pytest.raises(SafetyEmergencyStopError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 600),
            caller=OPERATOR,
        )
    with pytest.raises(SafetyEmergencyStopError):
        safety.grant_approval(
            spec(single_use=False, issued_at=clock(), expires_at=clock() + 600),
            granted_by=OPERATOR,
        )

    safety.release_emergency_stop(caller=OPERATOR)
    assert safety.arm_state is ArmState.DISARMED
    assert safety.approvals.outstanding() == ()

    # Authority gone: the pre-stop approval is not merely expired, it is absent.
    assert safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-1")).denied
    # And nothing dangerous happens just because the stop was released.
    assert safety.evaluate(request(DANGEROUS, caller=OPERATOR)).denied

    safety.arm(
        scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 600),
        caller=OPERATOR,
    )
    safety.confirm_arm(caller=OPERATOR)
    safety.grant_approval(
        spec(approval_id="appr-2", issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    assert safety.evaluate(request(DANGEROUS, caller=OPERATOR, approval_id="appr-2")).allowed


def test_an_arm_that_was_in_flight_does_not_survive_the_stop() -> None:
    """``ARMING`` is authority too, and the stop has to reach it."""
    clock = MovableClock()
    safety = kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    safety.arm(
        scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
        caller=OPERATOR,
    )
    assert safety.arm_state is ArmState.ARMING
    safety.engage_emergency_stop(caller=OPERATOR, reason="operator stop")
    assert safety.arm_state is ArmState.DISARMED


# -- Cancellation fan-out -------------------------------------------------------


def test_the_stop_requests_cancellation_from_every_registered_subsystem() -> None:
    safety = kernel()
    periodic = _RecordingCanceller(requested=("tx-1", "tx-2"))
    replay = _RecordingCanceller(requested=("replay-7",))
    safety.register_canceller("tx.periodic", periodic)
    safety.register_canceller("replay.worker", replay)

    state = safety.engage_emergency_stop(caller=AGENT, reason="detected")

    assert set(state.requested_cancellations) == {"tx-1", "tx-2", "replay-7"}
    assert state.cancellation_failures == ()


def test_a_subsystem_that_cannot_confirm_cancellation_is_reported() -> None:
    """Invariant S14: the operator must see exactly which subsystem did not answer."""
    safety = kernel()
    safety.register_canceller("tx.periodic", _RecordingCanceller())
    safety.register_canceller("replay.worker", _RefusingCanceller())

    state = safety.engage_emergency_stop(caller=AGENT, reason="detected")

    assert state.requested_cancellations == ("tx-1",)
    assert state.cancellation_failures == (
        CancellationFailure(
            canceller_id=CancellerId("replay.worker"),
            failure_code=CancellationFailureCode.CANCELLER_RAISED,
            failure_type="RuntimeError",
        ),
    )
    assert safety.emergency_stop_engaged is True


def test_engaging_twice_keeps_the_first_reason_and_retries_cancellation() -> None:
    safety = kernel()
    canceller = _RecordingCanceller()
    safety.register_canceller("tx.periodic", canceller)
    first = safety.engage_emergency_stop(caller=AGENT, reason="first")
    second = safety.engage_emergency_stop(caller=AUTOMATION, reason="second")
    assert first.reason_digest == digest_reason("first")
    assert second.reason_digest == digest_reason("first")
    # The retry happens, and the canceller sees a digest both times.
    assert canceller.digests == [digest_reason("first"), digest_reason("second")]


# -- SAFETY-01-FIX-3, P1: the cancellation boundary is typed --------------------
#
# The defect the third independent review found: `OperationCanceller` returned
# `tuple[str, ...]`, that tuple went into `EmergencyStopState.requested_cancellations`
# unvalidated, and `EmergencyStopState.describe()` rendered it into the audit
# event's `detail`. So a subsystem's arbitrary return value reached Safety Audit
# through the back door, outside the identifier contract (invariant S24). The
# raw operator reason fanned out to every canceller by the same route.


def test_a_canceller_cannot_inject_free_text_into_the_cancellation_state() -> None:
    """The headline P1: an invalid reference is dropped, not recorded."""
    safety = kernel()
    safety.register_canceller(
        "tx.periodic",
        _MalformedCanceller(("tx-1", "operator secret is hunter2")),
    )

    state = safety.engage_emergency_stop(caller=AGENT, reason="detected")

    assert state.requested_cancellations == ("tx-1",)
    assert "hunter2" not in str(state.describe())
    assert CancellationFailureCode.CANCELLER_INVALID_REFERENCE in {
        failure.failure_code for failure in state.cancellation_failures
    }


def test_a_cancellers_free_text_never_reaches_the_audit_trail() -> None:
    safety = kernel()
    safety.register_canceller(
        "tx.periodic",
        _MalformedCanceller(("op-ok", "operator secret is hunter2")),
    )
    safety.engage_emergency_stop(caller=AGENT, reason="detected")
    recorded = " | ".join(str(event.describe()) for event in safety.audit_events())
    assert "hunter2" not in recorded
    assert "op-ok" in recorded


def test_an_invalid_cancellation_reference_does_not_prevent_the_stop() -> None:
    """A stop must still engage: a malformed answer is a failure to report, not a veto."""
    safety = kernel()
    safety.register_canceller("tx.periodic", _MalformedCanceller(("not an identifier!",)))
    state = safety.engage_emergency_stop(caller=AGENT, reason="detected")
    assert state.engaged is True
    assert safety.emergency_stop_engaged is True
    assert safety.arm_state is ArmState.DISARMED
    assert len(state.cancellation_failures) == 1


def test_a_canceller_that_is_not_even_iterable_is_reported_not_raised() -> None:
    """The contract can be violated in more ways than one; none may defeat the stop."""
    safety = kernel()
    safety.register_canceller("tx.periodic", _MalformedCanceller(None))  # type: ignore[arg-type]
    state = safety.engage_emergency_stop(caller=AGENT, reason="detected")
    assert state.engaged is True
    assert [failure.failure_code for failure in state.cancellation_failures] == [
        CancellationFailureCode.CANCELLER_CONTRACT_VIOLATION
    ]
    assert state.requested_cancellations == ()


def test_a_canceller_receives_a_reason_digest_not_the_raw_reason() -> None:
    """The operator's explanation stops at the kernel (invariant S19, S24)."""
    safety = kernel()
    canceller = _RecordingCanceller()
    safety.register_canceller("tx.periodic", canceller)

    safety.engage_emergency_stop(caller=AGENT, reason="bench secret xyz")

    assert canceller.digests == [digest_reason("bench secret xyz")]
    assert "bench secret xyz" not in canceller.digests


def test_every_cancellation_reference_stored_is_a_typed_identifier() -> None:
    safety = kernel()
    safety.register_canceller("tx.periodic", _RecordingCanceller(("tx-1",)))
    state = safety.engage_emergency_stop(caller=AGENT, reason="detected")
    assert all(isinstance(reference, OperationId) for reference in state.requested_cancellations)
    assert all(
        isinstance(failure.canceller_id, CancellerId)
        for failure in state.cancellation_failures
    )


# -- The state validates its own audit-facing fields ----------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("engaged_at", float("nan")),
        ("engaged_at", float("inf")),
        ("reason_digest", "not-a-digest"),
        ("reason_digest", "A" * 64),
    ],
)
def test_the_emergency_state_refuses_a_malformed_scalar(field: str, value: object) -> None:
    from canx.safety.emergency import EmergencyStopState

    with pytest.raises(SafetyIdentifierError):
        EmergencyStopState(engaged=True, **{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["free text reference", "  padded  ", "", "a" * 65])
def test_the_emergency_state_refuses_a_free_form_cancellation_reference(text: str) -> None:
    from canx.safety.emergency import EmergencyStopState

    with pytest.raises(SafetyIdentifierError):
        EmergencyStopState(engaged=True, requested_cancellations=(text,))


def test_the_emergency_state_accepts_a_well_formed_one() -> None:
    from canx.safety.emergency import EmergencyStopState

    state = EmergencyStopState(
        engaged=True,
        engaged_at=1_000.0,
        reason_digest=digest_reason("bench smoke"),
        requested_cancellations=("op-1",),
        cancellation_failures=(
            CancellationFailure(
                canceller_id=CancellerId("replay.worker"),
                failure_code=CancellationFailureCode.CANCELLER_RAISED,
                failure_type="RuntimeError",
            ),
        ),
    )
    assert state.describe()["cancellation_failures"] == [
        {
            "canceller_id": "replay.worker",
            "failure_code": "canceller.raised",
            "failure_type": "RuntimeError",
        }
    ]


def test_a_cancellation_failure_refuses_free_form_text() -> None:
    with pytest.raises(SafetyIdentifierError):
        CancellationFailure(
            canceller_id="free text canceller",  # type: ignore[arg-type]
            failure_code=CancellationFailureCode.CANCELLER_RAISED,
        )
    with pytest.raises(SafetyIdentifierError):
        CancellationFailure(
            canceller_id=CancellerId("replay.worker"),
            failure_code=CancellationFailureCode.CANCELLER_RAISED,
            failure_type="not an identifier",
        )


def test_a_canceller_identity_must_satisfy_the_identifier_contract() -> None:
    safety = kernel()
    with pytest.raises(SafetyIdentifierError):
        safety.register_canceller("free text canceller", _RecordingCanceller())


def test_a_stop_state_with_a_broken_clock_still_engages() -> None:
    """A timestamp that cannot be read must not be able to stop the stop (S22).

    Engaging only ever reduces authority, so a broken clock is a reason to record
    ``engaged_at = None`` — never a reason to leave the runtime running.
    """

    def broken_clock() -> float:
        raise RuntimeError("clock provider is unavailable")

    from canx.safety.emergency import EmergencyStopController

    controller = EmergencyStopController(clock=broken_clock)
    state = controller.engage(caller=AGENT, reason_digest=digest_reason("detected"))
    assert state.engaged is True
    assert state.engaged_at is None
    assert controller.engaged is True


def test_a_malformed_reason_digest_cannot_prevent_the_stop() -> None:
    """Same rule for the other input: a broken digest loses attribution, not the stop.

    The loss is diagnosable rather than silent — ``engaged: true`` sits next to
    ``reason_digest: null`` in the state and the trail.
    """
    from canx.safety.emergency import EmergencyStopController

    controller = EmergencyStopController(clock=MovableClock())
    state = controller.engage(caller=AGENT, reason_digest="operator typed a sentence")
    assert state.engaged is True
    assert state.reason_digest is None
    assert controller.engaged is True


# -- SAFETY-01-FIX-4: metadata must never veto the reduction --------------------
#
# The defect: `SafetyKernel.engage_emergency_stop` digested the raw reason
# *before* the stop engaged, and `digest_reason` encodes to UTF-8. A reason that
# is a legal Python `str` but cannot be UTF-8 encoded — a lone surrogate,
# `"\ud800"`, which every other part of the runtime is happy to carry — therefore
# raised `UnicodeEncodeError` and the stop never ran. An ARMED runtime holding a
# live approval stayed exactly that way while the operator believed they had
# pulled the stop.
#
# The rule this section pins (invariant S25):
#
#   better an unattributed stop than an attributed non-stop
#
# Note what every test below does: it goes through the **public kernel path**.
# The defect was in the kernel's own ordering — it digested the reason before
# delegating — so driving `EmergencyStopController` directly would have missed it
# completely. The controller's own tolerance of an absent digest was already
# there and was never the broken half.

#: A legal `str` that UTF-8 cannot encode. Not exotic: any string that survived a
#: surrogate-passing path — a JS-to-Python bridge, a mis-decoded filename, a
#: truncation — carries one.
UNENCODABLE_REASON = "\ud800"


def test_unencodable_emergency_reason_cannot_prevent_the_stop() -> None:
    """The headline: a reason that will not encode must not decide the outcome."""
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        spec(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    assert safety.arm_state is ArmState.ARMED
    assert len(safety.approvals.outstanding()) == 1

    state = safety.engage_emergency_stop(caller=OPERATOR, reason=UNENCODABLE_REASON)

    assert safety.emergency_stop_engaged is True
    assert state.engaged is True
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None
    assert safety.approvals.outstanding() == ()
    # Attribution is the thing that was lost, and it is visible as an absence —
    # not as a digest of silently modified text.
    assert state.reason_digest is None


def test_an_unencodable_reason_still_clears_an_in_flight_arm() -> None:
    """``ARMING`` is authority too, and the reduction has to reach it."""
    clock = MovableClock()
    safety = kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    safety.arm(
        scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
        caller=OPERATOR,
    )
    assert safety.arm_state is ArmState.ARMING
    safety.engage_emergency_stop(caller=OPERATOR, reason=UNENCODABLE_REASON)
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None


def test_the_stop_still_blocks_authority_after_an_unencodable_reason() -> None:
    """The FIX-3 gate must survive the FIX-4 fallback."""
    clock = MovableClock()
    safety = kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    safety.engage_emergency_stop(caller=OPERATOR, reason=UNENCODABLE_REASON)
    with pytest.raises(SafetyEmergencyStopError):
        safety.arm(
            scope(Capability.CAN_TX, granted_at=clock(), expires_at=clock() + 60),
            caller=OPERATOR,
        )
    with pytest.raises(SafetyEmergencyStopError):
        safety.grant_approval(
            spec(single_use=False, issued_at=clock(), expires_at=clock() + 600),
            granted_by=OPERATOR,
        )
    assert safety.arm_state is ArmState.DISARMED
    assert safety.approvals.outstanding() == ()


def test_an_unencodable_reason_does_not_skip_the_cancellation_fan_out() -> None:
    """A missing *label* is not a reason to leave dangerous work running (S25)."""
    safety = kernel()
    canceller = _RecordingCanceller()
    safety.register_canceller("tx.periodic", canceller)

    state = safety.engage_emergency_stop(caller=OPERATOR, reason=UNENCODABLE_REASON)

    assert canceller.digests == [None]
    assert state.requested_cancellations == ("tx-1",)
    assert state.cancellation_failures == ()


def test_an_unencodable_reason_is_still_audited_as_an_absent_digest() -> None:
    """The audit attempt happens; only the attribution is missing (S19, S25)."""
    safety = kernel()
    safety.engage_emergency_stop(caller=OPERATOR, reason=UNENCODABLE_REASON)
    engaged = [
        event for event in safety.audit_events() if event.operation_id.endswith("engaged")
    ][-1]
    assert engaged.outcome == "control"
    assert engaged.emergency_stop_engaged is True
    assert engaged.reason_digest is None
    assert engaged.arm_state == "disarmed"


def test_an_unencodable_reason_never_reaches_the_trail() -> None:
    """The fallback is an absence, never the raw text — S19 is not weakened."""
    safety = kernel()
    safety.register_canceller(
        "tx.periodic", _MalformedCanceller(("tx-1", "operator secret is hunter2"))
    )
    safety.engage_emergency_stop(caller=OPERATOR, reason=UNENCODABLE_REASON)
    recorded = " | ".join(str(event.describe()) for event in safety.audit_events())
    assert "\ud800" not in recorded
    assert "hunter2" not in recorded


def test_the_best_effort_digest_degrades_and_never_fabricates() -> None:
    """The three-way choice S25 makes, asserted directly.

    ``errors="ignore"`` would make two different reasons collide;
    ``errors="replace"`` would record a digest of text nobody supplied. The
    contract is ``None`` — attribution unavailable — and specifically *not* the
    digest of an empty or substituted string.
    """
    assert digest_reason_best_effort("bench smoke") == digest_reason("bench smoke")
    assert digest_reason_best_effort(UNENCODABLE_REASON) is None
    assert digest_reason_best_effort(UNENCODABLE_REASON) != digest_reason("")
    assert digest_reason_best_effort(UNENCODABLE_REASON) != digest_reason("\ufffd")


def test_the_strict_digest_still_refuses_an_unencodable_reason() -> None:
    """The best-effort variant is a deliberate exception, not a global softening.

    On an authority-*increasing* path a reason that cannot be digested is a caller
    bug, and knowing about it is worth more than proceeding — the same asymmetry
    S17 makes between the two directions.
    """
    with pytest.raises(UnicodeEncodeError):
        digest_reason(UNENCODABLE_REASON)


# -- Compound metadata failures -------------------------------------------------


def test_a_failed_reason_and_a_failed_audit_still_leave_the_stop_engaged() -> None:
    """Two metadata failures at once; the reduction outranks both.

    The caller may be told the trail could not be written. It must not be able to
    read that as "the stop did not happen".
    """
    clock = MovableClock()
    sink = _StoppableSink()
    safety = armed_kernel(
        clock=clock,
        duration=600.0,
        permission_set=permissions(Capability.CAN_TX),
        audit_sink=sink,
    )
    safety.grant_approval(
        spec(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    assert len(safety.approvals.outstanding()) == 1
    sink.failing = True

    with pytest.raises(SafetyAuditError):
        safety.engage_emergency_stop(caller=OPERATOR, reason=UNENCODABLE_REASON)

    assert safety.emergency_stop_engaged is True
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None
    assert safety.approvals.outstanding() == ()


def test_a_failed_reason_and_a_broken_clock_still_engage_the_stop() -> None:
    """Both pieces of attribution are gone; the stop is not."""
    clock = _OneShotFailingClock()
    safety = armed_kernel(
        clock=clock, duration=600.0, permission_set=permissions(Capability.CAN_TX)
    )
    safety.grant_approval(
        spec(single_use=False, issued_at=clock(), expires_at=clock() + 600),
        granted_by=OPERATOR,
    )
    clock.fail_next_read()

    state = safety.engage_emergency_stop(caller=OPERATOR, reason=UNENCODABLE_REASON)

    assert state.engaged is True
    assert state.engaged_at is None
    assert state.reason_digest is None
    assert safety.arm_state is ArmState.DISARMED
    assert safety.active_scope is None
    assert safety.approvals.outstanding() == ()


def test_a_malformed_cancellation_and_a_failed_reason_still_engage_the_stop() -> None:
    """A violating canceller plus an absent digest: still a stop, still no prose."""
    safety = kernel()
    safety.register_canceller(
        "tx.periodic", _MalformedCanceller(("tx-1", "operator secret is hunter2"))
    )

    state = safety.engage_emergency_stop(caller=OPERATOR, reason=UNENCODABLE_REASON)

    assert state.engaged is True
    assert state.reason_digest is None
    assert state.requested_cancellations == ("tx-1",)
    assert [failure.failure_code for failure in state.cancellation_failures] == [
        CancellationFailureCode.CANCELLER_INVALID_REFERENCE
    ]
    assert safety.emergency_stop_engaged is True
    assert safety.arm_state is ArmState.DISARMED


def test_an_unencodable_reason_does_not_disturb_a_release() -> None:
    """Release still works normally after an unattributed stop (S23)."""
    clock = MovableClock()
    safety = armed_kernel(clock=clock, permission_set=permissions(Capability.CAN_TX))
    safety.engage_emergency_stop(caller=OPERATOR, reason=UNENCODABLE_REASON)
    state = safety.release_emergency_stop(caller=OPERATOR)
    assert state.engaged is False
    assert safety.arm_state is ArmState.DISARMED
    assert safety.approvals.outstanding() == ()


# -- The trail ------------------------------------------------------------------


def test_the_stop_is_recorded_on_the_audit_trail() -> None:
    safety = kernel()
    safety.engage_emergency_stop(caller=OPERATOR, reason="bench smoke")
    actions = [event.operation_id for event in safety.audit_events()]
    assert "kernel.emergency_stop.engaged" in actions
    engaged = [
        event for event in safety.audit_events() if event.operation_id.endswith("engaged")
    ][-1]
    assert engaged.caller_kind == "human.ui"
    assert engaged.reason_digest == digest_reason("bench smoke")
    assert "bench smoke" not in engaged.message
    assert engaged.emergency_stop_engaged is True


def test_releasing_the_stop_is_recorded() -> None:
    safety = kernel()
    safety.engage_emergency_stop(caller=OPERATOR, reason="bench smoke")
    safety.release_emergency_stop(caller=OPERATOR)
    actions = [event.operation_id for event in safety.audit_events()]
    assert "kernel.emergency_stop.released" in actions


def test_the_audit_detail_of_a_guided_cancellation_is_structured() -> None:
    """What reaches ``detail`` is identifiers and bounded codes — nothing else."""
    safety = kernel()
    safety.register_canceller("tx.periodic", _RecordingCanceller(("tx-1",)))
    safety.register_canceller("replay.worker", _RefusingCanceller())
    safety.engage_emergency_stop(caller=OPERATOR, reason="bench smoke")
    engaged = [
        event for event in safety.audit_events() if event.operation_id.endswith("engaged")
    ][-1]
    assert engaged.detail is not None
    assert "tx-1" in engaged.detail
    assert "replay.worker" in engaged.detail
    assert "canceller.raised" in engaged.detail
    # The raw exception message is never part of the trail.
    assert "cancellation channel is down" not in engaged.detail


def test_there_is_no_real_transmit_to_interrupt_in_this_stage() -> None:
    """The contract is implemented; the interruption is not, and says so.

    ``requested_cancellations`` records what was *asked*. Nothing in the safety
    package can stop a device write, because no device write exists to stop —
    that integration is a later task and is listed as NOT VERIFIED in the
    architecture document rather than implied by a passing test.
    """
    safety = kernel()
    state = safety.engage_emergency_stop(caller=AGENT, reason="detected")
    assert state.requested_cancellations == ()
    assert state.cancellation_failures == ()


def test_machine_callers_can_never_reach_the_release_path() -> None:
    for machine in (AGENT, SCRIPT, AUTOMATION):
        assert machine.may_release_emergency_stop is False
