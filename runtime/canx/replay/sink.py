"""The offline replay output port.

A replay hands every scheduled frame to exactly one sink and to nothing else.
That is the whole output surface of the domain: there is no adapter, no bus and
no transmit path here, so "an offline replay re-emits recorded traffic" is a
statement about a sink the caller supplied, never about a device.

Cancellation and completion are not sink concerns: the sink accepts frames, and
the :class:`~canx.replay.model.ReplayReport` a run returns says what happened.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from canx.replay.model import ReplayFrameEvent


@runtime_checkable
class ReplaySink(Protocol):
    """A consumer of replayed frames.

    The protocol is runtime-checkable so an unusable sink is refused when the
    replay is built, before a single frame has been read from the recording.
    """

    def emit(self, event: ReplayFrameEvent) -> None:
        """Accept one scheduled frame event.

        A sink that raises fails the whole replay: the frame is lost, so the run
        ends ``FAILED`` and never claims to have delivered the recording.
        """
