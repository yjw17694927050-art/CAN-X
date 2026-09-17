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
→ request cancellation of active dangerous operations
→ audit event
```

The four steps are the contract. This stage implements the state, the refusal
and the cancellation **request**; it does not implement interrupting a real
device write, because there is no real transmit path yet and claiming otherwise
would be a fabrication. The contract is written so that the transport layer can
be attached later without changing any of the four steps.

Two asymmetries are deliberate:

* **Any caller may engage.** An Agent, script or automation rule that detects
  danger may pull the stop; refusing would be absurd. Engaging can only ever
  reduce authority.
* **Only an operator may release.** Releasing restores the *possibility* of
  dangerous work, which is an authority decision and belongs to a human operator
  or the host, never to the machine that was just stopped.

Releasing never restores what was armed: a released runtime is ``DISARMED`` and
must be armed again from scratch (invariant S8).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from canx.safety.caller import CallerIdentity
from canx.safety.errors import SafetyCallerError


class OperationCanceller(Protocol):
    """A subsystem that can be asked to abandon its active dangerous operations.

    Implementations return the identifiers they accepted for cancellation, so
    the emergency event can record what was actually asked rather than what was
    hoped for. A canceller that cannot name its operations returns an empty
    tuple; it does not return ``None`` and it does not stay silent.
    """

    def cancel_active_operations(self, *, reason: str) -> tuple[str, ...]:
        """Abandon every active operation. Returns the identifiers requested."""
        ...


@dataclass(frozen=True, slots=True)
class EmergencyStopState:
    """A snapshot of the global emergency stop."""

    engaged: bool
    engaged_at: float | None = None
    reason: str | None = None
    requested_cancellations: tuple[str, ...] = ()
    cancellation_failures: tuple[str, ...] = ()

    def describe(self) -> dict[str, object]:
        """Return the audit-safe shape of this state."""
        return {
            "engaged": self.engaged,
            "engaged_at": self.engaged_at,
            "reason": self.reason,
            "requested_cancellations": list(self.requested_cancellations),
            "cancellation_failures": list(self.cancellation_failures),
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
        self._cancellers: list[OperationCanceller] = []
        self._state = EmergencyStopState(engaged=False)

    def attach_disarm(self, hook: Callable[[], None]) -> None:
        """Attach the disarm action performed when the stop engages."""
        with self._lock:
            self._disarm_hook = hook

    def register_canceller(self, canceller: OperationCanceller) -> None:
        """Register a subsystem whose active operations the stop must reach."""
        with self._lock:
            self._cancellers.append(canceller)

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

    def engage(self, *, caller: CallerIdentity, reason: str) -> EmergencyStopState:
        """Engage the global stop. Idempotent, and open to every caller kind.

        The three effects run in a fixed order and the order matters: the runtime
        is disarmed first, so that any decision taken while cancellations are
        still in flight already sees an unarmed runtime and refuses.

        A canceller that raises does not escape this method. Its failure is
        recorded in :attr:`EmergencyStopState.cancellation_failures` and reported
        to the caller, which is the opposite of swallowing it (invariant S14):
        the operator sees exactly which subsystem did not acknowledge.
        """
        with self._lock:
            # A second engagement keeps the original reason and timestamp — the
            # stop happened when it first happened — but still retries the
            # cancellations, because a subsystem that refused the first time may
            # be reachable now.
            if not self._state.engaged:
                self._state = EmergencyStopState(
                    engaged=True,
                    engaged_at=self._clock(),
                    reason=reason,
                )
            hook = self._disarm_hook
            cancellers = tuple(self._cancellers)

        if hook is not None:
            hook()

        requested: list[str] = []
        failures: list[str] = []
        for canceller in cancellers:
            try:
                requested.extend(canceller.cancel_active_operations(reason=reason))
            except Exception as error:  # recorded below, never swallowed
                failures.append(f"{type(canceller).__name__}: {type(error).__name__}")

        with self._lock:
            self._state = replace(
                self._state,
                requested_cancellations=tuple(requested),
                cancellation_failures=tuple(failures),
            )
            return self._state

    def reset(self, *, caller: CallerIdentity) -> EmergencyStopState:
        """Release the global stop.

        Raises:
            SafetyCallerError: ``caller`` is an Agent, a script or an automation
                rule. Only a human operator or the host system may restore the
                possibility of dangerous work.
        """
        if not caller.may_release_emergency_stop:
            raise SafetyCallerError(
                "Only a human operator or the host system may release the emergency stop.",
                details={"caller": str(caller.kind), "name": caller.name},
            )
        with self._lock:
            self._state = EmergencyStopState(engaged=False)
            return self._state
