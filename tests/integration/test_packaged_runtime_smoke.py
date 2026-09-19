"""Headless end-to-end smoke test for the packaged runtime (V0.1.1 / V0.2-04).

Proves the distribution-owned ``canx-runtime.exe`` — not a source-tree
interpreter — serves health, runs a virtual capture session, streams a binary
batch, stops and flushes, and exits cleanly after an authenticated shutdown.

V0.2-04 extends this with the project-backed path: the packaged executable must
itself create a data session, write real Parquet segments and leave the project
in a queryable ``COMPLETED`` state. That is what turns the V0.2-02
"Packaged Parquet execution path NOT VERIFIED" note into evidence, because the
segment files are produced by the frozen ``.exe`` and only read back here.

The GUI desktop window cannot be launched under the host execution policy, so
this covers the runtime half of the smoke path; the windowed half is recorded as
NOT VERIFIED in the acceptance report.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import socket
import subprocess
from pathlib import Path

import httpx
import pytest
import websockets
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.dbc.project_service import ProjectDbcService
from canx.project.service import ProjectService
from canx.query.model import FrameFilter, FrameQuery
from canx.query.service import QueryService

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_EXE = _REPO_ROOT / "build" / "runtime-dist" / "canx-runtime.exe"


def packaged_runtime() -> Path | None:
    """Resolve the packaged runtime executable, or None when it has not been built."""
    override = os.environ.get("CANX_TEST_RUNTIME_EXE")
    candidate = Path(override) if override else _DEFAULT_EXE
    return candidate if candidate.is_file() else None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    try:
        proc.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False
        )
        proc.wait(timeout=10)


async def _await_health(client: httpx.AsyncClient) -> httpx.Response:
    last: httpx.Response | None = None
    for _ in range(80):
        try:
            last = await client.get("/health")
            if last.status_code == 200:
                return last
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    raise AssertionError(f"packaged runtime never became healthy: {last!r}")


@pytest.mark.skipif(
    packaged_runtime() is None, reason="packaged canx-runtime.exe has not been built"
)
async def test_packaged_runtime_serves_health_capture_stream_and_shutdown() -> None:
    """Distribution-owned runtime: health → capture → stream → flush → graceful exit."""
    exe = packaged_runtime()
    assert exe is not None
    port = _free_port()
    token = "v011-smoke-token"
    proc = subprocess.Popen(
        [str(exe), "--host", "127.0.0.1", "--port", str(port), "--session-token", token],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
            health = await _await_health(client)
            assert health.json() == {
                "status": "ready",
                "service": "canx-runtime",
                "schema_version": 1,
            }

            started = await client.post(
                "/capture/start", json={"rate_hz": 4_000, "batch_size": 25}
            )
            assert started.status_code == 202
            assert started.json()["stream_id"]

            async with websockets.connect(f"ws://127.0.0.1:{port}/stream/frames") as stream:
                batch = await asyncio.wait_for(stream.recv(), timeout=10)
                assert isinstance(batch, bytes)
                assert len(batch) > 0

            stopped = await client.post("/capture/stop")
            assert stopped.status_code == 200
            assert stopped.json()["status"] == "stopped"

            metrics = (await client.get("/metrics")).json()
            assert metrics["captured_frames"] > 0
            assert metrics["streamed_frames"] > 0
            assert metrics["dropped_frames"] == 0

            assert (await client.get("/runtime/status")).json()["capture_active"] is False

            shutdown = await client.post(
                "/runtime/shutdown", headers={"X-CANX-Session-Token": token}
            )
            assert shutdown.status_code == 202

        proc.wait(timeout=15)
        assert proc.returncode == 0
    finally:
        _terminate(proc)


@pytest.mark.skipif(
    packaged_runtime() is None, reason="packaged canx-runtime.exe has not been built"
)
async def test_packaged_runtime_writes_a_queryable_project_data_session(tmp_path: Path) -> None:
    """The frozen executable — not the source tree — writes the Parquet segments.

    The project is created here, then handed to ``canx-runtime.exe`` as a capture
    target. Every assertion below reads artifacts produced by that process, so a
    passing run proves the packaged image really contains and executes the
    ``canx.data`` / ``pyarrow`` Parquet write path.
    """
    exe = packaged_runtime()
    assert exe is not None
    project_root = tmp_path / "packaged.canx"
    with ProjectService().create(project_root, display_name="Packaged"):
        pass

    port = _free_port()
    token = "v0204-smoke-token"
    proc = subprocess.Popen(
        [str(exe), "--host", "127.0.0.1", "--port", str(port), "--session-token", token],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            await _await_health(client)

            started = await client.post(
                "/capture/start",
                json={
                    "rate_hz": 4_000,
                    "batch_size": 25,
                    "project_path": str(project_root),
                },
            )
            assert started.status_code == 202, started.text
            body = started.json()
            stream_id = body["stream_id"]
            session_id = body["data_session_id"]
            assert stream_id
            assert session_id is not None

            async with websockets.connect(f"ws://127.0.0.1:{port}/stream/frames") as stream:
                batch = await asyncio.wait_for(stream.recv(), timeout=20)
                assert isinstance(batch, bytes)
                assert len(batch) > 0

            await asyncio.sleep(0.3)

            stopped = await client.post("/capture/stop")
            assert stopped.status_code == 200
            assert stopped.json()["status"] == "stopped"

            shutdown = await client.post(
                "/runtime/shutdown", headers={"X-CANX-Session-Token": token}
            )
            assert shutdown.status_code == 202

        proc.wait(timeout=30)
        assert proc.returncode == 0
    finally:
        _terminate(proc)

    with ProjectService().open(project_root) as reopened:
        data = DataSessionService(reopened.root)
        session = data.get_session(session_id)
        segments = data.list_segments(session_id)

        assert session.state is DataSessionState.COMPLETED
        assert session.stream_id == stream_id
        assert session.project_id == reopened.project_id
        assert session.frame_count > 0
        assert session.segment_count == len(segments) > 0
        for segment in segments:
            path = reopened.root / segment.relative_path
            assert path.is_file()
            assert path.stat().st_size == segment.byte_size
        assert data.inspect_integrity().clean is True
        # Reading frames back proves the packaged writer produced real Parquet.
        assert len(data.read_segment(session_id, 0)) == segments[0].frame_count

        query = QueryService(reopened.root)
        summary = query.summarize_frames(FrameFilter(session_id=session_id))
        assert summary.matching_frame_count == session.frame_count
        page = query.query_frames(
            FrameQuery(filter=FrameFilter(session_id=session_id), limit=10)
        )
        assert len(page.frames) == min(10, session.frame_count)


@pytest.mark.skipif(
    packaged_runtime() is None, reason="packaged canx-runtime.exe has not been built"
)
async def test_packaged_runtime_answers_a_trace_query_from_persisted_parquet(
    tmp_path: Path,
) -> None:
    """The frozen executable must serve a bounded Trace query off real Parquet.

    V0.2-03 recorded "Packaged DuckDB query execution path: NOT VERIFIED" because
    no HTTP surface reached ``canx.query`` inside the image. This test is what
    turns that note into evidence, and it is deliberately double-ended: the same
    ``canx-runtime.exe`` both captures (writing the Parquet segments) and answers
    ``POST /trace/query``. A response can therefore only come from DuckDB scanning
    the files that process wrote to disk — never from an in-process frame buffer,
    which a capture-less query path could otherwise be mistaken for.

    The id filter is built from a frame the executable itself returned, so the
    assertion does not depend on which ids the virtual adapter happens to use.
    """
    exe = packaged_runtime()
    assert exe is not None
    project_root = tmp_path / "packaged-trace.canx"
    with ProjectService().create(project_root, display_name="Packaged Trace"):
        pass

    port = _free_port()
    token = "v0301-trace-token"
    proc = subprocess.Popen(
        [str(exe), "--host", "127.0.0.1", "--port", str(port), "--session-token", token],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            await _await_health(client)

            started = await client.post(
                "/capture/start",
                json={
                    "rate_hz": 4_000,
                    "batch_size": 25,
                    "project_path": str(project_root),
                },
            )
            assert started.status_code == 202, started.text
            session_id = started.json()["data_session_id"]
            assert session_id is not None

            await asyncio.sleep(0.4)

            stopped = await client.post("/capture/stop")
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["finalization_pending"] is False

            def body(**overrides: object) -> dict[str, object]:
                payload: dict[str, object] = {
                    "project_path": str(project_root),
                    "session_id": session_id,
                    "filters": {},
                    "limit": 5,
                }
                payload.update(overrides)
                return payload

            unfiltered = await client.post("/trace/query", json=body())
            assert unfiltered.status_code == 200, unfiltered.text
            page = unfiltered.json()
            assert page["session_id"] == session_id
            assert page["frames"], "the packaged executable returned no frames at all"

            summary = await client.post(
                "/trace/summary",
                json={
                    "project_path": str(project_root),
                    "session_id": session_id,
                    "filters": {},
                },
            )
            assert summary.status_code == 200, summary.text
            summary_body = summary.json()

            first = page["frames"][0]
            exact_id = first["arbitration_id"]
            by_id = await client.post(
                "/trace/query", json=body(filters={"arbitration_ids": [exact_id]}, limit=1_000)
            )
            by_range = await client.post(
                "/trace/query",
                json=body(
                    filters={
                        "arbitration_id_start": exact_id,
                        "arbitration_id_end": exact_id,
                    },
                    limit=1_000,
                ),
            )
            mask = 0xFFF
            mask_value = exact_id & mask
            by_mask = await client.post(
                "/trace/query",
                json=body(
                    filters={
                        "arbitration_id_mask": mask,
                        "arbitration_id_mask_value": mask_value,
                    },
                    limit=1_000,
                ),
            )

            empty = await client.post(
                "/trace/query",
                json=body(
                    filters={
                        "arbitration_id_start": 0x1FFFFFF0,
                        "arbitration_id_end": 0x1FFFFFFF,
                    }
                ),
            )

            # The app-level request-validation boundary is registered in the same
            # application factory the frozen executable runs, so a malformed
            # payload must be answered with the shared envelope here too — not with
            # the framework's own payload and not with a packaging-specific variant.
            malformed = await client.post(
                "/trace/query",
                json={
                    "project_path": str(project_root),
                    "session_id": session_id,
                    "limit": "ten",
                },
            )
            assert malformed.status_code == 422, malformed.text
            assert set(malformed.json()) == {
                "code",
                "message",
                "details",
                "recoverable",
                "source",
            }
            assert malformed.json()["code"] == "api.request_validation_failed"
            assert malformed.json()["source"] == "api"
            assert malformed.json()["recoverable"] is False

            shutdown = await client.post(
                "/runtime/shutdown", headers={"X-CANX-Session-Token": token}
            )
            assert shutdown.status_code == 202

        proc.wait(timeout=30)
        assert proc.returncode == 0
    finally:
        _terminate(proc)

    # Read the same session from the source tree to fix the expected numbers.
    with ProjectService().open(project_root) as reopened:
        session = DataSessionService(reopened.root).get_session(session_id)

    assert session.state is DataSessionState.COMPLETED
    assert session.frame_count > 0

    assert summary_body["matching_frame_count"] == session.frame_count
    assert len(page["frames"]) == min(5, session.frame_count)
    assert [item["sequence"] for item in page["frames"]] == list(range(len(page["frames"])))
    assert isinstance(first["data"], str) and first["data"] == first["data"].upper()
    assert first["direction"] in {"rx", "tx"}
    assert first["dlc"] == len(first["data"]) // 2

    for response in (by_id, by_range):
        assert response.status_code == 200, response.text
        frames = response.json()["frames"]
        assert frames, "an id filter that names a real frame must match it"
        assert {item["arbitration_id"] for item in frames} == {exact_id}

    masked_frames = by_mask.json()["frames"]
    assert masked_frames, "the masked predicate must still select matching frames"
    assert all(
        (item["arbitration_id"] & mask) == (mask_value & mask) for item in masked_frames
    )

    assert empty.status_code == 200
    assert empty.json()["frames"] == []
    assert empty.json()["has_more"] is False


#: The DBC fixture used by the packaged DBC proof, and the frame the packaged
#: process must decode from the project-owned copy it loads itself.
_DBC_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "dbc" / "basic_standard.dbc"
_DBC_ENGINE_DATA = "B80B508000000000"


def _dbc_frame(data: str, **overrides: object) -> dict[str, object]:
    """Build one canonical frame wire payload carrying ``data``."""
    payload: dict[str, object] = {
        "sequence": 1,
        "channel_id": "can0",
        "arbitration_id": 0x123,
        "is_extended": False,
        "is_fd": False,
        "bitrate_switch": False,
        "error_state_indicator": False,
        "dlc": len(data) // 2,
        "data": data,
        "direction": "rx",
        "hardware_timestamp": 12.5,
        "host_timestamp": 100.25,
        "normalized_timestamp": 0.25,
        "clock_domain": "host.monotonic",
        "timestamp_quality": "hardware",
        "flags": 0,
    }
    payload.update(overrides)
    return payload


@pytest.mark.skipif(
    packaged_runtime() is None, reason="packaged canx-runtime.exe has not been built"
)
async def test_packaged_runtime_loads_a_project_dbc_and_decodes_with_it(
    tmp_path: Path,
) -> None:
    """The frozen executable — not the source tree — performs the DBC decode.

    V0.3-04 recorded that the packaged runtime did not provide DBC decode at all:
    ``canx.dbc`` was not in the runtime entry import graph. This test is what turns
    that note into evidence.

    The project and its project-owned DBC are created here with the trusted
    domain, and the executable is handed nothing but the project path. Every value
    asserted below is computed inside that process from the project-owned copy on
    disk. The tampered-asset case runs against the same process, so it also proves
    the packaged image re-verifies the registered digest rather than trusting the
    registry row.
    """
    exe = packaged_runtime()
    assert exe is not None
    project_root = tmp_path / "packaged-dbc.canx"
    inbox = tmp_path / "dbc-inbox"
    inbox.mkdir()
    source = inbox / "basic_standard.dbc"
    source.write_bytes(_DBC_FIXTURE.read_bytes())

    with ProjectService().create(project_root, display_name="Packaged DBC") as handle:
        asset_id = ProjectDbcService(handle.root).import_asset(source).asset_id

    port = _free_port()
    token = "v0305-dbc-token"
    proc = subprocess.Popen(
        [str(exe), "--host", "127.0.0.1", "--port", str(port), "--session-token", token],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            await _await_health(client)

            listed = await client.get(
                "/dbc/assets", params={"project_path": str(project_root)}
            )
            assert listed.status_code == 200, listed.text
            assert [asset["asset_id"] for asset in listed.json()["assets"]] == [asset_id]

            database = await client.get(
                f"/dbc/assets/{asset_id}/database",
                params={"project_path": str(project_root)},
            )
            assert database.status_code == 200, database.text
            assert [message["name"] for message in database.json()["messages"]] == [
                "EngineData"
            ]

            decoded = await client.post(
                f"/dbc/assets/{asset_id}/decode",
                json={
                    "project_path": str(project_root),
                    "frame": _dbc_frame(_DBC_ENGINE_DATA),
                },
            )
            assert decoded.status_code == 200, decoded.text
            body = decoded.json()
            assert body["message_name"] == "EngineData"
            assert body["frame"]["data"] == _DBC_ENGINE_DATA
            speed, coolant, throttle = body["signals"]
            assert speed["name"] == "EngineSpeed"
            assert speed["raw_value"] == 3000
            assert speed["physical_value"] == 750.0
            assert speed["unit"] == "rpm"
            assert coolant["raw_value"] == 80
            assert coolant["physical_value"] == 40.0
            assert throttle["raw_value"] == 128

            # The strict wire contract has to be in the *shipped* runtime, not only
            # in the source tree: a coercible wrong primitive is refused here too.
            coerced = await client.post(
                f"/dbc/assets/{asset_id}/decode",
                json={
                    "project_path": str(project_root),
                    "frame": _dbc_frame(_DBC_ENGINE_DATA, sequence="1"),
                },
            )
            assert coerced.status_code == 422, coerced.text
            assert coerced.json()["code"] == "api.request_validation_failed"
            assert coerced.json()["source"] == "api"
            assert coerced.json()["recoverable"] is False

            batch = await client.post(
                f"/dbc/assets/{asset_id}/decode-batch",
                json={
                    "project_path": str(project_root),
                    "stream_id": "packaged-dbc",
                    "frames": [
                        _dbc_frame(_DBC_ENGINE_DATA, sequence=1),
                        _dbc_frame(_DBC_ENGINE_DATA, sequence=2, arbitration_id=0x7FF),
                        _dbc_frame(_DBC_ENGINE_DATA, sequence=3),
                    ],
                },
            )
            assert batch.status_code == 200, batch.text
            outcomes = batch.json()["outcomes"]
            assert [outcome["decoded"] is not None for outcome in outcomes] == [
                True,
                False,
                True,
            ]
            assert outcomes[1]["failure"]["code"] == "dbc.message_not_found"

            unknown = await client.get(
                "/dbc/assets/11111111-2222-4333-8444-555555555555/database",
                params={"project_path": str(project_root)},
            )
            assert unknown.status_code == 404
            assert unknown.json()["code"] == "dbc.asset_not_found"

            absent = await client.get(
                "/dbc/assets", params={"project_path": str(tmp_path / "absent.canx")}
            )
            assert absent.status_code == 400
            assert absent.json()["source"] == "project"

            # Tamper with the project-owned copy while the process is running. A
            # 409 means the packaged image re-verified the registered digest rather
            # than trusting the registry row.
            target = project_root / "dbc" / f"{asset_id}.dbc"
            target.write_bytes(target.read_bytes() + b"\n")
            tampered = await client.post(
                f"/dbc/assets/{asset_id}/decode",
                json={
                    "project_path": str(project_root),
                    "frame": _dbc_frame(_DBC_ENGINE_DATA),
                },
            )
            assert tampered.status_code == 409, tampered.text
            assert tampered.json()["code"] == "dbc.asset_integrity_failed"

            shutdown = await client.post(
                "/runtime/shutdown", headers={"X-CANX-Session-Token": token}
            )
            assert shutdown.status_code == 202

        proc.wait(timeout=30)
        assert proc.returncode == 0
    finally:
        _terminate(proc)


@pytest.mark.skipif(
    packaged_runtime() is None, reason="packaged canx-runtime.exe has not been built"
)
async def test_packaged_runtime_imports_dbc_content_and_decodes_with_it(
    tmp_path: Path,
) -> None:
    """The frozen executable — not the source tree — performs the import itself.

    The V0.3-05 packaged proof imported the asset from the source side and handed
    the executable a project that already owned it. This test moves the import
    *into* the executable: the source side creates an empty project, submits Base64
    bytes, and never writes a DBC into that project at all. Every fact asserted
    below — the asset id, the registry row, the decoded values, the stored file —
    is therefore produced by the packaged process.

    The source-side check after shutdown is the exact-byte proof: it rules out a
    re-render, because the packaged runtime would then have persisted a rendering
    of the model it parsed instead of the bytes it was handed.
    """
    exe = packaged_runtime()
    assert exe is not None
    project_root = tmp_path / "packaged-dbc-import.canx"
    with ProjectService().create(project_root, display_name="Packaged DBC import"):
        pass

    submitted = _DBC_FIXTURE.read_bytes()
    expected_sha256 = hashlib.sha256(submitted).hexdigest()
    payload: dict[str, object] = {
        "project_path": str(project_root),
        "source_name": _DBC_FIXTURE.name,
        "content_base64": base64.b64encode(submitted).decode("ascii"),
    }

    # The precondition this test depends on: the source side imported nothing.
    assert list((project_root / "dbc").iterdir()) == []

    port = _free_port()
    token = "v0306-dbc-import-token"
    proc = subprocess.Popen(
        [str(exe), "--host", "127.0.0.1", "--port", str(port), "--session-token", token],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            await _await_health(client)

            created = await client.post("/dbc/assets", json=payload)
            assert created.status_code == 201, created.text
            asset = created.json()
            asset_id = asset["asset_id"]
            assert asset["source_name"] == _DBC_FIXTURE.name
            assert asset["sha256"] == expected_sha256
            assert asset["size_bytes"] == len(submitted)
            assert asset["encoding"] == "utf-8-sig"

            listed = await client.get(
                "/dbc/assets", params={"project_path": str(project_root)}
            )
            assert listed.status_code == 200, listed.text
            assert [item["asset_id"] for item in listed.json()["assets"]] == [asset_id]

            database = await client.get(
                f"/dbc/assets/{asset_id}/database",
                params={"project_path": str(project_root)},
            )
            assert database.status_code == 200, database.text
            assert [message["name"] for message in database.json()["messages"]] == [
                "EngineData"
            ]

            decoded = await client.post(
                f"/dbc/assets/{asset_id}/decode",
                json={
                    "project_path": str(project_root),
                    "frame": _dbc_frame(_DBC_ENGINE_DATA),
                },
            )
            assert decoded.status_code == 200, decoded.text
            body = decoded.json()
            assert body["message_name"] == "EngineData"
            speed, coolant, throttle = body["signals"]
            assert speed["name"] == "EngineSpeed"
            assert speed["raw_value"] == 3000
            assert speed["physical_value"] == 750.0
            assert coolant["name"] == "CoolantTemp"
            assert coolant["raw_value"] == 80
            assert coolant["physical_value"] == 40.0
            assert throttle["name"] == "ThrottlePosition"
            assert throttle["raw_value"] == 128

            # The content-import contract has to be in the *shipped* runtime, not
            # only in the source tree: a path-shaped extra field is refused there too.
            refused = await client.post(
                "/dbc/assets", json={**payload, "source_path": "C:\\secret\\vehicle.dbc"}
            )
            assert refused.status_code == 422, refused.text
            assert refused.json()["code"] == "api.request_validation_failed"
            assert refused.json()["source"] == "api"
            assert refused.json()["recoverable"] is False

            still_one = await client.get(
                "/dbc/assets", params={"project_path": str(project_root)}
            )
            assert [item["asset_id"] for item in still_one.json()["assets"]] == [asset_id]

            shutdown = await client.post(
                "/runtime/shutdown", headers={"X-CANX-Session-Token": token}
            )
            assert shutdown.status_code == 202

        proc.wait(timeout=30)
        assert proc.returncode == 0
    finally:
        _terminate(proc)

    # Exact-byte proof, read back from the source tree after the process exited.
    with ProjectService().open(project_root) as reopened:
        service = ProjectDbcService(reopened.root)
        registered = service.get_asset(asset_id)
        stored = (reopened.root / registered.relative_path).read_bytes()
        document = service.load_asset(asset_id)

        assert stored == submitted
        assert hashlib.sha256(stored).hexdigest() == expected_sha256
        assert len(stored) == len(submitted)
        assert registered.sha256 == expected_sha256
        assert registered.size_bytes == len(submitted)
        assert registered.source_name == _DBC_FIXTURE.name
        assert registered.encoding == "utf-8-sig"
        assert service.list_assets() == (registered,)
        assert [message.name for message in document.database.messages] == ["EngineData"]


@pytest.mark.skipif(
    packaged_runtime() is None, reason="packaged canx-runtime.exe has not been built"
)
async def test_packaged_runtime_serves_the_project_read_model_api(tmp_path: Path) -> None:
    """The frozen executable — not the source tree — answers ``GET /project/inspect``.

    V0.3-11 adds a Runtime HTTP endpoint, and the only honest way to prove it is in
    the distribution is to ask the distribution. Using ``/health`` as a proxy would
    prove nothing: the endpoint could be missing from the image and health would
    still be green.

    The project is created here, then closed, and the executable is handed nothing
    but its path. The identity asserted below is therefore read out of
    ``project.json`` and ``project.db`` by the packaged process itself, and compared
    against the same values read from the source tree. The negative cases are part
    of the same proof: a domain failure and a request-shape failure must both be
    answered by the frozen image with the shared envelope, not by a packaging
    specific variant.

    ``canx.api.project`` is reached because the runtime entry point imports the
    application factory, which mounts the router — this test is what turns "the new
    endpoint is in the image" from an expectation into evidence.

    V0.3-11-FINAL adds one more configuration, and it is the dangerous one: the
    packaged process is launched **with the project as its working directory**. That
    is exactly the situation in which ``Path("")`` would have resolved to a valid
    project and an empty ``project_path`` would have answered 200 with that
    project's identity. Launching it any other way would let the empty-path
    assertions pass for the wrong reason.
    """
    exe = packaged_runtime()
    assert exe is not None
    project_root = tmp_path / "packaged-project.canx"
    with ProjectService().create(project_root, display_name="Packaged Project") as handle:
        project_id = handle.project_id
        metadata = handle.read_metadata()

    port = _free_port()
    token = "v0311-project-token"
    proc = subprocess.Popen(
        [str(exe), "--host", "127.0.0.1", "--port", str(port), "--session-token", token],
        # The packaged runtime's own working directory is a valid CAN-X project, so
        # `Path("")` would have resolved to it. Nothing in the runtime reads or
        # writes the working directory, so this leaves the project untouched.
        cwd=str(project_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            await _await_health(client)

            inspected = await client.get(
                "/project/inspect", params={"project_path": str(project_root)}
            )
            assert inspected.status_code == 200, inspected.text
            body = inspected.json()
            assert set(body) == {
                "project_id",
                "display_name",
                "schema_version",
                "created_at",
                "updated_at",
            }
            assert body["project_id"] == project_id
            assert body["display_name"] == "Packaged Project"
            assert body["schema_version"] == 1
            assert body["created_at"] == metadata.created_at.isoformat()
            assert body["updated_at"] == metadata.updated_at.isoformat()
            # A read model is not a filesystem authority.
            assert str(project_root) not in str(body)

            # Repeating the request must not depend on anything the first one left
            # behind: there is no current project and no retained handle.
            again = await client.get(
                "/project/inspect", params={"project_path": str(project_root)}
            )
            assert again.status_code == 200, again.text
            assert again.json() == body

            # A real domain failure, produced by the packaged project domain.
            absent = await client.get(
                "/project/inspect", params={"project_path": str(tmp_path / "absent.canx")}
            )
            assert absent.status_code == 400, absent.text
            absent_body = absent.json()
            assert set(absent_body) == {
                "code",
                "message",
                "details",
                "recoverable",
                "source",
            }
            assert absent_body["code"] == "project.not_found"
            assert absent_body["source"] == "project"
            assert absent_body["recoverable"] is False
            assert not (tmp_path / "absent.canx").exists()

            # The shared request-validation boundary is registered in the same
            # application factory the frozen executable runs, so a request with no
            # project path must be answered with that envelope here too.
            missing = await client.get("/project/inspect")
            assert missing.status_code == 422, missing.text
            missing_body = missing.json()
            assert missing_body["code"] == "api.request_validation_failed"
            assert missing_body["source"] == "api"
            assert missing_body["recoverable"] is False

            # An empty project path is a request-contract failure — not "inspect the
            # directory this process happens to run in". This process was launched
            # *from* a valid CAN-X project, so the un-fixed runtime would have
            # answered 200 here with that project's identity.
            for target in (
                "/project/inspect?project_path=",
                "/project/inspect?project_path",
            ):
                empty = await client.get(target)
                assert empty.status_code == 422, f"{target}: {empty.text}"
                empty_body = empty.json()
                assert set(empty_body) == {
                    "code",
                    "message",
                    "details",
                    "recoverable",
                    "source",
                }
                assert empty_body["code"] == "api.request_validation_failed"
                assert empty_body["source"] == "api"
                assert empty_body["recoverable"] is False
                # It must not have answered about the working directory.
                assert "project_id" not in empty_body
                assert empty_body["code"] != "project.not_found"

            # ...and the launched-from-a-project configuration really was the
            # dangerous one: the same project still answers when it is named.
            named_from_cwd = await client.get(
                "/project/inspect", params={"project_path": str(project_root)}
            )
            assert named_from_cwd.status_code == 200, named_from_cwd.text
            assert named_from_cwd.json() == body

            shutdown = await client.post(
                "/runtime/shutdown", headers={"X-CANX-Session-Token": token}
            )
            assert shutdown.status_code == 202

        proc.wait(timeout=30)
        assert proc.returncode == 0
    finally:
        _terminate(proc)


#: The V0.3-FINAL live proof of the decode-batch envelope (see the test below).
#:
#: V0.3-14 recorded "live Runtime sidecar decode-batch round trip" as NOT VERIFIED,
#: because every decode assertion in that phase — including its round-2 FIX — ran
#: against a stubbed transport. The Desktop's own contract tests still pin what the
#: *client requires*; nothing there can say what the *runtime produces*. This test is
#: that second, independent observation, taken from the frozen executable over real
#: HTTP, and it is the only place the whole envelope is asserted end to end:
#: ``schema_version`` / ``stream_id`` / ``first_sequence`` / ``last_sequence`` /
#: ``frame_count`` / outcome order / decoded message / raw value / physical value /
#: unit / choice label / per-frame failure.
#:
#: Two fixtures, because no single one carries both a unit and a ``VAL_`` table:
#: ``basic_standard.dbc`` has scaled, unit-bearing signals and an id that is absent
#: from it (for the failure outcome), ``choices.dbc`` has the value tables.
_CHOICES_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "dbc" / "choices.dbc"
_DOOR_STATUS_ID = 0x300
_DOOR_DATA = "01010000"


@pytest.mark.skipif(
    packaged_runtime() is None, reason="packaged canx-runtime.exe has not been built"
)
async def test_packaged_runtime_answers_the_full_decode_batch_envelope(
    tmp_path: Path,
) -> None:
    """V0.3-FINAL: open project → import → list → database → decode → batch.

    The executable is handed an *empty* project and nothing else: both DBCs are
    imported into it over HTTP, so the asset registration, the canonical ``.dbc``
    on disk and every decoded value below are produced inside that process. The
    source tree only creates the project and reads the fixture bytes.

    The mid-batch undecodable frame is the point of the batch contract, not an
    afterthought: a caller must receive it as *data about one frame* while the
    request as a whole still succeeds, otherwise one bad identifier at the end of a
    viewport would take the whole viewport's decode down with it.
    """
    exe = packaged_runtime()
    assert exe is not None
    project_root = tmp_path / "v030final-live.canx"
    with ProjectService().create(project_root, display_name="V0.3-FINAL live"):
        pass

    engine_bytes = _DBC_FIXTURE.read_bytes()
    door_bytes = _CHOICES_FIXTURE.read_bytes()

    port = _free_port()
    token = "v030final-live-token"
    proc = subprocess.Popen(
        [str(exe), "--host", "127.0.0.1", "--port", str(port), "--session-token", token],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            await _await_health(client)

            inspected = await client.get(
                "/project/inspect", params={"project_path": str(project_root)}
            )
            assert inspected.status_code == 200, inspected.text
            assert inspected.json()["display_name"] == "V0.3-FINAL live"

            assets: dict[str, str] = {}
            for fixture, blob in (
                (_DBC_FIXTURE, engine_bytes),
                (_CHOICES_FIXTURE, door_bytes),
            ):
                created = await client.post(
                    "/dbc/assets",
                    json={
                        "project_path": str(project_root),
                        "source_name": fixture.name,
                        "content_base64": base64.b64encode(blob).decode("ascii"),
                    },
                )
                assert created.status_code == 201, created.text
                asset = created.json()
                assert asset["sha256"] == hashlib.sha256(blob).hexdigest()
                assert asset["size_bytes"] == len(blob)
                assert asset["encoding"] == "utf-8-sig"
                assets[fixture.name] = asset["asset_id"]

            assert set(assets) == {_DBC_FIXTURE.name, _CHOICES_FIXTURE.name}
            engine_id = assets[_DBC_FIXTURE.name]
            door_id = assets[_CHOICES_FIXTURE.name]

            listed = await client.get(
                "/dbc/assets", params={"project_path": str(project_root)}
            )
            assert listed.status_code == 200, listed.text
            assert {item["asset_id"] for item in listed.json()["assets"]} == set(assets.values())

            engine_db = await client.get(
                f"/dbc/assets/{engine_id}/database",
                params={"project_path": str(project_root)},
            )
            assert engine_db.status_code == 200, engine_db.text
            assert [message["name"] for message in engine_db.json()["messages"]] == [
                "EngineData"
            ]

            door_db = await client.get(
                f"/dbc/assets/{door_id}/database",
                params={"project_path": str(project_root)},
            )
            assert door_db.status_code == 200, door_db.text
            door_messages = door_db.json()["messages"]
            assert [message["name"] for message in door_messages] == ["DoorStatus"]
            door_state = next(
                signal for signal in door_messages[0]["signals"] if signal["name"] == "DoorState"
            )
            assert [(entry["value"], entry["label"]) for entry in door_state["choices"]] == [
                (0, "Closed"),
                (1, "Open"),
                (2, "Error"),
            ]

            single = await client.post(
                f"/dbc/assets/{engine_id}/decode",
                json={
                    "project_path": str(project_root),
                    "frame": _dbc_frame(_DBC_ENGINE_DATA),
                },
            )
            assert single.status_code == 200, single.text
            assert single.json()["message_name"] == "EngineData"
            assert single.json()["signals"][0]["physical_value"] == 750.0

            batch = await client.post(
                f"/dbc/assets/{engine_id}/decode-batch",
                json={
                    "project_path": str(project_root),
                    "stream_id": "v030final-live",
                    "frames": [
                        _dbc_frame(_DBC_ENGINE_DATA, sequence=1),
                        _dbc_frame(_DBC_ENGINE_DATA, sequence=2, arbitration_id=0x7FF),
                        _dbc_frame(_DBC_ENGINE_DATA, sequence=3),
                    ],
                },
            )
            assert batch.status_code == 200, batch.text
            envelope = batch.json()
            assert set(envelope) == {
                "schema_version",
                "stream_id",
                "first_sequence",
                "last_sequence",
                "frame_count",
                "outcomes",
            }
            assert envelope["schema_version"] == 1
            assert envelope["stream_id"] == "v030final-live"
            assert envelope["first_sequence"] == 1
            assert envelope["last_sequence"] == 3
            assert envelope["frame_count"] == 3

            outcomes = envelope["outcomes"]
            assert [outcome["frame"]["sequence"] for outcome in outcomes] == [1, 2, 3]
            assert [outcome["decoded"] is not None for outcome in outcomes] == [True, False, True]
            # ``decoded`` and ``failure`` are mutually exclusive on every outcome.
            assert all(
                outcome["decoded"] is None or outcome["failure"] is None
                for outcome in outcomes
            )

            decoded_first = outcomes[0]["decoded"]
            assert decoded_first["message_name"] == "EngineData"
            speed = next(
                signal for signal in decoded_first["signals"] if signal["name"] == "EngineSpeed"
            )
            assert speed["raw_value"] == 3000
            assert speed["physical_value"] == 750.0
            assert speed["unit"] == "rpm"
            assert speed["choice_label"] is None

            failure = outcomes[1]["failure"]
            assert set(failure) == {"code", "message", "details", "recoverable", "source"}
            assert failure["code"] == "dbc.message_not_found"
            assert failure["source"] == "dbc"
            assert failure["recoverable"] is False
            # The frame is echoed whether or not it decoded, so a caller can align
            # outcomes to its own submission without trusting the order.
            assert outcomes[1]["frame"]["arbitration_id"] == 0x7FF

            door_batch = await client.post(
                f"/dbc/assets/{door_id}/decode-batch",
                json={
                    "project_path": str(project_root),
                    "stream_id": "v030final-live",
                    "frames": [
                        _dbc_frame(_DOOR_DATA, sequence=10, arbitration_id=_DOOR_STATUS_ID)
                    ],
                },
            )
            assert door_batch.status_code == 200, door_batch.text
            door_envelope = door_batch.json()
            assert door_envelope["first_sequence"] == 10
            assert door_envelope["last_sequence"] == 10
            assert door_envelope["frame_count"] == 1
            door_message = door_envelope["outcomes"][0]["decoded"]
            assert door_message["message_name"] == "DoorStatus"
            assert {
                signal["name"]: signal["choice_label"] for signal in door_message["signals"]
            } == {"DoorState": "Open", "LockState": "Locked"}

            # A batch that is not one contiguous run is refused by the shipped
            # runtime itself — not only by the Desktop client that builds batches.
            non_contiguous = await client.post(
                f"/dbc/assets/{engine_id}/decode-batch",
                json={
                    "project_path": str(project_root),
                    "stream_id": "v030final-live",
                    "frames": [
                        _dbc_frame(_DBC_ENGINE_DATA, sequence=1),
                        _dbc_frame(_DBC_ENGINE_DATA, sequence=3),
                    ],
                },
            )
            assert non_contiguous.status_code == 422, non_contiguous.text
            refused = non_contiguous.json()
            assert refused["code"] == "api.request_validation_failed"
            assert refused["source"] == "api"
            assert refused["recoverable"] is False

            # The same non-contiguous shape on the decode-specific surface: accepted. A
            # decode work set is ordered and needs no consecutive sequences, which is what
            # lets an alternating-channel viewport cost one request per asset. Two
            # observations, one payload: the strict surface still refuses it, and the
            # work-set surface answers it — from the frozen executable, over real HTTP.
            work_set = await client.post(
                f"/dbc/assets/{engine_id}/decode-frames",
                json={
                    "project_path": str(project_root),
                    "stream_id": "v030final-live",
                    "frames": [
                        _dbc_frame(_DBC_ENGINE_DATA, sequence=1),
                        _dbc_frame(_DBC_ENGINE_DATA, sequence=3),
                        _dbc_frame(_DBC_ENGINE_DATA, sequence=7, arbitration_id=0x7FF),
                    ],
                },
            )
            assert work_set.status_code == 200, work_set.text
            work_envelope = work_set.json()
            assert set(work_envelope) == {
                "schema_version",
                "stream_id",
                "frame_count",
                "sequences",
                "outcomes",
            }
            assert work_envelope["schema_version"] == 1
            assert work_envelope["stream_id"] == "v030final-live"
            assert work_envelope["frame_count"] == 3
            # The submitted sequences come back verbatim, gaps included: that is what lets
            # a caller align an outcome to a frame without trusting position alone.
            assert work_envelope["sequences"] == [1, 3, 7]
            assert [
                outcome["frame"]["sequence"] for outcome in work_envelope["outcomes"]
            ] == [1, 3, 7]
            assert [
                outcome["decoded"] is not None for outcome in work_envelope["outcomes"]
            ] == [True, True, False]
            assert work_envelope["outcomes"][2]["failure"]["code"] == "dbc.message_not_found"
            work_speed = next(
                signal
                for signal in work_envelope["outcomes"][0]["decoded"]["signals"]
                if signal["name"] == "EngineSpeed"
            )
            assert work_speed["physical_value"] == 750.0

            shutdown = await client.post(
                "/runtime/shutdown", headers={"X-CANX-Session-Token": token}
            )
            assert shutdown.status_code == 202

        proc.wait(timeout=30)
        assert proc.returncode == 0
    finally:
        _terminate(proc)
