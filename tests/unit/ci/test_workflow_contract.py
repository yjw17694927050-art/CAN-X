"""CI-03 workflow contract.

The tiered gate changes *which* validation jobs run. It must not change the
required status check, the triggers, or the fail-closed behaviour of the gate
itself. Those are load-bearing invariants of the CI-02 protected integration, so
they are pinned here rather than left to review.

The workflow is read as text on purpose: the assertions are about exact tokens
(`Quality Gate`, `if: always()`, the `needs` list), and a YAML library is not a
dependency of this repository.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"
CLASSIFIER_MODULE = "tools/ci/classify_changes.py"
GATE_MODULE = "tools/ci/evaluate_gate.py"

DOMAIN_JOB_OUTPUT = {
    "runtime-python": "needs.change-classifier.outputs.runtime_required == 'true'",
    "frontend-typescript": "needs.change-classifier.outputs.frontend_required == 'true'",
    "desktop-rust": "needs.change-classifier.outputs.rust_required == 'true'",
}


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


#: The start of the next top-level job: a newline, exactly two spaces, then a
#: non-space. A plain `"\n  "` search would also match the leading pair of a
#: four-space-indented body line.
_JOB_END = re.compile(r"\n  (?=\S)")


def job_block(job: str) -> str:
    """Return the YAML block for one top-level job."""

    marker = f"\n  {job}:\n"
    text = workflow_text()
    assert marker in text, f"job {job!r} is missing from ci.yml"
    remainder = text.split(marker, 1)[1]
    end = _JOB_END.search(remainder)
    return remainder[: end.start()] if end else remainder


# --------------------------------------------------------------------------
# The required status check must not move
# --------------------------------------------------------------------------


def test_the_gate_job_is_still_named_exactly_quality_gate() -> None:
    """`Quality Gate` is the ruleset's required context string."""

    assert "    name: Quality Gate\n" in job_block("quality-gate")


def test_the_gate_job_runs_on_every_event() -> None:
    assert "    if: always()\n" in job_block("quality-gate")


def test_the_gate_job_depends_on_the_classifier_and_every_domain_job() -> None:
    gate = job_block("quality-gate")

    assert (
        "    needs: [change-classifier, runtime-python, frontend-typescript, desktop-rust]\n"
        in gate
    )


# --------------------------------------------------------------------------
# Domain jobs are conditional on the classifier, and only on it
# --------------------------------------------------------------------------


def test_domain_jobs_are_gated_on_the_classifier_output() -> None:
    for job, condition in DOMAIN_JOB_OUTPUT.items():
        block = job_block(job)
        assert f"    if: {condition}\n" in block, job


def test_domain_job_display_names_are_unchanged() -> None:
    """Actions history stays continuous only if the job names stay stable."""

    assert "    name: Runtime / Python\n" in job_block("runtime-python")
    assert "    name: Frontend / TypeScript\n" in job_block("frontend-typescript")
    assert "    name: Desktop System / Rust\n" in job_block("desktop-rust")


def test_the_classifier_job_has_no_condition() -> None:
    """The classifier must run on every event — it produces the gate's inputs."""

    block = job_block("change-classifier")

    assert "    if:" not in block
    assert "    name: Change Classification\n" in block


def test_the_classifier_job_exposes_the_five_outputs_the_gate_reads() -> None:
    block = job_block("change-classifier")

    for output in (
        "runtime_required",
        "frontend_required",
        "rust_required",
        "full_required",
        "classification",
    ):
        assert f"      {output}: ${{{{ steps.classify.outputs.{output} }}}}\n" in block, output


# --------------------------------------------------------------------------
# Wiring: the workflow runs the tested helpers, not inline logic
# --------------------------------------------------------------------------


def test_the_classifier_runs_the_tested_module() -> None:
    assert CLASSIFIER_MODULE in workflow_text()


def test_the_gate_runs_the_tested_module() -> None:
    assert GATE_MODULE in job_block("quality-gate")


def test_the_gate_receives_every_result_it_needs() -> None:
    gate = job_block("quality-gate")

    for variable in (
        "CANX_CI_CLASSIFIER_RESULT",
        "CANX_CI_CLASSIFICATION",
        "CANX_CI_FULL_REQUIRED",
        "CANX_CI_RUNTIME_REQUIRED",
        "CANX_CI_FRONTEND_REQUIRED",
        "CANX_CI_RUST_REQUIRED",
        "CANX_CI_RUNTIME_RESULT",
        "CANX_CI_FRONTEND_RESULT",
        "CANX_CI_RUST_RESULT",
    ):
        assert f"          {variable}:" in gate, variable


# --------------------------------------------------------------------------
# Triggers — the manual FULL CI escape hatch survives
# --------------------------------------------------------------------------


def test_triggers_are_unchanged() -> None:
    text = workflow_text()

    assert "  pull_request:\n    branches: [main]\n" in text
    assert "  push:\n    branches: [main]\n" in text
    assert "  workflow_dispatch:\n" in text


# --------------------------------------------------------------------------
# Anti-weakening
# --------------------------------------------------------------------------


def test_the_workflow_never_hides_a_failure() -> None:
    text = workflow_text()

    assert "continue-on-error" not in text
    assert "|| true" not in text


def test_the_workflow_never_removes_itself_for_a_path() -> None:
    """`paths-ignore` would make the required check `missing`, which blocks forever."""

    text = workflow_text()

    assert "paths-ignore" not in text
    assert "paths:" not in text


def test_the_workflow_keeps_the_read_only_permission() -> None:
    assert "permissions:\n  contents: read\n" in workflow_text()
