"""Approval — an authority that can be spent, and never escalates.

An approval is not a stronger permission. It is a **narrow, expiring,
consumable** authorisation for one capability at one target, and the kernel
checks that narrowness rather than trusting the issuer to have been careful
(invariant S11):

```text
an approval for READ           cannot authorise ECU_MUTATION
an approval for target A       cannot authorise target B
an expired approval            cannot authorise anything
an approval outside ARM scope  cannot authorise anything
```

Three structural decisions carry most of that weight:

**An approval names exactly one capability.** It is a :class:`Capability`, not a
set, so there is no ordering of an approval's contents that could hand a caller
more than was granted.

**An approval is consumed, not consulted.** ``single_use`` approvals are marked
spent inside the critical section that validated them, so two callers racing for
the same approval cannot both win (invariant S11). A caller that loses the race
gets a diagnosable ``safety.approval_reused`` fault, not a second execution.

**Only an authority-bearing caller can grant one.** :meth:`ApprovalStore.grant`
refuses a grant from an Agent, a script or an automation rule. That refusal is
the structural answer to "can an Agent self-approve?" — the store has no path
that would accept it.

Approvals are in-memory by design in this stage. They are not persisted, not
serialised and not restored: a restart returns the runtime to ``DISARMED`` with
no approvals (invariant S8), and there is deliberately no ``save``/``load`` that
could make a pre-restart authorisation outlive the session that reasoned about
it.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from canx.safety.caller import CallerIdentity, CallerKind
from canx.safety.errors import (
    SafetyApprovalError,
    SafetyApprovalProvenanceError,
    SafetyApprovalReusedError,
    SafetyCallerError,
)
from canx.safety.risk import Capability
from canx.safety.scope import OperationTarget, has_lapsed


class ApprovalIssuer(StrEnum):
    """Who a stored approval came from.

    A closed two-member vocabulary is the point: there is no ``AGENT`` member
    for a self-approving caller to occupy, and there is no ``UNKNOWN`` member
    for a malformed record to fall into. Anything that is not one of these two
    cannot be represented, so it cannot be evaluated leniently later.

    A member here is a *derived* fact, never a declared one — see
    :func:`issuer_for` and :class:`ApprovalSpec`.
    """

    HUMAN_OPERATOR = "human.operator"
    HOST_SYSTEM = "host.system"


#: The provenance each authority-bearing caller kind implies. The mapping is the
#: only place an issuer is decided, so "who issued this approval?" has exactly
#: one answer and it comes from the caller, never from the payload (invariant
#: S16).
_ISSUER_BY_CALLER_KIND: Final[MappingProxyType[CallerKind, ApprovalIssuer]] = MappingProxyType(
    {
        CallerKind.HUMAN_UI: ApprovalIssuer.HUMAN_OPERATOR,
        CallerKind.SYSTEM: ApprovalIssuer.HOST_SYSTEM,
    }
)


def issuer_for(caller: CallerIdentity) -> ApprovalIssuer:
    """Return the approval provenance a caller's identity implies.

    Raises:
        SafetyApprovalProvenanceError: the caller kind carries no provenance —
            an Agent, a script or an automation rule. There is no default to
            fall back to: an approval whose source cannot be named is an
            approval nobody can attribute.
    """
    try:
        return _ISSUER_BY_CALLER_KIND[caller.kind]
    except KeyError as error:
        raise SafetyApprovalProvenanceError(
            "This caller kind cannot supply approval provenance.",
            details={"caller": str(caller.kind), "name": caller.name},
        ) from error


def _validate_approval_fields(
    *,
    approval_id: str,
    capability: Capability,
    issued_at: float,
    expires_at: float,
) -> None:
    """Validate the fields an approval and an approval spec both must satisfy.

    Shared so the two shapes cannot drift: a spec that accepted a window the
    approval would reject would make the kernel's translation the only place the
    difference showed up.
    """
    if not approval_id:
        raise SafetyApprovalError(
            "An approval must carry a non-empty identifier.",
            details={},
        )
    if not (math.isfinite(issued_at) and math.isfinite(expires_at)):
        raise SafetyApprovalError(
            "An approval must be bounded by finite timestamps.",
            details={"issued_at": issued_at, "expires_at": expires_at},
        )
    if expires_at <= issued_at:
        raise SafetyApprovalError(
            "An approval must expire after the moment it was issued.",
            details={"issued_at": issued_at, "expires_at": expires_at},
        )
    if not isinstance(capability, Capability):
        raise SafetyApprovalError(
            "The approval capability is not part of the CAN-X capability vocabulary.",
            details={"capability": str(capability)},
        )


@dataclass(frozen=True, slots=True)
class ApprovalSpec:
    """What a caller asks to have approved.

    Deliberately **without** an ``issuer`` field. A caller describes what it
    wants authorised — a capability, a target, a window — and has no way to say
    who it is in that description. The kernel derives the provenance from the
    identity it was handed, which is the identity the rest of the runtime
    already trusts for arm control and audit attribution.

    The absence of the field is the control. A parameter that does not exist
    cannot be forged, which is a stronger guarantee than a validator that is
    supposed to catch a forged one.
    """

    approval_id: str
    capability: Capability
    target: OperationTarget
    issued_at: float
    expires_at: float
    single_use: bool = True

    def __post_init__(self) -> None:
        _validate_approval_fields(
            approval_id=self.approval_id,
            capability=self.capability,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
        )


@dataclass(frozen=True, slots=True)
class Approval:
    """A narrow, expiring authorisation for one capability at one target.

    Built by the kernel from an :class:`ApprovalSpec` and a trusted caller
    identity, or supplied directly to :meth:`ApprovalStore.grant`, which checks
    the issuer against the grantor. It is never accepted on the strength of its
    own ``issuer`` field (invariant S16).
    """

    approval_id: str
    capability: Capability
    target: OperationTarget
    issued_at: float
    expires_at: float
    issuer: ApprovalIssuer
    single_use: bool = True

    def __post_init__(self) -> None:
        _validate_approval_fields(
            approval_id=self.approval_id,
            capability=self.capability,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
        )
        if not isinstance(self.issuer, ApprovalIssuer):
            raise SafetyApprovalError(
                "The approval issuer is not part of the CAN-X approval vocabulary.",
                details={"issuer": type(self.issuer).__name__},
            )

    def is_expired(self, now: float) -> bool:
        """Whether this approval has lapsed at ``now``.

        A non-finite ``now`` reads as expired, never as "still valid": see
        :func:`canx.safety.scope.has_lapsed`.
        """
        return has_lapsed(now, self.expires_at)

    def covers(self, capability: Capability, target: OperationTarget) -> bool:
        """Whether this approval authorises ``capability`` at ``target``.

        Capability equality is exact, never "at least": an approval for a
        lower-risk capability cannot widen into a higher-risk one, and an
        approval for a higher-risk capability is not silently downgraded into a
        licence for a lower-risk one either — each operation gets the approval
        that names it (invariant S11).
        """
        return self.capability == capability and self.target.covers(target)

    def exactly_matches(self, capability: Capability, target: OperationTarget) -> bool:
        """Whether this approval names precisely ``capability`` at exactly ``target``.

        Stricter than :meth:`covers`: the stored target must name every
        coordinate the request names *and* name no coordinate the request leaves
        blank. High-assurance approvals use this, so an approval bought for
        ``channel="can1"`` cannot be spent on a request that never said which
        channel it meant.
        """
        return self.capability == capability and self.target == target

    def describe(self) -> dict[str, object]:
        """Return the audit-safe shape of this approval.

        No secret can appear here: the fields are an identifier, a capability, a
        target and two timestamps. There is no free-text field for a key, a
        seed, a token or a security-access payload to occupy (SAFETY-01 §20).
        """
        return {
            "approval_id": self.approval_id,
            "capability": self.capability.value,
            "target": self.target.describe(),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "issuer": self.issuer.value,
            "single_use": self.single_use,
        }


@dataclass(slots=True)
class _StoredApproval:
    """Mutable bookkeeping wrapped around an immutable approval."""

    approval: Approval
    consumed: bool = False


class ApprovalStore:
    """In-memory, session-scoped approval record with atomic consumption.

    Thread-safe: the runtime may receive a UI request, an Agent request and an
    automation request concurrently, and a check-then-use race on a single-use
    approval would be an authority granted twice. Validation and consumption
    therefore happen inside one critical section — a caller passing a
    :meth:`consume` check that runs *outside* the lock would reintroduce exactly
    the race this store exists to close (SAFETY-01 §25).
    """

    __slots__ = ("_approvals", "_lock")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._approvals: dict[str, _StoredApproval] = {}

    def grant(self, approval: Approval, *, granted_by: CallerIdentity) -> None:
        """Record an approval supplied by ``granted_by``.

        The approval's own ``issuer`` must be the provenance ``granted_by``
        implies. Checking only that the grantor *may* issue approvals was the
        original defect: the host system may issue approvals, so a host-supplied
        approval labelled ``HUMAN_OPERATOR`` was stored as a human one and went
        on to satisfy ``human_issuer_required`` (invariant S16).

        Raises:
            SafetyCallerError: ``granted_by`` is an Agent, a script or an
                automation rule. A machine caller cannot supply an approval, so
                the absence of a self-approval path is enforced here rather than
                assumed from policy.
            SafetyApprovalProvenanceError: the declared issuer is not the
                provenance of ``granted_by``.
        """
        if not granted_by.may_issue_approval:
            raise SafetyCallerError(
                "Only a human operator or the host system may issue an approval.",
                details={"caller": str(granted_by.kind), "name": granted_by.name},
            )
        expected = issuer_for(granted_by)
        if approval.issuer is not expected:
            raise SafetyApprovalProvenanceError(
                "The approval's declared issuer is not the provenance of the caller "
                "supplying it.",
                details={
                    "caller": str(granted_by.kind),
                    "declared": approval.issuer.value,
                    "expected": expected.value,
                },
            )
        with self._lock:
            self._approvals[approval.approval_id] = _StoredApproval(approval)

    def lookup(self, approval_id: str) -> Approval:
        """Return a stored approval without consuming it.

        Raises:
            SafetyApprovalError: ``approval_id`` is not a reference this store
                holds — including the case where it is empty, malformed or names
                an approval issued by a since-restarted process. An unresolvable
                reference fails closed; it never degrades into "no approval
                required".
        """
        with self._lock:
            record = self._approvals.get(approval_id)
            if record is None:
                raise SafetyApprovalError(
                    "The approval reference is not known to this runtime session.",
                    details={"approval_id": approval_id},
                )
            return record.approval

    def consume(
        self,
        approval_id: str,
        *,
        check: Callable[[Approval], None] | None = None,
    ) -> Approval:
        """Atomically resolve, validate and spend an approval.

        Args:
            approval_id: The reference carried by the operation request.
            check: Re-validates the approval *inside* the critical section. It
                raises a :class:`SafetyError` to refuse; the refusal leaves the
                approval unspent, because an approval rejected for one operation
                may legitimately be spent on the one it was issued for.

        Returns:
            The approval that was spent.

        Raises:
            SafetyApprovalError: The reference is unknown.
            SafetyApprovalReusedError: A single-use approval is already spent.
            SafetyError: Whatever ``check`` raised.

        The order is deliberate: validity is judged before consumption, and
        consumption is judged before the approval is handed back. A check that
        ran before the lock would be a check-then-use race, and a consumption
        that happened before the check would burn approvals that were never
        usable.
        """
        with self._lock:
            record = self._approvals.get(approval_id)
            if record is None:
                raise SafetyApprovalError(
                    "The approval reference is not known to this runtime session.",
                    details={"approval_id": approval_id},
                )
            if check is not None:
                check(record.approval)
            if record.consumed:
                raise SafetyApprovalReusedError(
                    "This approval has already been consumed.",
                    details={"approval_id": approval_id},
                )
            if record.approval.single_use:
                record.consumed = True
            return record.approval

    def revoke(self, approval_id: str) -> bool:
        """Drop an approval. Returns whether one was held.

        Used by the emergency-stop path and by permission invalidation: losing an
        approval can never make a runtime more dangerous, so this is the one
        mutation that needs no authority check.
        """
        with self._lock:
            return self._approvals.pop(approval_id, None) is not None

    def clear(self) -> None:
        """Drop every approval held in this session."""
        with self._lock:
            self._approvals.clear()

    def outstanding(self) -> tuple[Approval, ...]:
        """Return every unspent approval, for diagnostics and tests."""
        with self._lock:
            return tuple(
                record.approval for record in self._approvals.values() if not record.consumed
            )
