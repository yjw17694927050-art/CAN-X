"""The policy core — a deterministic function from (request, authority) to verdict.

The engine is deliberately pure in the sense AGENTS.md and SAFETY-01 §24 ask for:

```text
Operation  +  Safety Context  +  Policy  =  Decision
```

It opens no device, calls no UI, reaches no network, sleeps nowhere and reads no
filesystem. Time enters as ``context.now`` rather than from a clock call inside
a branch, so a decision is reproducible from its inputs — a property the tests
depend on and an incident review will depend on later.

The decision chain is the one SAFETY-01 §4 fixes:

```text
classify risk → validate caller → validate target/scope → validate ARM
→ validate permission → validate approval → validate limits → ALLOW / DENY
```

and any failure inside it is a ``DENY`` with a diagnosable reason. Nothing in
this module can raise its way out into an ``ALLOW``: the outermost handler turns
an unexpected fault into ``safety.policy_failure`` (invariant S9). The one
refusal that is deliberately *not* a decision here is a consumed approval that
cannot be recorded — the kernel turns an unrecordable decision into a fault,
because a verdict nobody can audit is not a verdict (invariant S10).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from canx.safety.approval import Approval, ApprovalIssuer, ApprovalStore
from canx.safety.arm import ArmState
from canx.safety.caller import CallerKind
from canx.safety.decision import PolicyDecision, SafetyReason, allow, deny
from canx.safety.errors import (
    SafetyApprovalError,
    SafetyApprovalExpiredError,
    SafetyApprovalReusedError,
    SafetyScopeError,
    SafetyUnknownOperationError,
)
from canx.safety.operation import OperationRequest
from canx.safety.permission import PermissionSet
from canx.safety.risk import Capability, RiskLevel, classify_operation, required_capability
from canx.safety.scope import ArmScope


@dataclass(frozen=True, slots=True)
class ApprovalRequirement:
    """How much approval a risk level demands.

    Four independent booleans rather than an ordinal severity scale, because the
    requirements are genuinely different *kinds* of demand, not points on one
    line: ``exact_target_required`` is about precision, ``human_issuer_required``
    is about who, and ``single_use_required`` is about how many times. A scale
    would force a later reader to guess which of them "stronger" upgraded.
    """

    required: bool
    single_use_required: bool
    exact_target_required: bool
    human_issuer_required: bool

    def describe(self) -> dict[str, object]:
        """Return the audit-safe shape of this requirement."""
        return {
            "required": self.required,
            "single_use_required": self.single_use_required,
            "exact_target_required": self.exact_target_required,
            "human_issuer_required": self.human_issuer_required,
        }


#: The default approval model (SAFETY-01 §14). Frozen as data so it can be read
#: in one place and reviewed as a table rather than reconstructed from branches.
#:
#: ``READ`` / ``COMPUTE`` / ``WRITE_PROJECT`` need no approval at all. ``TX``
#: needs one but may reuse it until it expires, because a periodic transmit is a
#: single authorised intent that lasts — and its reach is bounded by the ARM
#: scope's own expiry instead. Everything that mutates the vehicle needs a
#: single-use approval; the two most consequential classes additionally require
#: that a human, not the host, issued it.
_NO_APPROVAL: Final[ApprovalRequirement] = ApprovalRequirement(
    required=False,
    single_use_required=False,
    exact_target_required=False,
    human_issuer_required=False,
)

_DEFAULT_APPROVAL_REQUIREMENTS: Final[MappingProxyType[RiskLevel, ApprovalRequirement]] = (
    MappingProxyType(
        {
            RiskLevel.READ: _NO_APPROVAL,
            RiskLevel.COMPUTE: _NO_APPROVAL,
            RiskLevel.WRITE_PROJECT: _NO_APPROVAL,
            RiskLevel.TX: ApprovalRequirement(
                required=True,
                single_use_required=False,
                exact_target_required=True,
                human_issuer_required=False,
            ),
            RiskLevel.DIAGNOSTIC_MUTATION: ApprovalRequirement(
                required=True,
                single_use_required=True,
                exact_target_required=True,
                human_issuer_required=False,
            ),
            RiskLevel.ACTUATION: ApprovalRequirement(
                required=True,
                single_use_required=True,
                exact_target_required=True,
                human_issuer_required=True,
            ),
            RiskLevel.ECU_MUTATION: ApprovalRequirement(
                required=True,
                single_use_required=True,
                exact_target_required=True,
                human_issuer_required=True,
            ),
            RiskLevel.CRITICAL: ApprovalRequirement(
                required=True,
                single_use_required=True,
                exact_target_required=True,
                human_issuer_required=True,
            ),
        }
    )
)


@dataclass(frozen=True, slots=True)
class SafetyContext:
    """Everything the policy is allowed to consult, captured at one instant.

    Assembled by the kernel and passed in whole so that the engine has no second
    source of truth. ``arm_state`` and ``arm_scope`` are both present because
    they answer different questions — "which state is the machine in?" and "does
    it hold a live authority?" — and a rule that needed one and read the other
    would be reading a stale answer.
    """

    now: float
    arm_state: ArmState
    arm_scope: ArmScope | None
    permissions: PermissionSet
    approvals: ApprovalStore
    emergency_stop_engaged: bool


class SafetyPolicy:
    """The deterministic policy core.

    Constructed once per runtime and never mutated while decisions are being
    taken: a policy that could change mid-flight would make two identical
    requests answer differently for reasons no audit event records.
    """

    __slots__ = ("_requirements",)

    def __init__(
        self, *, requirements: Mapping[RiskLevel, ApprovalRequirement] | None = None
    ) -> None:
        self._requirements: dict[RiskLevel, ApprovalRequirement] = dict(
            _DEFAULT_APPROVAL_REQUIREMENTS if requirements is None else requirements
        )

    def approval_requirement(self, risk: RiskLevel) -> ApprovalRequirement:
        """Return the approval demand for a risk level.

        An unlisted level falls back to the strictest possible demand rather
        than to "no approval": a level the table does not mention is one nobody
        reasoned about, and the safe reading of an unreasoned level is that it
        needs everything.
        """
        return self._requirements.get(
            risk,
            ApprovalRequirement(
                required=True,
                single_use_required=True,
                exact_target_required=True,
                human_issuer_required=True,
            ),
        )

    def decide(self, request: OperationRequest, context: SafetyContext) -> PolicyDecision:
        """Return the verdict for ``request`` under ``context``.

        Never raises for an ordinary refusal, and never returns ``ALLOW`` out of
        an internal fault. The only exceptions that may escape are the ones
        :meth:`canx.safety.kernel.SafetyKernel.evaluate` raises about the trail,
        which happen above this method.
        """
        try:
            return self._decide(request, context)
        except _Refusal as refusal:
            return refusal.decision
        except Exception:
            # Fail closed (invariant S9). An engine that cannot finish its chain
            # has not answered the question, and an unanswered question is a no.
            return deny(
                SafetyReason.POLICY_FAILURE,
                "The safety policy could not complete its evaluation; the operation is refused.",
            )

    def _decide(self, request: OperationRequest, context: SafetyContext) -> PolicyDecision:
        """Run the decision chain, refusing by raising :class:`_Refusal`.

        Refusals are raised rather than returned so that each rule reads as a
        guard clause whose condition is the thing being refused, and so that no
        rule can fall through into an ``ALLOW`` by forgetting a return. The
        single ``ALLOW`` is the last statement of the chain.
        """
        risk = self._classify(request)
        self._require_known_caller(request)
        capability = required_capability(risk)

        if not risk.is_dangerous:
            # The three levels below the boundary are authorised by their
            # capability alone. No arm state, no approval: they observe the
            # project or compute from it.
            self._require_permission(capability, risk, request, context)
            return allow(risk_level=risk)

        # Emergency stop comes before the arm state on purpose. A stop that has
        # engaged has already disarmed, so the arm check would refuse too — but
        # it would refuse with "not armed", which names the wrong cause and
        # would send an operator looking at the arm state instead of at the stop
        # they pulled.
        if context.emergency_stop_engaged:
            raise _Refusal(
                deny(
                    SafetyReason.EMERGENCY_STOP,
                    "The global emergency stop is engaged; no dangerous operation is authorised.",
                    risk_level=risk,
                    required_conditions=("emergency_stop_released",),
                )
            )

        self._require_arm(capability, risk, request, context)
        self._require_permission(capability, risk, request, context)
        self._require_approval(capability, risk, request, context)
        return allow(risk_level=risk)

    # -- Chain rules ------------------------------------------------------

    @staticmethod
    def _classify(request: OperationRequest) -> RiskLevel:
        """Return the operation's risk level, refusing an unclassifiable one."""
        try:
            return classify_operation(request.operation_class)
        except SafetyUnknownOperationError as error:
            raise _Refusal(
                deny(
                    SafetyReason.UNKNOWN_OPERATION,
                    "The operation is not part of the CAN-X risk taxonomy; it is refused.",
                    required_conditions=("known_operation_class",),
                )
            ) from error

    @staticmethod
    def _require_known_caller(request: OperationRequest) -> None:
        """Refuse a caller kind outside the vocabulary.

        :class:`~canx.safety.caller.CallerIdentity` already refuses to be
        constructed with an unknown kind, so this should be unreachable. It is
        kept as the second lock on that door: a caller object built by some
        future path that skips validation must not be read as "some caller",
        and the cost of the check is one membership test.
        """
        try:
            CallerKind(request.caller.kind)
        except ValueError as error:
            raise _Refusal(
                deny(
                    SafetyReason.CALLER_NOT_PERMITTED,
                    "The caller kind is not part of the CAN-X caller vocabulary.",
                    required_conditions=("known_caller_kind",),
                )
            ) from error

    @staticmethod
    def _require_arm(
        capability: Capability,
        risk: RiskLevel,
        request: OperationRequest,
        context: SafetyContext,
    ) -> None:
        """Refuse unless the runtime is armed and its scope reaches this operation."""
        if context.arm_state is not ArmState.ARMED or context.arm_scope is None:
            # ``arm_scope`` is None when the state is not ARMED *or* when the
            # scope has lapsed. Both mean the same thing to a caller: there is
            # no live authority, so there is nothing to exceed.
            raise _Refusal(
                deny(
                    SafetyReason.NOT_ARMED,
                    "The runtime is not armed for this operation.",
                    risk_level=risk,
                    required_conditions=("runtime_armed",),
                )
            )
        if not context.arm_scope.covers(capability, request.target, context.now):
            raise _Refusal(
                deny(
                    SafetyReason.SCOPE_VIOLATION,
                    "The arm scope does not cover this capability at this target.",
                    risk_level=risk,
                    required_conditions=("arm_scope_covers_operation",),
                )
            )

    @staticmethod
    def _require_permission(
        capability: Capability,
        risk: RiskLevel,
        request: OperationRequest,
        context: SafetyContext,
    ) -> None:
        """Refuse unless the session holds a capability grant that reaches the target."""
        if not context.permissions.covers(capability, request.target, context.now):
            raise _Refusal(
                deny(
                    SafetyReason.PERMISSION_DENIED,
                    f"The session holds no {capability.value} grant for this target.",
                    risk_level=risk,
                    required_conditions=(f"permission:{capability.value}",),
                )
            )

    def _require_approval(
        self,
        capability: Capability,
        risk: RiskLevel,
        request: OperationRequest,
        context: SafetyContext,
    ) -> None:
        """Refuse unless a suitable approval is presented, and consume it."""
        requirement = self.approval_requirement(risk)
        if not requirement.required:
            return
        if request.approval_id is None:
            raise _Refusal(
                deny(
                    SafetyReason.APPROVAL_REQUIRED,
                    "This operation requires an approval and none was presented.",
                    risk_level=risk,
                    required_conditions=("approval",),
                )
            )
        try:
            context.approvals.consume(
                request.approval_id,
                check=self._approval_check(capability, requirement, request, context),
            )
        except SafetyApprovalExpiredError as error:
            # Most specific first: both of these are SafetyApprovalError
            # subclasses, and collapsing them into the generic refusal below
            # would hide *why* the approval was unusable from the operator who
            # has to decide whether to issue another one.
            raise _Refusal(
                deny(
                    SafetyReason.APPROVAL_EXPIRED,
                    "The approval expired before this operation was requested.",
                    risk_level=risk,
                    required_conditions=("approval_valid",),
                )
            ) from error
        except SafetyApprovalReusedError as error:
            raise _Refusal(
                deny(
                    SafetyReason.APPROVAL_REUSED,
                    "This approval has already been consumed by an earlier operation.",
                    risk_level=risk,
                    required_conditions=("unspent_approval",),
                )
            ) from error
        except SafetyScopeError as error:
            raise _Refusal(
                deny(
                    SafetyReason.SCOPE_VIOLATION,
                    "The approval does not cover exactly this operation's target.",
                    risk_level=risk,
                    required_conditions=("approval_covers_operation",),
                )
            ) from error
        except SafetyApprovalError as error:
            # Covers "no such reference" as well as a wrong-kind or non-conforming
            # approval. An unresolvable reference fails closed — it never degrades
            # into "no approval was required".
            raise _Refusal(
                deny(
                    SafetyReason.APPROVAL_INVALID,
                    "The presented approval does not authorise this operation.",
                    risk_level=risk,
                    required_conditions=("approval_valid",),
                )
            ) from error

    @staticmethod
    def _approval_check(
        capability: Capability,
        requirement: ApprovalRequirement,
        request: OperationRequest,
        context: SafetyContext,
    ) -> Callable[[Approval], None]:
        """Build the validator run inside the approval store's critical section.

        Returning a closure rather than validating before the call is what makes
        single-use consumption race-free: the store runs this between resolving
        the approval and deciding whether to spend it (SAFETY-01 §25).

        The order of the checks is the order of specificity. Capability equality
        is asked before target precision so that "this is a READ approval
        presented for a transmit" is reported as an invalid approval rather than
        as a scope problem — the caller needs to know the approval is of the
        wrong kind, not that it was aimed badly.
        """

        def check(candidate: Approval) -> None:
            if candidate.is_expired(context.now):
                raise SafetyApprovalExpiredError(
                    "The approval expired before this operation was requested.",
                    details={"approval_id": candidate.approval_id},
                )
            if candidate.capability != capability:
                raise SafetyApprovalError(
                    "The approval authorises a different capability than this operation needs.",
                    details={
                        "granted": candidate.capability.value,
                        "required": capability.value,
                    },
                )
            if requirement.exact_target_required:
                if not candidate.exactly_matches(capability, request.target):
                    raise SafetyScopeError(
                        "The approval does not name exactly the target this operation uses.",
                        details={"approval_id": candidate.approval_id},
                    )
            elif not candidate.covers(capability, request.target):
                raise SafetyScopeError(
                    "The approval does not cover this operation's target.",
                    details={"approval_id": candidate.approval_id},
                )
            if requirement.single_use_required and not candidate.single_use:
                raise SafetyApprovalError(
                    "This operation requires a single-use approval.",
                    details={"approval_id": candidate.approval_id},
                )
            if (
                requirement.human_issuer_required
                and candidate.issuer is not ApprovalIssuer.HUMAN_OPERATOR
            ):
                raise SafetyApprovalError(
                    "This operation requires an approval issued by a human operator.",
                    details={
                        "approval_id": candidate.approval_id,
                        "issuer": candidate.issuer.value,
                    },
                )
            scope = context.arm_scope
            if scope is not None and not scope.target.covers(candidate.target):
                raise SafetyScopeError(
                    "The approval reaches outside the current arm scope.",
                    details={"approval_id": candidate.approval_id},
                )

        return check


class _Refusal(Exception):
    """Internal control flow: a verdict already decided."""

    __slots__ = ("decision",)

    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(decision.reason_code)
        self.decision = decision


def approval_requirement_for(risk: RiskLevel) -> ApprovalRequirement:
    """Return the default approval demand for a risk level.

    A module-level convenience over the default table, so a caller that has no
    policy instance — a test, a report, a future UI that explains what an
    operation would cost — reads the same numbers the engine uses.
    """
    return _DEFAULT_APPROVAL_REQUIREMENTS.get(
        risk,
        ApprovalRequirement(
            required=True,
            single_use_required=True,
            exact_target_required=True,
            human_issuer_required=True,
        ),
    )
