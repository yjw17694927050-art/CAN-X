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

from canx.safety.caller import CallerIdentity
from canx.safety.errors import (
    SafetyApprovalError,
    SafetyApprovalReusedError,
    SafetyCallerError,
)
from canx.safety.risk import Capability
from canx.safety.scope import OperationTarget, has_lapsed


class ApprovalIssuer(StrEnum):
    """Who a stored approval claims to come from.

    A closed two-member vocabulary is the point: there is no ``AGENT`` member
    for a self-approving caller to occupy, and there is no ``UNKNOWN`` member
    for a malformed record to fall into. Anything that is not one of these two
    cannot be represented, so it cannot be evaluated leniently later.
    """

    HUMAN_OPERATOR = "human.operator"
    HOST_SYSTEM = "host.system"


@dataclass(frozen=True, slots=True)
class Approval:
    """A narrow, expiring authorisation for one capability at one target."""

    approval_id: str
    capability: Capability
    target: OperationTarget
    issued_at: float
    expires_at: float
    issuer: ApprovalIssuer
    single_use: bool = True

    def __post_init__(self) -> None:
        if not self.approval_id:
            raise SafetyApprovalError(
                "An approval must carry a non-empty identifier.",
                details={},
            )
        if not (math.isfinite(self.issued_at) and math.isfinite(self.expires_at)):
            raise SafetyApprovalError(
                "An approval must be bounded by finite timestamps.",
                details={"issued_at": self.issued_at, "expires_at": self.expires_at},
            )
        if self.expires_at <= self.issued_at:
            raise SafetyApprovalError(
                "An approval must expire after the moment it was issued.",
                details={"issued_at": self.issued_at, "expires_at": self.expires_at},
            )
        if not isinstance(self.issuer, ApprovalIssuer):
            raise SafetyApprovalError(
                "The approval issuer is not part of the CAN-X approval vocabulary.",
                details={"issuer": type(self.issuer).__name__},
            )
        if not isinstance(self.capability, Capability):
            raise SafetyApprovalError(
                "The approval capability is not part of the CAN-X capability vocabulary.",
                details={"capability": str(self.capability)},
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

        Raises:
            SafetyCallerError: ``granted_by`` is an Agent, a script or an
                automation rule. A machine caller cannot supply an approval, so
                the absence of a self-approval path is enforced here rather than
                assumed from policy.
        """
        if not granted_by.may_issue_approval:
            raise SafetyCallerError(
                "Only a human operator or the host system may issue an approval.",
                details={"caller": str(granted_by.kind), "name": granted_by.name},
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
