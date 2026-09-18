# RELIABILITY-01 — Capture / DataSession Finalization Timing

> **Status**: Final Acceptance: PASS · Status: CLOSED
> **Acceptance source**: external / independent reviewer
> **Scope**: capture stop, recorder cleanup, DataSession terminal-state observation
> **Production lifecycle change**: none

## Scope of this record

Two increments belong to this reliability work, and this document covers both:

```text
RELIABILITY-01        the original flake and its first remediation
                      (merged to main as PR #9 / 831930ee)
RELIABILITY-01-FIX-1  the sibling the first remediation did not cover, exposed by
                      the post-merge main CI run 35321725801
RELIABILITY-01-FIX-2  the test harness itself: two budgets instead of one,
                      a teardown that owns every gate, no executor thread spent
                      on waiting, and failures that explain themselves
RELIABILITY-01-CLOSE-FINAL
                      protected integration, post-merge verification and closure
```

Each increment extends the previous one; nothing earlier is reverted.

Status: **Final Acceptance: PASS · Status: CLOSED** — external independent
acceptance on the accepted head `07a95f675527cb14c742edc93f108f5c1260ef06`
(P0: 0, P1: 0; the P2 PR-metadata finding was resolved before the merge).

## Symptom

### The original failure

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

### The failure the first remediation did not cover

PR #9 was green on its own head `1d2a026594a9cef869f065e649a868f70a146dc6`
(`run 35320171207`, Runtime / Python 4m55s) and was merged as `831930ee`. The
post-merge `main` run failed:

```text
run 35321725801, attempt 1
head 831930ee (the merge commit)
event: push (post-merge main)
Runtime / Python: failure      (308.03 s)
Frontend / TypeScript: success
Desktop System / Rust: success
Quality Gate: failure          (a failed quality job fails the gate)

1 failed, 2597 passed, 6 skipped

AssertionError: assert <DataSessionState.FAILED: 'failed'> is
                <DataSessionState.COMPLETED: 'completed'>
tests\unit\runtime\test_project_capture.py:52
```

Same code, green on the PR run and red on the post-merge run, and the failing
test is in a **different file** from the one the first remediation touched:

```text
PR #9 changed         tests/unit/api/test_capture_project_target.py
main CI failed in     tests/unit/runtime/test_project_capture.py
```

Neither CI log contains a recorder failure code or a lifecycle timeline. The
investigation therefore used the live code path, measured stop timings, a
controlled-contention reproduction, and the deterministic recorder tests rather
than treating the assertion message as the whole diagnosis.

## Code-path finding

The sibling test starts a 2 kHz virtual capture for 0.1 s with:

```text
project_max_frames_per_segment = 16
recorder_cleanup_timeout_seconds = 1.0   # RuntimeService default
```

The 16-frame threshold is deliberately pathological: it produces ~12 Parquet
segments and ~190 frames in a 0.1 s capture, so the stop path carries far more
per-segment and registry work than production segment sizes imply.

`RuntimeService` uses **that one budget three times** on the stop path:

```text
stop_capture()
  ├─ drain:    asyncio.wait_for(gather(consumer tasks), timeout=budget)
  ├─ cleanup:  recorder.arm_stop_deadline(budget)   # the recorder decides
  │            whether a terminal transition still fits
  └─ close:    asyncio.wait_for(recorder.stop(), timeout=budget)
```

If the budget expires before the terminal decision is durable, the policy in
ADR 0001 requires the recording to become `FAILED` rather than claim `COMPLETED`
for an incomplete finalization. The authoritative terminal state remains in
`DataSessionWriter`/SQLite:

```text
ACTIVE
  ├─ successful flush + ACTIVE → COMPLETED
  └─ cleanup deadline before terminal decision / real finalization fault
       → ACTIVE → FAILED
```

`COMPLETED → FAILED` is not performed by this path. A timeout result is a real
terminal decision, not an overwrite of an already completed session.
`stop_capture()` does not raise on a missed deadline; it records the failure and
returns, which is why an unrelated assertion in the same test can still pass
while the session state is already doomed.

The same timing class had already been found and corrected for the sibling
integration test in `fc98f8e`:

```text
tests/integration/test_project_backed_capture.py
  pathological segment threshold
  → explicit CLEANUP_TIMEOUT_SECONDS = 6.0
  → successful-stop tests state their own drain budget
```

