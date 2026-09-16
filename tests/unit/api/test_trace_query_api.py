"""HTTP contract for bounded historical Trace queries.

The desktop must be able to ask the runtime for a bounded page of canonical
frames from a persisted project, and every failure must arrive as the structured
CAN-X error envelope rather than a bare HTTP framework error. These tests drive
the real ASGI app over a real project whose Parquet segments were really written
by the data-session domain, so a passing case proves the whole chain:

``ASGITransport → POST /trace/query → ProjectService validation → QueryService →
DuckDB → Parquet → JSON``.

The response contract is asserted field by field: a canonical frame's payload is
uppercase hex (not ``bytes``), its direction is ``"rx"``/``"tx"`` (not an enum
``repr``), and no engine object is ever serialized.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from canx.api.app import create_app
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from httpx import ASGITransport, AsyncClient

STREAM_ID = "trace-api-stream"
FRAMES_PER_SEGMENT = 10
TOTAL_FRAMES = 20
SEGMENT_COUNT = TOTAL_FRAMES // FRAMES_PER_SEGMENT

ID_PATTERN: tuple[int, ...] = (
    0x0FF,
    0x100,
    0x101,
    0x17F,
    0x1FF,
    0x200,
    0x120,
    0x12F,
    0x130,
    0x220,
)

MASK = 0x7F0
MASK_VALUE = 0x120

#: Every field a canonical Trace frame exposes. The projection is the persisted
#: column set, so nothing in the Parquet schema is silently dropped by the API.
TRACE_FRAME_FIELDS = frozenset(
    {
        "sequence",
        "channel_id",
        "arbitration_id",
        "is_extended",
        "is_fd",
        "bitrate_switch",
        "error_state_indicator",
        "dlc",
        "data",
        "direction",
        "hardware_timestamp",
        "host_timestamp",
        "normalized_timestamp",
        "clock_domain",
        "timestamp_quality",
        "flags",
    }
)

#: Sequences whose id falls inside ``0x100..0x1FF`` (pattern indices 1, 2, 3, 4,
#: 6, 7 and 8 — ``0x130`` is inside the window, ``0x0FF``/``0x200``/``0x220`` are not).
IN_WINDOW_SEQUENCES = [1, 2, 3, 4, 6, 7, 8, 11, 12, 13, 14, 16, 17, 18]
#: Sequences whose id is inside the ``0x7F0 / 0x120`` masked group.
MASKED_SEQUENCES = [6, 7, 16, 17]

ERROR_FIELDS = {"code", "message", "details", "recoverable", "source"}


def frame(sequence: int) -> Frame:
    is_fd = sequence % 4 == 0
    hardware_timestamp = 1_000.0 + sequence if sequence % 3 == 0 else None
    return Frame(
        sequence=sequence,
        channel_id=f"can{sequence % 3}",
        arbitration_id=ID_PATTERN[sequence % len(ID_PATTERN)],
        is_extended=sequence % 5 == 0,
        is_fd=is_fd,
        bitrate_switch=is_fd and sequence % 8 == 0,
        error_state_indicator=is_fd and sequence % 12 == 0,
        dlc=3,
        data=bytes([sequence % 256, 0xAB, 0xCD]),
        direction=Direction.RX if sequence % 2 == 0 else Direction.TX,
        hardware_timestamp=hardware_timestamp,
        host_timestamp=2_000.0 + sequence,
        normalized_timestamp=sequence / 100.0,
        clock_domain="host.monotonic",
        timestamp_quality=(
            TimestampQuality.HARDWARE if hardware_timestamp else TimestampQuality.HOST
        ),
        flags=0,
    )


SOURCE_FRAMES: tuple[Frame, ...] = tuple(frame(sequence) for sequence in range(TOTAL_FRAMES))


def _write_session(handle: ProjectHandle) -> str:
    service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    for start in range(0, TOTAL_FRAMES, FRAMES_PER_SEGMENT):
        writer.append(
            FrameBatch.create(
                stream_id=STREAM_ID,
                frames=list(SOURCE_FRAMES[start : start + FRAMES_PER_SEGMENT]),
            )
        )
    writer.finalize()
    return writer.session_id


@pytest.fixture
def trace_project(tmp_path: Path) -> tuple[Path, str]:
    """A real two-segment project written by the data-session domain."""
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Trace Project") as handle:
        session_id = _write_session(handle)
    return root, session_id


def _body(
    root: Path,
    session_id: object,
    *,
    filters: dict[str, object] | None = None,
    **rest: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "project_path": str(root),
        "session_id": session_id,
        "filters": {} if filters is None else filters,
        "limit": rest.pop("limit", 1_000),
    }
    payload.update(rest)
    return payload


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://testserver")


async def test_a_query_returns_a_bounded_page_of_canonical_frames(
    trace_project: tuple[Path, str],
) -> None:
    root, session_id = trace_project
    async with _client() as client:
        response = await client.post("/trace/query", json=_body(root, session_id))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"session_id", "frames", "has_more", "next_after_sequence"}
    assert body["session_id"] == session_id
    assert body["has_more"] is False
    assert body["next_after_sequence"] is None
    assert [item["sequence"] for item in body["frames"]] == list(range(TOTAL_FRAMES))
    assert {frozenset(item) for item in body["frames"]} == {TRACE_FRAME_FIELDS}


async def test_a_frame_payload_is_uppercase_hex_and_its_direction_is_a_plain_string(
    trace_project: tuple[Path, str],
) -> None:
    root, session_id = trace_project
    async with _client() as client:
        response = await client.post("/trace/query", json=_body(root, session_id))

    first = response.json()["frames"][0]
    assert first["data"] == "00ABCD"
    assert isinstance(first["data"], str)
    assert first["direction"] == "rx"
    assert first["is_fd"] is True
    assert first["arbitration_id"] == 0x0FF
    assert first["normalized_timestamp"] == 0.0
    assert first["timestamp_quality"] == "hardware"
    assert first["hardware_timestamp"] == 1_000.0

    second = response.json()["frames"][1]
    assert second["data"] == "01ABCD"
    assert second["direction"] == "tx"
    assert second["hardware_timestamp"] is None
    assert second["timestamp_quality"] == "host"


async def test_the_page_reports_its_continuation_cursor(
    trace_project: tuple[Path, str],
) -> None:
    root, session_id = trace_project
    async with _client() as client:
        response = await client.post("/trace/query", json=_body(root, session_id, limit=5))

    body = response.json()
    assert [item["sequence"] for item in body["frames"]] == [0, 1, 2, 3, 4]
    assert body["has_more"] is True
    assert body["next_after_sequence"] == 4


async def test_a_second_page_continues_strictly_after_the_cursor(
    trace_project: tuple[Path, str],
) -> None:
    root, session_id = trace_project
    async with _client() as client:
        response = await client.post(
            "/trace/query", json=_body(root, session_id, limit=5, after_sequence=4)
        )

    body = response.json()
    assert [item["sequence"] for item in body["frames"]] == [5, 6, 7, 8, 9]


async def test_a_filtered_page_walk_reconstructs_the_single_query(
    trace_project: tuple[Path, str],
) -> None:
    root, session_id = trace_project
    filters = {"arbitration_id_start": 0x100, "arbitration_id_end": 0x1FF}

    async with _client() as client:
        single = await client.post(
            "/trace/query", json=_body(root, session_id, filters=filters)
        )
        collected: list[int] = []
        cursor: int | None = None
        while True:
            page = await client.post(
                "/trace/query",
                json=_body(root, session_id, filters=filters, limit=4, after_sequence=cursor),
            )
            body = page.json()
            collected.extend(item["sequence"] for item in body["frames"])
            if not body["has_more"]:
                break
            cursor = body["next_after_sequence"]

    assert [item["sequence"] for item in single.json()["frames"]] == IN_WINDOW_SEQUENCES
    assert collected == IN_WINDOW_SEQUENCES


async def test_an_id_range_filter_selects_exactly_the_window(
    trace_project: tuple[Path, str],
) -> None:
    root, session_id = trace_project
    async with _client() as client:
        response = await client.post(
            "/trace/query",
            json=_body(
                root,
                session_id,
                filters={"arbitration_id_start": 0x100, "arbitration_id_end": 0x1FF},
            ),
        )

    frames = response.json()["frames"]
    assert [item["sequence"] for item in frames] == IN_WINDOW_SEQUENCES
    assert {item["arbitration_id"] for item in frames} == {
        0x100,
        0x101,
        0x17F,
        0x1FF,
        0x120,
        0x12F,
        0x130,
    }


async def test_a_mask_filter_selects_the_masked_group(
    trace_project: tuple[Path, str],
) -> None:
    root, session_id = trace_project
    async with _client() as client:
        response = await client.post(
            "/trace/query",
            json=_body(
                root,
                session_id,
                filters={
                    "arbitration_id_mask": MASK,
                    "arbitration_id_mask_value": MASK_VALUE,
                },
            ),
        )

    frames = response.json()["frames"]
    assert [item["sequence"] for item in frames] == MASKED_SEQUENCES
    assert all((item["arbitration_id"] & MASK) == MASK_VALUE for item in frames)


async def test_a_combined_filter_applies_every_axis(
    trace_project: tuple[Path, str],
) -> None:
    """Only sequence 16 satisfies the range, the mask, can1, RX and CAN FD."""
    root, session_id = trace_project
    async with _client() as client:
        response = await client.post(
            "/trace/query",
            json=_body(
                root,
                session_id,
                filters={
                    "arbitration_id_start": 0x100,
                    "arbitration_id_end": 0x1FF,
                    "arbitration_id_mask": MASK,
                    "arbitration_id_mask_value": MASK_VALUE,
                    "channel_ids": ["can1"],
                    "directions": ["rx"],
                    "is_fd": True,
                },
            ),
        )

    frames = response.json()["frames"]
    assert [item["sequence"] for item in frames] == [16]
    assert frames[0]["channel_id"] == "can1"
    assert frames[0]["arbitration_id"] == 0x120


async def test_an_empty_result_reports_no_cursor(trace_project: tuple[Path, str]) -> None:
    root, session_id = trace_project
    async with _client() as client:
        response = await client.post(
            "/trace/query",
            json=_body(
                root,
                session_id,
                filters={"arbitration_id_start": 0x800, "arbitration_id_end": 0x8FF},
            ),
        )

    assert response.status_code == 200
    assert response.json() == {
        "session_id": session_id,
        "frames": [],
        "has_more": False,
        "next_after_sequence": None,
    }


async def test_a_summary_returns_the_count_and_the_bounds(
    trace_project: tuple[Path, str],
) -> None:
    root, session_id = trace_project
    async with _client() as client:
        response = await client.post(
            "/trace/summary",
            json=_body(
                root,
                session_id,
                filters={
                    "arbitration_id_mask": MASK,
                    "arbitration_id_mask_value": MASK_VALUE,
                },
            ),
        )

    assert response.status_code == 200
    assert response.json() == {
        "session_id": session_id,
        "matching_frame_count": 4,
        "first_sequence": 6,
        "last_sequence": 17,
        "first_normalized_timestamp": 0.06,
        "last_normalized_timestamp": 0.17,
    }


async def test_a_summary_of_an_empty_result_reports_no_bounds(
    trace_project: tuple[Path, str],
) -> None:
    root, session_id = trace_project
    async with _client() as client:
        response = await client.post(
            "/trace/summary",
            json=_body(
                root,
                session_id,
                filters={"arbitration_id_mask": MASK, "arbitration_id_mask_value": 0x700},
            ),
        )

    assert response.status_code == 200
    assert response.json() == {
        "session_id": session_id,
        "matching_frame_count": 0,
        "first_sequence": None,
        "last_sequence": None,
        "first_normalized_timestamp": None,
        "last_normalized_timestamp": None,
    }


async def test_a_query_over_an_empty_session_returns_an_empty_page(
    tmp_path: Path,
) -> None:
    root = tmp_path / "empty.canx"
    with ProjectService().create(root, display_name="Empty") as handle:
        writer = DataSessionService(handle.root).start(stream_id=STREAM_ID)
        session_id = writer.session_id
        writer.finalize()

    async with _client() as client:
        response = await client.post("/trace/query", json=_body(root, session_id))

    assert response.status_code == 200
    assert response.json()["frames"] == []
    assert response.json()["has_more"] is False


@pytest.mark.parametrize(
    ("filters", "expected_code"),
    [
        ({"arbitration_id_start": -1}, "query.invalid_arbitration_id"),
        ({"arbitration_id_end": 0x20000000}, "query.invalid_arbitration_id"),
        ({"arbitration_id_start": 0x1FF, "arbitration_id_end": 0x100},
         "query.invalid_arbitration_id_range"),
        ({"arbitration_id_mask": MASK}, "query.invalid_arbitration_id_mask"),
        ({"arbitration_id_mask_value": MASK_VALUE}, "query.invalid_arbitration_id_mask"),
        ({"arbitration_id_mask": -1, "arbitration_id_mask_value": 0},
         "query.invalid_arbitration_id"),
        ({"directions": ["sideways"]}, "query.invalid_direction"),
        ({"directions": []}, "query.invalid_direction"),
        ({"channel_ids": []}, "query.invalid_channel_id"),
        ({"channel_ids": [""]}, "query.invalid_channel_id"),
        ({"channel_ids": ["   "]}, "query.invalid_channel_id"),
        ({"arbitration_ids": []}, "query.invalid_arbitration_id"),
        ({"arbitration_ids": [0x20000000]}, "query.invalid_arbitration_id"),
        ({"normalized_timestamp_start": -1.0}, "query.invalid_timestamp"),
        ({"normalized_timestamp_end": -0.5}, "query.invalid_timestamp"),
        ({"normalized_timestamp_start": 2.0, "normalized_timestamp_end": 1.0},
         "query.invalid_timestamp_range"),
        ({"sequence_start": -1}, "query.invalid_sequence"),
        ({"sequence_start": 5, "sequence_end": 4}, "query.invalid_sequence_range"),
    ],
    ids=[
        "id-negative",
        "id-too-large",
        "range-inverted",
        "mask-without-value",
        "value-without-mask",
        "mask-negative",
        "direction-unknown",
        "direction-empty",
        "channel-empty-list",
        "channel-blank",
        "channel-whitespace",
        "id-set-empty",
        "id-set-out-of-range",
        "timestamp-start-negative",
        "timestamp-end-negative",
        "timestamp-inverted",
        "sequence-negative",
        "sequence-inverted",
    ],
)
async def test_an_invalid_filter_is_a_structured_400(
    trace_project: tuple[Path, str], filters: dict[str, object], expected_code: str
) -> None:
    root, session_id = trace_project
    async with _client() as client:
        response = await client.post(
            "/trace/query", json=_body(root, session_id, filters=filters)
        )

    assert response.status_code == 400
    body = response.json()
    assert set(body) == ERROR_FIELDS
    assert body["code"] == expected_code
    assert body["source"] == "query"
    assert body["recoverable"] is False


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"limit": 0}, "query.invalid_limit"),
        ({"limit": -1}, "query.invalid_limit"),
        ({"limit": 10_001}, "query.limit_exceeded"),
        ({"after_sequence": -1}, "query.invalid_sequence"),
    ],
    ids=["limit-zero", "limit-negative", "limit-above-maximum", "cursor-negative"],
)
async def test_an_invalid_page_request_is_a_structured_400(
    trace_project: tuple[Path, str], overrides: dict[str, object], expected_code: str
) -> None:
    root, session_id = trace_project
    async with _client() as client:
        response = await client.post("/trace/query", json=_body(root, session_id, **overrides))

    assert response.status_code == 400
    assert response.json()["code"] == expected_code
    assert set(response.json()) == ERROR_FIELDS


async def test_a_maximum_limit_page_is_accepted(trace_project: tuple[Path, str]) -> None:
    root, session_id = trace_project
    async with _client() as client:
        response = await client.post("/trace/query", json=_body(root, session_id, limit=10_000))

    assert response.status_code == 200
    assert len(response.json()["frames"]) == TOTAL_FRAMES


async def test_a_session_id_that_is_not_a_uuid_is_a_structured_400(
    trace_project: tuple[Path, str],
) -> None:
    root, _session_id = trace_project
    async with _client() as client:
        response = await client.post("/trace/query", json=_body(root, "not-a-uuid"))

    assert response.status_code == 400
    assert response.json()["code"] == "query.invalid_session_id"


async def test_an_unregistered_session_is_a_404_not_a_500(
    trace_project: tuple[Path, str],
) -> None:
    root, _session_id = trace_project
    async with _client() as client:
        response = await client.post(
            "/trace/query",
            json=_body(root, "11111111-2222-4333-8444-555555555555"),
        )

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "query.session_not_found"
    assert body["source"] == "query"
    assert body["details"]["session_id"] == "11111111-2222-4333-8444-555555555555"


async def test_an_invalid_project_is_a_structured_400(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-project"
    plain.mkdir()

    async with _client() as client:
        response = await client.post(
            "/trace/query", json=_body(plain, "11111111-2222-4333-8444-555555555555")
        )

    assert response.status_code == 400
    body = response.json()
    assert set(body) == ERROR_FIELDS
    assert body["code"].startswith("project.")
    assert body["source"] == "project"
    assert sorted(path.name for path in plain.iterdir()) == []


async def test_a_project_that_does_not_exist_is_a_structured_400(tmp_path: Path) -> None:
    async with _client() as client:
        response = await client.post(
            "/trace/query",
            json=_body(tmp_path / "absent.canx", "11111111-2222-4333-8444-555555555555"),
        )

    assert response.status_code == 400
    assert response.json()["code"] == "project.not_found"
    assert set(response.json()) == ERROR_FIELDS


async def test_a_missing_segment_is_a_storage_diagnostic_and_never_a_404_session(
    trace_project: tuple[Path, str],
) -> None:
    root, session_id = trace_project
    segment = DataSessionService(root).list_segments(session_id)[0]
    (root / segment.relative_path).unlink()

    async with _client() as client:
        response = await client.post("/trace/query", json=_body(root, session_id))

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "query.segment_missing"
    assert body["recoverable"] is True
    assert body["details"]["relative_path"] == segment.relative_path
    assert body["details"]["session_id"] == session_id


async def test_a_corrupt_segment_is_an_integrity_diagnostic(
    trace_project: tuple[Path, str],
) -> None:
    root, session_id = trace_project
    segment = DataSessionService(root).list_segments(session_id)[0]
    (root / segment.relative_path).write_bytes(b"not a parquet file")

    async with _client() as client:
        response = await client.post("/trace/query", json=_body(root, session_id))

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "query.segment_unreadable"
    assert body["recoverable"] is False
    assert body["source"] == "query"


async def test_an_interrupted_session_still_serves_its_committed_frames(
    tmp_path: Path,
) -> None:
    """A failed capture is still evidence; its committed segments stay queryable."""
    root = tmp_path / "interrupted.canx"
    with ProjectService().create(root, display_name="Interrupted") as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(
            FrameBatch.create(
                stream_id=STREAM_ID,
                frames=list(SOURCE_FRAMES[:FRAMES_PER_SEGMENT]),
            )
        )
        session_id = writer.session_id
        service.recover_incomplete_sessions()

    async with _client() as client:
        response = await client.post("/trace/query", json=_body(root, session_id))

    assert response.status_code == 200
    assert [item["sequence"] for item in response.json()["frames"]] == list(
        range(FRAMES_PER_SEGMENT)
    )


async def test_a_failed_session_still_serves_its_committed_frames(tmp_path: Path) -> None:
    """A failed capture is still engineering evidence.

    Frames that reached disk before the failure keep their analytic value, so the
    Trace surface must serve them without requiring the session to have
    completed. Only the unflushed tail disappears — it was never committed, so it
    is correctly invisible rather than silently missing.
    """
    root = tmp_path / "failed.canx"
    with ProjectService().create(root, display_name="Failed") as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(
            FrameBatch.create(
                stream_id=STREAM_ID, frames=list(SOURCE_FRAMES[:FRAMES_PER_SEGMENT])
            )
        )
        session_id = writer.session_id
        assert writer.fail() is True

        stored = service.get_session(session_id)
        assert stored.state is DataSessionState.FAILED
        assert stored.frame_count == FRAMES_PER_SEGMENT

    async with _client() as client:
        response = await client.post("/trace/query", json=_body(root, session_id))

    assert response.status_code == 200
    assert [item["sequence"] for item in response.json()["frames"]] == list(
        range(FRAMES_PER_SEGMENT)
    )


async def test_a_quote_in_a_channel_name_stays_data(trace_project: tuple[Path, str]) -> None:
    """A caller value is a bound parameter; the request must not become SQL."""
    root, session_id = trace_project
    async with _client() as client:
        injected = await client.post(
            "/trace/query",
            json=_body(root, session_id, filters={"channel_ids": ["can0' OR 1=1 --"]}),
        )
        literal = await client.post(
            "/trace/query", json=_body(root, session_id, filters={"channel_ids": ["can0"]})
        )

    assert injected.status_code == 200
    assert injected.json()["frames"] == []
    assert len(literal.json()["frames"]) > 0


async def test_an_error_response_never_leaks_a_storage_or_engine_exception(
    trace_project: tuple[Path, str],
) -> None:
    root, _session_id = trace_project
    async with _client() as client:
        response = await client.post("/trace/query", json=_body(root, "not-a-uuid"))

    body = response.json()
    assert set(body) == ERROR_FIELDS
    for leaked in ("duckdb", "sqlite", "traceback", "pyarrow", "ValueError"):
        assert leaked not in str(body).lower()
