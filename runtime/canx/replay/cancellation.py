"""A one-way cancellation signal for offline replay.

Cancellation is a request from the caller, not a fault of the replay: the replay
stops at the next frame boundary, reports the terminal
:attr:`~canx.replay.model.ReplayState.CANCELLED` state together with what it had
already delivered, and emits nothing afterwards.

The signal is a plain object on purpose. It is handed to whoever has to be able
to stop a replay — a sink, a UI controller, a supervising task — instead of that
component reaching back into the replay object.
"""

from __future__ import annotations


class ReplayCancellation:
    """A permanent, one-way cancellation flag shared with a running replay.

    The flag is not a synchronisation primitive: it is a single boolean, which is
    enough because reading and writing it are atomic in CPython and the replay
    only acts on it at frame boundaries.
    """

    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        """Return whether cancellation has been requested."""
        return self._cancelled

    def cancel(self) -> None:
        """Request cancellation. Idempotent, and never undone."""
        self._cancelled = True