That correction — and the first remediation — covered the API file only. The
unit-level sibling in `tests/unit/runtime/test_project_capture.py` was never
audited for the same contract.

## Sibling audit

Every occurrence of `project_max_frames_per_segment` and
`recorder_cleanup_timeout_seconds` under `tests/` was classified:

```text
A  normal success path asserting COMPLETED
B  intentional timeout / failure-semantics test (its own small budget is the point)
C  startup / validation / backpressure / degraded-path test (no success terminal state)
```

Measured stop-path drain (this host, `VirtualAdapterConfig(rate_hz=2_000, seed=3)`,
0.1 s capture, 16-frame segments unless stated); the production default is 1.0 s:

```text
workload                                     drain                headroom vs 1 s
16-frame segments, serial, 20 runs           median 0.100 s       1.34x at max
                                             max    0.747 s
16-frame segments, 8 overlapping captures    median 0.912 s       1.07x at max
                                             max    0.930 s
64-frame segments, serial, 10 runs           max    0.066 s       15x
4096-frame segments, 20 kHz, 2 s (soak)      0.065 s             15x
```

| file / test | segment threshold | expected state | cleanup budget | changed? why |
| --- | --- | --- | --- | --- |
| `unit/runtime/test_project_capture.py::test_a_project_capture_creates_one_data_session_bound_to_the_stream` | 16 | `COMPLETED` | default 1.0 s | **yes** — the CI failure; measured 1.34x headroom |
| `...::test_a_startup_failure_after_the_session_was_created_leaves_it_failed` | 16 | `FAILED`, then `COMPLETED` | default 1.0 s | **yes** — its second capture is a success path on the 16-frame workload. The deliberate `recorder.open_failed` half is untouched |
| `...::test_repeated_project_captures_create_independent_sessions` | 16 | `COMPLETED` ×2 | default 1.0 s | **yes** — same workload, two stops |
| `...::test_stopping_a_project_capture_twice_is_safe` | 16 | `COMPLETED` | default 1.0 s | **yes** — same workload |
| `...::test_a_project_capture_does_not_depend_on_a_stream_client` | 64 | `COMPLETED` | default 1.0 s | no — measured 15x headroom |
| `...::test_the_realtime_stream_keeps_flowing_during_a_project_capture` | 64 | no terminal assertion | default 1.0 s | no — asserts only that the stream flows; a missed deadline does not raise |
| `...::test_saturated_archive_fails_the_project_session_without_stopping_capture` | 4 | `FAILED` (by design) | explicit 0.05 s | no — B: the small budget *is* the failure semantics |
| `...::test_recording_targets_are_mutually_exclusive`, `test_an_invalid_project_target_…`, `test_a_missing_project_path_…`, `test_an_unusable_project_segment_threshold_…`, `test_a_project_that_cannot_open_a_session_…` | — | structured error | — | no — C: no success terminal state |
| `unit/api/test_capture_project_target.py` (both tests) | 16 | `COMPLETED` | explicit 6.0 s | no — already corrected by PR #9 |
| `integration/test_project_backed_capture.py` success paths | 32 | `COMPLETED` | explicit 6.0 s | no — already corrected by `fc98f8e` |
| `integration/test_project_backed_capture.py` case B / case C / write-fault / saturation | 32 | not `COMPLETED` (by design) | default or explicit 0.5 s | no — B/C |
| `integration/test_project_recording_soak.py` | 4096 | `COMPLETED` | default 1.0 s | no — measured 0.065 s drain; 15x headroom |
| `integration/test_recorder_cleanup_timeout.py` (all) | 32 | mixed, by design | explicit 0.8 s / 5.0 s / window | no — B: this file *is* the timeout-semantics suite |
| `integration/test_recorder_independence.py`, `integration/test_runtime_capture.py` | — | failure/validation | explicit | no — B/C |

The measurement is what removed two tests from the change set: a first pass
assumed every small-segment success path needed an override, and the probe showed
the 64-frame and soak workloads sit 15x inside the default. Only the 16-frame
success paths were changed.

## Reproduction record

### Original failure (first remediation)

Natural repetition did not reproduce it on this machine:

```text
target test, repeated                  20/20 passed
target module, repeated                10/10 passed
target test, 16 parallel processes     16/16 passed
```

