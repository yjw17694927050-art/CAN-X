"""The DBC HTTP surface over a real, reopened project.

Nothing about the DBC stack is mocked here: the project is created by
:class:`~canx.project.service.ProjectService`, the assets are imported by
:class:`~canx.dbc.project_service.ProjectDbcService` into project-owned files,
the requests go through the real FastAPI ASGI application, and the decode is
performed by :class:`~canx.dbc.decode.DbcDecoder` on the canonical database read
back from disk. A passing run therefore proves the whole chain — create, import,
close, reopen, serve — and not merely that a handler returns a shape.

Three facts get their own test, because each is a claim the architecture makes:

* **the API is a projection, not a second decoder.** The same frame is decoded
  through the domain directly and through HTTP, and the two answers must agree
  signal for signal;
* **one project, two assets, no crossing.** Two DBCs that both define ``0x123``
  answer for their own asset only, in both identifier spaces;
* **a tampered project-owned copy is a 409.** CAN-X reports the broken project
  and never silently re-reads, re-hashes or substitutes another file.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from canx.api.app import create_app
from canx.dbc.decode import DbcDecoder
from canx.dbc.project_service import ProjectDbcService
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectService
from httpx import ASGITransport, AsyncClient

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "dbc"

BASIC = FIXTURES / "basic_standard.dbc"
EXTENDED = FIXTURES / "extended.dbc"
MALFORMED = FIXTURES / "malformed.dbc"

#: ``basic_standard.dbc``'s ``EngineData`` (standard, id 0x123).
ENGINE_DATA = "B80B508000000000"
#: ``extended.dbc``'s ``TruckStatus`` (extended, id 0x123).
TRUCK_DATA = "1027090000000000"

STREAM_ID = "dbc-integration-stream"
UNKNOWN_ASSET = "11111111-2222-4333-8444-555555555555"

ENVELOPE_FIELDS = {"code", "message", "details", "recoverable", "source"}


def _imported_project(tmp_path: Path, *fixtures: Path) -> tuple[Path, list[str]]:
    """Create a project, import the fixtures as assets, then close it again.

    The project is deliberately left closed: every HTTP request below has to open
    and validate it itself, which is the behaviour under test.
    """
    root = tmp_path / "vehicle.canx"
    asset_ids: list[str] = []
    with ProjectService().create(root, display_name="Integration") as handle:
        service = ProjectDbcService(handle.root)
        for index, fixture in enumerate(fixtures):
            inbox = tmp_path / f"inbox-{index}"
            inbox.mkdir(parents=True, exist_ok=True)
            source = inbox / fixture.name
            source.write_bytes(fixture.read_bytes())
            asset_ids.append(service.import_asset(source).asset_id)
    return root, asset_ids


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://testserver")


def _wire(data: str, **overrides: object) -> dict[str, object]:
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


def _canonical(data: bytes, *, is_extended: bool = False) -> Frame:
    """The canonical frame the wire payload above describes."""
    return Frame(
        sequence=1,
        channel_id="can0",
        arbitration_id=0x123,
        is_extended=is_extended,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=len(data),
        data=data,
        direction=Direction.RX,
        hardware_timestamp=12.5,
        host_timestamp=100.25,
        normalized_timestamp=0.25,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HARDWARE,
        flags=0,
    )


async def test_a_reopened_project_serves_asset_database_and_decode(tmp_path: Path) -> None:
    """create → import → close → HTTP list/get/database/decode/decode-batch."""
    root, asset_ids = _imported_project(tmp_path, BASIC)
    asset_id = asset_ids[0]
    assert (root / "dbc" / f"{asset_id}.dbc").is_file()

    async with _client() as client:
        listed = await client.get("/dbc/assets", params={"project_path": str(root)})
        assert listed.status_code == 200, listed.text
        assert [asset["asset_id"] for asset in listed.json()["assets"]] == [asset_id]

        fetched = await client.get(
            f"/dbc/assets/{asset_id}", params={"project_path": str(root)}
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["source_name"] == BASIC.name

        database = await client.get(
            f"/dbc/assets/{asset_id}/database", params={"project_path": str(root)}
        )
        assert database.status_code == 200, database.text
        assert [message["name"] for message in database.json()["messages"]] == ["EngineData"]

        decoded = await client.post(
            f"/dbc/assets/{asset_id}/decode",
            json={"project_path": str(root), "frame": _wire(ENGINE_DATA)},
        )
        assert decoded.status_code == 200, decoded.text
        assert decoded.json()["message_name"] == "EngineData"

        batch = await client.post(
            f"/dbc/assets/{asset_id}/decode-batch",
            json={
                "project_path": str(root),
                "stream_id": STREAM_ID,
                "frames": [
                    _wire(ENGINE_DATA, sequence=sequence) for sequence in range(3)
                ],
            },
        )
        assert batch.status_code == 200, batch.text
        assert batch.json()["frame_count"] == 3

    # The project is still exactly what the domain says it is, read back from disk.
    with ProjectService().open(root) as reopened:
        stored = ProjectDbcService(reopened.root).get_asset(asset_id)
        assert stored.asset_id == asset_id
        assert stored.project_id == reopened.project_id


async def test_the_http_answer_equals_the_domain_answer_signal_for_signal(
    tmp_path: Path,
) -> None:
    """The endpoint is a projection of the decoder, not a second implementation."""
    root, asset_ids = _imported_project(tmp_path, BASIC)
    raw = bytes.fromhex(ENGINE_DATA)

    with ProjectService().open(root) as reopened:
        document = ProjectDbcService(reopened.root).load_asset(asset_ids[0])
    expected = DbcDecoder(document.database).decode_frame(_canonical(raw))

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json={"project_path": str(root), "frame": _wire(ENGINE_DATA)},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message_name"] == expected.message_name
    assert [signal["name"] for signal in body["signals"]] == [
        signal.name for signal in expected.signals
    ]
    for reported, decoded in zip(body["signals"], expected.signals, strict=True):
        assert reported["raw_value"] == decoded.raw_value
        assert reported["physical_value"] == decoded.physical_value
        assert reported["choice_label"] == decoded.choice_label
        assert reported["unit"] == decoded.unit


async def test_two_assets_in_one_project_never_cross(tmp_path: Path) -> None:
    """Two DBCs that both claim ``0x123`` answer for their own asset only."""
    root, asset_ids = _imported_project(tmp_path, BASIC, EXTENDED)
    standard_id, extended_id = asset_ids

    async with _client() as client:
        standard_database = (
            await client.get(
                f"/dbc/assets/{standard_id}/database", params={"project_path": str(root)}
            )
        ).json()
        extended_database = (
            await client.get(
                f"/dbc/assets/{extended_id}/database", params={"project_path": str(root)}
            )
        ).json()

        standard_decode = await client.post(
            f"/dbc/assets/{standard_id}/decode",
            json={
                "project_path": str(root),
                "frame": _wire(ENGINE_DATA, arbitration_id=0x123, is_extended=False),
            },
        )
        extended_decode = await client.post(
            f"/dbc/assets/{extended_id}/decode",
            json={
                "project_path": str(root),
                "frame": _wire(TRUCK_DATA, arbitration_id=0x123, is_extended=True),
            },
        )
        crossed = await client.post(
            f"/dbc/assets/{extended_id}/decode",
            json={
                "project_path": str(root),
                "frame": _wire(ENGINE_DATA, arbitration_id=0x123, is_extended=False),
            },
        )

    assert [message["name"] for message in standard_database["messages"]] == ["EngineData"]
    assert standard_database["messages"][0]["is_extended"] is False
    assert [message["name"] for message in extended_database["messages"]] == ["TruckStatus"]
    assert extended_database["messages"][0]["is_extended"] is True

    assert standard_decode.json()["message_name"] == "EngineData"
    assert extended_decode.json()["message_name"] == "TruckStatus"
    assert crossed.status_code == 422
    assert crossed.json()["code"] == "dbc.message_not_found"


async def test_a_tampered_asset_is_a_409_integrity_failure(tmp_path: Path) -> None:
    """The registry row is checked against the file on every load, never trusted."""
    root, asset_ids = _imported_project(tmp_path, BASIC)
    target = root / "dbc" / f"{asset_ids[0]}.dbc"
    target.write_bytes(target.read_bytes() + b"\n")

    async with _client() as client:
        decode = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json={"project_path": str(root), "frame": _wire(ENGINE_DATA)},
        )
        database = await client.get(
            f"/dbc/assets/{asset_ids[0]}/database", params={"project_path": str(root)}
        )

    for response in (decode, database):
        assert response.status_code == 409, response.text
        body = response.json()
        assert set(body) == ENVELOPE_FIELDS
        assert body["code"] == "dbc.asset_integrity_failed"
        assert body["source"] == "dbc"
        assert body["recoverable"] is False


async def test_a_missing_asset_file_is_a_409_not_a_404(tmp_path: Path) -> None:
    """The asset is registered; its file is gone. Those are different facts."""
    root, asset_ids = _imported_project(tmp_path, BASIC)
    (root / "dbc" / f"{asset_ids[0]}.dbc").unlink()

    async with _client() as client:
        response = await client.get(
            f"/dbc/assets/{asset_ids[0]}/database", params={"project_path": str(root)}
        )

    assert response.status_code == 409
    assert response.json()["code"] == "dbc.asset_integrity_failed"


async def test_an_unknown_asset_is_a_404(tmp_path: Path) -> None:
    root, _ = _imported_project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.get(
            f"/dbc/assets/{UNKNOWN_ASSET}/database", params={"project_path": str(root)}
        )

    assert response.status_code == 404
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == "dbc.asset_not_found"
    assert body["source"] == "dbc"


async def test_an_invalid_project_target_is_reported_by_the_project_domain(
    tmp_path: Path,
) -> None:
    plain = tmp_path / "not-a-project"
    plain.mkdir()

    async with _client() as client:
        response = await client.get("/dbc/assets", params={"project_path": str(plain)})

    assert response.status_code == 400
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["source"] == "project"
    assert body["code"].startswith("project.")
    # A rejected target is left exactly as it was.
    assert sorted(path.name for path in plain.iterdir()) == []


async def test_a_decode_against_an_absent_project_is_a_400_from_the_project_domain(
    tmp_path: Path,
) -> None:
    absent = tmp_path / "absent.canx"

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{UNKNOWN_ASSET}/decode",
            json={"project_path": str(absent), "frame": _wire(ENGINE_DATA)},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "project.not_found"
    assert response.json()["source"] == "project"
    assert not absent.exists()


# --- content import over HTTP ------------------------------------------------
#
# The V0.3-06 chain, with nothing mocked: a closed project, the real ASGI app, the
# bytes submitted as Base64, and every value asserted below produced by the
# project-owned asset the Runtime itself wrote — not by a fixture this test
# placed into the project.


def _empty_project(tmp_path: Path) -> Path:
    """Create a real project and return it closed.

    Every request below has to open and validate it, which is the behaviour under
    test: no request is handed an already-open project.
    """
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Content import"):
        pass
    return root


def _content_body(
    root: Path, fixture: Path, *, source_name: str | None = None, encoding: str | None = None
) -> dict[str, object]:
    """Build one content-import body carrying ``fixture``'s exact bytes."""
    payload: dict[str, object] = {
        "project_path": str(root),
        "source_name": fixture.name if source_name is None else source_name,
        "content_base64": base64.b64encode(fixture.read_bytes()).decode("ascii"),
    }
    if encoding is not None:
        payload["encoding"] = encoding
    return payload


