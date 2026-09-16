"""HTTP contract for the DBC runtime surface.

Every endpoint here is driven over the real ASGI app against a real CAN-X
project whose DBC assets were really imported by the asset domain, so a passing
case proves the whole chain:

``ASGITransport → /dbc/… → ProjectDbcService → project-owned file → DbcDecoder →
JSON``.

Three things are pinned deliberately:

* **the projection is canonical, not engine-shaped.** Field names, ordering and
  values come from :mod:`canx.dbc.model`; no ``cantools`` object, path or parser
  internal is ever serialized;
* **the frame contract is the shared one.** A decoded frame carries the full
  provenance — timestamps, ``direction``, ``flags`` — and its payload is uppercase
  hex, the same shape Trace publishes;
* **a rejection is the shared envelope.** A payload that does not describe a
  canonical frame is answered with ``api.request_validation_failed`` / ``source =
  api``, never with the framework's own ``{"detail": …}`` and never with an echo of
  the caller's payload.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from canx.api.app import create_app
from canx.dbc.project_service import ProjectDbcService
from canx.project.service import ProjectService
from httpx import ASGITransport, AsyncClient

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "dbc"

BASIC = FIXTURES / "basic_standard.dbc"
CHOICES = FIXTURES / "choices.dbc"
CAN_FD = FIXTURES / "can_fd.dbc"
FD_EXTRA_PAYLOAD = FIXTURES / "fd_extra_payload.dbc"
FLOAT_SIGNAL = FIXTURES / "float_signal.dbc"
METADATA = FIXTURES / "metadata.dbc"
MIXED_ENDIAN = FIXTURES / "endian_signed_scale.dbc"
MULTIPLEXED = FIXTURES / "multiplexed.dbc"
EXTENDED = FIXTURES / "extended.dbc"
MALFORMED = FIXTURES / "malformed.dbc"

#: ``basic_standard.dbc``'s ``EngineData``: EngineSpeed 3000 → 750 rpm,
#: CoolantTemp 80 → 40 degC, ThrottlePosition 128 → 50.19607808 %.
ENGINE_DATA = "B80B508000000000"

#: ``extended.dbc``'s ``TruckStatus``: VehicleSpeed 10000 → 39.0625 km/h,
#: GearPosition 9 → 9.
TRUCK_DATA = "1027090000000000"

STREAM_ID = "dbc-api-stream"

ENVELOPE_FIELDS = {"code", "message", "details", "recoverable", "source"}
REQUEST_VALIDATION_CODE = "api.request_validation_failed"

#: A syntactically valid asset id that no project registers.
UNKNOWN_ASSET = "11111111-2222-4333-8444-555555555555"

#: Substrings that must never appear in a rejection: framework internals, engine
#: internals and any rendering of the caller's own payload.
LEAK_MARKERS = ("traceback", "pydantic", "sqlite", "cantools", "pyarrow", '"input"')


def _project(tmp_path: Path, *fixtures: Path) -> tuple[Path, list[str]]:
    """Create a real CAN-X project and import each fixture as a project-owned asset.

    The fixtures are copied into a directory *outside* the project first, because
    that is the only way the import service is ever asked to read a file: an
    in-project source is not a thing this domain imports.
    """
    root = tmp_path / "vehicle.canx"
    asset_ids: list[str] = []
    with ProjectService().create(root, display_name="DBC API") as handle:
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
    """Build one canonical frame wire payload carrying ``data``."""
    payload: dict[str, object] = {
        "sequence": 1,
        "channel_id": "can0",
        "arbitration_id": 0x123,
        "is_extended": False,
        "is_fd": False,
        "bitrate_switch": False,
        "error_state_indicator": False,
        "dlc": len(data) // 2 if isinstance(data, str) else len(ENGINE_DATA) // 2,
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


def _decode_body(root: Path, data: str, **overrides: object) -> dict[str, object]:
    return {"project_path": str(root), "frame": _wire(data, **overrides)}


def _assert_validation_envelope(response_status: int, body: dict[str, object]) -> None:
    """Assert the whole request-validation contract for one response."""
    assert response_status == 422
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == REQUEST_VALIDATION_CODE
    assert body["source"] == "api"
    assert body["recoverable"] is False
    details = body["details"]
    assert isinstance(details, dict)
    issues = details["errors"]
    assert isinstance(issues, list) and issues
    for issue in issues:
        assert isinstance(issue, dict)
        assert set(issue) == {"location", "type", "message"}
        assert isinstance(issue["location"], list) and issue["location"]
    rendered = str(body).lower()
    for marker in LEAK_MARKERS:
        assert marker not in rendered, marker


# --- asset endpoints ---------------------------------------------------------


async def test_an_empty_project_lists_no_assets(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)
    async with _client() as client:
        response = await client.get("/dbc/assets", params={"project_path": str(root)})

    assert response.status_code == 200
    assert response.json() == {"assets": []}


async def test_one_asset_is_listed_with_its_registered_metadata(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, BASIC)
    raw = BASIC.read_bytes()

    async with _client() as client:
        response = await client.get("/dbc/assets", params={"project_path": str(root)})

    assert response.status_code == 200
    assets = response.json()["assets"]
    assert len(assets) == 1
    asset = assets[0]
    assert set(asset) == {
        "asset_id",
        "source_name",
        "sha256",
        "size_bytes",
        "encoding",
        "imported_at",
    }
    assert asset["asset_id"] == asset_ids[0]
    assert asset["source_name"] == BASIC.name
    assert asset["sha256"] == hashlib.sha256(raw).hexdigest()
    assert asset["size_bytes"] == len(raw)
    assert asset["encoding"]
    assert datetime.fromisoformat(asset["imported_at"]).tzinfo is not None


async def test_assets_keep_the_domain_order_and_never_adopt_a_stray_file(
    tmp_path: Path,
) -> None:
    """Registration is what makes a file an asset; ``dbc/`` is not a directory scan."""
    root, asset_ids = _project(tmp_path, BASIC, EXTENDED, CHOICES)
    (root / "dbc" / "unregistered.dbc").write_bytes(b'VERSION "1.0"\n')

    async with _client() as client:
        response = await client.get("/dbc/assets", params={"project_path": str(root)})

    listed = [asset["asset_id"] for asset in response.json()["assets"]]
    assert len(listed) == len(asset_ids)
    assert set(listed) == set(asset_ids)

    # The API must not reorder what the domain already ordered deterministically.
    with ProjectService().open(root) as reopened:
        domain_order = [
            asset.asset_id for asset in ProjectDbcService(reopened.root).list_assets()
        ]
    assert listed == domain_order


async def test_get_asset_returns_exactly_the_requested_asset(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, BASIC, EXTENDED)

    async with _client() as client:
        response = await client.get(
            f"/dbc/assets/{asset_ids[1]}", params={"project_path": str(root)}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"] == asset_ids[1]
    assert body["source_name"] == EXTENDED.name


async def test_an_unregistered_asset_is_a_structured_404(tmp_path: Path) -> None:
    root, _ = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.get(
            f"/dbc/assets/{UNKNOWN_ASSET}", params={"project_path": str(root)}
        )

    assert response.status_code == 404
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == "dbc.asset_not_found"
    assert body["source"] == "dbc"
    assert body["recoverable"] is False
    assert body["details"]["asset_id"] == UNKNOWN_ASSET


async def test_an_asset_id_that_is_not_a_uuid_is_a_404_not_a_500(tmp_path: Path) -> None:
    """A lookup key that could never identify an asset is "not registered"."""
    root, _ = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.get(
            "/dbc/assets/not-a-uuid", params={"project_path": str(root)}
        )

    assert response.status_code == 404
    assert response.json()["code"] == "dbc.asset_not_found"


async def test_an_invalid_project_is_a_structured_400(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-project"
    plain.mkdir()

    async with _client() as client:
        response = await client.get("/dbc/assets", params={"project_path": str(plain)})

    assert response.status_code == 400
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["source"] == "project"
    assert body["code"].startswith("project.")
    assert sorted(path.name for path in plain.iterdir()) == []


async def test_a_missing_project_is_a_structured_400(tmp_path: Path) -> None:
    async with _client() as client:
        response = await client.get(
            "/dbc/assets", params={"project_path": str(tmp_path / "absent.canx")}
        )

    assert response.status_code == 400
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == "project.not_found"
    assert body["source"] == "project"
    assert not (tmp_path / "absent.canx").exists()


async def test_a_missing_project_path_parameter_is_rejected() -> None:
    async with _client() as client:
        response = await client.get("/dbc/assets")

    _assert_validation_envelope(response.status_code, response.json())


# --- canonical database projection -------------------------------------------


async def test_the_database_projection_is_the_canonical_definition(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.get(
            f"/dbc/assets/{asset_ids[0]}/database", params={"project_path": str(root)}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"version", "messages", "nodes"}
    assert body["version"] == "1.0"
    assert [node["name"] for node in body["nodes"]] == ["Engine", "ECU"]
    assert all(set(node) == {"name", "comment"} for node in body["nodes"])

    assert len(body["messages"]) == 1
    message = body["messages"][0]
    assert set(message) == {
        "frame_id",
        "name",
        "length",
        "is_extended",
        "is_fd",
        "senders",
        "comment",
        "cycle_time",
        "signals",
    }
    assert (message["frame_id"], message["name"]) == (291, "EngineData")
    assert message["length"] == 8
    assert message["is_extended"] is False
    assert message["is_fd"] is False
    assert message["senders"] == ["Engine"]
    assert message["comment"] == "Primary engine broadcast frame"
    assert message["cycle_time"] is None

    assert [signal["name"] for signal in message["signals"]] == [
        "EngineSpeed",
        "CoolantTemp",
        "ThrottlePosition",
    ]
    signal = message["signals"][0]
    assert set(signal) == {
        "name",
        "start_bit",
        "length",
        "byte_order",
        "is_signed",
        "is_float",
        "factor",
        "offset",
        "minimum",
        "maximum",
        "unit",
        "receivers",
        "choices",
        "is_multiplexer",
        "multiplexer_signal",
        "multiplexer_ids",
        "comment",
    }
    assert signal["start_bit"] == 0
    assert signal["length"] == 16
    assert signal["byte_order"] == "little_endian"
    assert signal["is_signed"] is False
    assert signal["is_float"] is False
    assert signal["factor"] == 0.25
    assert signal["offset"] == 0.0
    assert signal["minimum"] == 0.0
    assert signal["maximum"] == 16383.75
    assert signal["unit"] == "rpm"
    assert signal["receivers"] == ["ECU"]
    assert signal["choices"] == []
    assert signal["is_multiplexer"] is False
    assert signal["multiplexer_signal"] is None
    assert signal["multiplexer_ids"] is None
    assert signal["comment"] == "Crank-shaft speed"

    # The offset is the load-bearing value: it proves the projection carries the
    # document's mapping rather than the engine's already-scaled output.
    coolant = message["signals"][1]
    assert (coolant["factor"], coolant["offset"], coolant["unit"]) == (1.0, -40.0, "degC")


async def test_the_projection_carries_choices_signedness_and_mixed_byte_order(
    tmp_path: Path,
) -> None:
    root, asset_ids = _project(tmp_path, CHOICES, MIXED_ENDIAN)

    async with _client() as client:
        choices_body = (
            await client.get(
                f"/dbc/assets/{asset_ids[0]}/database", params={"project_path": str(root)}
            )
        ).json()
        endian_body = (
            await client.get(
                f"/dbc/assets/{asset_ids[1]}/database", params={"project_path": str(root)}
            )
        ).json()

    message = choices_body["messages"][0]
    assert (message["frame_id"], message["name"], message["length"]) == (768, "DoorStatus", 4)
    door_state, lock_state = message["signals"]
    assert door_state["choices"] == [
        {"value": 0, "label": "Closed"},
        {"value": 1, "label": "Open"},
        {"value": 2, "label": "Error"},
    ]
    assert lock_state["choices"] == [
        {"value": 0, "label": "Unlocked"},
        {"value": 1, "label": "Locked"},
    ]

    little, big = endian_body["messages"][0]["signals"]
    assert little["byte_order"] == "little_endian"
    assert little["is_signed"] is False
    assert (little["factor"], little["offset"], little["unit"]) == (2.0, 1.0, "mV")
    assert big["byte_order"] == "big_endian"
    assert big["is_signed"] is True
    assert big["start_bit"] == 39
    assert (big["factor"], big["offset"], big["unit"]) == (0.5, -100.0, "deg")


async def test_the_projection_carries_multiplexing_metadata(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, MULTIPLEXED)

    async with _client() as client:
        body = (
            await client.get(
                f"/dbc/assets/{asset_ids[0]}/database", params={"project_path": str(root)}
            )
        ).json()

    message = body["messages"][0]
    assert (message["frame_id"], message["name"]) == (1024, "MuxFrame")
    switch, mode_a, mode_b, mode_c = message["signals"]
    assert switch["is_multiplexer"] is True
    assert switch["multiplexer_signal"] is None
    assert switch["multiplexer_ids"] is None
    assert (mode_a["multiplexer_signal"], mode_a["multiplexer_ids"]) == ("ModeSwitch", [0])
    assert (mode_b["multiplexer_signal"], mode_b["multiplexer_ids"]) == ("ModeSwitch", [1])
    assert (mode_c["multiplexer_signal"], mode_c["multiplexer_ids"]) == ("ModeSwitch", [2])
    assert mode_a["is_multiplexer"] is False


async def test_the_projection_carries_can_fd_metadata(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, CAN_FD)

    async with _client() as client:
        body = (
            await client.get(
                f"/dbc/assets/{asset_ids[0]}/database", params={"project_path": str(root)}
            )
        ).json()

    message = body["messages"][0]
    assert message["is_fd"] is True
    assert message["length"] == 64
    assert [(signal["name"], signal["length"]) for signal in message["signals"]] == [
        ("BlobFirst", 32),
        ("BlobSecond", 32),
    ]


async def test_the_projection_carries_nodes_comments_and_cycle_time(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, METADATA)

    async with _client() as client:
        body = (
            await client.get(
                f"/dbc/assets/{asset_ids[0]}/database", params={"project_path": str(root)}
            )
        ).json()

    assert body["version"] == "2.0"
    assert body["nodes"] == [
        {"name": "Gateway", "comment": "Gateway node comment"},
        {"name": "Sensor", "comment": None},
        {"name": "Actuator", "comment": None},
    ]
    message = body["messages"][0]
    assert message["cycle_time"] == 50
    assert message["comment"] == "Status frame comment"
    node_id, counter = message["signals"]
    assert node_id["receivers"] == ["Sensor", "Actuator"]
    assert counter["comment"] == "Rolling counter"


async def test_the_projection_carries_float_metadata_without_decoding_it(
    tmp_path: Path,
) -> None:
    root, asset_ids = _project(tmp_path, FLOAT_SIGNAL)

    async with _client() as client:
        body = (
            await client.get(
                f"/dbc/assets/{asset_ids[0]}/database", params={"project_path": str(root)}
            )
        ).json()

    speed, pressure = body["messages"][0]["signals"]
    assert speed["is_float"] is False
    assert pressure["is_float"] is True
    assert pressure["length"] == 32


async def test_the_database_projection_serializes_no_engine_or_storage_internals(
    tmp_path: Path,
) -> None:
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.get(
            f"/dbc/assets/{asset_ids[0]}/database", params={"project_path": str(root)}
        )

    rendered = str(response.json()).lower()
    for leaked in ("cantools", "namedvalue", "traceback", "sqlite", "pyarrow", ".dbc"):
        assert leaked not in rendered, leaked


# --- single frame decode -----------------------------------------------------


async def test_a_standard_frame_decodes_into_raw_and_physical_values(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, ENGINE_DATA),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"frame", "message_name", "signals"}
    assert body["message_name"] == "EngineData"
    assert [signal["name"] for signal in body["signals"]] == [
        "EngineSpeed",
        "CoolantTemp",
        "ThrottlePosition",
    ]
    for signal in body["signals"]:
        assert set(signal) == {"name", "raw_value", "physical_value", "choice_label", "unit"}

    speed, coolant, throttle = body["signals"]
    assert speed["raw_value"] == 3000
    assert speed["physical_value"] == pytest.approx(750.0)
    assert speed["unit"] == "rpm"
    assert speed["choice_label"] is None
    assert coolant["raw_value"] == 80
    assert coolant["physical_value"] == pytest.approx(40.0)
    assert throttle["raw_value"] == 128
    assert throttle["physical_value"] == pytest.approx(50.196078, rel=1e-6)


async def test_the_response_keeps_the_full_provenance_of_the_frame(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, BASIC)
    original = _wire(
        ENGINE_DATA,
        sequence=41,
        channel_id="can3",
        direction="tx",
        hardware_timestamp=None,
        host_timestamp=7.5,
        normalized_timestamp=1.25,
        clock_domain="host.monotonic",
        timestamp_quality="host",
        flags=9,
    )

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json={"project_path": str(root), "frame": original},
        )

    assert response.status_code == 200, response.text
    frame = response.json()["frame"]
    assert frame == {
        "sequence": 41,
        "channel_id": "can3",
        "arbitration_id": 0x123,
        "is_extended": False,
        "is_fd": False,
        "bitrate_switch": False,
        "error_state_indicator": False,
        "dlc": 8,
        "data": ENGINE_DATA,
        "direction": "tx",
        "hardware_timestamp": None,
        "host_timestamp": 7.5,
        "normalized_timestamp": 1.25,
        "clock_domain": "host.monotonic",
        "timestamp_quality": "host",
        "flags": 9,
    }


async def test_a_lowercase_payload_is_answered_with_uppercase_hex(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, ENGINE_DATA.lower()),
        )

    assert response.status_code == 200, response.text
    assert response.json()["frame"]["data"] == ENGINE_DATA


async def test_an_extended_frame_decodes_against_an_extended_definition(
    tmp_path: Path,
) -> None:
    root, asset_ids = _project(tmp_path, EXTENDED)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, TRUCK_DATA, arbitration_id=0x123, is_extended=True),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message_name"] == "TruckStatus"
    assert body["frame"]["is_extended"] is True
    speed, gear = body["signals"]
    assert speed["raw_value"] == 10000
    assert speed["physical_value"] == pytest.approx(39.0625)
    assert speed["unit"] == "km/h"
    assert gear["raw_value"] == 9


async def test_the_same_number_in_two_identifier_spaces_does_not_cross(
    tmp_path: Path,
) -> None:
    """``0x123`` standard and ``0x123`` extended are two different messages."""
    root, asset_ids = _project(tmp_path, BASIC, EXTENDED)
    standard_id, extended_id = asset_ids

    async with _client() as client:
        standard_frame = await client.post(
            f"/dbc/assets/{standard_id}/decode",
            json=_decode_body(root, ENGINE_DATA, arbitration_id=0x123, is_extended=False),
        )
        extended_frame_against_standard = await client.post(
            f"/dbc/assets/{standard_id}/decode",
            json=_decode_body(root, TRUCK_DATA, arbitration_id=0x123, is_extended=True),
        )
        extended_frame = await client.post(
            f"/dbc/assets/{extended_id}/decode",
            json=_decode_body(root, TRUCK_DATA, arbitration_id=0x123, is_extended=True),
        )
        standard_frame_against_extended = await client.post(
            f"/dbc/assets/{extended_id}/decode",
            json=_decode_body(root, ENGINE_DATA, arbitration_id=0x123, is_extended=False),
        )

    assert standard_frame.json()["message_name"] == "EngineData"
    assert extended_frame.json()["message_name"] == "TruckStatus"
    for response in (extended_frame_against_standard, standard_frame_against_extended):
        assert response.status_code == 422
        assert response.json()["code"] == "dbc.message_not_found"


async def test_a_choice_label_is_reported_next_to_the_raw_value(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, CHOICES)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, "02010000", arbitration_id=0x300),
        )

    assert response.status_code == 200, response.text
    door_state, lock_state = response.json()["signals"]
    assert door_state["raw_value"] == 2
    assert door_state["choice_label"] == "Error"
    assert lock_state["raw_value"] == 1
    assert lock_state["choice_label"] == "Locked"


async def test_a_can_fd_frame_decodes_with_a_bucket_payload(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, CAN_FD)
    data = "01020304" + "05060708" + "00" * 56

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, data, is_fd=True, dlc=64, arbitration_id=0x600),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message_name"] == "FdFramePayload"
    assert body["frame"]["is_fd"] is True
    assert body["frame"]["dlc"] == 64
    first, second = body["signals"]
    assert first["raw_value"] == 0x04030201
    assert second["raw_value"] == 0x08070605


async def test_an_fd_payload_longer_than_the_definition_spends_only_the_defined_bytes(
    tmp_path: Path,
) -> None:
    """A CAN FD payload bucket may legitimately exceed the engineering length."""
    root, asset_ids = _project(tmp_path, FD_EXTRA_PAYLOAD)
    data = "07" + "3412" + "FF" * 13

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(
                root, data, is_fd=True, dlc=len(data) // 2, arbitration_id=0x600
            ),
        )

    assert response.status_code == 200, response.text
    counter, value = response.json()["signals"]
    assert counter["raw_value"] == 7
    assert value["raw_value"] == 0x1234


async def test_a_multiplexed_frame_reports_only_the_active_branch(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, MULTIPLEXED)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, "01" + "01020304050607", arbitration_id=0x400),
        )

    assert response.status_code == 200, response.text
    signals = response.json()["signals"]
    assert [signal["name"] for signal in signals] == ["ModeSwitch", "ModeBSignal"]
    assert signals[0]["raw_value"] == 1
    assert signals[1]["raw_value"] == 0x0201
    assert signals[1]["unit"] == "count"


# --- decode failures ---------------------------------------------------------


async def test_a_frame_the_database_does_not_define_is_a_structured_422(
    tmp_path: Path,
) -> None:
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, ENGINE_DATA, arbitration_id=0x7FF),
        )

    assert response.status_code == 422
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == "dbc.message_not_found"
    assert body["source"] == "dbc"
    assert body["recoverable"] is False
    assert body["details"]["arbitration_id"] == 0x7FF


async def test_a_frame_that_disagrees_about_can_fd_is_a_structured_422(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, ENGINE_DATA, is_fd=True, dlc=8),
        )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "dbc.frame_type_mismatch"
    assert body["details"]["message_is_fd"] is False
    assert body["details"]["frame_is_fd"] is True


async def test_a_payload_shorter_than_the_definition_is_a_structured_422(
    tmp_path: Path,
) -> None:
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, "B80B5080"),
        )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "dbc.payload_too_short"
    assert body["details"]["expected_length"] == 8
    assert body["details"]["actual_length"] == 4


async def test_a_float_payload_is_refused_as_unsupported_and_never_decoded(
    tmp_path: Path,
) -> None:
    root, asset_ids = _project(tmp_path, FLOAT_SIGNAL)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, ENGINE_DATA),
        )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "dbc.decode_unsupported"
    assert body["source"] == "dbc"
    assert body["details"]["reason"] == "float_signal_payload"


# --- request validation ------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "overrides"),
    [
        ("B80B508000000000ZZ", {}),
        ("B80B5080000000000", {}),
        ("B80B5080 0000 0000", {}),
        (ENGINE_DATA, {"dlc": 4}),
        (ENGINE_DATA, {"arbitration_id": 0x800}),
        (ENGINE_DATA, {"is_extended": True, "arbitration_id": 0x2000_0000}),
        (ENGINE_DATA, {"dlc": 9}),
        ("00" * 12, {"dlc": 12}),
        (ENGINE_DATA, {"bitrate_switch": True}),
        (ENGINE_DATA, {"direction": "sideways"}),
        (ENGINE_DATA, {"timestamp_quality": "guessed"}),
        (ENGINE_DATA, {"host_timestamp": -1.0}),
        (ENGINE_DATA, {"sequence": [1]}),
        (ENGINE_DATA, {"flags": -1}),
        (ENGINE_DATA, {"channel_id": ""}),
        (ENGINE_DATA, {"clock_domain": ""}),
    ],
    ids=[
        "invalid-hex",
        "odd-hex-length",
        "hex-with-whitespace",
        "data-length-differs-from-dlc",
        "standard-id-out-of-range",
        "extended-id-out-of-range",
        "dlc-not-legal-for-classic",
        "fd-only-dlc-on-a-classic-frame",
        "classic-frame-with-an-fd-flag",
        "unknown-direction",
        "unknown-timestamp-quality",
        "negative-timestamp",
        "sequence-is-a-list",
        "negative-flags",
        "empty-channel",
        "empty-clock-domain",
    ],
)
async def test_a_payload_that_is_not_a_canonical_frame_is_rejected(
    tmp_path: Path, data: str, overrides: dict[str, object]
) -> None:
    """The frame's rules are the domain's; the failure is the shared envelope."""
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, data, **overrides),
        )

    _assert_validation_envelope(response.status_code, response.json())


@pytest.mark.parametrize(
    "literal", ["NaN", "Infinity", "-Infinity"], ids=["nan", "infinity", "negative-infinity"]
)
async def test_a_non_finite_timestamp_is_rejected(tmp_path: Path, literal: str) -> None:
    """A non-finite timestamp is refused rather than admitted.

    ``httpx`` will not *send* a non-finite float and JSON has no literal for one,
    so the document is written out the way any other client could send it. What
    matters is the runtime's answer: a frame whose time cannot be compared with
    anything is not a frame this system accepts.
    """
    root, asset_ids = _project(tmp_path, BASIC)
    frame = _wire(data=ENGINE_DATA)
    frame["normalized_timestamp"] = literal
    document = json.dumps({"project_path": str(root), "frame": frame}).replace(
        f'"{literal}"', literal
    )

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            content=document,
            headers={"content-type": "application/json"},
        )

    _assert_validation_envelope(response.status_code, response.json())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"frame": {}},
        {"project_path": 5, "frame": {}},
        {"project_path": "x"},
        {"frame": "not-an-object"},
    ],
    ids=[
        "empty-body",
        "body-is-an-array",
        "missing-project-path-and-body",
        "project-path-is-an-integer",
        "missing-frame",
        "frame-is-not-an-object",
    ],
)
async def test_a_decode_request_that_is_not_an_object_is_rejected(
    payload: object,
) -> None:
    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{UNKNOWN_ASSET}/decode", json=payload
        )

    _assert_validation_envelope(response.status_code, response.json())


async def test_a_rejected_request_never_reaches_the_project_and_echoes_nothing(
    tmp_path: Path,
) -> None:
    """The control case is what makes this a proof rather than an assertion.

    With a *legal* frame and the same absent project the handler runs and answers
    ``project.not_found``; with an illegal frame the very same target is answered
    with the request-validation envelope instead. So the rejection happened before
    any project was opened — and nothing about the caller's payload came back.
    """
    absent = tmp_path / "absent.canx"

    async with _client() as client:
        rejected = await client.post(
            f"/dbc/assets/{UNKNOWN_ASSET}/decode",
            json={
                "project_path": str(absent),
                "frame": _wire("SECRET-VALUE-0000", dlc=8),
            },
        )
        reached = await client.post(
            f"/dbc/assets/{UNKNOWN_ASSET}/decode",
            json=_decode_body(absent, ENGINE_DATA),
        )

    _assert_validation_envelope(rejected.status_code, rejected.json())
    assert "SECRET" not in str(rejected.json())
    assert "secret" not in str(rejected.json()).lower()

    assert reached.status_code == 400
    assert reached.json()["code"] == "project.not_found"
    assert not absent.exists()


# --- batch decode ------------------------------------------------------------


async def test_a_mixed_batch_answers_200_with_one_outcome_per_frame(tmp_path: Path) -> None:
    """A frame that fails is data; it must not abort the batch around it."""
    root, asset_ids = _project(tmp_path, BASIC)
    frames = [
        _wire(ENGINE_DATA, sequence=1),
        _wire(ENGINE_DATA, sequence=2, arbitration_id=0x7FF),
        _wire(ENGINE_DATA, sequence=3),
        _wire("B80B5080", sequence=4),
    ]

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode-batch",
            json={"project_path": str(root), "stream_id": STREAM_ID, "frames": frames},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "schema_version",
        "stream_id",
        "first_sequence",
        "last_sequence",
        "frame_count",
        "outcomes",
    }
    assert body["schema_version"] == 1
    assert body["stream_id"] == STREAM_ID
    assert body["first_sequence"] == 1
    assert body["last_sequence"] == 4
    assert body["frame_count"] == 4

    outcomes = body["outcomes"]
    assert len(outcomes) == 4
    assert [outcome["frame"]["sequence"] for outcome in outcomes] == [1, 2, 3, 4]
    for outcome in outcomes:
        # Exactly one of the two is present, always.
        assert (outcome["decoded"] is None) != (outcome["failure"] is None)

    assert [outcome["decoded"] is not None for outcome in outcomes] == [
        True,
        False,
        True,
        False,
    ]
    assert [
        None if outcome["failure"] is None else outcome["failure"]["code"]
        for outcome in outcomes
    ] == [None, "dbc.message_not_found", None, "dbc.payload_too_short"]

    decoded_first = outcomes[0]["decoded"]
    assert decoded_first["message_name"] == "EngineData"
    assert decoded_first["signals"][0]["raw_value"] == 3000

    failure = outcomes[1]["failure"]
    assert set(failure) == ENVELOPE_FIELDS
    assert failure["source"] == "dbc"
    assert failure["recoverable"] is False
    assert failure["details"]["arbitration_id"] == 0x7FF


async def test_a_batch_that_decodes_completely_reports_every_frame(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, BASIC)
    frames = [_wire(ENGINE_DATA, sequence=sequence) for sequence in range(10, 20)]

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode-batch",
            json={"project_path": str(root), "stream_id": STREAM_ID, "frames": frames},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["first_sequence"] == 10
    assert body["last_sequence"] == 19
    assert body["frame_count"] == 10
    assert all(outcome["failure"] is None for outcome in body["outcomes"])


async def test_exactly_the_maximum_batch_size_is_accepted(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, BASIC)
    frames = [_wire(ENGINE_DATA, sequence=sequence) for sequence in range(1000)]

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode-batch",
            json={"project_path": str(root), "stream_id": STREAM_ID, "frames": frames},
        )

    assert response.status_code == 200, response.text
    assert response.json()["frame_count"] == 1000


async def test_more_than_the_maximum_batch_size_is_rejected(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, BASIC)
    frames = [_wire(ENGINE_DATA, sequence=sequence) for sequence in range(1001)]

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode-batch",
            json={"project_path": str(root), "stream_id": STREAM_ID, "frames": frames},
        )

    _assert_validation_envelope(response.status_code, response.json())
    assert response.json()["details"]["errors"][0]["location"][-1] == "frames"


async def test_an_empty_batch_is_rejected(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode-batch",
            json={"project_path": str(root), "stream_id": STREAM_ID, "frames": []},
        )

    _assert_validation_envelope(response.status_code, response.json())


async def test_a_batch_whose_sequences_are_not_contiguous_is_rejected(tmp_path: Path) -> None:
    """A ``FrameBatch`` is contiguous by definition, so this is not one."""
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode-batch",
            json={
                "project_path": str(root),
                "stream_id": STREAM_ID,
                "frames": [_wire(ENGINE_DATA, sequence=1), _wire(ENGINE_DATA, sequence=5)],
            },
        )

    _assert_validation_envelope(response.status_code, response.json())


async def test_a_batch_member_that_is_not_a_canonical_frame_is_rejected(tmp_path: Path) -> None:
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode-batch",
            json={
                "project_path": str(root),
                "stream_id": STREAM_ID,
                "frames": [
                    _wire(ENGINE_DATA, sequence=1),
                    _wire(ENGINE_DATA, sequence=2, dlc=4),
                ],
            },
        )

    _assert_validation_envelope(response.status_code, response.json())


async def test_a_batch_against_an_unknown_asset_is_a_404(tmp_path: Path) -> None:
    root, _ = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{UNKNOWN_ASSET}/decode-batch",
            json={
                "project_path": str(root),
                "stream_id": STREAM_ID,
                "frames": [_wire(ENGINE_DATA, sequence=1)],
            },
        )

    assert response.status_code == 404
    assert response.json()["code"] == "dbc.asset_not_found"


# --- wire primitive-type strictness (V0.3-05-FINAL) --------------------------


@pytest.mark.parametrize(
    ("data", "overrides"),
    [
        (ENGINE_DATA, {"sequence": "1"}),
        (ENGINE_DATA, {"sequence": True}),
        (ENGINE_DATA, {"sequence": 1.0}),
        (ENGINE_DATA, {"arbitration_id": "291"}),
        (ENGINE_DATA, {"arbitration_id": False}),
        (ENGINE_DATA, {"dlc": "8"}),
        (ENGINE_DATA, {"flags": "0"}),
        (ENGINE_DATA, {"flags": True}),
        (ENGINE_DATA, {"is_extended": 0}),
        (ENGINE_DATA, {"is_extended": "false"}),
        (ENGINE_DATA, {"is_fd": 1}),
        (ENGINE_DATA, {"is_fd": "false"}),
        (ENGINE_DATA, {"bitrate_switch": "false"}),
        (ENGINE_DATA, {"error_state_indicator": "false"}),
        (ENGINE_DATA, {"channel_id": 123}),
        (ENGINE_DATA, {"clock_domain": 123}),
        (ENGINE_DATA, {"direction": 1}),
        (ENGINE_DATA, {"timestamp_quality": 1}),
        (123, {}),
        (ENGINE_DATA, {"hardware_timestamp": "12.5"}),
        (ENGINE_DATA, {"host_timestamp": "100.25"}),
        (ENGINE_DATA, {"normalized_timestamp": "0.25"}),
    ],
    ids=[
        "sequence-from-string",
        "sequence-from-boolean",
        "sequence-from-float",
        "arbitration-id-from-string",
        "arbitration-id-from-boolean",
        "dlc-from-string",
        "flags-from-string",
        "flags-from-boolean",
        "is-extended-from-integer",
        "is-extended-from-string",
        "is-fd-from-integer",
        "is-fd-from-string",
        "bitrate-switch-from-string",
        "error-state-indicator-from-string",
        "channel-id-from-integer",
        "clock-domain-from-integer",
        "direction-from-integer",
        "timestamp-quality-from-integer",
        "data-from-integer",
        "hardware-timestamp-from-string",
        "host-timestamp-from-string",
        "normalized-timestamp-from-string",
    ],
)
async def test_a_frame_field_sent_as_the_wrong_json_category_is_rejected(
    tmp_path: Path, data: object, overrides: dict[str, object]
) -> None:
    """A JSON payload has primitive categories, and this contract is about them.

    Each case below is one cross-category send. Without wire-level strictness the
    coercion happens inside the model layer, *before* ``wire_to_frame`` runs, so
    the canonical frame would be built from a different payload than the caller
    sent — and a wrong primitive type would be accepted instead of rejected.

    ``error_state_indicator`` is sent as ``"false"`` rather than ``"true"`` on
    purpose: a coerced ``True`` on a classic frame is also semantically illegal, so
    the latter could not tell a type refusal apart from a domain refusal.
    """
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, data, **overrides),
        )

    _assert_validation_envelope(response.status_code, response.json())


@pytest.mark.parametrize(
    ("data", "overrides"),
    [
        (ENGINE_DATA, {}),
        (ENGINE_DATA, {"host_timestamp": 1}),
        (ENGINE_DATA, {"host_timestamp": 1.0}),
        (ENGINE_DATA, {"hardware_timestamp": 1}),
        (ENGINE_DATA, {"normalized_timestamp": 0}),
        (ENGINE_DATA, {"hardware_timestamp": None}),
        (ENGINE_DATA, {"sequence": 0, "flags": 0, "dlc": 8}),
        (ENGINE_DATA.lower(), {}),
        (ENGINE_DATA.upper(), {}),
    ],
    ids=[
        "baseline",
        "timestamp-as-json-integer",
        "timestamp-as-json-float",
        "optional-timestamp-as-json-integer",
        "timestamp-zero-as-json-integer",
        "optional-timestamp-null",
        "integer-fields-as-json-integers",
        "lowercase-hex",
        "uppercase-hex",
    ],
)
async def test_a_legal_json_payload_still_decodes_with_its_values(
    tmp_path: Path, data: str, overrides: dict[str, object]
) -> None:
    """Strictness must not narrow the legal value set.

    A JSON integer where the field is a float, and ``null`` for the optional
    timestamp, are legal input and stay legal. What is refused is a *string* where
    a number belongs — not an integer where a float belongs.
    """
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, data, **overrides),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message_name"] == "EngineData"
    assert body["signals"][0]["raw_value"] == 3000
    assert body["frame"]["data"] == ENGINE_DATA
    if overrides.get("host_timestamp") is not None:
        assert body["frame"]["host_timestamp"] == float(str(overrides["host_timestamp"]))


@pytest.mark.parametrize(
    "override",
    [{"sequence": "2"}, {"is_fd": 0}, {"channel_id": 42}],
    ids=["string-sequence", "integer-boolean", "integer-channel"],
)
async def test_a_batch_member_with_a_wrong_json_category_rejects_the_request(
    tmp_path: Path, override: dict[str, object]
) -> None:
    """A coercible member is a request-shape failure, not a per-frame outcome.

    The whole request is refused, and it is refused *as a request*: a payload that
    does not match the contract is not "a batch with one bad frame", so no
    ``outcomes`` may come back and no asset may be loaded.
    """
    root, asset_ids = _project(tmp_path, BASIC)
    member = _wire(ENGINE_DATA, sequence=2)
    member.update(override)
    frames = [
        _wire(ENGINE_DATA, sequence=1),
        member,
        _wire(ENGINE_DATA, sequence=3),
    ]

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode-batch",
            json={"project_path": str(root), "stream_id": STREAM_ID, "frames": frames},
        )

    body = response.json()
    _assert_validation_envelope(response.status_code, body)
    assert "outcomes" not in body
    assert "frame_count" not in body


async def test_a_coercion_refusal_echoes_no_value_and_no_internals(tmp_path: Path) -> None:
    """A rejected value is a diagnostic location, never an echo of the payload."""
    root, asset_ids = _project(tmp_path, BASIC)

    async with _client() as client:
        response = await client.post(
            f"/dbc/assets/{asset_ids[0]}/decode",
            json=_decode_body(root, ENGINE_DATA, channel_id=99887766),
        )

    body = response.json()
    _assert_validation_envelope(response.status_code, body)
    assert "99887766" not in str(body)


# --- content import: POST /dbc/assets ----------------------------------------
#
# The create half of the collection ``GET /dbc/assets`` lists. The Runtime is
# handed content, never a location: no request field names a file for the Runtime
# to open, and no code path could honour one if it did.

#: The HTTP request guard on one content import. Mirrors the contract, which is a
#: bound on this endpoint rather than a permanent limit of the DBC domain.
MAX_IMPORT_BYTES = 16 * 1024 * 1024

#: A ``°`` in ``"°C"``: not valid UTF-8 on its own, so it proves the declared
#: encoding is what decoded the bytes.
LEGACY_CP1252_DBC = (
    'VERSION "1.0"\n'
    "\n"
    "BU_: N1\n"
    "\n"
    "BO_ 256 Demo: 8 N1\n"
    ' SG_ Temp : 0|8@1+ (1,-40) [-40|215] "\xb0C" N1\n'
)

#: A legal DBC document that is not any fixture: used where the *identity* of the
#: submitted bytes matters more than their content.
OTHER_DBC = (
    'VERSION "1.0"\n'
    "\n"
    "BU_: N1\n"
    "\n"
    "BO_ 512 Other: 8 N1\n"
    ' SG_ Level : 0|8@1+ (1,0) [0|255] "" N1\n'
)


def _empty_project(tmp_path: Path) -> Path:
    """Create a real project and return it closed.

    Every request below has to open and validate the project itself, which is the
    behaviour under test — the import is not handed an already-open handle.
    """
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="DBC import"):
        pass
    return root


def _import_body(
    root: Path, *, content: bytes = b"", source_name: str = "vehicle.dbc", **overrides: object
) -> dict[str, object]:
    """Build one content-import body carrying ``content``."""
    payload: dict[str, object] = {
        "project_path": str(root),
        "source_name": source_name,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }
    payload.update(overrides)
    return payload


def _stored_assets(root: Path) -> list[str]:
    """Names of the files the project owns, whatever the registry says."""
    return sorted(path.name for path in (root / "dbc").iterdir())


def _valid_import_payload() -> dict[str, object]:
    """A shape-valid import body whose project path need not exist.

    Shape validation happens before any project is opened, so these payloads can
    name a path that is not there — which is exactly what makes the control test
    below able to tell "refused as a request" from "refused as a project".
    """
    return {
        "project_path": "C:/projects/vehicle.canx",
        "source_name": "vehicle.dbc",
        "content_base64": base64.b64encode(b'VERSION "1.0"\n').decode("ascii"),
    }


def _without_import_field(field: str) -> dict[str, object]:
    """A shape-valid import body with one declared field removed."""
    return {key: value for key, value in _valid_import_payload().items() if key != field}


def _import_field(field: str, value: object) -> dict[str, object]:
    """A shape-valid import body with one field replaced or added."""
    return {**_valid_import_payload(), field: value}


MALFORMED_IMPORTS: list[tuple[str, dict[str, object]]] = [
    ("missing project_path", _without_import_field("project_path")),
    ("missing source_name", _without_import_field("source_name")),
    ("project_path as integer", _import_field("project_path", 42)),
    ("source_name as integer", _import_field("source_name", 42)),
    ("content_base64 as integer", _import_field("content_base64", 42)),
    ("encoding as integer", _import_field("encoding", 42)),
    ("content_base64 is not base64", _import_field("content_base64", "!!not-base64!!")),
    ("content_base64 is empty", _import_field("content_base64", "")),
    ("unknown extra field", _import_field("unexpected", 1)),
    ("extra source_path field", _import_field("source_path", "C:\\secret\\vehicle.dbc")),
    ("extra content field", _import_field("content", 'VERSION "1.0"\n')),
]


async def test_importing_content_creates_an_asset_the_existing_apis_can_use(
    tmp_path: Path,
) -> None:
    """create → every V0.3-05 read path, against the real project on disk."""
    root = _empty_project(tmp_path)
    raw = BASIC.read_bytes()

    async with _client() as client:
        created = await client.post("/dbc/assets", json=_import_body(root, content=raw))
        assert created.status_code == 201, created.text
        asset = created.json()
        assert set(asset) == {
            "asset_id",
            "source_name",
            "sha256",
            "size_bytes",
            "encoding",
            "imported_at",
        }
        assert asset["source_name"] == "vehicle.dbc"
        assert asset["sha256"] == hashlib.sha256(raw).hexdigest()
        assert asset["size_bytes"] == len(raw)
        assert asset["encoding"] == "utf-8-sig"
        assert datetime.fromisoformat(asset["imported_at"]).tzinfo is not None
        assert "relative_path" not in asset

        asset_id = asset["asset_id"]
        listed = await client.get("/dbc/assets", params={"project_path": str(root)})
        fetched = await client.get(
            f"/dbc/assets/{asset_id}", params={"project_path": str(root)}
        )
        database = await client.get(
            f"/dbc/assets/{asset_id}/database", params={"project_path": str(root)}
        )
        decoded = await client.post(
            f"/dbc/assets/{asset_id}/decode", json=_decode_body(root, ENGINE_DATA)
        )

    assert [item["asset_id"] for item in listed.json()["assets"]] == [asset_id]
    assert fetched.json() == asset
    assert [message["name"] for message in database.json()["messages"]] == ["EngineData"]
    assert decoded.status_code == 200, decoded.text
    body = decoded.json()
    assert body["message_name"] == "EngineData"
    assert body["signals"][0]["name"] == "EngineSpeed"
    assert body["signals"][0]["raw_value"] == 3000
    assert body["signals"][0]["physical_value"] == 750.0
    assert body["signals"][1]["raw_value"] == 80
    assert body["signals"][1]["physical_value"] == 40.0

    # The project-owned copy is the submitted payload, byte for byte.
    assert _stored_assets(root) == [f"{asset_id}.dbc"]
    assert (root / "dbc" / f"{asset_id}.dbc").read_bytes() == raw


async def test_the_import_records_the_bytes_it_was_given_and_not_a_re_render(
    tmp_path: Path,
) -> None:
    """A byte-order mark survives: nothing between the request and the file rewrites."""
    root = _empty_project(tmp_path)
    raw = b"\xef\xbb\xbf" + BASIC.read_bytes()

    async with _client() as client:
        created = await client.post("/dbc/assets", json=_import_body(root, content=raw))

    assert created.status_code == 201, created.text
    asset = created.json()
    stored = (root / "dbc" / f"{asset['asset_id']}.dbc").read_bytes()
    assert stored == raw
    assert asset["sha256"] == hashlib.sha256(raw).hexdigest()
    assert asset["size_bytes"] == len(raw)


async def test_importing_legacy_bytes_with_a_declared_encoding_succeeds(
    tmp_path: Path,
) -> None:
    root = _empty_project(tmp_path)
    raw = LEGACY_CP1252_DBC.encode("cp1252")

    async with _client() as client:
        created = await client.post(
            "/dbc/assets", json=_import_body(root, content=raw, encoding="cp1252")
        )

    assert created.status_code == 201, created.text
    asset = created.json()
    assert asset["encoding"] == "cp1252"
    assert (root / "dbc" / f"{asset['asset_id']}.dbc").read_bytes() == raw


async def test_importing_the_same_content_twice_creates_two_assets(tmp_path: Path) -> None:
    """No content de-duplication: an import is an explicit asset creation."""
    root = _empty_project(tmp_path)
    raw = BASIC.read_bytes()

    async with _client() as client:
        first = await client.post("/dbc/assets", json=_import_body(root, content=raw))
        second = await client.post("/dbc/assets", json=_import_body(root, content=raw))

    assert first.status_code == second.status_code == 201
    assert first.json()["asset_id"] != second.json()["asset_id"]
    assert first.json()["sha256"] == second.json()["sha256"]
    assert len(_stored_assets(root)) == 2


async def test_an_import_of_unparseable_content_is_a_typed_422(tmp_path: Path) -> None:
    """A malformed document is the *database's* failure, not the request's."""
    root = _empty_project(tmp_path)

    async with _client() as client:
        response = await client.post(
            "/dbc/assets", json=_import_body(root, content=MALFORMED.read_bytes())
        )

    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == "dbc.parse_failed"
    assert body["source"] == "dbc"
    assert body["recoverable"] is False
    assert _stored_assets(root) == []


async def test_an_import_of_legacy_bytes_without_their_encoding_is_a_typed_422(
    tmp_path: Path,
) -> None:
    root = _empty_project(tmp_path)

    async with _client() as client:
        response = await client.post(
            "/dbc/assets", json=_import_body(root, content=LEGACY_CP1252_DBC.encode("cp1252"))
        )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "dbc.decode_failed"
    assert response.json()["source"] == "dbc"
    assert _stored_assets(root) == []


async def test_an_import_with_an_unknown_encoding_is_a_typed_422(tmp_path: Path) -> None:
    root = _empty_project(tmp_path)

    async with _client() as client:
        response = await client.post(
            "/dbc/assets",
            json=_import_body(root, content=BASIC.read_bytes(), encoding="not-a-real-codec"),
        )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "dbc.decode_failed"
    assert _stored_assets(root) == []


async def test_an_import_into_an_invalid_project_is_a_400(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-project"
    plain.mkdir()

    async with _client() as client:
        response = await client.post(
            "/dbc/assets", json=_import_body(plain, content=BASIC.read_bytes())
        )

    assert response.status_code == 400
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["source"] == "project"
    assert body["code"].startswith("project.")
    assert sorted(path.name for path in plain.iterdir()) == []


@pytest.mark.parametrize(
    "source_name",
    [
        "",
        "   ",
        ".",
        "..",
        "../vehicle.dbc",
        "..\\vehicle.dbc",
        "folder/vehicle.dbc",
        "folder\\vehicle.dbc",
        "/tmp/vehicle.dbc",
        "\\tmp\\vehicle.dbc",
        "C:\\temp\\vehicle.dbc",
        "C:vehicle.dbc",
        "vehicle.txt",
        "vehicle",
        " vehicle.dbc",
        "vehicle.dbc ",
    ],
)
async def test_an_import_refuses_a_source_name_that_is_not_a_plain_dbc_name(
    tmp_path: Path, source_name: str
) -> None:
    """A provenance label, not a path — refused by the domain, reported as a 422."""
    root = _empty_project(tmp_path)

    async with _client() as client:
        response = await client.post(
            "/dbc/assets",
            json=_import_body(root, content=BASIC.read_bytes(), source_name=source_name),
        )

    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == "dbc.unsupported_format"
    assert body["source"] == "dbc"
    assert body["recoverable"] is False
    assert _stored_assets(root) == []


@pytest.mark.parametrize(
    ("label", "payload"), MALFORMED_IMPORTS, ids=[label for label, _ in MALFORMED_IMPORTS]
)
async def test_a_malformed_import_request_is_refused_by_the_shared_envelope(
    label: str, payload: dict[str, object]
) -> None:
    """Every shape failure is the shared envelope, and never the framework's own."""
    async with _client() as client:
        response = await client.post("/dbc/assets", json=payload)

    _assert_validation_envelope(response.status_code, response.json())


