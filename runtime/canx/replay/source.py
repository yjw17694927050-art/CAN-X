"""Bounded frame sources for offline replay.

A replay source is the adapter between the read-only query domain and the
scheduler: it hands over the query domain's **own** bounded pages, one at a time,
so the scheduler never materializes a whole data session and the page cursor is
the only place read progress lives.

:class:`SessionReplaySource` is the production source. It reads one persisted
``DataSession`` through :class:`~canx.query.service.QueryService` — the one
bounded pagination implementation the project already has — in the engine's
deterministic ``sequence ASC`` order. Nothing in this module, or anywhere else in
the replay package, touches an adapter, a bus or a transmit path: offline replay
has no device to talk to.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Generator

from canx.query.model import FrameFilter, FrameQuery, FrameQueryPage
from canx.query.service import QueryService
from canx.replay.errors import ReplayValidationError
from canx.replay.model import MAX_REPLAY_PAGE_SIZE


class ReplaySource(ABC):
    """The recorded frames of one session, delivered as bounded pages.

    ``pages()`` is a generator so that a replay which stops early closes the
    stream instead of leaving it suspended, and so that the scheduler can only
    ever hold one page at a time.
    """

    def __init__(self) -> None:
        self._closed = False

    @property
    def closed(self) -> bool:
        """Return whether :meth:`close` has been called on this source."""
        return self._closed

    @abstractmethod
    def pages(self) -> Generator[FrameQueryPage]:
        """Yield bounded pages in replay order, one page per iteration.

        The final page must report ``has_more = False``; a stream that ends
        without one is refused by the scheduler rather than treated as complete.
        """

    def close(self) -> None:
        """Release anything this source holds. Idempotent."""
        self._closed = True


class SessionReplaySource(ReplaySource):
    """One persisted data session, read through bounded query pages.

    The source holds no open handle between pages: every page is one short-lived
    :class:`~canx.query.service.QueryService` call, which is what keeps a long
    replay's memory bound to a single page.
    """

    def __init__(
        self,
        query_service: QueryService,
        session_id: str,
        *,
        page_size: int,
    ) -> None:
        super().__init__()
        _require_page_size(page_size)
        self._service = query_service
        self._page_size = page_size
        # Building the filter validates the session id here, at construction,
        # rather than in the middle of a replay.
        self._filter = FrameFilter(session_id=session_id)

    @property
    def session_id(self) -> str:
        """Return the session this source reads."""
        return self._filter.session_id

    @property
    def page_size(self) -> int:
        """Return how many frames one page may carry."""
        return self._page_size

    def pages(self) -> Generator[FrameQueryPage]:
        """Yield the session's frames, one bounded page at a time."""
        cursor: int | None = None
        while True:
            page = self._service.query_frames(
                FrameQuery(
                    filter=self._filter,
                    after_sequence=cursor,
                    limit=self._page_size,
                )
            )
            yield page
            if not page.has_more:
                return
            cursor = page.next_after_sequence


def _require_page_size(page_size: object) -> None:
    if (
        not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or page_size <= 0
        or page_size > MAX_REPLAY_PAGE_SIZE
    ):
        raise ReplayValidationError(
            "page_size must be a positive integer within one bounded query page.",
            code="replay.invalid_page_size",
            details={
                "page_size": repr(page_size),
                "max_page_size": MAX_REPLAY_PAGE_SIZE,
            },
        )
