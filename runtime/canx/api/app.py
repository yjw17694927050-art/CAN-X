"""FastAPI application factory for the headless CAN-X runtime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from canx.agent.tools import (
    PermissionDeniedError,
    ToolDefinition,
    ToolExecutor,
    ToolRegistry,
    ToolRisk,
    UnknownToolError,
)
from canx.agent.trace_summary import TraceSummaryInput, TraceSummaryOutput, summarize_frames
from canx.api.errors import (
    REQUEST_VALIDATION_FAILED_STATUS,
    ErrorResponse,
    error_envelope,
    request_validation_envelope,
    status_for,
)
from canx.api.trace import create_trace_router
from canx.devices.virtual import VirtualAdapterConfig
from canx.metrics.models import MetricsSnapshot
from canx.project.errors import ProjectError
from canx.query.errors import QueryError
from canx.runtime.errors import CaptureConfigurationError
from canx.runtime.service import RuntimeService
from canx.transport.broker import BatchBroker
from canx.transport.msgpack_codec import encode_batch


class HealthResponse(BaseModel):
    """Stable control-plane response used by process supervisors."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ready"] = "ready"
    service: Literal["canx-runtime"] = "canx-runtime"
    schema_version: Literal[1] = 1


class RuntimeStatusResponse(BaseModel):
    """Current process and capture lifecycle state."""

    model_config = ConfigDict(frozen=True)

    state: Literal["ready", "failed"]
    capture_active: bool
    capture_state: Literal["idle", "running", "degraded", "finalizing", "failed"]
    failure: ErrorResponse | None


class CaptureStartRequest(BaseModel):
    """Validated virtual capture configuration and its recording target.

    ``recording_path`` is the V0.1 MessagePack compatibility target and
    ``project_path`` the V0.2 project-backed one. They are alternatives, not a
    fallback chain: supplying both is a validation error rather than a silent
    choice by the runtime.
    """

    model_config = ConfigDict(frozen=True)
    channel_count: Literal[1, 4, 8] = 1
    is_fd: bool = False
    is_extended: bool = False
    rate_hz: float = Field(default=1_000.0, gt=0)
    seed: int = 1
    batch_size: int = Field(default=250, gt=0, le=10_000)
    recording_path: str | None = None
    project_path: str | None = None


class CaptureStartResponse(BaseModel):
    """Identity of a started capture and of its data session, when persisted."""

    model_config = ConfigDict(frozen=True)
    status: Literal["started"] = "started"
    stream_id: str
    data_session_id: str | None = None


class CaptureStopResponse(BaseModel):
    """Acknowledged stop, plus whether a project recording is still settling.

    ``status`` reports that capture ingress stopped; ``finalization_pending``
    says whether the recording's durable terminal state is still being resolved.
    The two are different facts and a caller that needs the data must wait for
    the latter.
    """

    model_config = ConfigDict(frozen=True)
    status: Literal["stopped"] = "stopped"
    finalization_pending: bool = False


class ToolExecuteRequest(BaseModel):
    """One allow-listed Agent tool request."""

    model_config = ConfigDict(frozen=True)
    name: str
    input: dict[str, object]


class ToolExecuteResponse(BaseModel):
    """Schema-validated Agent tool output envelope."""

    model_config = ConfigDict(frozen=True)
    name: str
    output: dict[str, object]


class ShutdownResponse(BaseModel):
    """Acknowledgement that graceful shutdown has started."""

    model_config = ConfigDict(frozen=True)

    status: Literal["stopping"] = "stopping"


