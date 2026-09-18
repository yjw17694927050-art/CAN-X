"""Unit tests for the CI-03 Quality Gate evaluator.

The gate is the only required status check, so its fail-closed semantics must be
executable and tested rather than embedded in a shell string. These tests pin
the two halves of the rule:

* a **required** domain job must report ``success`` — ``skipped`` blocks;
* an **authorised** skip (a domain job the classifier did not require) passes.
"""

from __future__ import annotations

import pytest

from tools.ci.evaluate_gate import GateDecision, evaluate, main

FULL = {
    "runtime_required": True,
    "frontend_required": True,
    "rust_required": True,
    "full_required": True,
    "classification": "full",
}
DOCS_ONLY = {
    "runtime_required": False,
    "frontend_required": False,
    "rust_required": False,
    "full_required": False,
    "classification": "docs_only",
}
RUNTIME_ONLY = {
    "runtime_required": True,
    "frontend_required": False,
    "rust_required": False,
    "full_required": False,
    "classification": "runtime",
}


def results(
    *, classifier: str = "success", runtime: str = "skipped",
    frontend: str = "skipped", rust: str = "skipped",
) -> dict[str, str]:
    return {
        "change-classifier": classifier,
        "runtime-python": runtime,
        "frontend-typescript": frontend,
        "desktop-rust": rust,
    }


# --------------------------------------------------------------------------
# The happy paths
# --------------------------------------------------------------------------


def test_authorised_skips_pass_for_a_docs_only_change() -> None:
    decision = evaluate(DOCS_ONLY, results())

    assert decision.ok is True
    assert decision.reasons == ()


def test_full_change_with_every_required_job_green_passes() -> None:
    decision = evaluate(
        FULL,
        results(runtime="success", frontend="success", rust="success"),
    )

    assert decision.ok is True


def test_runtime_only_change_passes_when_python_is_green() -> None:
    decision = evaluate(RUNTIME_ONLY, results(runtime="success"))

    assert decision.ok is True


def test_a_non_required_job_that_ran_and_succeeded_is_accepted() -> None:
    """A debug re-run of a non-required job must not fail the gate."""

    decision = evaluate(DOCS_ONLY, results(runtime="success", rust="success"))

    assert decision.ok is True


# --------------------------------------------------------------------------
# Required jobs must be success — skipped/cancelled/failure all block
# --------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["skipped", "cancelled", "failure", "timed_out", ""])
def test_a_required_job_that_is_not_success_blocks(outcome: str) -> None:
    decision = evaluate(RUNTIME_ONLY, results(runtime=outcome))

    assert decision.ok is False
    assert any("runtime-python" in reason for reason in decision.reasons)


@pytest.mark.parametrize("outcome", ["skipped", "cancelled", "failure"])
def test_a_required_rust_job_that_is_not_success_blocks(outcome: str) -> None:
    decision = evaluate(FULL, results(runtime="success", frontend="success", rust=outcome))

    assert decision.ok is False
    assert any("desktop-rust" in reason for reason in decision.reasons)


# --------------------------------------------------------------------------
# Non-required jobs: skipped/success pass, anything red still blocks
# --------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["failure", "cancelled", "timed_out"])
def test_a_non_required_job_that_failed_still_blocks(outcome: str) -> None:
    """Fail closed: a red job is evidence of a problem even if it was not required."""

    decision = evaluate(DOCS_ONLY, results(frontend=outcome))

    assert decision.ok is False
    assert any("frontend-typescript" in reason for reason in decision.reasons)


# --------------------------------------------------------------------------
# The classifier itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["failure", "cancelled", "timed_out", "skipped", ""])
def test_a_classifier_that_did_not_succeed_blocks(outcome: str) -> None:
    decision = evaluate(DOCS_ONLY, results(classifier=outcome))

    assert decision.ok is False
    assert any("classifier" in reason for reason in decision.reasons)


def test_a_failed_classifier_blocks_even_though_the_domain_jobs_are_green() -> None:
    decision = evaluate(
        DOCS_ONLY,
        results(classifier="failure", runtime="success", frontend="success", rust="success"),
    )

    assert decision.ok is False


# --------------------------------------------------------------------------
# Incomplete / malformed classification blocks
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    ["runtime_required", "frontend_required", "rust_required"],
)
def test_a_classification_missing_a_requirement_flag_blocks(missing: str) -> None:
    incomplete = {k: v for k, v in DOCS_ONLY.items() if k != missing}

    decision = evaluate(incomplete, results(runtime="success"))

    assert decision.ok is False
    assert any("classification" in reason for reason in decision.reasons)


def test_a_full_classification_requires_all_three_domain_jobs() -> None:
    """`full_required` is authoritative: a docs-shaped flag set cannot skip FULL CI."""

    decision = evaluate(FULL, results())

    assert decision.ok is False
    assert len(decision.reasons) == 3


def test_classification_that_is_not_a_mapping_blocks() -> None:
    decision = evaluate("docs_only", results())  # type: ignore[arg-type]

    assert decision.ok is False


def test_decision_is_immutable_and_serialisable() -> None:
    decision = evaluate(DOCS_ONLY, results())

    assert isinstance(decision, GateDecision)
    assert isinstance(decision.as_dict()["ok"], bool)
    assert isinstance(decision.as_dict()["reasons"], list)


# --------------------------------------------------------------------------
# CLI contract
# --------------------------------------------------------------------------


def _set_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    env = {
        "CANX_CI_CLASSIFIER_RESULT": "success",
        "CANX_CI_CLASSIFICATION": "docs_only",
        "CANX_CI_FULL_REQUIRED": "false",
        "CANX_CI_RUNTIME_REQUIRED": "false",
        "CANX_CI_FRONTEND_REQUIRED": "false",
        "CANX_CI_RUST_REQUIRED": "false",
        "CANX_CI_RUNTIME_RESULT": "skipped",
        "CANX_CI_FRONTEND_RESULT": "skipped",
        "CANX_CI_RUST_RESULT": "skipped",
    }
    env.update(overrides)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_main_returns_zero_for_an_authorised_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)

    assert main([]) == 0


def test_main_returns_non_zero_when_a_required_job_was_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, CANX_CI_RUNTIME_REQUIRED="true", CANX_CI_CLASSIFICATION="runtime")

    assert main([]) == 1


def test_main_returns_non_zero_when_the_classifier_job_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, CANX_CI_CLASSIFIER_RESULT="failure")

    assert main([]) == 1


def test_main_fails_closed_when_a_requirement_flag_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, CANX_CI_RUNTIME_REQUIRED="")

    assert main([]) == 1


def test_main_accepts_an_explicit_classification_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, CANX_CI_RUNTIME_REQUIRED="")
    override = (
        '{"runtime_required": false, "frontend_required": false, "rust_required": false,'
        ' "full_required": false, "classification": "docs_only"}'
    )

    assert main(["--classification", override]) == 0


def test_main_ignores_a_malformed_override_and_falls_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, CANX_CI_RUNTIME_REQUIRED="true")

    assert main(["--classification", "{not json"]) == 1

