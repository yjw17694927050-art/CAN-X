"""The bounded page source: one page at a time, never a whole session.

The source is the only place a replay reads stored data, so "a large session
never lands in memory" is asserted here at the level that decides it: how many
frames a single query may return, and how often the cursor advances. The real
QueryService wiring is covered by the integration suite; what is checked here is
the cursor walk itself.
"""

from __future__ import annotations

from collections.abc import Generator, Sequence

import pytest
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.query.errors import QueryValidationError
from canx.query.model import MAX_QUERY_ROWS, FrameFilter, FrameQuery, FrameQueryPage
from canx.replay.errors import ReplayValidationError
from canx.replay.source import ReplaySource, SessionReplaySource

SESSION_ID = "11111111-2222-3333-4444-555555555555"


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x100,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=1,
        data=bytes([sequence % 256]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=float(sequence),
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def page(sequences: Sequence[int], *, has_more: bool) -> FrameQueryPage:
    frames = tuple(frame(sequence) for sequence in sequences)
    return FrameQueryPage(
        session_id=SESSION_ID,
        frames=frames,
        has_more=has_more,
        next_after_sequence=frames[-1].sequence if has_more else None,
    )


class StubQueryService:
    """A query service that serves pre-built pages and records every request."""

    def __init__(self, pages: Sequence[FrameQueryPage]) -> None:
        self._pages = list(pages)
        self.queries: list[FrameQuery] = []

    def query_frames(self, query: FrameQuery) -> FrameQueryPage:
        self.queries.append(query)
        return self._pages.pop(0)


def source(
    pages: Sequence[FrameQueryPage], *, page_size: int = 2
) -> SessionReplaySource:
    return SessionReplaySource(
        StubQueryService(pages),  # type: ignore[arg-type]
        SESSION_ID,
        page_size=page_size,
    )


def test_the_source_walks_the_cursor_until_the_final_page() -> None:
    pages = [
        page([0, 1], has_more=True),
        page([2, 3], has_more=True),
        page([4], has_more=False),
    ]
    service = StubQueryService(pages)
    replay_source = SessionReplaySource(service, SESSION_ID, page_size=2)  # type: ignore[arg-type]

    delivered = list(replay_source.pages())

    assert [len(one.frames) for one in delivered] == [2, 2, 1]
    assert [query.after_sequence for query in service.queries] == [None, 1, 3]
    assert all(query.limit == 2 for query in service.queries)
    assert delivered[-1].has_more is False


def test_the_source_asks_for_exactly_one_page_per_step() -> None:
    service = StubQueryService(
        [page([0, 1], has_more=True), page([2], has_more=False)]
    )
    replay_source = SessionReplaySource(service, SESSION_ID, page_size=2)  # type: ignore[arg-type]

    stream = replay_source.pages()
    assert service.queries == []

    first = next(stream)
    assert len(service.queries) == 1
    assert [item.sequence for item in first.frames] == [0, 1]

    second = next(stream)
    assert len(service.queries) == 2
    assert [item.sequence for item in second.frames] == [2]

    with pytest.raises(StopIteration):
        next(stream)
    assert len(service.queries) == 2


def test_the_filter_only_scopes_the_session_it_never_filters_its_frames() -> None:
    service = StubQueryService([page([0], has_more=False)])
    replay_source = SessionReplaySource(service, SESSION_ID, page_size=4)  # type: ignore[arg-type]

    list(replay_source.pages())

    frame_filter: FrameFilter = service.queries[0].filter
    assert frame_filter.session_id == SESSION_ID
    assert frame_filter.sequence_start is None
    assert frame_filter.sequence_end is None
    assert frame_filter.normalized_timestamp_start is None
    assert frame_filter.arbitration_ids is None
    assert frame_filter.channel_ids is None


def test_the_source_exposes_the_session_and_page_size_it_was_built_with() -> None:
    replay_source = source([page([0], has_more=False)], page_size=7)

    assert replay_source.session_id == SESSION_ID
    assert replay_source.page_size == 7
    assert replay_source.closed is False

    replay_source.close()

    assert replay_source.closed is True


@pytest.mark.parametrize("page_size", [0, -1, 1.5, True, MAX_QUERY_ROWS + 1])
def test_an_unusable_page_size_is_refused_at_construction(page_size: object) -> None:
    with pytest.raises(ReplayValidationError) as raised:
        SessionReplaySource(  # type: ignore[arg-type]
            StubQueryService([]), SESSION_ID, page_size=page_size  # type: ignore[arg-type]
        )

    assert raised.value.code == "replay.invalid_page_size"


def test_a_session_id_that_is_not_a_uuid_is_refused_at_construction() -> None:
    with pytest.raises(QueryValidationError):
        SessionReplaySource(StubQueryService([]), "not-a-uuid", page_size=2)  # type: ignore[arg-type]


def test_a_source_without_pages_reports_closed_by_default() -> None:
    class EmptySource(ReplaySource):
        def pages(self) -> Generator[FrameQueryPage]:
            return
            yield  # pragma: no cover - makes this method a generator

    empty = EmptySource()

    assert list(empty.pages()) == []
    assert empty.closed is False
    empty.close()
    assert empty.closed is True