async def test_a_content_import_feeds_the_existing_read_and_decode_apis(
    tmp_path: Path,
) -> None:
    """create → close → POST content → list → database → decode, all over HTTP."""
    root = _empty_project(tmp_path)
    raw = BASIC.read_bytes()

    async with _client() as client:
        created = await client.post("/dbc/assets", json=_content_body(root, BASIC))
        assert created.status_code == 201, created.text
        asset = created.json()
        asset_id = asset["asset_id"]

        listed = await client.get("/dbc/assets", params={"project_path": str(root)})
        assert listed.status_code == 200, listed.text

        database = await client.get(
            f"/dbc/assets/{asset_id}/database", params={"project_path": str(root)}
        )
        assert database.status_code == 200, database.text

        decoded = await client.post(
            f"/dbc/assets/{asset_id}/decode",
            json={"project_path": str(root), "frame": _wire(ENGINE_DATA)},
        )
        assert decoded.status_code == 200, decoded.text

    assert [item["asset_id"] for item in listed.json()["assets"]] == [asset_id]
    assert asset["sha256"] == hashlib.sha256(raw).hexdigest()
    assert asset["size_bytes"] == len(raw)
    assert [message["name"] for message in database.json()["messages"]] == ["EngineData"]

    body = decoded.json()
    assert body["message_name"] == "EngineData"
    signals = {signal["name"]: signal for signal in body["signals"]}
    assert signals["EngineSpeed"]["raw_value"] == 3000
    assert signals["EngineSpeed"]["physical_value"] == 750.0
    assert signals["EngineSpeed"]["unit"] == "rpm"
    assert signals["CoolantTemp"]["raw_value"] == 80
    assert signals["CoolantTemp"]["physical_value"] == 40.0
    assert signals["CoolantTemp"]["unit"] == "degC"

    # Read back from the source tree: the project-owned copy is what was submitted.
    with ProjectService().open(root) as reopened:
        stored = ProjectDbcService(reopened.root).get_asset(asset_id)
        owned = reopened.root / stored.relative_path
    assert owned.read_bytes() == raw
    assert stored.sha256 == hashlib.sha256(raw).hexdigest()
    assert stored.size_bytes == len(raw)


