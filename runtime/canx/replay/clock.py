"""The clock abstraction an offline replay schedules against.

A replay must be reproducible, so it never reads the wall clock and never sleeps
directly: it is handed a :class:`ReplayClock` and uses exactly two operations,
:meth:`ReplayClock.monotonic` and :meth:`ReplayClock.sleep_until`. Swapping the
clock is therefore the whole difference between a real-time replay and a test
that runs instantly and deterministically.

Production uses :class:`MonotonicReplayClock`; :class:`VirtualReplayClock` is the
deterministic clock a caller (or a test) uses when it wants the recorded schedule
without waiting for it. Both honour the same contract, which the scheduler
enforces: readings are finite and never move backwards.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from math import isfinite

from canx.replay.errors import ReplaySchedulerError


class ReplayClock(ABC):
    """The only time source an offline replay may use.

    ``sleep_until`` is defined here, once, in terms of ``monotonic`` and
    ``sleep``: that keeps a single definition of "wait until this deadline" for
    every clock, including the ones a test supplies.
    """

    @abstractmethod
    def monotonic(self) -> float:
        """Return the current reading, in seconds, from a monotonic source."""

    @abstractmethod
    def sleep(self, seconds: float) -> None:
        """Block until ``seconds`` have passed on this clock."""

    def sleep_until(self, deadline: float) -> None:
        """Block until ``deadline``, in the same domain as :meth:`monotonic`.

        A deadline already in the past returns immediately — replay never waits
        for time that has already been spent.

        Raises:
            ReplaySchedulerError: If ``deadline`` is not a finite number.
        """
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not isfinite(deadline)
        ):
            raise ReplaySchedulerError(
                "The replay deadline is not a usable clock reading.",
                code="replay.invalid_deadline",
                details={"deadline": repr(deadline)},
            )
        delay = deadline - self.monotonic()
        if delay > 0:
            self.sleep(delay)


class MonotonicReplayClock(ReplayClock):
    """The production clock: the process monotonic clock and a real sleep.

    Offline replay reproduces the recorded *timing*, so a real replay really
    waits. Nothing here talks to hardware: sleeping is how the recorded schedule
    is honoured, not a device operation.
    """

    def monotonic(self) -> float:
        """Return :func:`time.monotonic`."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Sleep in the real process for ``seconds``."""
        time.sleep(seconds)


class VirtualReplayClock(ReplayClock):
    """A clock that only moves when it is slept on.

    The reading starts at ``start`` and advances by exactly the requested delay,
    so the resulting schedule is a pure function of the recording. It also keeps
    the requested waits, which is what makes "the schedule was honoured" an
    assertion instead of a guess.
    """

    __slots__ = ("_now", "_sleeps")

    def __init__(self, *, start: float = 0.0) -> None:
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or not isfinite(start)
        ):
            raise ReplaySchedulerError(
                "A virtual clock needs a finite starting reading.",
                code="replay.invalid_clock_origin",
                details={"start": repr(start)},
            )
        self._now = float(start)
        self._sleeps: list[float] = []

    def monotonic(self) -> float:
        """Return the current virtual reading."""
        return self._now

    def sleep(self, seconds: float) -> None:
        """Advance the virtual reading by ``seconds`` and record the wait.

        Raises:
            ReplaySchedulerError: If ``seconds`` is negative or not finite.
        """
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not isfinite(seconds)
            or seconds < 0
        ):
            raise ReplaySchedulerError(
                "A virtual clock can only be advanced by a finite non-negative delay.",
                code="replay.invalid_sleep",
                details={"seconds": repr(seconds)},
            )
        self._sleeps.append(float(seconds))
        self._now += float(seconds)

    @property
    def sleeps(self) -> tuple[float, ...]:
        """Return every wait this clock was asked for, in order."""
        return tuple(self._sleeps)

    @property
    def total_slept(self) -> float:
        """Return the sum of every requested wait, i.e. the replayed span."""
        return sum(self._sleeps)
