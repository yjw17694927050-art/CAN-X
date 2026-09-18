"""CI-03 — Quality Gate evaluation.

The gate is the repository's only required status check, so its decision must be
executable and testable rather than a shell string. The rule is:

* the classifier must have succeeded;
* ``full_required`` must be readable as ``true`` or ``false`` and is authoritative:
  when it is ``true`` every domain job is required regardless of the individual
  flags, and when it is neither ``true`` nor ``false`` the gate fails closed — a
  value that cannot be read is never defaulted away;
* a domain job the classifier marked **required** must report ``success``;
* a domain job the classifier did **not** require may report ``skipped`` or
  ``success`` — an *authorised* skip, not a missing validation;
* the ``classification`` label must be present and consistent with the requirement
  flags the gate is about to act on — an empty or unrecognised label is unreadable
  and fails closed;
* anything else — a red job, a cancelled job, an unexpected skip, a missing
  result, an unreadable classification — fails the gate.

An authorised skip is not a missing validation: the classifier is what
authorises it, the classifier is itself a required job, and this gate fails
closed when the classifier did not run.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

CLASSIFIER_JOB = "change-classifier"
RUNTIME_JOB = "runtime-python"
FRONTEND_JOB = "frontend-typescript"
RUST_JOB = "desktop-rust"

_JOB_FLAGS: tuple[tuple[str, str], ...] = (
    (RUNTIME_JOB, "runtime_required"),
    (FRONTEND_JOB, "frontend_required"),
    (RUST_JOB, "rust_required"),
)

#: Outcomes that are acceptable for a domain job the classifier did not require.
_ACCEPTABLE_WHEN_NOT_REQUIRED = frozenset({"success", "skipped"})

_ENV_CLASSIFIER_RESULT = "CANX_CI_CLASSIFIER_RESULT"
_ENV_CLASSIFICATION = "CANX_CI_CLASSIFICATION"
_ENV_FULL_REQUIRED = "CANX_CI_FULL_REQUIRED"
_ENV_JOB_RESULT: dict[str, str] = {
    RUNTIME_JOB: "CANX_CI_RUNTIME_RESULT",
    FRONTEND_JOB: "CANX_CI_FRONTEND_RESULT",
    RUST_JOB: "CANX_CI_RUST_RESULT",
}
_ENV_JOB_REQUIRED: dict[str, str] = {
    RUNTIME_JOB: "CANX_CI_RUNTIME_REQUIRED",
    FRONTEND_JOB: "CANX_CI_FRONTEND_REQUIRED",
    RUST_JOB: "CANX_CI_RUST_REQUIRED",
}


@dataclass(frozen=True)
class GateDecision:
    ok: bool
    reasons: tuple[str, ...]
    classification: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "classification": self.classification,
            "reasons": list(self.reasons),
        }


def as_bool(raw: object) -> bool | None:
    """Parse a boolean, or ``None`` when it is neither ``true`` nor ``false``."""

    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalised = raw.strip().lower()
        if normalised == "true":
            return True
        if normalised == "false":
            return False
    return None


def _recognised_labels(*, full_required: bool, required: Mapping[str, bool]) -> frozenset[str]:
    """The classification labels consistent with the declared requirement flags.

    The classifier emits ``full``, ``docs_only``, ``no_changes``, or a ``+``-joined
    union of the three domain names in a fixed order. Re-deriving the label from the
    flags the gate is about to act on keeps it from trusting a label that contradicts
    them: a truncated, empty or unrecognised label fails closed.
    """

    if full_required:
        return frozenset({"full"})

    names = [flag.removesuffix("_required") for _job, flag in _JOB_FLAGS if required[flag]]
    if not names:
        return frozenset({"docs_only", "no_changes"})
    return frozenset({"+".join(names)})


def evaluate(
    classification: Mapping[str, object],
    results: Mapping[str, str],
) -> GateDecision:
    """Decide whether the gate passes, given a classification and job results."""

    if not isinstance(classification, Mapping):
        return GateDecision(False, ("classification is not readable",))

    label = str(classification.get("classification", ""))
    reasons: list[str] = []

    classifier_result = results.get(CLASSIFIER_JOB)
    if classifier_result != "success":
        reasons.append(f"the change classifier did not succeed ({classifier_result!r})")

    # `full_required` is authoritative, so it has to be readable. A value that is
    # neither `true` nor `false` is not defaulted away: falling back to the
    # individual flags would let a corrupt FULL classification run a selective gate.
    full_required = as_bool(classification.get("full_required"))
    if full_required is None:
        reasons.append("classification is incomplete: full_required is missing or not a boolean")

    declared: dict[str, bool | None] = {
        flag: as_bool(classification.get(flag)) for _job, flag in _JOB_FLAGS
    }

    for job, flag in _JOB_FLAGS:
        requirement = declared[flag]
        if requirement is None:
            reasons.append(f"classification is incomplete: {flag} is missing or not a boolean")
            continue

        required = True if full_required is True else requirement
        outcome = results.get(job)

        if required:
            if outcome != "success":
                reasons.append(f"a required job did not succeed ({job}={outcome!r})")
        elif outcome not in _ACCEPTABLE_WHEN_NOT_REQUIRED:
            reasons.append(f"a non-required job ended in an unexpected state ({job}={outcome!r})")

    if full_required is not None and all(
        requirement is not None for requirement in declared.values()
    ):
        recognised = _recognised_labels(
            full_required=full_required,
            required={flag: value is True for flag, value in declared.items()},
        )
        if label not in recognised:
            reasons.append(
                "classification is unreadable: the classification label "
                f"{label!r} is missing or is not one the requirement flags describe"
            )

    return GateDecision(not reasons, tuple(reasons), label)


def _classification_from_env() -> dict[str, object]:
    payload: dict[str, object] = {
        "classification": os.environ.get(_ENV_CLASSIFICATION, ""),
        "full_required": os.environ.get(_ENV_FULL_REQUIRED, ""),
    }
    for _job, flag in _JOB_FLAGS:
        payload[flag] = os.environ.get(_ENV_JOB_REQUIRED[_job], "")
    return payload


def _parse_override(raw: str) -> Mapping[str, object] | None:
    if not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _requirement_label(classification: Mapping[str, object], flag: str) -> str:
    declared = as_bool(classification.get(flag))
    if declared is None:
        return "unknown"
    if as_bool(classification.get("full_required")) is True:
        return "required"
    return "required" if declared else "not required"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the CI-03 quality gate.")
    parser.add_argument(
        "--classification",
        default=None,
        help="classification as a JSON object (defaults to the CANX_CI_* environment)",
    )
    args = parser.parse_args(argv)

    override = _parse_override(args.classification or "")
    classification: Mapping[str, object] = (
        override if override is not None else _classification_from_env()
    )

    results: dict[str, str] = {
        CLASSIFIER_JOB: os.environ.get(_ENV_CLASSIFIER_RESULT, ""),
        **{job: os.environ.get(var, "") for job, var in _ENV_JOB_RESULT.items()},
    }

    decision = evaluate(classification, results)

    print(f"classification        : {decision.classification or '(unreadable)'}")
    print(f"{CLASSIFIER_JOB:<22}: {results[CLASSIFIER_JOB] or '(missing)'}")
    for job, flag in _JOB_FLAGS:
        outcome = results[job] or "(missing)"
        print(f"{job:<22}: {outcome}  [{_requirement_label(classification, flag)}]")
    print(json.dumps(decision.as_dict(), indent=2, sort_keys=True))

    if decision.ok:
        print("Quality Gate: PASS")
        return 0

    for reason in decision.reasons:
        print(f"::error::{reason}")
    print("Quality Gate: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
