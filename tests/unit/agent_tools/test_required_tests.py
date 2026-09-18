"""P1-1: a required test must be reported as *passed* before integration.

``not_run`` / ``skipped`` / ``failed`` remain valid *reports* - a handoff that is
honest about a test it did not run is more useful than one that lies. What they
cannot be is an integration-passing result for a required test.
"""

from __future__ import annotations

import pytest
from agent_tools_support import config, make_handoff, make_task

from tools.agent.contracts import TestResult
from tools.agent.errors import HandoffInvalidError
from tools.agent.validation import validate_handoff

REQUIRED = ("unit", "integration")


def _task():
    return make_task(required_tests=REQUIRED)


def _handoff(*results: tuple[str, str], **overrides: object):
    tests = tuple(
        TestResult(name=name, command=f"run {name}", result=result) for name, result in results
    )
    return make_handoff(tests=tests, **overrides)


def test_every_required_test_passing_is_ready() -> None:
    validate_handoff(
        _handoff(("unit", "passed"), ("integration", "passed")), _task(), config()
    )


@pytest.mark.parametrize("result", ["failed", "skipped", "not_run"])
def test_a_required_test_that_did_not_pass_blocks_readiness(result: str) -> None:
    """RED -> GREEN: ``unit passed / integration not_run`` used to be accepted."""
    with pytest.raises(HandoffInvalidError) as raised:
        validate_handoff(_handoff(("unit", "passed"), ("integration", result)), _task(), config())
    assert raised.value.details["not_passed"] == {"integration": result}


def test_a_missing_required_test_blocks_readiness() -> None:
    with pytest.raises(HandoffInvalidError) as raised:
        validate_handoff(_handoff(("unit", "passed")), _task(), config())
    assert raised.value.details["missing"] == ["integration"]


def test_a_required_test_reported_twice_is_rejected() -> None:
    """A duplicate could otherwise let the first, passing copy hide the second."""
    with pytest.raises(HandoffInvalidError) as raised:
        validate_handoff(
            _handoff(("unit", "passed"), ("unit", "passed"), ("integration", "passed")),
            _task(),
            config(),
        )
    assert raised.value.details["duplicates"] == ["unit"]


def test_a_duplicate_is_rejected_even_when_the_results_are_both_passing() -> None:
    with pytest.raises(HandoffInvalidError):
        validate_handoff(
            _handoff(("unit", "passed"), ("unit", "passed")),
            make_task(required_tests=("unit",)),
            config(),
        )


def test_an_optional_test_that_did_not_run_does_not_block_readiness() -> None:
    validate_handoff(
        _handoff(("unit", "passed"), ("integration", "passed"), ("packaged", "not_run")),
        _task(),
        config(),
    )


@pytest.mark.parametrize("result", ["failed", "skipped", "not_run"])
def test_a_not_ready_handoff_may_still_report_these_results_honestly(result: str) -> None:
    """Honesty is not punished: without the ready claim, these are just facts."""
    validate_handoff(
        _handoff(("unit", "passed"), ("integration", result), ready_for_integration=False),
        _task(),
        config(),
    )


def test_a_handoff_still_must_report_at_least_one_test() -> None:
    with pytest.raises(HandoffInvalidError):
        validate_handoff(make_handoff(tests=()), _task(), config())
