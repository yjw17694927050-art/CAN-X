# RELIABILITY-01 — Capture / DataSession Finalization Timing

> **Status**: Implementation complete · self-verification complete · awaiting independent acceptance
> **Scope**: capture stop, recorder cleanup, DataSession terminal-state observation
> **Production lifecycle change**: none

## Symptom

The historical failure was:

```text
tests/unit/api/test_capture_project_target.py::
test_a_project_capture_reports_the_data_session_it_created

expected: DataSessionState.COMPLETED
observed: DataSessionState.FAILED
```

The same SHA passed on a rerun. The known failed GitHub Actions attempt is
recorded in `docs/PROJECT_STATE.md`:

```text
run 35296791223, attempt 1
head 6dd29842 (docs-only change)
pytest: 1 failed, 2227 passed, 6 skipped

AssertionError:
assert <DataSessionState.FAILED: 'failed'> is
       <DataSessionState.COMPLETED: 'completed'>

tests\unit\api\test_capture_project_target.py:50
```

The failed output does not contain the recorder failure code or a lifecycle
timeline. The investigation therefore used the live code path, the historical
fixes, and deterministic recorder tests rather than treating the assertion
message as the whole diagnosis.

## Code-path finding

The target test starts a 2 kHz virtual capture for 0.1 s with:

```text
project_max_frames_per_segment = 16
recorder_cleanup_timeout_seconds = 1.0   # RuntimeService default
```

The 16-frame threshold is deliberately pathological. It creates multiple
Parquet segments and SQLite registry transactions during a very short capture.
`RuntimeService` bounds archive drain and recorder close with the configured
cleanup budget. If that budget expires before the terminal decision is durable,
the policy in ADR 0001 requires the recording to become `FAILED` rather than
claim `COMPLETED` for an incomplete finalization. The authoritative terminal
state remains in `DataSessionWriter`/SQLite:

```text
ACTIVE
  ├─ successful flush + ACTIVE → COMPLETED
  └─ cleanup deadline before terminal decision / real finalization fault
       → ACTIVE → FAILED
```

`COMPLETED → FAILED` is not performed by this path. A timeout result is a real
terminal decision, not an overwrite of an already completed session.

The same timing class was already found and corrected for the sibling
integration test in `fc98f8e`:

```text
tests/integration/test_project_backed_capture.py
  pathological segment threshold
  → explicit CLEANUP_TIMEOUT_SECONDS = 6.0
  → successful-stop tests state their own drain budget
```

That correction did not cover the smaller API target test.

## Reproduction record

Natural repetition did not reproduce the CI failure on this machine:

```text
target test, repeated                  20/20 passed
target module, repeated                10/10 passed
target test, 16 parallel processes     16/16 passed
```

The exact local stop timings for ten target-flow runs were `0.094 s` to
`0.386 s`; the CI run that failed took materially longer overall than the later
same-code run, consistent with runner contention. A CI failure that has already
occurred is historical evidence even when local repetition is green.

The deterministic causal path is covered by the existing recorder tests:

```text
tests/integration/test_recorder_cleanup_timeout.py
  blocked finalization + expired deadline
    → recorder.cleanup_timeout → durable FAILED
  slow but inside the deadline
    → COMPLETED
```

A one-off target-flow probe with the default 1 s budget and a controlled block
in `DataSessionWriter.flush_pending` produced:

```text
before_release: {'status': 'stopped', 'finalization_pending': True}
                state=failed failure=recorder.cleanup_timeout
after_release:  state=failed failure=recorder.cleanup_timeout
```

That probe is deterministic causal reproduction, not a natural occurrence of
the CI flake. It shows why an expired cleanup budget is sufficient to produce
the observed `FAILED` state even when the finalizer later resumes.

The relevant focused suites were run together:

```text
pytest -q --tb=short
  tests/unit/api/test_capture_project_target.py
  tests/unit/recorder/test_project_recorder.py
  tests/unit/recorder/test_project_recorder_gate.py
  tests/unit/data/test_session_writer_failure.py
  tests/integration/test_recorder_cleanup_timeout.py

63 passed in 21.14s
```

## Root cause

```text
Trigger
  Contended CI execution of a test whose 16-frame segment threshold creates
  pathological finalization work.

Mechanism
  The test inherited RuntimeService's 1 s production cleanup default. Under
  runner contention the bounded cleanup path could expire before the archive
  drain/terminal decision completed.

Observable result
  ADR 0001's failure policy correctly placed the DataSession in FAILED because
  the recording could not demonstrate a completed terminal transition inside
  its configured budget.

Why it was intermittent
  Drain and SQLite/Parquet work vary with runner load; the same code can finish
  inside 1 s on one attempt and exceed it on another.

Authoritative invariant
  A timeout may prevent COMPLETED; it may not turn a truly completed session
  into FAILED. The target test asserted immediate COMPLETED for a workload
  whose own configuration did not supply a budget sized for that workload.
```

The residual uncertainty is narrow: the historical CI log does not identify
whether the drain or the terminal commit consumed the budget on that specific
attempt. The evidence does establish the terminal decision path and the
workload/budget mismatch.

## Fix

The target test and its sibling healthy-stop API test now state the cleanup
budget their own pathological segment workload needs:

```text
CLEANUP_TIMEOUT_SECONDS = 6.0
```

No production file, timeout default, retry policy, test assertion, or Safety
invariant was changed. The fix does not add a sleep, polling loop, retry,
`xfail`, skip, or `continue-on-error`.

## Verification

```text
focused recorder / API suites        63 passed
target test repeated                 20/20 passed
target module repeated               10/10 passed
target test, 16 parallel processes   16/16 passed
full pytest                          2597 passed, 7 skipped in 239.68 s
ruff                                 All checks passed
mypy                                 Success: no issues found in 95 source files
frontend                             lint + typecheck + 162 tests + build passed
rust                                 fmt + clippy + 31 tests passed
GitHub Quality Gate                   NOT RUN — PR handoff pending
```

## Remaining uncertainty

This record does not claim that the original CI failure was observed locally
under the exact same runner conditions. It establishes the causal timeout path,
the historical workload mismatch, and the test correction; the original CI
attempt remains the evidence that the environment crossed the old budget.

AGENT-02 remains blocked pending independent acceptance of this reliability
record. `Final Acceptance: PASS`, `Status: CLOSED`, and AGENT-02 entry approval
are external decisions and are not asserted here.