### Sibling failure (this remediation) — reproduced under controlled contention

The unit sibling *was* reproduced locally, by oversubscribing the CPU with 16
busy processes while running the exact workload with the production-default 1 s
budget:

```text
cpu_count = 16, burners = 16

baseline, no burners, 1 s budget       0 failed / 50 runs   max stop 0.561 s
loaded,   16 burners, 1 s budget       (114 runs total across three experiments)
                                        2 failed, max stop 1.059 s and 1.079 s
loaded,   16 burners, 6 s budget       0 failed / 80 runs   max stop 0.951 s
```

Both observed failures crossed the budget with the drain itself: the failing
runs' stops took **1.059 s** and **1.079 s** against a 1.0 s deadline, i.e. the
failure rate is low and clusters exactly where the distribution crosses 1 s —
which is what an intermittent, contention-driven failure looks like. The same
experiment produced 0 failures in 114 runs without the extra load.

This is a *local* reproduction of the terminal state the CI run reported. It is
recorded as such, not as a claim about the specific CI attempt's internals.

### Measured on the CI runner itself

Local reproduction of the *budget* path was possible under emulated contention,
but the post-merge CI failure could not be reproduced locally at all (16-core,
2-core-pinned and CPU-oversubscribed runs alike). The FIX-1 branch therefore
carried a one-off diagnostic test that ran the same workload on the runner and
reported its timings into the CI log. Runner: `cpus=4`.

```text
[0] sleep=0.104s drain=0.253s segments=12 frames=186 state=completed failure=none
[1] sleep=0.100s drain=0.301s segments=13 frames=199 state=completed failure=none
[2] sleep=0.100s drain=0.280s segments=12 frames=185 state=completed failure=none
[3] sleep=0.100s drain=0.308s segments=13 frames=196 state=completed failure=none
[4] sleep=0.101s drain=0.237s segments=12 frames=191 state=completed failure=none
```

Two things follow, and both matter for reading the CI history honestly:

- On the runner this workload's drain is **0.24-0.31 s** (locally 0.09-0.13 s),
  so a 6 s budget carries roughly 20x headroom and the 1 s production default
  roughly 3x. The `sleep` is not stretched and the segment/frame counts match
  local runs: the workload behaves identically on the runner.
- The same run reported `1 failed, 2598 passed, 6 skipped` — the single failure
  being the diagnostic itself. Nothing else in the suite failed.

The diagnostic was removed in the following commit and was never part of the fix.

### CI attempts on the FIX-1 head

```text
attempt 1       5 failed, 2593 passed, 6 skipped in 362.63 s
                test_recorder_cleanup_timeout.py :: 4 tests
                  ("the finalization worker never entered its blocking phase" at a
                   15 s wait, "never finished its flush", two FAILED != COMPLETED)
                test_capture_project_target.py   :: 1 test (carries a 6 s budget)
attempt 2       1 failed, 2597 passed, 6 skipped in 268.88 s
                test_project_capture.py::test_repeated_project_captures_create_independent_sessions
diagnostic run  1 failed, 2598 passed, 6 skipped in 228.01 s  (the diagnostic only)
```

Attempts 1 and 2 failed on **different tests, with different counts**, and the
third run passed everything except the deliberate diagnostic. Together with the
runner measurement above, the remaining intermittent failures are not explained
by the cleanup budget: a 15 s thread-wait expiring, and a session turning FAILED
while its own drain measures 0.3 s, point at runner resource starvation rather
than at a deadline that is too small. That is a separate defect class from the
one this increment repairs, and it is recorded here as an open observation rather
than claimed fixed.

### Deterministic causal control

The causal path was also proved deterministically, independent of contention, by
shrinking the budget below an uncontended drain:

```text
budget = 0.05 s, 16-frame workload, no extra load
  -> state=failed  failure=recorder.cleanup_timeout   (3/3 runs)

budget = 1.0 s / 6.0 s, same workload, no extra load
  -> state=completed                                  (all runs)
```

So: an expired cleanup budget is sufficient and necessary to produce the
observed `FAILED`, and the recorded failure code on that path is
`recorder.cleanup_timeout`.

The deterministic recorder suite covers the same path structurally:

```text
tests/integration/test_recorder_cleanup_timeout.py
  blocked finalization + expired deadline
    → recorder.cleanup_timeout → durable FAILED
  slow but inside the deadline
    → COMPLETED
```