def create_app(
    *,
    session_token: str | None = None,
    shutdown_callback: Callable[[], None] | None = None,
    batch_broker: BatchBroker | None = None,
    runtime_service: RuntimeService | None = None,
) -> FastAPI:
    """Create an isolated FastAPI application instance."""
    service = runtime_service if runtime_service is not None else RuntimeService()

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            # Bounded: a project recording whose terminal commit is still in
            # flight is settled or abandoned, never guessed at, on the way out.
            await service.aclose()

    app = FastAPI(title="CAN-X Runtime", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:1420",
            "http://tauri.localhost",
            "tauri://localhost",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(QueryError)
    async def _query_failure(_request: Request, error: QueryError) -> JSONResponse:
        """Report a typed query failure as the shared envelope.

        A domain failure is a diagnosis, not an incident: it already carries the
        code, message, details, recoverability and source the contract asks for,
        so the boundary only has to pick an honest status for it.
        """
        return JSONResponse(
            status_code=status_for(error), content=error_envelope(error).model_dump()
        )

    @app.exception_handler(ProjectError)
    async def _project_failure(_request: Request, error: ProjectError) -> JSONResponse:
        """Report a typed project failure the same way.

        A request that names a directory which is not a readable CAN-X project is
        a bad request; it must not reach the caller as an opaque server error.
        """
        return JSONResponse(
            status_code=status_for(error), content=error_envelope(error).model_dump()
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation_failure(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        """Report a request the framework rejected before any route handler ran.

        This is a different layer from the two handlers above: nothing in the
        domain was reached, so there is no domain diagnosis to report. It keeps
        the framework's own 422 and gets its own code and source — a caller must be
        able to tell "my payload does not match the contract" apart from "the
        contract rejected my values". Registering it app-wide is what keeps every
        endpoint on one error protocol instead of each route inventing its own.
        """
        return JSONResponse(
            status_code=REQUEST_VALIDATION_FAILED_STATUS,
            content=request_validation_envelope(error.errors()).model_dump(),
        )

    # The historical Trace surface reads persisted data and shares no state with
    # the capture lifecycle, so it is mounted as its own router.
    app.include_router(create_trace_router())

    broker = batch_broker if batch_broker is not None else service.broker
    registry = ToolRegistry()

    async def trace_summary_handler(query: TraceSummaryInput) -> TraceSummaryOutput:
        return summarize_frames(service.frames(), query)

    registry.register(
        ToolDefinition(
            name="trace.summary",
            description="Summarize captured frames over a bounded time range.",
            input_model=TraceSummaryInput,
            output_model=TraceSummaryOutput,
            risk_level=ToolRisk.READ,
            permissions=frozenset({"READ"}),
            timeout_seconds=2.0,
            idempotency="idempotent",
        ),
        trace_summary_handler,
    )
    tool_executor = ToolExecutor(registry)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Report that the process is ready to serve control requests."""
        return HealthResponse()

    @app.get("/runtime/status", response_model=RuntimeStatusResponse)
    async def runtime_status() -> RuntimeStatusResponse:
        """Report the lifecycle state without exposing process internals."""
        return RuntimeStatusResponse(
            state="ready",
            capture_active=service.capture_active,
            capture_state=service.capture_state.value,
            failure=None
            if service.failure is None
            else ErrorResponse(
                code=service.failure.code,
                message=service.failure.message,
                details=service.failure.context,
                recoverable=service.failure.recoverable,
                source="recorder",
            ),
        )

    @app.post("/capture/start", response_model=None, status_code=202)
    async def capture_start(request: CaptureStartRequest) -> CaptureStartResponse | JSONResponse:
        """Start the deterministic virtual adapter with at most one recording target.

        A project-backed capture answers with the data session it created, so a
        caller can query the persisted data without guessing an identity. A
        capture with no target stays realtime-only and reports ``null``.
        """
        if service.has_session:
            error = ErrorResponse(
                code="capture.already_running",
                message="Capture is already running.",
                details={},
                recoverable=True,
                source="runtime",
            )
            return JSONResponse(status_code=409, content=error.model_dump())
        config = VirtualAdapterConfig(
            channel_count=request.channel_count,
            is_fd=request.is_fd,
            is_extended=request.is_extended,
            rate_hz=request.rate_hz,
            seed=request.seed,
        )
        try:
            stream_id = await service.start_capture(
                config,
                batch_size=request.batch_size,
                recording_path=(
                    None if request.recording_path is None else Path(request.recording_path)
                ),
                project_path=(
                    None if request.project_path is None else Path(request.project_path)
                ),
            )
        except CaptureConfigurationError as error:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    code=error.code,
                    message=error.message,
                    details=error.details,
                    recoverable=error.recoverable,
                    source=error.source,
                ).model_dump(),
            )
        return CaptureStartResponse(
            stream_id=stream_id, data_session_id=service.data_session_id
        )

    @app.post("/capture/stop", response_model=CaptureStopResponse)
    async def capture_stop() -> CaptureStopResponse:
        """Idempotently stop and flush the active capture session.

        Bounded: a project-backed recording whose terminal commit is already in
        flight is left to settle in the background, and this reports that with
        ``finalization_pending`` instead of guessing a terminal verdict.
        """
        await service.stop_capture()
        return CaptureStopResponse(finalization_pending=service.finalization_pending)

    @app.get("/metrics", response_model=MetricsSnapshot)
    async def metrics() -> MetricsSnapshot:
        """Expose a typed point-in-time runtime metrics snapshot."""
        return service.metrics_snapshot()

    @app.post("/tools/execute", response_model=None)
    async def execute_tool(request: ToolExecuteRequest) -> ToolExecuteResponse | JSONResponse:
        """Execute one runtime allow-listed tool with V0.1 read permission."""
        try:
            output = await tool_executor.execute(request.name, request.input, {"READ"})
        except UnknownToolError:
            error = ErrorResponse(
                code="tool.unknown",
                message="The requested tool is not registered.",
                details={"name": request.name},
                recoverable=True,
                source="agent-runtime",
            )
            return JSONResponse(status_code=404, content=error.model_dump())
        except PermissionDeniedError:
            error = ErrorResponse(
                code="tool.permission_denied",
                message="The requested tool is not permitted.",
                details={"name": request.name},
                recoverable=False,
                source="agent-runtime",
            )
            return JSONResponse(status_code=403, content=error.model_dump())
        except ValidationError as validation_error:
            error = ErrorResponse(
                code="tool.invalid_input",
                message="The tool input is invalid.",
                details={"errors": validation_error.errors(include_url=False)},
                recoverable=True,
                source="agent-runtime",
            )
            return JSONResponse(status_code=422, content=error.model_dump(mode="json"))
        except TimeoutError:
            error = ErrorResponse(
                code="tool.timeout",
                message="The tool execution timed out.",
                details={"name": request.name},
                recoverable=True,
                source="agent-runtime",
            )
            return JSONResponse(status_code=504, content=error.model_dump())
        return ToolExecuteResponse(name=request.name, output=output)

    @app.post("/runtime/shutdown", response_model=None, status_code=202)
    async def shutdown(
        x_canx_session_token: str | None = Header(default=None),
    ) -> ShutdownResponse | JSONResponse:
        """Request shutdown when called by the owning desktop process."""
        if session_token is None or x_canx_session_token != session_token:
            error = ErrorResponse(
                code="runtime.invalid_session_token",
                message="The runtime session token is invalid.",
                details={},
                recoverable=False,
                source="runtime",
            )
            return JSONResponse(status_code=403, content=error.model_dump())
        if shutdown_callback is None:
            error = ErrorResponse(
                code="runtime.shutdown_unavailable",
                message="This runtime was not started with a shutdown controller.",
                details={},
                recoverable=False,
                source="runtime",
            )
            return JSONResponse(status_code=409, content=error.model_dump())
        await service.stop_capture()
        shutdown_callback()
        return ShutdownResponse()

    @app.websocket("/stream/frames")
    async def stream_frames(websocket: WebSocket) -> None:
        """Send future frame batches as binary MessagePack messages."""
        await websocket.accept()
        try:
            async with broker.subscribe() as queue:
                while True:
                    batch_task = asyncio.create_task(queue.get())
                    receive_task = asyncio.create_task(websocket.receive())
                    completed, pending = await asyncio.wait(
                        {batch_task, receive_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if receive_task in completed:
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        message = receive_task.result()
                        if message["type"] == "websocket.disconnect":
                            return
                        await websocket.close(
                            code=1003, reason="Client messages are not accepted on this stream."
                        )
                        return
                    receive_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await receive_task
                    batch = batch_task.result()
                    await websocket.send_bytes(encode_batch(batch))
                    service.metrics.increment("streamed_frames", batch.frame_count)
                    service.metrics.observe_queue("stream_queue_depth", queue.qsize())
        except WebSocketDisconnect:
            return

    return app