async def test_two_content_imports_in_one_project_never_cross(tmp_path: Path) -> None:
    """Two DBCs that both answer for ``0x123`` answer for their own asset only."""
    root = _empty_project(tmp_path)

    async with _client() as client:
        standard = await client.post("/dbc/assets", json=_content_body(root, BASIC))
        extended = await client.post("/dbc/assets", json=_content_body(root, EXTENDED))
        assert standard.status_code == 201, standard.text
        assert extended.status_code == 201, extended.text
        standard_id = standard.json()["asset_id"]
        extended_id = extended.json()["asset_id"]

        standard_decode = await client.post(
            f"/dbc/assets/{standard_id}/decode",
            json={"project_path": str(root), "frame": _wire(ENGINE_DATA, is_extended=False)},
        )
        extended_decode = await client.post(
            f"/dbc/assets/{extended_id}/decode",
            json={
                "project_path": str(root),
                "frame": _wire(TRUCK_DATA, is_extended=True),
            },
        )
        crossed = await client.post(
            f"/dbc/assets/{extended_id}/decode",
            json={"project_path": str(root), "frame": _wire(ENGINE_DATA, is_extended=False)},
        )
        listed = await client.get("/dbc/assets", params={"project_path": str(root)})

    assert [item["asset_id"] for item in listed.json()["assets"]] == [
        standard_id,
        extended_id,
    ]
    assert standard_decode.json()["message_name"] == "EngineData"
    assert extended_decode.json()["message_name"] == "TruckStatus"
    assert crossed.status_code == 422
    assert crossed.json()["code"] == "dbc.message_not_found"
    assert crossed.json()["source"] == "dbc"


