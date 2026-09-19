"""CAN-X offline replay domain.

Public surface: the replay lifecycle and its models, the deterministic scheduler,
the clock abstraction, the bounded frame source and the sink port, plus the
replay failure contract.

This domain is **offline only**. It reads a persisted
:class:`~canx.data.model.DataSession` through the bounded query service and hands
canonical frames to a caller-supplied sink. It never opens an adapter, never
touches a bus and never transmits, and it has no dependency on
:mod:`canx.devices`, :mod:`canx.transport` or :mod:`canx.safety` — real CAN
replay, which would need a transmit path and therefore a safety gate, is a later
phase.
"""

from canx.replay.cancellation import ReplayCancellation
from canx.replay.clock import MonotonicReplayClock, ReplayClock, VirtualReplayClock
from canx.replay.errors import (
    ReplayError,
    ReplayProjectError,
    ReplaySchedulerError,
    ReplaySessionError,
    ReplaySinkError,
    ReplaySourceError,
    ReplayStateError,
    ReplayValidationError,
)
from canx.replay.model import (
    DEFAULT_REPLAY_PAGE_SIZE,
    MAX_REPLAY_PAGE_SIZE,
    EmptySessionPolicy,
    ReplayConfig,
    ReplayFrameEvent,
    ReplayReport,
    ReplayState,
    SessionCompletionPolicy,
)
from canx.replay.session import ReplaySession
from canx.replay.sink import ReplaySink
from canx.replay.source import ReplaySource, SessionReplaySource

__all__ = [
    "DEFAULT_REPLAY_PAGE_SIZE",
    "MAX_REPLAY_PAGE_SIZE",
    "EmptySessionPolicy",
    "MonotonicReplayClock",
    "ReplayCancellation",
    "ReplayClock",
    "ReplayConfig",
    "ReplayError",
    "ReplayFrameEvent",
    "ReplayProjectError",
    "ReplayReport",
    "ReplaySchedulerError",
    "ReplaySession",
    "ReplaySessionError",
    "ReplaySink",
    "ReplaySinkError",
    "ReplaySource",
    "ReplaySourceError",
    "ReplayState",
    "ReplayStateError",
    "ReplayValidationError",
    "SessionCompletionPolicy",
    "SessionReplaySource",
    "VirtualReplayClock",
]