async def test_an_oversized_import_request_is_refused(tmp_path: Path) -> None:
    """The request guard: decoded content above the bound never reaches the parser."""
    root = _empty_project(tmp_path)
    oversized = base64.b64encode(b"V" * (MAX_IMPORT_BYTES + 1)).decode("ascii")

    async with _client() as client:
        response = await client.post(
            "/dbc/assets",
            json={
                "project_path": str(root),
                "source_name": "vehicle.dbc",
                "content_base64": oversized,
            },
        )

    _assert_validation_envelope(response.status_code, response.json())
    assert _stored_assets(root) == []


async def test_a_rejected_import_never_reaches_the_project(tmp_path: Path) -> None:
    """The control group that proves *where* a refusal happened.

    The same absent project is addressed twice with differently-broken payloads. A
    shape failure is answered as a request failure (422, ``source = api``) and the
    domain is never entered; a shape-valid payload reaches the handler and is
    answered by the project domain (400, ``source = project``). Without the second
    half, the first could not tell "refused as a request" from "refused because the
    project is missing".
    """
    absent = tmp_path / "absent.canx"

    async with _client() as client:
        shape_failure = await client.post(
            "/dbc/assets",
            json={
                **_valid_import_payload(),
                "project_path": str(absent),
                "source_path": "C:\\secret\\vehicle.dbc",
            },
        )
        reached_the_handler = await client.post(
            "/dbc/assets", json=_import_body(absent, content=BASIC.read_bytes())
        )

    assert shape_failure.status_code == 422
    assert shape_failure.json()["code"] == REQUEST_VALIDATION_CODE
    assert shape_failure.json()["source"] == "api"

    assert reached_the_handler.status_code == 400
    assert reached_the_handler.json()["code"] == "project.not_found"
    assert reached_the_handler.json()["source"] == "project"
    assert not absent.exists()