async def test_a_content_import_lives_beside_a_path_import_in_one_project(
    tmp_path: Path,
) -> None:
    """The two import entry points share the project, the registry and the API."""
    root, asset_ids = _imported_project(tmp_path, BASIC)
    path_imported = asset_ids[0]

    async with _client() as client:
        created = await client.post("/dbc/assets", json=_content_body(root, EXTENDED))
        assert created.status_code == 201, created.text
        content_imported = created.json()["asset_id"]

        listed = await client.get("/dbc/assets", params={"project_path": str(root)})
        path_decode = await client.post(
            f"/dbc/assets/{path_imported}/decode",
            json={"project_path": str(root), "frame": _wire(ENGINE_DATA)},
        )
        content_decode = await client.post(
            f"/dbc/assets/{content_imported}/decode",
            json={
                "project_path": str(root),
                "frame": _wire(TRUCK_DATA, is_extended=True),
            },
        )

    assert {item["asset_id"] for item in listed.json()["assets"]} == {
        path_imported,
        content_imported,
    }
    assert path_decode.json()["message_name"] == "EngineData"
    assert content_decode.json()["message_name"] == "TruckStatus"


async def test_a_rejected_project_and_rejected_content_are_reported_distinctly(
    tmp_path: Path,
) -> None:
    """A bad target is the project domain's 400; bad content is the DBC domain's 422."""
    plain = tmp_path / "not-a-project"
    plain.mkdir()
    root = _empty_project(tmp_path)
    broken = tmp_path / "broken.dbc"
    broken.write_bytes(MALFORMED.read_bytes())

    async with _client() as client:
        bad_project = await client.post("/dbc/assets", json=_content_body(plain, BASIC))
        bad_content = await client.post("/dbc/assets", json=_content_body(root, broken))

    assert bad_project.status_code == 400
    assert bad_project.json()["source"] == "project"
    assert bad_project.json()["code"].startswith("project.")

    assert bad_content.status_code == 422
    assert bad_content.json()["code"] == "dbc.parse_failed"
    assert bad_content.json()["source"] == "dbc"
    assert bad_content.json()["recoverable"] is False

    # Neither refusal left anything behind.
    assert sorted(path.name for path in plain.iterdir()) == []
    assert list((root / "dbc").iterdir()) == []


