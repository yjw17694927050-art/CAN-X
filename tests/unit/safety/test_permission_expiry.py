"""A grant that opens a dangerous capability must be bounded by an expiry.

The defect this file pins down (SAFETY-01-FIX-1, P1-2) is a contract that was
described and never enforced:

```text
PermissionGrant.expires_at             nullable
PermissionGrant docstring              "deliberately not optional for the
                                        dangerous capabilities ... policy
                                        enforces that distinction"
SafetyPolicy                           had no such check
```

So the documentation asserted a rule the code did not hold, and a
``CAN_TX`` grant could exist with ``expires_at=None`` — an authority to transmit
that outlives the reason it was issued, for as long as the process runs.

The rule (invariant S18):

> A permission grant that opens any dangerous capability must carry a finite
> expiry. Safe capabilities may last as long as the session.

It is enforced in :class:`~canx.safety.permission.PermissionGrant` rather than in
policy, because a grant that cannot be constructed cannot be handed to a policy
that might forget to check it. A rule that lives only in the consumer is a rule
the next consumer does not have.
"""

from __future__ import annotations

import math

import pytest
from canx.safety.decision import SafetyReason
from canx.safety.errors import SafetyScopeError
from canx.safety.permission import PermissionGrant, PermissionSet
from canx.safety.risk import DANGEROUS_CAPABILITIES, Capability, OperationClass
from canx.safety.scope import OperationTarget
from safety_builders import OPERATOR, MovableClock, armed_kernel, request

TARGET = OperationTarget()


def bounded(capabilities: set[Capability], *, expires_at: float) -> PermissionSet:
    """A session holding exactly one bounded grant."""
    return PermissionSet(
        [PermissionGrant(capabilities=frozenset(capabilities), expires_at=expires_at)]
    )


# -- Safe capabilities may last as long as the session -------------------------


def test_a_safe_grant_may_have_no_expiry() -> None:
    grant = PermissionGrant(capabilities=frozenset({Capability.READ}), expires_at=None)
    assert grant.expires_at is None
    assert grant.covers(Capability.READ, TARGET, 0.0) is True


@pytest.mark.parametrize(
    "capability",
    [Capability.READ, Capability.COMPUTE, Capability.WRITE_PROJECT],
)
def test_every_safe_capability_may_have_no_expiry(capability: Capability) -> None:
    grant = PermissionGrant(capabilities=frozenset({capability}), expires_at=None)
    assert grant.covers(capability, TARGET, 0.0) is True


# -- Dangerous capabilities must be bounded -----------------------------------


@pytest.mark.parametrize(
    "capability", sorted(DANGEROUS_CAPABILITIES, key=lambda candidate: candidate.value)
)
def test_a_dangerous_grant_without_an_expiry_is_refused(capability: Capability) -> None:
    with pytest.raises(SafetyScopeError):
        PermissionGrant(capabilities=frozenset({capability}), expires_at=None)


def test_a_mixed_grant_is_dangerous_as_a_whole() -> None:
    """One dangerous capability bounds the grant that carries it.

    ``READ + CAN_TX`` is not a read grant with a bonus: it is a transmit grant,
    and the caller that wants an unbounded read grant should hold a separate one.
    """
    with pytest.raises(SafetyScopeError):
        PermissionGrant(
            capabilities=frozenset({Capability.READ, Capability.CAN_TX}), expires_at=None
        )


@pytest.mark.parametrize("expiry", [math.nan, math.inf, -math.inf])
def test_a_non_finite_dangerous_expiry_is_refused(expiry: float) -> None:
    with pytest.raises(SafetyScopeError):
        PermissionGrant(capabilities=frozenset({Capability.CAN_TX}), expires_at=expiry)


def test_a_bounded_dangerous_grant_is_usable_and_then_lapses() -> None:
    grant = PermissionGrant(capabilities=frozenset({Capability.CAN_TX}), expires_at=100.0)
    assert grant.covers(Capability.CAN_TX, TARGET, 99.999) is True
    assert grant.covers(Capability.CAN_TX, TARGET, 100.0) is False


def test_an_expired_dangerous_grant_denies_the_operation() -> None:
    """The consequence: a lapsed transmit grant stops authorising transmit.

    ``diagnostic.read`` is used rather than ``bus.transmit`` because it needs
    ``CAN_TX`` and no approval, so the refusal can only be the lapsed grant —
    the variable under test is isolated.
    """
    clock = MovableClock()
    safety = armed_kernel(
        clock=clock,
        capabilities=frozenset({Capability.CAN_TX}),
        duration=600.0,
        permission_set=bounded(
            {Capability.READ, Capability.CAN_TX}, expires_at=clock() + 10
        ),
    )
    assert safety.evaluate(request(OperationClass.DIAGNOSTIC_READ, caller=OPERATOR)).allowed
    clock.advance(10)
    decision = safety.evaluate(request(OperationClass.DIAGNOSTIC_READ, caller=OPERATOR))
    assert decision.denied
    assert decision.reason_code is SafetyReason.PERMISSION_DENIED


def test_a_safe_grant_is_not_forced_to_expire() -> None:
    """The rule must not make ordinary reading cost more than it needs to."""
    safety = armed_kernel(
        clock=MovableClock(),
        capabilities=frozenset({Capability.CAN_TX}),
        duration=600.0,
        permission_set=PermissionSet(
            [PermissionGrant(capabilities=frozenset({Capability.READ}))]
        ),
    )
    assert safety.evaluate(request(OperationClass.ENGINEERING_READ, caller=OPERATOR)).allowed