async def test_the_import_content_comes_only_from_the_request_bytes(tmp_path: Path) -> None:
    """A decoy file on this machine must never become the content of an import.

    A perfectly importable ``secret.dbc`` sits on disk. A caller names that same
    file but submits different, unparseable bytes: the import must fail *on the
    submitted bytes*. The mirror case is the one that would catch a silent read —
    the same name, real bytes submitted, and a project-owned copy that is provably
    not the decoy.
    """
    decoy_directory = tmp_path / "elsewhere"
    decoy_directory.mkdir()
    decoy = decoy_directory / "secret.dbc"
    decoy.write_bytes(BASIC.read_bytes())
    root = _empty_project(tmp_path)

    async with _client() as client:
        mismatched = await client.post(
            "/dbc/assets",
            json=_import_body(root, content=b"not a dbc document\n", source_name="secret.dbc"),
        )
        assert mismatched.status_code == 422, mismatched.text
        assert mismatched.json()["code"] == "dbc.parse_failed"
        # The submitted bytes decided the outcome: the decoy could not have been
        # consulted, or this import would have succeeded.
        assert _stored_assets(root) == []

        genuine = await client.post(
            "/dbc/assets",
            json=_import_body(root, content=OTHER_DBC.encode("utf-8"), source_name="secret.dbc"),
        )

    assert genuine.status_code == 201, genuine.text
    stored = (root / "dbc" / f"{genuine.json()['asset_id']}.dbc").read_bytes()
    assert stored == OTHER_DBC.encode("utf-8")
    assert stored != decoy.read_bytes()
    assert genuine.json()["sha256"] == hashlib.sha256(OTHER_DBC.encode("utf-8")).hexdigest()
    assert decoy.read_bytes() == BASIC.read_bytes()
    assert _stored_assets(root) == [f"{genuine.json()['asset_id']}.dbc"]


