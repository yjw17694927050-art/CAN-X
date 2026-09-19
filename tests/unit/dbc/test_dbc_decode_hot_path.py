"""A decode request must not repeat the work only the asset's content decides.

One decode request today opens the project, looks the asset up in the registry,
reads the ``.dbc`` bytes, verifies size and SHA-256, decodes text, parses with
``cantools``, converts to the canonical model and compiles a
:class:`~canx.dbc.decode.DbcDecoder`. Every one of those steps is a function of the
asset's immutable content — only the final per-frame decode varies with the
request — yet the live viewport sends one small request at a time, so the whole
static sequence recurs on the realtime path.

These tests pin the contract that the repetition is gone for an *unchanged* asset,
and that removing it did not buy throughput by weakening the integrity rule.

The counts are taken by wrapping the two functions that do the expensive static
work — ``cantools.database.load_string`` and ``DbcDecoder.__init__`` — with a
counter that calls straight through to the real implementation. Nothing about the
DBC stack is mocked: the project domain, the registry, the filesystem and the
parser all run for real.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cantools
import pytest
from canx.api.dbc import DbcFramePayload, _decode_batch
from canx.api.frame import frame_to_wire
from canx.dbc.decode import DbcDecoder
from canx.dbc.errors import DbcAssetIntegrityError
from canx.dbc.project_service import ProjectDbcService
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectService

BASIC_DBC = (
    'VERSION "1.0"\n'
    "\n"
    "BU_: N1 N2\n"
    "\n"
    "BO_ 256 Demo: 8 N1\n"
    ' SG_ Speed : 0|16@1+ (0.5,-10) [-10|1000] "km/h" N2\n'
)
OTHER_DBC = (
    'VERSION "1.0"\n'
    "\n"
    "BU_: N1\n"
    "\n"
    "BO_ 512 Other: 8 N1\n"
    ' SG_ Level : 0|8@1+ (1,0) [0|255] "" N1\n'
)
STREAM_ID = "hot-path-stream"


class StaticWork:
    """How many times the expensive static steps ran while a test was running."""

    def __init__(self) -> None:
        self.parses = 0
        self.compiles = 0

    def reset(self) -> None:
        """Forget the parses an import performs, so only the decode phase is measured."""
        self.parses = 0
        self.compiles = 0


@pytest.fixture
def static_work(monkeypatch: pytest.MonkeyPatch) -> StaticWork:
    """Count parses and compiles without changing what either does."""
    counter = StaticWork()
    real_load_string: Any = cantools.database.load_string
    real_init: Any = DbcDecoder.__init__

    def load_string(*args: Any, **kwargs: Any) -> Any:
        counter.parses += 1
        return real_load_string(*args, **kwargs)

    def decoder_init(decoder: Any, *args: Any, **kwargs: Any) -> None:
        counter.compiles += 1
        real_init(decoder, *args, **kwargs)

    monkeypatch.setattr(cantools.database, "load_string", load_string)
    monkeypatch.setattr(DbcDecoder, "__init__", decoder_init)
    return counter


def build_project(
    tmp_path: Path, *contents: str, name: str = "vehicle"
) -> tuple[Path, list[str]]:
    """Create a real project, import every DBC text as an asset, then close it."""
    root = tmp_path / f"{name}.canx"
    asset_ids: list[str] = []
    with ProjectService().create(root, display_name=name) as handle:
        service = ProjectDbcService(handle.root)
        for index, text in enumerate(contents):
            inbox = tmp_path / f"inbox-{name}-{index}"
            inbox.mkdir(parents=True, exist_ok=True)
            source = inbox / f"asset_{index}.dbc"
            source.write_text(text, encoding="utf-8")
            asset_ids.append(service.import_asset(source).asset_id)
    return root, asset_ids


def payload(sequence: int, arbitration_id: int, data: bytes) -> DbcFramePayload:
    """Build the wire payload a decode request carries."""
    frame = Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=arbitration_id,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=len(data),
        data=data,
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=1.0,
        normalized_timestamp=0.0,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )
    return DbcFramePayload(**frame_to_wire(frame).model_dump())


def decode_once(root: Path, asset_id: str, sequence: int = 1) -> int:
    """Decode one frame through the real handler body and return the frame count."""
    batch = _decode_batch(
        str(root), asset_id, STREAM_ID, [payload(sequence, 256, bytes(8))]
    )
    return batch.frame_count


def test_repeated_decode_requests_parse_and_compile_the_asset_once(
    tmp_path: Path, static_work: StaticWork
) -> None:
    """The static work is a function of the content, so it must run once, not per request."""
    root, (asset_id,) = build_project(tmp_path, BASIC_DBC)
    static_work.reset()

    for _ in range(5):
        assert decode_once(root, asset_id) == 1

    assert static_work.parses == 1
    assert static_work.compiles == 1


def test_two_assets_with_different_content_parse_separately(
    tmp_path: Path, static_work: StaticWork
) -> None:
    """A different asset is different content, so it gets its own parse."""
    root, (first, second) = build_project(tmp_path, BASIC_DBC, OTHER_DBC)
    static_work.reset()

    assert decode_once(root, first) == 1
    assert decode_once(root, second, sequence=2) == 1
    assert decode_once(root, first, sequence=3) == 1

    assert static_work.parses == 2
    assert static_work.compiles == 2


def test_two_projects_with_identical_content_do_not_share_a_parse(
    tmp_path: Path, static_work: StaticWork
) -> None:
    """Project identity is part of the key, so two projects never share an entry."""
    root_a, (asset_a,) = build_project(tmp_path, BASIC_DBC, name="project_a")
    root_b, (asset_b,) = build_project(tmp_path, BASIC_DBC, name="project_b")
    static_work.reset()

    assert decode_once(root_a, asset_a) == 1
    assert decode_once(root_b, asset_b) == 1

    assert static_work.parses == 2
    assert static_work.compiles == 2


def test_a_tampered_asset_is_refused_even_with_a_warm_decode(
    tmp_path: Path, static_work: StaticWork
) -> None:
    """The integrity check still runs every request; a warm entry must not mask tamper."""
    root, (asset_id,) = build_project(tmp_path, BASIC_DBC)
    static_work.reset()
    assert decode_once(root, asset_id) == 1

    target = root / "dbc" / f"{asset_id}.dbc"
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(DbcAssetIntegrityError) as caught:
        decode_once(root, asset_id, sequence=2)

    assert caught.value.code == "dbc.asset_integrity_failed"
    assert caught.value.recoverable is False
    # The tampered bytes never reached the parser, so no second parse happened.
    assert static_work.parses == 1


def test_a_replacement_asset_is_parsed_for_its_own_content(
    tmp_path: Path, static_work: StaticWork
) -> None:
    """New content is a new entry: the replacement's messages decode, the old one's do not."""
    root, (asset_id,) = build_project(tmp_path, BASIC_DBC)
    static_work.reset()
    assert decode_once(root, asset_id) == 1

    with ProjectService().open(root) as handle:
        replacement = tmp_path / "replacement.dbc"
        replacement.write_text(OTHER_DBC, encoding="utf-8")
        replacement_id = ProjectDbcService(handle.root).import_asset(replacement).asset_id
    static_work.reset()

    # ``OTHER_DBC`` defines 512 and not 256, so the replacement's own content is what is used.
    replacement_batch = _decode_batch(
        str(root), replacement_id, STREAM_ID, [payload(1, 512, bytes(8))]
    )
    assert replacement_batch.outcomes[0].decoded is not None
    assert replacement_batch.outcomes[0].decoded.message_name == "Other"

    stale_batch = _decode_batch(
        str(root), replacement_id, STREAM_ID, [payload(2, 256, bytes(8))]
    )
    assert stale_batch.outcomes[0].failure is not None
    assert stale_batch.outcomes[0].failure.code == "dbc.message_not_found"

    assert static_work.parses == 1
    assert static_work.compiles == 1
