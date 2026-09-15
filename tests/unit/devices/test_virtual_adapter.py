"""Behavior tests for the deterministic Virtual CAN adapter."""

import pytest
from canx.devices.base import AdapterClosedError
from canx.devices.virtual import VirtualAdapter, VirtualAdapterConfig


async def collect_signature(adapter: VirtualAdapter, count: int) -> list[tuple[object, ...]]:
    """Collect deterministic fields while excluding real host clock values."""
    await adapter.open()
    frames = [await adapter.recv() for _ in range(count)]
    await adapter.close()
    return [
        (frame.sequence, frame.channel_id, frame.arbitration_id, frame.dlc, frame.data)
        for frame in frames
    ]


async def test_same_seed_produces_the_same_frame_sequence() -> None:
    config = VirtualAdapterConfig(channel_count=4, rate_hz=1_000_000, seed=73)

    first = await collect_signature(VirtualAdapter(config), 12)
    second = await collect_signature(VirtualAdapter(config), 12)

    assert first == second
    assert [item[0] for item in first] == list(range(12))
    assert {item[1] for item in first} == {"can0", "can1", "can2", "can3"}


@pytest.mark.parametrize("channel_count", [1, 4, 8])
async def test_virtual_adapter_reports_multi_channel_capability(channel_count: int) -> None:
    adapter = VirtualAdapter(
        VirtualAdapterConfig(channel_count=channel_count, rate_hz=1_000_000, seed=1)
    )

    assert adapter.capabilities().multi_channel is (channel_count > 1)
    assert adapter.capabilities().can_fd is True


async def test_classic_generator_never_exceeds_eight_data_bytes() -> None:
    adapter = VirtualAdapter(
        VirtualAdapterConfig(channel_count=1, is_fd=False, rate_hz=1_000_000, seed=2)
    )
    await adapter.open()

    frames = [await adapter.recv() for _ in range(20)]

    assert all(frame.is_fd is False and frame.dlc <= 8 for frame in frames)


async def test_fd_generator_uses_only_legal_payload_lengths() -> None:
    adapter = VirtualAdapter(
        VirtualAdapterConfig(channel_count=1, is_fd=True, rate_hz=1_000_000, seed=3)
    )
    await adapter.open()

    frames = [await adapter.recv() for _ in range(30)]

    valid_lengths = {0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64}
    assert {frame.dlc for frame in frames} <= valid_lengths


async def test_recv_after_close_is_rejected() -> None:
    adapter = VirtualAdapter(VirtualAdapterConfig())
    await adapter.open()
    await adapter.close()

    with pytest.raises(AdapterClosedError):
        await adapter.recv()