## Root cause

```text
Trigger
  Contended CI execution of a test whose 16-frame segment threshold creates
  pathological finalization work, in a file the earlier remediation did not audit.

Mechanism
  The test inherited RuntimeService's 1 s production cleanup default. Under
  runner load the bounded drain/close could expire before the terminal decision
  was durable; the deadline is applied to the drain, the recorder's arm_stop_deadline
  and the close.

Observable result
  ADR 0001's failure policy correctly placed the DataSession in FAILED because
  the recording could not demonstrate a completed terminal transition inside its
  configured budget.

Why it was intermittent
  Drain and SQLite/Parquet work vary with runner load. The PR run had 4m55s of
  wall clock and passed; the post-merge run took 5m08s and failed, on identical
  code. Locally, 2 of 114 contended runs failed and 0 of 114 uncontended runs did.

Authoritative invariant
  A timeout may prevent COMPLETED; it may not turn a truly completed session
  into FAILED. The test asserted immediate COMPLETED for a workload whose own
  configuration did not supply a budget sized for that workload.
```

## Fix

The four 16-frame success-path tests in
`tests/unit/runtime/test_project_capture.py` now state the cleanup budget their
own pathological segment workload needs:

```text
CLEANUP_TIMEOUT_SECONDS = 6.0
```

The two 64-frame tests and the soak test were left alone because the measurement
shows they are 15x inside the default budget.

No production file, timeout default, retry policy, test assertion, or Safety
invariant was changed. No assertion was weakened, and the fix does not add a
sleep, polling loop, retry, `xfail`, skip, or `continue-on-error`. The
failure-semantics tests (saturation, cleanup-timeout suite, write-fault cases)
keep their own deliberately small budgets and their `FAILED` assertions.

## Verification

Local, on the FIX-1 tree:

```text
target test, repeated ×3                            1 passed each
target module, repeated ×3                          15 passed each
focused suites (project_capture, api target, recorder, data,
  project_backed_capture, recorder_cleanup_timeout,
  recorder_independence)                            246 passed in 34.95 s
contended pytest, 12 busy processes, ×3             22 passed / 22 / 22, 0 failures
controlled-contention probe, 16 busy processes      2 failed / 114 runs at 1 s budget
                                                    0 failed /  80 runs at 6 s budget
full pytest                                         2603 passed, 1 skipped in 254.97 s
ruff check runtime tests tools                      All checks passed
mypy runtime tools/agent                            Success: no issues found in 95 source files
frontend  lint / typecheck / test / build           passed; 162 tests; built
rust      fmt --check / clippy / cargo test         passed; 31 tests
```

The single skip is `tests/unit/dbc/test_dbc_asset.py`'s directory-link case,
which this Windows host cannot create (`WinError 1314`); it is environmental and
pre-existing, unrelated to this change.

Local, on the FIX-2 tree:

```text
the three target files, repeated ×3                 34 passed / 34 / 34
same three under 12 busy processes, ×3              34 passed / 34 / 34, 0 failures
harness teardown probe, pre-FIX-2 vs harness        gate released False -> True,
                                                    worker joined False -> True,
                                                    teardown 1.009 s -> 0.000 s
executor probe, 5 concurrent waits                  +4 threads -> +0 threads
full pytest                                         2604 passed, 1 skipped in 238.93 s
ruff check runtime tests tools                      All checks passed
mypy runtime tools/agent                            Success: no issues found in 95 source files
frontend  lint / typecheck / test / build           passed; 162 tests; built
rust      fmt --check / clippy / cargo test         passed; 31 tests
```

CI stability is a separate, weaker claim than "it passed once": the FIX-2 head is
required to run the same SHA three times, all green, before this increment is
offered for acceptance. Those run ids are recorded in the FIX-2 completion report
rather than asserted here.

GitHub:

```text
PR #9 head        run 35320171207   all four checks success
post-merge main   run 35321725801   Runtime / Python failure   <- repaired here
```

The FIX-1 head's own run is what this branch exists to produce; its result lives
in the pull request's check rollup rather than being asserted here.

## RELIABILITY-01-FIX-2 — deterministic finalization harness

FIX-1 sized the budget. FIX-2 makes the *harness* deterministic, because the two
red attempts on the FIX-1 head failed on different tests with different counts and
one of them expired a 15 s wait — a signature that points at the test rig rather
than at the deadline.

