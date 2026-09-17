"""The emergency-stop contract — one authority that outranks every operation family.

Emergency stop is not "a way to stop periodic transmit". It is a single global
authority that has to be able to reach *every* family of dangerous operation
that CAN-X will ever have — periodic TX, replay, injection, automation TX, Agent
TX and diagnostic workflows — because the one thing an operator must never have
to reason about during an emergency is which subsystem is still running
(invariant S13).

```text
Emergency Stop
→ globally disarm
→ deny new dangerous operations
→ block the creation of new authority
→ request cancellation of active dangerous operations
→ audit event
```

**The stop is a safety epoch boundary, not a pause** (invariants S22, S23).
SAFETY-01-FIX-3 found the version of this module that treated it as a pause:
authority could be *pre-staged* while the stop was engaged — ``arm``,
``confirm_arm`` and ``grant_approval`` all still worked — so a released runtime
came back ``ARMED`` holding a live approval and dangerous work resumed at the
speed of one release call. The invariant this module now holds is:

> While the stop is engaged no caller may establish or pre-stage dangerous
> vehicle authority, and a successful release leaves the runtime ``DISARMED``
> with no outstanding approval.

This stage implements the state, the refusal, the authority gate and the
cancellation **request**; it does not implement interrupting a real device
write, because there is no real transmit path yet and claiming otherwise would
be a fabrication. The contract is written so that the transport layer can be
attached later without changing any of the five steps.

**The cancellation boundary is typed** (invariant S24). A canceller returns
``OperationId`` values and receives a reason *digest*; it never receives the
operator's raw reason and its return value never reaches Safety Audit untested.
A subsystem that answers with free text gets its valid references kept and the
rest recorded as a structured :class:`CancellationFailure` — the text is not
stored, and, just as importantly, the stop still engages.

Two asymmetries are deliberate:

* **Any caller may engage.** An Agent, script or automation rule that detects
  danger may pull the stop; refusing would be absurd. Engaging can only ever
  reduce authority — which is also why a broken clock or a malformed canceller
  answer can never prevent it.
* **Only an operator may release.** Releasing restores the *possibility* of
  dangerous work, which is an authority decision and belongs to a human operator
  or the host, never to the machine that was just stopped.

Releasing never restores what was armed: a released runtime is ``DISARMED`` with
no approvals and must be armed again from scratch (invariants S8, S23).
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from canx.safety.caller import CallerIdentity
from canx.safety.errors import SafetyCallerError, SafetyIdentifierError, SafetyStateError
from canx.safety.identifiers import (
    REASON_DIGEST_ROLE,
    CancellerId,
    OperationId,
    validate_audit_identifier,
    validate_sha256_digest,
)


class OperationCanceller(Protocol):
    """A subsystem that can be asked to abandon its active dangerous operations.

    Implementations return the identifiers they accepted for cancellation, so
    the emergency event can record what was actually asked rather than what was
    hoped for. A canceller that cannot name its operations returns an empty
    tuple; it does not return ``None`` and it does not stay silent.

    ``reason_digest`` rather than the reason itself (invariant S24): a reason
    field is where a token ends up once somebody is describing *why* they
    stopped the runtime, and a canceller is outside the safety domain — it may
    log what it is handed. The digest still lets a canceller correlate its own
    record with the trail; it just cannot read the operator's words.

    The return type is a *typing* promise, not a runtime guarantee. Python does
    not enforce it, so :class:`EmergencyStopController` revalidates every value
    on the way in; an implementation that violates this signature gets a
    structured failure recorded, not a place in the audit trail.
    """

    def cancel_active_operations(self, *, reason_digest: str) -> tuple[OperationId, ...]:
        """Abandon every active operation. Returns the identifiers requested."""
        ...


class CancellationFailureCode(StrEnum):
    """Why a cancellation request did not produce an answer the runtime could keep.

    A closed vocabulary with no ``UNKNOWN`` member: a failure that cannot be
    classified has no representation, so a later reader cannot evaluate it
    leniently. Every member is a bounded identifier, which is what lets it sit in
    an audit ``detail`` (invariant S21).
    """

    #: The canceller raised instead of returning. The fault's *type* is recorded
    #: separately; its message never is.
    CANCELLER_RAISED = "canceller.raised"
    #: The canceller returned a value that is not a valid ``OperationId``. Valid
    #: references from the same answer are still kept.
    CANCELLER_INVALID_REFERENCE = "canceller.invalid_reference"
    #: The canceller's answer was not a sequence of references at all — ``None``,
    #: a bare string, a non-iterable. Nothing from it can be trusted or kept.
    CANCELLER_CONTRACT_VIOLATION = "canceller.contract_violation"


@dataclass(frozen=True, slots=True)
class CancellationFailure:
    """One subsystem's failure to answer a cancellation request, structured.

    Replaces the string this used to be — ``f"{ClassName}: {ExceptionName}"`` —
    which was free-form prose assembled at the failure site and then rendered
    straight into the audit ``detail`` (SAFETY-01-FIX-3, P1). Everything here is
    bounded: two validated identifiers and one validated vocabulary member.

    ``failure_type`` is the fault's *class name*, and only ever that (invariant
    S37): an exception message is a log line waiting to happen and this one would
    quote a payload. It is sanitised rather than trusted, because a dynamically
    created class can name itself anything at all.
    """

    canceller_id: CancellerId
    failure_code: CancellationFailureCode
    failure_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "canceller_id", CancellerId(self.canceller_id))
        if not isinstance(self.failure_code, CancellationFailureCode):
            raise SafetyIdentifierError(
                "A cancellation failure code is not part of the cancellation vocabulary.",
                details={
                    "field": "failure_code",
                    "received": type(self.failure_code).__name__,
                },
            )
        if self.failure_type is not None:
            validate_audit_identifier(self.failure_type, role="cancellation failure type")

    def describe(self) -> dict[str, object]:
        """Return the audit-safe shape of this failure."""
        return {
            "canceller_id": self.canceller_id,
            "failure_code": self.failure_code.value,
            "failure_type": self.failure_type,
        }


@dataclass(frozen=True, slots=True)
class EmergencyStopState:
    """A snapshot of the global emergency stop.

    ``reason_digest`` rather than the reason: the operator's explanation is still
    attributable — the same reason always hashes the same way — but the trail and
    this state never hold its text (invariant S19). A stop reason is exactly the
    kind of field that ends up carrying a token.

    ``__post_init__`` validates every field that can be rendered into an audit
    event — the same defence in depth :class:`~canx.safety.audit.SafetyAuditEvent`
    applies to itself, and for the same reason (invariants S21, S24): this state
    is what ``engage_emergency_stop`` hands to the trail, so a path that built one
    without going through the controller must still be unable to store free text
    or a malformed digest. The failures are
    :class:`SafetyIdentifierError`, matching how the audit event refuses its own
    malformed scalars.
    """

    engaged: bool
    engaged_at: float | None = None
    reason_digest: str | None = None
    requested_cancellations: tuple[OperationId, ...] = ()
    cancellation_failures: tuple[CancellationFailure, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.engaged, bool):
            raise SafetyIdentifierError(
                "An emergency stop state must carry a boolean engaged flag.",
                details={"field": "engaged", "received": type(self.engaged).__name__},
            )
        if self.engaged_at is not None and (
            not isinstance(self.engaged_at, (int, float))
            or not math.isfinite(self.engaged_at)
        ):
            raise SafetyIdentifierError(
                "An emergency stop state must carry a finite engaged_at timestamp.",
                details={"field": "engaged_at"},
            )
        if self.reason_digest is not None:
            validate_sha256_digest(self.reason_digest, role=REASON_DIGEST_ROLE)
        object.__setattr__(
            self,
            "requested_cancellations",
            tuple(OperationId(value) for value in self.requested_cancellations),
        )
        failures = tuple(self.cancellation_failures)
        for failure in failures:
            if not isinstance(failure, CancellationFailure):
                raise SafetyIdentifierError(
                    "A cancellation failure must be a structured CancellationFailure.",
                    details={"field": "cancellation_failures"},
                )
        object.__setattr__(self, "cancellation_failures", failures)

    def describe(self) -> dict[str, object]:
        """Return the audit-safe shape of this state."""
        return {
            "engaged": self.engaged,
            "engaged_at": self.engaged_at,
            "reason_digest": self.reason_digest,
            "requested_cancellations": list(self.requested_cancellations),
            "cancellation_failures": [failure.describe() for failure in self.cancellation_failures],
        }


class EmergencyStopController:
    """Global stop state, its disarm hook and its cancellation fan-out.

    The controller does not know what "disarm" means — it is handed a hook by
    whoever owns the arm machine. That keeps the dependency one-way (the safety
    kernel owns both and wires them together) and keeps this module testable
    without an arm machine at all.
    """

    __slots__ = ("_cancellers", "_clock", "_disarm_hook", "_lock", "_state")

    def __init__(self, *, clock: Callable[[], float]) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._disarm_hook: Callable[[], None] | None = None
        self._cancellers: list[tuple[CancellerId, OperationCanceller]] = []
        self._state = EmergencyStopState(engaged=False)

    def attach_disarm(self, hook: Callable[[], None]) -> None:
        """Attach the disarm action performed when the stop engages."""
        with self._lock:
            self._disarm_hook = hook

    def register_canceller(self, canceller_id: str, canceller: OperationCanceller) -> None:
        """Register a subsystem whose active operations the stop must reach.

        Args:
            canceller_id: A stable runtime identity — ``"tx.periodic"``,
                ``"replay.worker"``, ``"diagnostic.workflow"`` — not a class name.
                Validated as an identifier so a cancellation failure names
                something the audit contract can hold (invariant S24).

        Raises:
            SafetyIdentifierError: ``canceller_id`` is not a bounded identifier.
        """
        validated = CancellerId(canceller_id)
        with self._lock:
            self._cancellers.append((validated, canceller))

    @property
    def engaged(self) -> bool:
        """Whether the global stop is currently engaged."""
        with self._lock:
            return self._state.engaged

    @property
    def state(self) -> EmergencyStopState:
        """The current stop state."""
        with self._lock:
            return self._state

    def engage(self, *, caller: CallerIdentity, reason_digest: str) -> EmergencyStopState:
        """Engage the global stop. Idempotent, and open to every caller kind.

        The effects run in a fixed order and the order matters: the runtime is
        disarmed first, so that any decision taken while cancellations are still
        in flight already sees an unarmed runtime and refuses.

        A canceller that raises does not escape this method. Its failure is
        recorded as a structured :class:`CancellationFailure` and reported to the
        caller, which is the opposite of swallowing it (invariant S14): the
        operator sees exactly which subsystem did not acknowledge.

        Nothing here can fail the stop. This is the one authority-*reducing*
        entry point on the emergency path, so a broken clock, a canceller that
        raises and a canceller that answers with prose all end the same way — the
        stop is engaged and the fault is a line in its state (invariants S22,
        S24). The audit write is the kernel's business and is attempted after
        this returns; if *it* fails the stop still stands, because undoing a
        reduction to restore dangerous authority is the failure, not the fix
        (invariant S17).
        """
        with self._lock:
            # A second engagement keeps the original reason and timestamp — the
            # stop happened when it first happened — but still retries the
            # cancellations, because a subsystem that refused the first time may
            # be reachable now.
            if not self._state.engaged:
                self._state = EmergencyStopState(
                    engaged=True,
                    engaged_at=self._read_engaged_at(),
                    reason_digest=self._read_reason_digest(reason_digest),
                )
            hook = self._disarm_hook
            cancellers = tuple(self._cancellers)

        if hook is not None:
            hook()

        requested: list[OperationId] = []
        failures: list[CancellationFailure] = []
        for canceller_id, canceller in cancellers:
            self._request_cancellation(
                canceller_id=canceller_id,
                canceller=canceller,
                reason_digest=reason_digest,
                requested=requested,
                failures=failures,
            )

        with self._lock:
            self._state = replace(
                self._state,
                requested_cancellations=tuple(requested),
                cancellation_failures=tuple(failures),
            )
            return self._state

    def require_release_authority(self, *, caller: CallerIdentity) -> None:
        """Refuse a caller that may not release the global stop.

        Exposed rather than inlined into :meth:`reset` so the kernel can judge
        the caller *before* it starts reducing authority: a refused release must
        not be able to leave the runtime half-released. ``reset`` applies the
        same rule itself, so a direct caller of this controller cannot skip it.

        Raises:
            SafetyCallerError: ``caller`` is an Agent, a script or an automation
                rule. Only a human operator or the host system may restore the
                possibility of dangerous work.
        """
        if not caller.may_release_emergency_stop:
            raise SafetyCallerError(
                "Only a human operator or the host system may release the emergency stop.",
                details={"caller": str(caller.kind), "caller_id": caller.caller_id},
            )

    def reset(self, *, caller: CallerIdentity) -> EmergencyStopState:
        """Release the global stop.

        Raises:
            SafetyCallerError: ``caller`` is an Agent, a script or an automation
                rule — see :meth:`require_release_authority`.
        """
        self.require_release_authority(caller=caller)
        with self._lock:
            self._state = EmergencyStopState(engaged=False)
            return self._state

    def restore_engagement(self, *, state: EmergencyStopState) -> EmergencyStopState:
        """Put an engaged stop back after a release that could not be audited.

        Only ever used to *undo* a release, so the snapshot handed back is always
        an engaged one — a caller trying to use this to disengage a stop would be
        fighting both the name and the check below.

        Note what this does **not** restore: ARM state and approvals. A failed
        release must leave the runtime ``DISARMED`` with no outstanding approval,
        because reducing authority is never rolled back into more of it
        (invariants S17, S23).

        Releasing the stop remains :meth:`reset`, which is authority-checked.
        This method deliberately is not: it can only ever restore authority that
        was already there, and the caller is the kernel undoing its own partial
        work after a failed audit (invariant S17).

        Raises:
            SafetyStateError: ``state`` is not an engaged one.
        """
        if not state.engaged:
            raise SafetyStateError(
                "Only an engaged stop state can be restored.",
                details={"engaged": state.engaged},
            )
        with self._lock:
            self._state = state
            return self._state

    # -- Internals --------------------------------------------------------

    def _read_engaged_at(self) -> float | None:
        """Read the clock, or ``None`` when it cannot be read honestly.

        A stop is authority-*reducing*: a broken timestamp must not be able to
        prevent it (invariant S22). Recording ``engaged_at = None`` is the
        truthful outcome — the stop happened, the clock did not answer — and it
        keeps ``SafetyAuditEvent``'s finite-timestamp rule intact, because a
        non-finite reading never reaches the state to begin with.
        """
        try:
            now = float(self._clock())
        except Exception:
            return None
        return now if math.isfinite(now) else None

    @staticmethod
    def _read_reason_digest(reason_digest: str) -> str | None:
        """Return a valid digest, or ``None`` when the caller's value is malformed.

        The one input this controller receives that can be wrong is the digest the
        kernel derived from the operator's reason. A stop is authority-*reducing*,
        so a malformed digest must not be able to prevent it (invariant S22):
        losing the attribution is the smaller loss and engaging is the larger win.

        It is not a silent swallow either. The state and the trail then record
        ``reason_digest: null`` beside ``engaged: true``, which is at least as
        diagnosable as a malformed value would have been — and it is the caller's
        bug, so the fault being visible rather than fatal is the right trade.
        """
        try:
            return validate_sha256_digest(reason_digest, role=REASON_DIGEST_ROLE)
        except SafetyIdentifierError:
            return None

    @staticmethod
    def _request_cancellation(
        *,
        canceller_id: CancellerId,
        canceller: OperationCanceller,
        reason_digest: str,
        requested: list[OperationId],
        failures: list[CancellationFailure],
    ) -> None:
        """Ask one subsystem to abandon its work, and record what came back.

        Every returned value is revalidated here rather than trusted
        (invariant S24): the protocol's ``tuple[OperationId, ...]`` is a typing
        promise, and Python does not enforce it, so a subsystem that answers with
        free text does not get that text recorded. The references it did name are
        kept, the rest become a structured failure.

        This method never raises. A malformed answer is a failure to *report*,
        never a reason for the stop itself to fail (invariant S22).
        """
        try:
            returned = canceller.cancel_active_operations(reason_digest=reason_digest)
        except Exception as error:  # recorded below, never swallowed
            failures.append(
                CancellationFailure(
                    canceller_id=canceller_id,
                    failure_code=CancellationFailureCode.CANCELLER_RAISED,
                    failure_type=_identifier_or_none(type(error).__name__),
                )
            )
            return

        if isinstance(returned, (str, bytes, bytearray)):
            # A bare string is not a sequence of references; iterating it would
            # turn one answer into a character-by-character "reference" set.
            failures.append(
                CancellationFailure(
                    canceller_id=canceller_id,
                    failure_code=CancellationFailureCode.CANCELLER_CONTRACT_VIOLATION,
                )
            )
            return

        try:
            answers = tuple(returned)
        except Exception:
            failures.append(
                CancellationFailure(
                    canceller_id=canceller_id,
                    failure_code=CancellationFailureCode.CANCELLER_CONTRACT_VIOLATION,
                )
            )
            return

        for answer in answers:
            try:
                requested.append(OperationId(answer))
            except SafetyIdentifierError:
                failures.append(
                    CancellationFailure(
                        canceller_id=canceller_id,
                        failure_code=CancellationFailureCode.CANCELLER_INVALID_REFERENCE,
                        failure_type=_identifier_or_none(type(answer).__name__),
                    )
                )


def _identifier_or_none(value: str) -> str | None:
    """Return ``value`` when it is a valid audit identifier, else ``None``.

    Used for a fault's *type name*. A class name is normally a valid identifier,
    but a dynamically created class can name itself anything at all — including
    free text — so the name is sanitised rather than trusted. The alternative
    would be exactly the defect this contract closes: prose in the audit detail.
    """
    try:
        return validate_audit_identifier(value, role="cancellation failure type")
    except SafetyIdentifierError:
        return None
