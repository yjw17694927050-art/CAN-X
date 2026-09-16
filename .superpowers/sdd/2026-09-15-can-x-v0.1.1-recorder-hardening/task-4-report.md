# Task 4 report: saturation integration and bounded shutdown

Status: implemented and verified against `task-4-brief.md` on base `c1df7f5`.

## Changes

- RuntimeService accepts `archive_capacity`; omitted/None preserves the existing
  production sizing rule `max(batch_size * 4, 1000)`. Existing archive publication
  timeout defaults to 1.0 seconds; new recorder cleanup timeout defaults to 1.0
  seconds. Explicit capacity must be positive and deadlines finite and positive.
- Failure preserves the queue depth from SubscriberFailure before queue shutdown,
  so the recorder peak records the actual saturation depth rather than the empty
  queue observed after disable.
- Archive failure counts queued, in-flight and rejected frames through the existing
  first-failure path, cancels archive work, and schedules one owned cleanup task.
  Cleanup joins archive cancellation then applies the configured Recorder stop
  deadline. Cleanup errors preserve the first recorder diagnosis.
- Capture stop only joins live consumer work, bounds draining, records
  `recorder.cleanup_timeout` if drain/close times out without an earlier failure,
  joins cleanup, clears task/session references, and resets all current queue gauges.
  Failure state and cumulative failure/uncommitted counters survive stop.

## Acceptance evidence

`test_archive_saturation_degrades_and_stops_without_releasing_recorder` runs with
capacity 4, batch size 1, Virtual Capture requested rate 50,000 Hz, publication
deadline 0.05 seconds and cleanup deadline 0.05 seconds. The fixture has no release
mechanism: first append waits on an unset event forever, ending only on owner
cancellation. Two cases cover ordinary close and close that blocks until cancelled.

Within `asyncio.wait_for(..., timeout=1.0)` the test verifies:

- Recorder queue peak exactly 4; failure context independently records capacity
  and pre-disable depth 4.
- Exactly one pressure event and one recorder failure; recorder failed while
  capture remains active/degraded.
- Exactly 6 uncommitted frames: one blocked append, four queued, one rejected.
  Recorded frames remain zero and archive loss is explicitly counted.
- Current service failure, metrics and `/runtime/status` agree on code/context;
  failure stream_id matches the stream_id returned by this capture start.
- A stream batch arrives before degradation, and a later batch has a sequence
  beyond the rejected frame's sequence, proving it was generated after failure.
- Recorder cleanup completes while capture remains degraded and active.

Stop is separately wrapped in a one-second hard timeout. Assertions verify no
session, consumer references, cleanup-task reference, or new live asyncio task
remains, all current queue gauges are zero, and failure counters/state persist.

`test_stop_bounds_blocked_archive_before_publication_deadline` starts the same
kind of permanently blocked append, then requests stop before publication failure.
It proves drain timeout is handled without waiting for the publication deadline,
records explicit uncommitted loss, and leaves no live task/session.

Eight validation cases reject zero/negative capacity and nonpositive/nonfinite
deadline controls before starting a capture session.

## TDD record and validation

1. First saturation RED: `test_recorder_independence.py` reported 2 failed,
   2 passed; missing `archive_capacity` constructor parameter.
2. After adding constructor plumbing only, saturation remained RED: recorded peak
   was 0 rather than 4, and blocking close exceeded the one-second stop timeout.
3. Stop-before-pressure RED: `test_runtime_capture.py` reported 1 failed,
   5 passed; existing consumer gather exceeded the one-second stop timeout.
4. After bounded cleanup/peak preservation: the two requested integration files
   reported 10 passed.
5. Validation RED: all 8 invalid control cases failed to raise ValueError.
   Added validation, then requested integration files reported 18 passed.
6. Final full regression: `.venv\Scripts\python.exe -m pytest -q` reported
   **106 passed in 4.36s**.
7. Final changed-file Ruff lint passed, Ruff format check reported all 3 files
   formatted, mypy on runtime/canx/runtime/service.py passed, and `git diff --check`
   passed. No test warnings were emitted.

## Concerns / boundaries

- Deadlines use cooperative asyncio cancellation. This proves the brief's blocked
  async Recorder scenario, including a close coroutine that releases its real file
  in cancellation cleanup. Python cannot forcibly interrupt a cancellation-resistant
  coroutine or an operating-system disk operation running in `asyncio.to_thread`.
  This test does not establish a hard wall-clock bound or filesystem reclamation
  for an indefinitely hung OS disk thread.
- Queue capacities were not increased; no pressure deadline was relaxed and no
  Recorder was manually released to make the tests finish. The 50,000 Hz value is
  the requested VirtualAdapter input rate, not a claim of measured throughput.
- This task changes only the three requested Python files plus this report. No
  UI, transport schema, benchmark or architecture documents were modified.
