# ADR 0001: Recorder Pressure Policy

- Status: Accepted
- Date: 2026-09-15
- Decision owners: CAN-X sole author and runtime architecture
- Scope: V0.1.1 acceptance hardening

## Context

The V0.1 capture loop publishes to subscribers in registration order. A lossless
subscriber waits indefinitely when its bounded queue is full. If the Recorder
stops making progress, the archive queue eventually blocks capture and every
later subscriber. The existing test releases the slow Recorder before the queue
reaches capacity, so it does not cover this failure.

PRD priority is CAN RX integrity first, Recorder second, and UI freshness after
both. SPEC requires Recorder failures and queue pressure to be observable. A
finite machine cannot preserve an unlimited recording backlog, so saturation
must become an explicit session failure instead of an indefinite wait or silent
loss.

## Decision

CAN-X will continue capture after a Recorder pressure or write failure, while
marking the active capture session `degraded` and the Recorder `failed`.

The archive subscriber remains bounded and lossless while healthy. A full
archive queue may wait only for a configured deadline. When that deadline
expires:

1. the archive subscriber emits a structured `recorder_backpressure` failure;
2. the failed subscriber is removed from future capture fan-out;
3. the Recorder consumer is cancelled and closed with bounded cleanup;
4. the Recorder state becomes `failed` and retains a diagnostic reason;
5. the capture session becomes `degraded` but CAN ingress and the lossy UI
   stream continue;
6. metrics expose the event, queue peak, failure reason, failure sequence, and
   uncommitted frame count.

Fan-out for one frame is concurrent across active subscribers. A lossless
archive wait therefore cannot postpone delivery of that same frame to the UI
stream. Capture may pause for at most the archive deadline before degradation;
it never waits forever.

Recorder write errors use the same state transition. No later frame is described
as recorded after the failure. `captured_frames` continues to represent frames
normalized by capture; `recorded_frames` represents committed chunks only.

## Alternatives considered

### Fail the entire capture session

This prevents capture from continuing without a Recorder, but discards later
bus observations. It conflicts with the PRD ordering that places CAN RX above
Recorder, so it is rejected for V0.1.1.

### Grow the queue or spill to another store

Increasing capacity only postpones saturation. A second spill format would add
another Recorder and recovery protocol outside this acceptance-hardening scope.

### Make Recorder lossy

Silent or counted frame dropping inside a healthy recording contradicts the
lossless Recorder contract and is rejected.

## Consequences

- A degraded session remains useful for live observation but its recording is
  incomplete and must never be represented as valid evidence.
- API clients can distinguish idle, running, degraded, and failed capture state.
- Stop and shutdown must cancel failed Recorder work instead of draining an
  impossible backlog.
- Long-duration disk behavior still requires real-device soak testing; the
  V0.1.1 synthetic suite proves state and lifecycle semantics only.