async def test_a_content_imported_asset_is_integrity_checked_like_any_other(
    tmp_path: Path,
) -> None:
    """Importing through content does not create a second, weaker asset kind."""
    root = _empty_project(tmp_path)

    async with _client() as client:
        created = await client.post("/dbc/assets", json=_content_body(root, BASIC))
        assert created.status_code == 201, created.text
        asset_id = created.json()["asset_id"]

        target = root / "dbc" / f"{asset_id}.dbc"
        target.write_bytes(target.read_bytes() + b"\n")

        database = await client.get(
            f"/dbc/assets/{asset_id}/database", params={"project_path": str(root)}
        )
        decode = await client.post(
            f"/dbc/assets/{asset_id}/decode",
            json={"project_path": str(root), "frame": _wire(ENGINE_DATA)},
        )

    for response in (database, decode):
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "dbc.asset_integrity_failed"
        assert response.json()["recoverable"] is False


async def test_every_request_that_imports_the_same_content_gets_its_own_asset(
    tmp_path: Path,
) -> None:
    """No de-duplication: the same bytes submitted twice are two project assets."""
    root = _empty_project(tmp_path)

    async with _client() as client:
        first = await client.post("/dbc/assets", json=_content_body(root, BASIC))
        second = await client.post("/dbc/assets", json=_content_body(root, BASIC))
        listed = await client.get("/dbc/assets", params={"project_path": str(root)})

    assert first.status_code == second.status_code == 201
    assert first.json()["asset_id"] != second.json()["asset_id"]
    assert first.json()["sha256"] == second.json()["sha256"]
    assert len(listed.json()["assets"]) == 2
    assert sorted(path.name for path in (root / "dbc").iterdir()) == sorted(
        f"{item.json()['asset_id']}.dbc" for item in (first, second)
    )
