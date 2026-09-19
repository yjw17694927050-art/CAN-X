"""Typed read model for the recorder control plane.

This module owns no capture authority and no storage. It defines *what the
runtime may report* about its recorder — the active/inactive fact, the stream and
data-session identity, the recorder's own lifecycle state and frame counter, the
retained failure, whether a project finalization has settled, and the durable
session state — and the HTTP boundary projects that shape.

Two rules shape it:

* **It reports, it does not own.** There is no ``start``, ``stop`` or ``append``
  here. The Capture Runtime remains the single owner of recording ingress; this is
  only the observability seam onto it.
* **Durable only.** Every non-live field comes from a value the runtime or the
  project database already holds. Nothing is inferred by scanning Parquet data, so
  a state SQLite has not written can never be reported as though it had been.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from canx.data.model import DataSessionState
from canx.recorder.msgpack_recorder import RecorderFailure, RecorderState


class FinalizationState(StrEnum):
    """Whether a project recording's terminal state has settled.

    ``IDLE``    — no project recording has been finalized by this recorder.
    ``PENDING`` — a finalization worker is still running, so the durable terminal
                  state is not yet known and must not be guessed at.
    ``SETTLED`` — the terminal decision was taken and the durable row reflects it.
    """

    IDLE = "idle"
    PENDING = "pending"
    SETTLED = "settled"


@dataclass(frozen=True, slots=True)
class RecorderStatus:
    """One immutable, durable snapshot of the recorder control plane.

    ``active`` is the capture ingress fact (the Capture Runtime owns it); the
    identity and counter fields describe the recording the runtime currently owns;
    ``finalization_state`` says whether that recording's terminal write has
    settled; and ``durable_session_state`` is the row the data domain actually
    holds — the only place a ``COMPLETED`` / ``FAILED`` verdict may come from.
    """

    active: bool
    stream_id: str | None
    data_session_id: str | None
    lifecycle_state: RecorderState
    recorded_frame_count: int
    failure: RecorderFailure | None
    finalization_state: FinalizationState
    durable_session_state: DataSessionState | None
