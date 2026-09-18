"""The ARM state machine — a scoped, expiring claim, never a boolean.

CAN-X has three arm states and no fourth:

```text
             request()              confirm()
DISARMED  ───────────────▶ ARMING  ───────────▶ ARMED
    ▲                         │                    │
    └───────── disarm() ──────┴────────────────────┘
```

Two properties are load-bearing and neither is expressible with ``armed = True``
(invariant S7):

**ARMING is a real state.** Arming is a two-step act — a request, then a
confirmation — and the gap between them is a state in which the runtime is
*not* armed. A dangerous operation during ``ARMING`` is refused exactly as it
would be during ``DISARMED``. Collapsing the two steps would make the
confirmation decorative.

**Scope is part of the state.** The controller holds the :class:`ArmScope`
alongside the state, and :meth:`ArmController.active_scope` returns it only
while the runtime is ``ARMED`` *and* the scope has not lapsed. An expired scope
is not reported as armed-with-an-old-scope; it is reported as no authority at
all, which is what it is.

Every transition is explicit and the machine has no tacit edges — most
importantly no ``DISARMED → ARMED``. Any transition outside the diagram raises
:class:`canx.safety.errors.SafetyStateError` with code
``safety.invalid_transition`` rather than being silently ignored, because a
caller whose arm attempt was dropped without a fault would believe the runtime
was armed when it was not.

All state is behind one lock. The runtime may receive requests from the UI, an
Agent, a script and an automation rule concurrently, and a stale read of ARM
state is a dangerous operation authorised against a state that had already
changed (SAFETY-01 §25).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from canx.safety.errors import SafetyScopeError, SafetyStateError
from canx.safety.scope import ArmScope


class ArmState(StrEnum):
    """The three states of the CAN-X arm machine."""

    DISARMED = "disarmed"
    ARMING = "arming"
    ARMED = "armed"


#: The complete transition table. A state's entry lists the states it may move
#: to; anything absent is not a transition the machine has. Written as data so
#: the legal graph can be read at a glance and diffed in review, rather than
#: reconstructed from the branches of a method.
_TRANSITIONS: Final[MappingProxyType[ArmState, frozenset[ArmState]]] = MappingProxyType(
    {
        ArmState.DISARMED: frozenset({ArmState.ARMING}),
        ArmState.ARMING: frozenset({ArmState.ARMED, ArmState.DISARMED}),
        ArmState.ARMED: frozenset({ArmState.DISARMED}),
    }
)


class ArmController:
    """Owns the arm state and the scope that accompanies it.

    The controller reads a clock only to answer "has the scope lapsed?", and it
    reads it from the injected :class:`~collections.abc.Callable` so that a test
    can expire a scope without sleeping and a host can supply a monotonic source
    without this module knowing which one it got.
    """

    __slots__ = ("_clock", "_lock", "_scope", "_state")

    def __init__(self, *, clock: Callable[[], float]) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._state: ArmState = ArmState.DISARMED
        self._scope: ArmScope | None = None

    @property
    def state(self) -> ArmState:
        """The current arm state.

        A scope that lapsed while the runtime sat in ``ARMED`` does not silently
        rewrite the state to ``DISARMED`` — that would make the field depend on
        when it was read. The lapse is enforced where it matters, in
        :meth:`active_scope`, and reported honestly in :meth:`describe`.
        """
        with self._lock:
            return self._state

    @property
    def scope(self) -> ArmScope | None:
        """The scope attached to the current state, expired or not."""
        with self._lock:
            return self._scope

    def active_scope(self) -> ArmScope | None:
        """The authority the runtime currently holds, or ``None``.

        Returns the scope only when the runtime is ``ARMED`` **and** the scope
        has not lapsed. This is the single call the decision chain makes, so
        "armed but expired" cannot be read as armed anywhere by accident.
        """
        with self._lock:
            if self._state is not ArmState.ARMED or self._scope is None:
                return None
            if self._scope.is_expired(self._clock()):
                return None
            return self._scope

    def request(self, scope: ArmScope) -> ArmState:
        """Move ``DISARMED → ARMING`` with ``scope`` as the intended authority.

        Raises:
            SafetyStateError: The machine is not ``DISARMED``. Re-arming an
                already-armING runtime would let a second scope overwrite the
                first; re-requesting from ``ARMED`` would silently re-target a
                live authority. Both are refused instead.
            SafetyScopeError: The scope has already lapsed when it is presented.
        """
        with self._lock:
            self._require_transition(self._state, ArmState.ARMING)
            now = self._clock()
            if scope.is_expired(now):
                raise SafetyScopeError(
                    "An already-expired scope cannot be presented for arming.",
                    details={"expires_at": scope.expires_at, "now": now},
                )
            self._state = ArmState.ARMING
            self._scope = scope
            return self._state

    def confirm(self) -> ArmState:
        """Move ``ARMING → ARMED``.

        Raises:
            SafetyStateError: The machine is not ``ARMING``.
            SafetyScopeError: The scope lapsed between the request and the
                confirmation. The machine is returned to ``DISARMED`` first: a
                confirmation that failed must not leave a live-looking
                ``ARMING`` state behind for the next caller to confirm.
        """
        with self._lock:
            self._require_transition(self._state, ArmState.ARMED)
            scope = self._scope
            now = self._clock()
            if scope is None or scope.is_expired(now):
                self._state = ArmState.DISARMED
                self._scope = None
                raise SafetyScopeError(
                    "The arm scope expired before it was confirmed.",
                    details={"now": now},
                )
            self._state = ArmState.ARMED
            return self._state

    def disarm(self) -> ArmState:
        """Return to ``DISARMED`` from any state. Idempotent.

        Idempotence is deliberate and is the one asymmetry in this machine:
        disarming is the safe direction, so calling it on an already-disarmed
        runtime — which is what an emergency stop does, unconditionally, to
        every subsystem — must succeed rather than raise.

        The scope is dropped, not retained: a stale scope is authority nobody
        holds, and keeping it around would let a later bug re-attach it.
        """
        with self._lock:
            self._state = ArmState.DISARMED
            self._scope = None
            return self._state

    def describe(self) -> dict[str, object]:
        """Return the audit-safe shape of the current arm context."""
        with self._lock:
            scope = self._scope
            now = self._clock()
            return {
                "state": self._state.value,
                "scope": None if scope is None else scope.describe(),
                "scope_expired": None if scope is None else scope.is_expired(now),
            }

    @staticmethod
    def _require_transition(current: ArmState, target: ArmState) -> None:
        """Raise unless ``current → target`` is a transition the machine has."""
        if target not in _TRANSITIONS[current]:
            raise SafetyStateError(
                f"The arm state machine has no {current.value} → {target.value} transition.",
                details={"from": current.value, "to": target.value},
            )