async def test_a_refused_import_echoes_no_content_and_no_internals(tmp_path: Path) -> None:
    """A rejection carries a diagnostic location, never a rendering of the payload."""
    root = _empty_project(tmp_path)
    secret_path = "C:\\customer\\secret-project\\vehicle.dbc"

    async with _client() as client:
        malformed_request = await client.post(
            "/dbc/assets",
            json={
                **_import_body(root, content=b"TOP-SECRET-DBC-CONTENT"),
                "content_base64": "!!!not-base64!!!",
                "source_path": secret_path,
            },
        )
        unparseable_content = await client.post(
            "/dbc/assets", json=_import_body(root, content=MALFORMED.read_bytes())
        )

    for response in (malformed_request, unparseable_content):
        assert response.status_code == 422, response.text
        rendered = str(response.json()).lower()
        for marker in LEAK_MARKERS:
            assert marker not in rendered, marker

    request_body = str(malformed_request.json()).lower()
    assert "not-base64" not in request_body
    assert "secret-project" not in request_body
    assert secret_path.lower() not in request_body
    assert "top-secret" not in request_body
    # The parser's own message embeds the offending source line; the imported one
    # reports a position instead, so no fragment of the document travels back.
    assert "brokenframe" not in str(unparseable_content.json()).lower()
