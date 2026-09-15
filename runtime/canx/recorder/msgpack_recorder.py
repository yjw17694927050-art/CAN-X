"""V0.1 append-only, chunked MessagePack recorder."""

import asyncio
import struct
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO

from canx.domain.batch import FrameBatch
from canx.transport.msgpack_codec import decode_batch, encode_batch

_MAGIC = b"CANXMSG1"


class RecorderState(StrEnum):
    """Observable recorder lifecycle state."""

    IDLE = "idle"
    RECORDING = "recording"
    STOPPED = "stopped"
    FAILED = "failed"


class MsgpackRecorder:
    """Append independently decodable batch chunks to one capture session."""

    def __init__(self) -> None:
        self.state = RecorderState.IDLE
        self.recorded_frames = 0
        self._stream_id: str | None = None
        self._file: BinaryIO | None = None

    async def start(self, path: Path, *, stream_id: str) -> None:
        """Open a new recording without creating missing parent directories."""
        if self.state is RecorderState.RECORDING:
            raise RuntimeError("recorder is already running")
        try:
            file = await asyncio.to_thread(path.open, "xb")
            await asyncio.to_thread(file.write, _MAGIC)
        except OSError:
            self.state = RecorderState.FAILED
            raise
        self._file = file
        self._stream_id = stream_id
        self.recorded_frames = 0
        self.state = RecorderState.RECORDING

    async def append(self, batch: FrameBatch) -> None:
        """Append one length-prefixed batch and update committed frame count."""
        if self._file is None or self.state is not RecorderState.RECORDING:
            raise RuntimeError("recorder is not running")
        if batch.stream_id != self._stream_id:
            raise ValueError("batch stream_id differs from recorder session")
        payload = encode_batch(batch)
        chunk = struct.pack(">I", len(payload)) + payload
        try:
            await asyncio.to_thread(self._file.write, chunk)
        except OSError:
            self.state = RecorderState.FAILED
            raise
        self.recorded_frames += batch.frame_count

    async def stop(self) -> None:
        """Flush and close the owned file before reporting stopped."""
        file = self._file
        if file is None:
            return
        was_failed = self.state is RecorderState.FAILED
        try:
            await asyncio.to_thread(file.flush)
            await asyncio.to_thread(file.close)
        except OSError:
            self.state = RecorderState.FAILED
            raise
        finally:
            self._file = None
        self.state = RecorderState.FAILED if was_failed else RecorderState.STOPPED


def read_recording(path: Path) -> list[FrameBatch]:
    """Read all complete V0.1 chunks for validation and recovery tooling."""
    batches: list[FrameBatch] = []
    with path.open("rb") as file:
        if file.read(len(_MAGIC)) != _MAGIC:
            raise ValueError("recording magic is invalid")
        while prefix := file.read(4):
            if len(prefix) != 4:
                raise ValueError("recording chunk prefix is truncated")
            length = struct.unpack(">I", prefix)[0]
            payload = file.read(length)
            if len(payload) != length:
                raise ValueError("recording chunk is truncated")
            batches.append(decode_batch(payload))
    return batches
