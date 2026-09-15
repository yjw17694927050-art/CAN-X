import pytest
from canx.agent.trace_summary import TraceSummaryInput, summarize_frames
from canx.api.app import create_app
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.runtime.service import RuntimeService
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError


def frame(sequence: int, timestamp: float, arbitration_id: int, channel: str = "can0") -> Frame:
    return Frame(
        sequence,
        channel,
        arbitration_id,
        False,
        False,
        False,
        False,
        0,
        b"",
        Direction.RX,
        None,
        timestamp,
        timestamp,
        "host.monotonic",
        TimestampQuality.HOST,
        0,
    )


def test_trace_summary_filters_and_orders_ties_by_id() -> None:
    frames = [
        frame(0, 1.0, 0x200),
        frame(1, 1.5, 0x100),
        frame(2, 2.0, 0x200),
        frame(3, 2.5, 0x100),
        frame(4, 3.0, 0x300, "can1"),
    ]
    result = summarize_frames(
        frames, TraceSummaryInput(start_time=1.0, end_time=2.5, channel_id="can0")
    )
    assert result.frame_count == 4
    assert result.unique_ids == 2
    assert [item.arbitration_id for item in result.top_ids] == [0x100, 0x200]
    assert result.frame_rate == 4 / 1.5


def test_trace_summary_handles_empty_data() -> None:
    result = summarize_frames([], TraceSummaryInput(start_time=4.0, end_time=5.0))
    assert result.frame_count == 0
    assert result.frame_rate == 0
    assert result.top_ids == ()


def test_trace_summary_filters_by_arbitration_id() -> None:
    frames = [frame(0, 1.0, 0x100), frame(1, 1.5, 0x200)]
    result = summarize_frames(
        frames,
        TraceSummaryInput(start_time=0.0, end_time=2.0, arbitration_id=0x200),
    )
    assert result.frame_count == 1
    assert result.top_ids[0].arbitration_id == 0x200


def test_trace_summary_rejects_an_invalid_time_range() -> None:
    with pytest.raises(ValidationError, match="end_time"):
        TraceSummaryInput(start_time=2.0, end_time=2.0)


async def test_trace_summary_executes_through_http_registry() -> None:
    app = create_app(runtime_service=RuntimeService())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/tools/execute",
            json={
                "name": "trace.summary",
                "input": {"start_time": 1.0, "end_time": 2.0},
            },
        )
    assert response.status_code == 200
    assert response.json()["output"]["frame_count"] == 0