### P1-1 — the fault deadline no longer leaks into a healthy path

`test_recorder_cleanup_timeout.py` used one constant for two different questions:

```text
FAULT_CLEANUP_TIMEOUT_SECONDS    0.8  a deadline a failure injection *wants* to expire
HEALTHY_CLEANUP_TIMEOUT_SECONDS  6.0  the stop budget a capture needs to reach COMPLETED
```

Two tests ran a *recovery capture* through the same 0.8 s runtime that had just
been used to force a timeout, and then required `COMPLETED` from it:

```text
test_a_new_capture_is_refused_while_a_finalize_worker_is_still_running
test_a_pending_finalization_blocks_the_next_capture_and_repeated_stop_is_safe
```

Both now keep the fault half on the fault runtime — the refusal, the pending flag
and its release are all still asserted there — and run the recovery capture that
must complete on a runtime that states the healthy budget. The blocking contract
and the completion contract are each pinned by the runtime that should own them;
neither assertion was relaxed, and no production default was touched.

Tests whose `COMPLETED` is produced by a worker clearing its own gate rather than
by a deadline (in-flight terminal commit, shutdown-while-pending, the slow-but-
in-time finalize) keep the fault budget, and their docstrings say why.

### P1-2 — the teardown owns every gate

`asyncio` cannot cancel a worker thread. A test that failed while a worker was
parked on `BlockingFinalize.release` or `FlushCompletedSeam.terminal_unblocked`
left that worker — and its default-executor slot, and sometimes a held SQLite write
lock — behind for the next test; the gate's own 30 s/60 s timeout was the only
thing that eventually freed it. Measured on the pre-FIX-2 shape:

```text
                              teardown   gate released   worker joined (<1 s)
pre-FIX-2 shape (no harness)   1.009 s   False           False
harness shape                  0.000 s   True            True
```

Every parkable resource is now created through `FinalizationHarness`, whose
`close()` runs in a `finally` via the `finalization_harness` async context
manager: it releases every gate, rolls back and closes the SQLite write lock,
undoes the monkeypatch, and joins the finalization worker with a bounded wait. A
failing body can no longer strand anything for the next test to trip over.

`test_the_harness_releases_a_parked_worker_when_the_body_fails` pins the property:
it drives the exact failure (a worker parked, the gate closed, the body raising),
then asserts the gate is released, nothing is pending, and the worker is joined.

### FIX-2 §4 — the waiting side no longer costs an executor thread

The waits used `asyncio.to_thread(threading.Event.wait, …)`, which occupies a
default-executor worker for the whole wait. Five concurrent waits, measured while
they were in flight:

```text
asyncio.to_thread(Event.wait)         1 -> 5   (+4 executor threads)
asyncio.Event + call_soon_threadsafe  5 -> 5   (+0)
```

`WorkerSignals` keeps the blocking side a `threading.Event` — no worker can await
— and signals the async side through `loop.call_soon_threadsafe`, so a parked
worker no longer costs a waiting thread. Production behaviour is untouched: this
is entirely test-side plumbing.

### FIX-2 §5 — a failure now explains itself

`assert_completed()` replaces the bare `assert stored.state is COMPLETED` in all
three files. The condition and its strength are unchanged; what is added is a
diagnosis, because a bare `FAILED != COMPLETED` in a CI log said nothing about
which stage consumed the budget:

```text
session <id> is <state>, not COMPLETED
[capture_state=… failure=<code> message=… context=… finalization_pending=…]
```

No `sleep`, retry, `xfail`, `skip` or `continue-on-error` was added anywhere, and
no failure-semantics budget was widened.

## Protected integration and closure

The accepted head was intact when the protected integration ran:

```text
accepted head              07a95f675527cb14c742edc93f108f5c1260ef06
PR #10                     OPEN, not draft, head 07a95f675527cb14c742edc93f108f5c1260ef06
PR base                    main @ 831930ee6a2b728837087c560ff9b4b9af37c61a
Quality Gate on PR head    SUCCESS (run 35341733910)
merge method               merge commit — no admin bypass, no force push, ruleset untouched
merge commit               d22e989910ae896d40f59e718a502993126dd055
merged at                  2026-09-18T13:14:05Z
```

