"""The Safety Kernel — the one authority every dangerous capability must pass.

```text
UI ───────────────┐
Agent ────────────┤
Automation ───────┤
Script ───────────┤
Protocol Workflow ┤
                  ↓
             Safety Kernel          ← the policy authority
                  ↓
          controlled execution
                  ↓
              Adapter
```

Invariant S2: this is the policy authority. Invariant S12: no future
``Adapter.send`` path may bypass it. Neither statement is enforced by this module
alone — a caller that never asks the kernel simply never asks — and that is
precisely why the rule is frozen in ``docs/architecture/SAFETY_ARCHITECTURE.md``
and in ``AGENTS.md``, and why a regression test asserts that no transmit path
exists in the device layer today.

**This stage authorises; it does not execute.** ``evaluate`` returns a verdict.
There is no ``execute``, no adapter handle, no bus object and no transmit call
anywhere in this package. SAFETY-01 establishes the boundary that later TX,
replay, injection and diagnostic work has to cross; adding the crossing itself
is explicitly out of scope, and the absence of an execution path is asserted by
test rather than promised in prose (SAFETY-01 §6, §23).

The kernel owns four pieces of authority state and lets no caller hold them
directly:

```text
ARM state + scope      can be moved only by an authority-bearing caller
permissions            immutable once constructed; there is no widening method
approvals              granted only by an authority-bearing caller; consumed atomically
emergency stop         engagable by anyone, releasable only by an operator
```

Everything the kernel does is recorded on the audit trail — ``ALLOW`` and
``DENY`` alike, plus the control actions that move authority (arm, confirm,
disarm, emergency engage/release, approval grant). A decision that cannot be
recorded is not returned: it is raised as
:class:`canx.safety.errors.SafetyAuditError`, because a verdict nobody can audit
is not a verdict the rest of the system may act on (invariants S10, S14).

**Concurrency.** The kernel takes one re-entrant lock across evaluation and
every authority mutation, so a decision is never taken against authority that
changed halfway through it. The lock covers the kernel's own state only. It
cannot make an *external* execution atomic with the decision — a caller that
evaluates and then acts still has a gap between the two — and closing that gap
belongs to whoever owns the future execution path, not here. The limitation is
named in the architecture document rather than papered over.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable

from canx.safety.approval import Approval, ApprovalSpec, ApprovalStore, issuer_for
from canx.safety.arm import ArmController, ArmState
from canx.safety.audit import (
    InMemoryAuditSink,
    SafetyAuditEvent,
    SafetyAuditSink,
    digest_reason,
)
from canx.safety.caller import CallerIdentity
from canx.safety.decision import PolicyDecision, SafetyReason
from canx.safety.emergency import EmergencyStopController, EmergencyStopState, OperationCanceller
from canx.safety.errors import (
    SafetyApprovalProvenanceError,
    SafetyAuditError,
    SafetyCallerError,
)
from canx.safety.operation import OperationRequest
from canx.safety.permission import PermissionSet
from canx.safety.policy import SafetyContext, SafetyPolicy
from canx.safety.scope import ArmScope


class SafetyKernel:
    """The runtime's safety authority.

    Args:
        clock: Source of "now" for expiry decisions. Injected so tests can expire
            a scope or an approval without sleeping, and so a host can supply a
            monotonic source without this module choosing for it.
        audit_sink: Where decisions are recorded. Defaults to a bounded in-memory
            trail, which is what this stage has use for — there is no persistence
            requirement yet, and inventing a database table would be building
            ahead of the need.
        permissions: The session's standing capability grants. Defaults to the
            empty set: a kernel that was handed no authority authorises nothing.
        policy: The decision core. Defaults to the standard policy; passed in
            only by tests that need a different approval table.
    """

    __slots__ = (
        "_approvals",
        "_arm",
        "_audit",
        "_clock",
        "_emergency",
        "_lock",
        "_permissions",
        "_policy",
    )

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        audit_sink: SafetyAuditSink | None = None,
        permissions: PermissionSet | None = None,
        policy: SafetyPolicy | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self._audit: SafetyAuditSink = (
            audit_sink if audit_sink is not None else InMemoryAuditSink()
        )
        self._permissions = permissions if permissions is not None else PermissionSet()
        self._policy = policy if policy is not None else SafetyPolicy()
        self._arm = ArmController(clock=clock)
        self._approvals = ApprovalStore()
        self._emergency = EmergencyStopController(clock=clock)
        # Disarm is wired here, not inside the stop controller: the controller
        # knows how to stop the world, and the kernel knows what "disarmed" means.
        self._emergency.attach_disarm(self._disarm_for_emergency_stop)

    # -- ARM surface ------------------------------------------------------

    @property
    def arm_state(self) -> ArmState:
        """The current arm state."""
        return self._arm.state

    @property
    def active_scope(self) -> ArmScope | None:
        """The authority the runtime currently holds, or ``None``."""
        return self._arm.active_scope()

    def arm(self, scope: ArmScope, *, caller: CallerIdentity) -> ArmState:
        """Begin arming with ``scope``. Moves ``DISARMED → ARMING``.

        Raises:
            SafetyCallerError: ``caller`` may not control arm. An Agent, script
                or automation rule is refused here — this is the structural
                answer to "can an Agent self-arm?" (invariants S3, S4).
        """
        self._require_arm_authority(caller)
        with self._lock:
            state = self._arm.request(scope)
            self._record_or_rollback(
                record=lambda: self._record_control(
                    action="arm.requested",
                    caller=caller,
                    reason_code=SafetyReason.ALLOWED,
                    message="Arming was requested with a bounded scope.",
                    detail=_render(scope.describe()),
                ),
                rollback=self._arm.disarm,
            )
            return state

    def confirm_arm(self, *, caller: CallerIdentity) -> ArmState:
        """Complete arming. Moves ``ARMING → ARMED``.

        Raises:
            SafetyCallerError: ``caller`` may not control arm.
            SafetyStateError: The machine is not ``ARMING``.
            SafetyScopeError: The scope lapsed before the confirmation.
        """
        self._require_arm_authority(caller)
        with self._lock:
            state = self._arm.confirm()
            self._record_or_rollback(
                record=lambda: self._record_control(
                    action="arm.confirmed",
                    caller=caller,
                    reason_code=SafetyReason.ALLOWED,
                    message="The runtime is armed within its scope.",
                    detail=None,
                ),
                rollback=self._arm.disarm,
            )
            return state

    def disarm(self, *, caller: CallerIdentity, reason: str = "disarmed") -> ArmState:
        """Return to ``DISARMED``. Open to every caller.

        Deliberately unrestricted: disarming only removes authority, and a
        runtime in which an Agent could not drop the arm state would be a runtime
        where the safest available action was reserved for the most privileged
        caller.
        """
        with self._lock:
            state = self._arm.disarm()
            self._record_control(
                action="disarm",
                caller=caller,
                reason_code=SafetyReason.NOT_ARMED,
                message="The runtime was disarmed.",
                detail=None,
                reason_digest=digest_reason(reason),
            )
            return state

    # -- Approval surface -------------------------------------------------

    @property
    def approvals(self) -> ApprovalStore:
        """The session's approval store.

        Exposed so a host can revoke an approval it no longer stands behind.
        There is no method here that *widens* authority: the store's ``grant``
        still refuses a machine caller, and nothing in this class can mint one.
        """
        return self._approvals

    def grant_approval(self, spec: ApprovalSpec, *, granted_by: CallerIdentity) -> Approval:
        """Record an approval for ``spec``, with provenance derived from ``granted_by``.

        The caller describes **what** it wants authorised; the kernel decides
        **who** is authorising it, from the identity the rest of the runtime
        already trusts for arm control and audit attribution. There is no
        parameter through which a caller could claim a provenance that is not
        its own (invariant S16).

        Returns:
            The stored approval, including the provenance the kernel assigned.

        Raises:
            SafetyCallerError: ``granted_by`` is an Agent, script or automation
                rule (invariant S4).
            SafetyApprovalProvenanceError: ``granted_by`` carries no provenance,
                or the request is not an :class:`ApprovalSpec`.
        """
        if not granted_by.may_issue_approval:
            raise SafetyCallerError(
                "Only a human operator or the host system may issue an approval.",
                details={"caller": str(granted_by.kind), "name": granted_by.name},
            )
        if type(spec) is not ApprovalSpec:
            # An ``Approval`` carries an issuer, and a caller that handed one in
            # would reasonably expect its label to matter. It does not — the
            # kernel derives provenance — and silently overwriting it would
            # teach that caller something false. The shape that has no label to
            # supply is the shape that is accepted.
            raise SafetyApprovalProvenanceError(
                "An approval must be requested with an ApprovalSpec; provenance is "
                "derived from the granting caller and cannot be supplied.",
                details={"supplied_type": type(spec).__name__},
            )
        with self._lock:
            approval = Approval(
                approval_id=spec.approval_id,
                capability=spec.capability,
                target=spec.target,
                issued_at=spec.issued_at,
                expires_at=spec.expires_at,
                issuer=issuer_for(granted_by),
                single_use=spec.single_use,
            )
            self._approvals.grant(approval, granted_by=granted_by)
            self._record_or_rollback(
                record=lambda: self._record_control(
                    action="approval.granted",
                    caller=granted_by,
                    reason_code=SafetyReason.ALLOWED,
                    message="An approval was recorded for this session.",
                    detail=_render(approval.describe()),
                ),
                rollback=lambda: self._approvals.revoke(approval.approval_id),
            )
            return approval

    def revoke_approval(self, approval_id: str) -> bool:
        """Drop an approval. Returns whether one was held."""
        with self._lock:
            return self._approvals.revoke(approval_id)

    # -- Decision surface -------------------------------------------------

    def evaluate(self, request: OperationRequest) -> PolicyDecision:
        """Return the verdict for ``request``.

        Raises:
            SafetyAuditError: The decision could not be recorded. This is
                deliberately an exception rather than a ``DENY``: the caller
                must not be able to act on a verdict that left no trace, and it
                must not be able to read the failure as "denied, try again
                differently" either (invariants S10, S14).
        """
        with self._lock:
            context = SafetyContext(
                now=self._clock(),
                arm_state=self._arm.state,
                arm_scope=self._arm.active_scope(),
                permissions=self._permissions,
                approvals=self._approvals,
                emergency_stop_engaged=self._emergency.engaged,
            )
            decision = self._policy.decide(request, context)
            self._record_decision(request, decision)
            return decision

    # -- Emergency stop surface -------------------------------------------

    @property
    def emergency_stop_engaged(self) -> bool:
        """Whether the global emergency stop is engaged."""
        return self._emergency.engaged

    def register_canceller(self, canceller: OperationCanceller) -> None:
        """Register a subsystem whose active dangerous work the stop must reach."""
        self._emergency.register_canceller(canceller)

    def engage_emergency_stop(
        self, *, caller: CallerIdentity, reason: str
    ) -> EmergencyStopState:
        """Engage the global stop. Open to every caller kind.

        Disarms the runtime and drops this session's approvals: a stop that left
        an approval in place would let the next request re-arm cheaply, which is
        the opposite of what the operator asked for.
        """
        with self._lock:
            state = self._emergency.engage(caller=caller, reason=reason)
            self._approvals.clear()
            self._record_control(
                action="emergency_stop.engaged",
                caller=caller,
                reason_code=SafetyReason.EMERGENCY_STOP,
                message="The global emergency stop was engaged.",
                detail=_render(state.describe()),
                reason_digest=digest_reason(reason),
            )
            return state

    def release_emergency_stop(self, *, caller: CallerIdentity) -> EmergencyStopState:
        """Release the global stop.

        Raises:
            SafetyCallerError: ``caller`` is an Agent, script or automation rule.

        Releasing restores the *possibility* of dangerous work and nothing more:
        the runtime is left ``DISARMED`` and every approval is gone, so authority
        has to be re-established from scratch (invariant S8).
        """
        with self._lock:
            before = self._emergency.state
            state = self._emergency.reset(caller=caller)
            self._record_or_rollback(
                record=lambda: self._record_control(
                    action="emergency_stop.released",
                    caller=caller,
                    reason_code=SafetyReason.EMERGENCY_STOP,
                    message="The emergency stop was released.",
                    detail=None,
                ),
                rollback=lambda: self._emergency.restore_engagement(state=before),
            )
            return state

    # -- Audit surface ----------------------------------------------------

    def audit_events(self) -> tuple[SafetyAuditEvent, ...]:
        """Return the retained audit events, when the sink is the default one.

        Returns an empty tuple for a custom sink, because the kernel has no right
        to assume a sink can be read back — the trail belongs to whoever supplied
        it.
        """
        if isinstance(self._audit, InMemoryAuditSink):
            return self._audit.events()
        return ()

    # -- Internals --------------------------------------------------------

    @staticmethod
    def _require_arm_authority(caller: CallerIdentity) -> None:
        if not caller.may_control_arm:
            raise SafetyCallerError(
                "An Agent, script or automation rule may not control the arm state.",
                details={"caller": str(caller.kind), "name": caller.name},
            )

    def _disarm_for_emergency_stop(self) -> None:
        """Drop the arm state when the global stop engages.

        A named method rather than ``self._arm.disarm`` directly: the stop
        controller wants an action with no return value, and giving it the arm
        controller's own ``disarm`` would let it read the resulting state — which
        is authority it has no business holding.
        """
        self._arm.disarm()

    def _record_decision(self, request: OperationRequest, decision: PolicyDecision) -> None:
        arm_scope = self._arm.scope
        event = SafetyAuditEvent(
            event_id=uuid.uuid4().hex,
            recorded_at=self._clock(),
            caller_kind=str(request.caller.kind),
            caller_name=request.caller.name,
            operation_id=request.operation_id,
            operation_class=str(request.operation_class),
            risk_level=None if decision.risk_level is None else int(decision.risk_level),
            device_id=request.target.device_id,
            channel=request.target.channel,
            target_address=request.target.target_address,
            decision=decision.outcome.value,
            reason_code=decision.reason_code.value,
            message=decision.message,
            detail=None,
            approval_id=request.approval_id,
            parameters_digest=request.parameters_digest,
            reason_digest=None,
            arm_state=self._arm.state.value,
            arm_scope_expired=None if arm_scope is None else arm_scope.is_expired(self._clock()),
            emergency_stop_engaged=self._emergency.engaged,
            outcome="decision",
        )
        self._write(event, request.operation_id)

    def _record_control(
        self,
        *,
        action: str,
        caller: CallerIdentity,
        reason_code: SafetyReason,
        message: str,
        detail: str | None,
        reason_digest: str | None = None,
    ) -> None:
        """Record an authority-moving control action on the same trail.

        Control actions share the event shape with decisions on purpose: one
        trail that reads in order is what makes "who armed this, and when did the
        approval arrive?" answerable.

        ``message`` is **kernel text** — a fixed sentence per action. When an
        action carries an operator's reason, the reason is passed as
        ``reason_digest`` and the text itself is never written down (invariant
        S19). ``detail`` carries kernel-rendered coordinates, which are structured
        values this domain owns and never caller-supplied payloads.
        """
        event = SafetyAuditEvent(
            event_id=uuid.uuid4().hex,
            recorded_at=self._clock(),
            caller_kind=str(caller.kind),
            caller_name=caller.name,
            operation_id=f"kernel.{action}",
            operation_class="safety.control",
            risk_level=None,
            device_id=None,
            channel=None,
            target_address=None,
            decision="control",
            reason_code=reason_code.value,
            message=message,
            detail=detail,
            approval_id=None,
            parameters_digest=None,
            reason_digest=reason_digest,
            arm_state=self._arm.state.value,
            arm_scope_expired=None,
            emergency_stop_engaged=self._emergency.engaged,
            outcome="control",
        )
        self._write(event, f"kernel.{action}")

    def _write(self, event: SafetyAuditEvent, correlation: str) -> None:
        try:
            self._audit.record(event)
        except Exception as error:
            raise SafetyAuditError(
                "The safety decision could not be recorded; it is not handed back.",
                details={"operation_id": correlation},
            ) from error

    @staticmethod
    def _record_or_rollback(
        record: Callable[[], None],
        rollback: Callable[[], object],
    ) -> None:
        """Write the audit record, or undo the authority change and re-raise.

        The one place this discipline is expressed, so the four
        authority-increasing operations cannot drift into three that roll back
        and one that does not (invariant S17):

        > Audit failure may remove authority, but must never create, retain or
        > restore unaudited authority.

        Recording *before* the mutation would be the other way to satisfy the
        invariant, but it would write down an intention rather than a fact: the
        event's arm state, approval coordinates and emergency state all describe
        the world after the change. Committing first and rolling back keeps the
        record truthful and still leaves no unaudited authority.

        ``rollback`` is always a *reducing* action — disarm, revoke, restore an
        engagement — so a rollback can never itself create authority. That
        asymmetry is why the reducing operations on this class do not come
        through here: for them the authority is already gone, and undoing it
        would be the failure rather than the fix.
        """
        try:
            record()
        except SafetyAuditError:
            rollback()
            raise


def _render(payload: dict[str, object]) -> str:
    """Render kernel-owned coordinates into a bounded audit detail string.

    Only ever called with a ``describe()`` projection of a scope, an approval or
    an emergency state — values this domain constructed from declared fields.
    Caller-supplied payloads never reach here, which is what keeps the audit
    shape's no-free-text-for-secrets property intact (SAFETY-01 §20).
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
