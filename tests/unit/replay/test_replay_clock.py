"""The clock contract an offline replay schedules against.

The scheduler may only ever see time through this abstraction, so the contract
itself is asserted here: a virtual clock advances exactly by what it was slept
on, a deadline in the past costs nothing, and every unusable value is refused
instead of silently producing a wrong schedule.
"""

from __future__ import annotations

import pytest
from canx.replay.clock import MonotonicReplayClock, ReplayClock, VirtualReplayClock
from canx.replay.errors import ReplaySchedulerError


def test_a_virtual_clock_only_moves_when_it_is_slept_on() -> None:
    clock = VirtualReplayClock()

    assert clock.monotonic() == 0.0
    assert clock.sleeps == ()
    assert clock.total_slept == 0.0

    clock.sleep(0.25)
    clock.sleep(0.5)

    assert clock.monotonic() == 0.75
    assert clock.sleeps == (0.25, 0.5)
    assert clock.total_slept == 0.75


def test_a_virtual_clock_can_start_anywhere() -> None:
    clock = VirtualReplayClock(start=1_500.5)

    clock.sleep_until(1_501.0)

    assert clock.monotonic() == 1_501.0
    assert clock.sleeps == (0.5,)


def test_sleep_until_a_deadline_already_in_the_past_costs_nothing() -> None:
    clock = VirtualReplayClock(start=10.0)

    clock.sleep_until(9.0)
    clock.sleep_until(10.0)

    assert clock.monotonic() == 10.0
    assert clock.sleeps == ()


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), float("-inf")])
def test_sleep_until_a_non_finite_deadline_is_refused(deadline: float) -> None:
    clock = VirtualReplayClock()

    with pytest.raises(ReplaySchedulerError) as raised:
        clock.sleep_until(deadline)

    assert raised.value.code == "replay.invalid_deadline"


@pytest.mark.parametrize("start", [float("nan"), float("inf")])
def test_a_virtual_clock_refuses_a_non_finite_origin(start: float) -> None:
    with pytest.raises(ReplaySchedulerError) as raised:
        VirtualReplayClock(start=start)

    assert raised.value.code == "replay.invalid_clock_origin"


@pytest.mark.parametrize("seconds", [-0.5, float("nan"), float("inf")])
def test_a_virtual_clock_refuses_an_unusable_delay(seconds: float) -> None:
    clock = VirtualReplayClock()

    with pytest.raises(ReplaySchedulerError) as raised:
        clock.sleep(seconds)

    assert raised.value.code == "replay.invalid_sleep"


def test_the_production_clock_reads_a_monotonic_source_and_really_waits() -> None:
    clock = MonotonicReplayClock()

    first = clock.monotonic()
    clock.sleep(0.01)
    second = clock.monotonic()

    assert second >= first
    assert second - first >= 0.0


def test_the_clock_abstraction_cannot_be_used_without_an_implementation() -> None:
    with pytest.raises(TypeError):
        ReplayClock()  # type: ignore[abstract]


def test_a_caller_supplied_clock_only_needs_the_two_operations() -> None:
    class CountingClock(ReplayClock):
        def __init__(self) -> None:
            self._now = 0.0
            self.reads = 0

        def monotonic(self) -> float:
            self.reads += 1
            return self._now

        def sleep(self, seconds: float) -> None:
            self._now += seconds

    clock = CountingClock()

    clock.sleep_until(2.0)

    assert clock.monotonic() == 2.0
    assert clock.reads == 2