The same-SHA stability gate the increment was accepted against:

```text
35341733910  pull_request       Runtime=success Frontend=success Rust=success Quality Gate=success
35342314866  workflow_dispatch  Runtime=success Frontend=success Rust=success Quality Gate=success
35342873157  workflow_dispatch  Runtime=success Frontend=success Rust=success Quality Gate=success
```

Post-merge `main` CI — the hard gate, and the exact place this whole record
started, since the original failure was itself a post-merge run:

```text
run                     35349031072
event / attempt         push / attempt 1
main SHA                d22e989910ae896d40f59e718a502993126dd055
Runtime / Python        success   pytest 2599 passed, 6 skipped in 289.83 s
                                  ruff  All checks passed
                                  mypy  Success: no issues found in 95 source files
Frontend / TypeScript   success   12 test files, 162 tests passed
Desktop System / Rust   success   31 tests passed (28 + 0 + 3 + 0)
Quality Gate            success
```

The six skips are the known packaged-runtime smoke tests (`canx-runtime.exe` not
staged in that job), unchanged from every earlier run.

The docs-only closeout that carried this record into `main`, and the final `main`
CI that closes the whole sequence:

```text
closeout PR          #11  docs(reliability): close RELIABILITY-01 after protected integration
closeout head        060e29b5f21308978100af7d228183732f9e1cfa
closeout PR CI       35349886546 — SUCCESS (attempt 1)
closeout merge       0ab81aea600c2a2bae7585dbd9072bde5aeaff46 (2026-09-18T13:30:24Z)
files changed        docs/engineering/RELIABILITY_CAPTURE_FINALIZATION.md
                     docs/PROJECT_STATE.md          (documentation only)
final `main` CI      35350608526 — SUCCESS (attempt 1), all four jobs
```

### What the closure does and does not establish

```text
establishes   the project-owned test instability was hardened
              the accepted SHA met the pre-declared 3x same-SHA stability gate
              the protected merge produced a green post-merge main CI
does not      establish that hosted-runner scheduling variance is gone
              establish that the failure class cannot recur at some lower rate
```

Residual hosted-runner scheduling variance remains an environment uncertainty,
not a property this repository controls.

## Remaining uncertainty

- The post-merge CI log does not contain a recorder failure code, so this record
  does not claim that `recorder.cleanup_timeout` was *observed in CI* on run
  35321725801, nor which internal stage (drain versus terminal commit) consumed
  the budget on that attempt. What is established: the failing assertion's
  terminal state, the workload/budget mismatch, a local reproduction of that
  terminal state under controlled contention, and the deterministic code that
  this budget-expiry path produces.
- Local contention is emulated with busy processes on a 16-vCPU host, not a real
  CI runner. It reproduces the *class* of the budget failure (2 of 114 loaded
  runs) rather than the runner's exact timing distribution. The runner was
  separately measured (above): its drain for this workload is 0.24-0.31 s.
- The two red attempts on the FIX-1 head failed on different tests with different
  counts, and include a failure whose own wait window is 15 s. With the runner
  drain measured at ~0.3 s, those failures cannot be attributed to the cleanup
  budget. They are recorded as an **open** runner-starvation observation; no
  claim is made that this increment fixes them, and no assertion, wait window or
  failure-semantics budget in the affected tests was changed to make them pass.
  FIX-2 removes the harness-owned part of that exposure — a failed test no longer
  leaves a parked worker or a held lock behind, and a parked worker no longer
  costs an executor thread — but runner-side starvation itself is outside what a
  test harness can fix, and is not claimed as fixed.
- Three consecutive green CI runs on one SHA is evidence about that SHA on
  `windows-latest`, not a proof that the flake class is gone. The failure rate was
  always low (2 of 114 contended local runs); three runs cannot exclude it.
- The reproduction rate is low by nature; a green repetition is not evidence that
  the historical CI failure did not occur. The historical run remains evidence.

RELIABILITY-01 closed with the external verdict `Final Acceptance: PASS` /
`Status: CLOSED`. The reliability prerequisite for AGENT-02 is **CLEARED**;
AGENT-02 itself has **not started**. `DOC-GOV-01` — Documentation & Agent Context
Governance — is the immediate next engineering task and is **NOT STARTED · READY**.
`V0.3-12` and `CD-01` are **NOT STARTED**.
