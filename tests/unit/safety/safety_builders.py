"""Small builders shared by the SAFETY-01 safety tests.

These are *not* fixtures, and none of them produces an unauthorised shortcut.
Every builder goes through the same public constructors the runtime uses, so a
test that wants "an armed kernel with a CAN_TX permission" has to arm it and
grant it — the test cannot reach a state the runtime could not.

The caller constants exist so the cross-caller tests read as a matrix rather
than as five strings repeated eighteen times, and so a test that means "any
machine caller" can iterate :data:`MACHINE_CALLERS` instead of restating the
claim once per kind.
"""

from __future__ import annotations

from canx.safety.approval import Approval, ApprovalIssuer
from canx.safety.caller import CallerIdentity, CallerKind
from canx.safety.kernel import SafetyKernel
from canx.safety.operation import OperationRequest
from canx.safety.permission import PermissionGrant, PermissionSet
from canx.safety.risk import Capability, OperationClass
from canx.safety.scope import ArmScope, OperationTarget

OPERATOR = CallerIdentity(CallerKind.HUMAN_UI, "ui.main")
HOST = CallerIdentity(CallerKind.SYSTEM, "runtime.host")
AGENT = CallerIdentity(CallerKind.AGENT, "agent.session-1")
SCRIPT = CallerIdentity(CallerKind.SCRIPT, "script.cleanup")
AUTOMATION = CallerIdentity(CallerKind.AUTOMATION, "automation.rule-3")

#: The three caller kinds that are requesters and nothing more (invariant S4).
MACHINE_CALLERS = (AGENT, SCRIPT, AUTOMATION)

#: Every caller kind, for the matrix tests.
ALL_CALLERS = (OPERATOR, HOST, AGENT, SCRIPT, AUTOMATION)

#: A representative dangerous operation and a representative safe one.
DANGEROUS = OperationClass.BUS_TRANSMIT
SAFE = OperationClass.ENGINEERING_READ


class MovableClock:
    """A clock a test moves by hand.

    Expiry is a boundary, and a test that reached it by sleeping would be slow
    and would judge the boundary on whatever the machine happened to do. This
    clock makes "one microsecond before expiry" and "exactly at expiry" two
    distinct, reproducible instants.
    """

    def __init__(self, now: float = 1_000.0) -> None:
        self._now = now

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        """Move the clock forward and return the new instant."""
        self._now += seconds
        return self._now

    def set(self, now: float) -> float:
        """Jump the clock to an absolute instant."""
        self._now = now
        return self._now


def target(
    *,
    device_id: str | None = None,
    channel: str | None = None,
    target_address: int | None = None,
) -> OperationTarget:
    """Build an operation target from its coordinates."""
    return OperationTarget(
        device_id=device_id,
        channel=channel,
        target_address=target_address,
    )


def scope(
    *capabilities: Capability,
    target_: OperationTarget | None = None,
    granted_at: float = 1_000.0,
    expires_at: float = 1_060.0,
) -> ArmScope:
    """Build an arm scope."""
    return ArmScope(
        capabilities=frozenset(capabilities),
        target=target_ if target_ is not None else OperationTarget(),
        granted_at=granted_at,
        expires_at=expires_at,
    )


def approval(
    *,
    capability: Capability = Capability.CAN_TX,
    target_: OperationTarget | None = None,
    issued_at: float = 1_000.0,
    expires_at: float = 1_060.0,
    issuer: ApprovalIssuer = ApprovalIssuer.HUMAN_OPERATOR,
    single_use: bool = True,
    approval_id: str = "appr-1",
) -> Approval:
    """Build an approval."""
    return Approval(
        approval_id=approval_id,
        capability=capability,
        target=target_ if target_ is not None else OperationTarget(),
        issued_at=issued_at,
        expires_at=expires_at,
        issuer=issuer,
        single_use=single_use,
    )


def permissions(
    *capabilities: Capability,
    target_: OperationTarget | None = None,
    expires_at: float | None = None,
) -> PermissionSet:
    """Build a permission set holding one grant for ``capabilities``."""
    if not capabilities:
        return PermissionSet()
    return PermissionSet(
        [
            PermissionGrant(
                capabilities=frozenset(capabilities),
                target=target_ if target_ is not None else OperationTarget(),
                expires_at=expires_at,
            )
        ]
    )


def request(
    operation_class: OperationClass = DANGEROUS,
    *,
    caller: CallerIdentity = AGENT,
    target_: OperationTarget | None = None,
    operation_id: str = "op-1",
    requested_at: float = 1_000.0,
    approval_id: str | None = None,
) -> OperationRequest:
    """Build an operation request."""
    return OperationRequest(
        operation_id=operation_id,
        operation_class=operation_class,
        caller=caller,
        target=target_ if target_ is not None else OperationTarget(),
        requested_at=requested_at,
        approval_id=approval_id,
    )


def kernel(
    *,
    clock: MovableClock | None = None,
    permission_set: PermissionSet | None = None,
) -> SafetyKernel:
    """Build an unarmed kernel. Exactly what the runtime would start with."""
    return SafetyKernel(
        clock=clock if clock is not None else MovableClock(),
        permissions=permission_set,
    )


def armed_kernel(
    *,
    clock: MovableClock,
    capabilities: frozenset[Capability] = frozenset({Capability.CAN_TX}),
    target_: OperationTarget | None = None,
    duration: float = 60.0,
    permission_set: PermissionSet | None = None,
) -> SafetyKernel:
    """Build a kernel that has been armed by the operator, through the real API."""
    safety = kernel(clock=clock, permission_set=permission_set)
    safety.arm(
        scope(
            *capabilities,
            target_=target_,
            granted_at=clock(),
            expires_at=clock() + duration,
        ),
        caller=OPERATOR,
    )
    safety.confirm_arm(caller=OPERATOR)
    return safety
