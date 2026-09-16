"""V0.1 append-only, chunked MessagePack recorder."""

import asyncio
import struct
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO

from canx.domain.batch import FrameBatch
from canx.transport.msgpack_codec import decode_batch, encode_batch

_MAGIC = b"CANXMSG1"


@dataclass(frozen=True, slots=True)
class RecorderFailure:
    """First failure of a recording session, retained through cleanup."""

    code: str
    message: str
    recoverable: bool
    context: dict[str, object]


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
        self.failure: RecorderFailure | None = None
        self._stream_id: str | None = None
        self._file: BinaryIO | None = None
        self._write_task: asyncio.Task[None] | None = None

    def fail(
        self, *, code: str, message: str, recoverable: bool, context: dict[str, object]
    ) -> None:
        """Retain the first diagnostic, even when cleanup also fails."""
        if self.failure is None:
            self.failure = RecorderFailure(code, message, recoverable, dict(context))
        self.state = RecorderState.FAILED

    async def start(self, path: Path, *, stream_id: str) -> None:
        """Open a new recording without creating missing parent directories."""
        if self.state is RecorderState.RECORDING:
            raise RuntimeError("recorder is already running")
        file: BinaryIO | None = None
        try:
            file = await asyncio.to_thread(path.open, "xb")
            await asyncio.to_thread(file.write, _MAGIC)
        except OSError as error:
            self.fail(
                code="recorder.open_failed",
                message=str(error),
                recoverable=False,
                context={"path": str(path), "stream_id": stream_id},
            )
            if file is not None:
                with suppress(OSError):
                    await asyncio.to_thread(file.close)
            raise
        self._file = file
        self._stream_id = stream_id
        self.recorded_frames = 0
        self.failure = None
        self.state = RecorderState.RECORDING

    async def append(self, batch: FrameBatch) -> None:
        """Append one length-prefixed batch and update committed frame count."""
        if self._file is None or self.state is not RecorderState.RECORDING:
            raise RuntimeError("recorder is not running")
        if batch.stream_id != self._stream_id:
            raise ValueError("batch stream_id differs from recorder session")
        payload = encode_batch(batch)
        chunk = struct.pack(">I", len(payload)) + payload
        self._write_task = asyncio.create_task(self._write_chunk(self._file, chunk, batch))
        try:
            await asyncio.shield(self._write_task)
        finally:
            if self._write_task.done():
                self._write_task = None
        self.recorded_frames += batch.frame_count

    async def _write_chunk(self, file: BinaryIO, chunk: bytes, batch: FrameBatch) -> None:
        try:
            await asyncio.to_thread(file.write, chunk)
        except OSError as error:
            self.fail(
                code="recorder.write_failed",
                message=str(error),
                recoverable=False,
                context={
                    "stream_id": batch.stream_id,
                    "first_sequence": batch.first_sequence,
                    "last_sequence": batch.last_sequence,
                    "frame_count": batch.frame_count,
                },
            )
            raise

    async def stop(self) -> None:
        """Flush and close the owned file before reporting stopped."""
        file = self._file
        if file is None:
            return
        # Cancelling the archive task cannot cancel a disk thread. Join it
        # before flush/close; a cancelled append remains uncommitted.
        if self._write_task is not None:
            with suppress(OSError):
                await self._write_task
            self._write_task = None
        was_failed = self.state is RecorderState.FAILED
        try:
            await asyncio.to_thread(file.flush)
        except OSError as error:
            self.fail(
                code="recorder.flush_failed",
                message=str(error),
                recoverable=False,
                context={"stream_id": self._stream_id},
            )
            raise
        finally:
            try:
                await asyncio.to_thread(file.close)
            except OSError as error:
                self.fail(
                    code="recorder.flush_failed",
                    message=str(error),
                    recoverable=False,
                    context={"stream_id": self._stream_id, "operation": "close"},
                )
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
