"""A measured BEFORE/AFTER of the live DBC decode hot path, not a throughput target.

A decode request repeats work that is a function of an *immutable project-owned
asset*: opening and validating the project, looking the asset up in the registry,
reading the ``.dbc`` bytes, hashing them, decoding text, parsing with ``cantools``,
converting to the canonical model and compiling a :class:`~canx.dbc.decode.DbcDecoder`.
Only the final per-frame decode varies with the request. This module measures that
split on the real code path — a real project, a real imported asset, the real
``_decode_batch`` body the HTTP handler runs — so a change can be justified by
numbers rather than by reading.

Two asset families are measured, because the checked-in fixtures are 134-405 bytes
and cannot show what a production DBC costs:

* the tiny fixtures (``basic_standard.dbc``, ``extended.dbc``);
* a synthetic document generated into a temp directory at benchmark time, sized
  like a real one (a few hundred messages, a few thousand signals).

Nothing is mocked. ``cantools``, the project domain, SQLite and the filesystem all
run for real. The only instrumentation is a counter wrapped around the functions
that do the static work, and each counter calls straight through to the real
implementation.

Run it from the worktree root:

    PYTHONPATH=<worktree>/runtime python tests/unit/dbc/bench_dbc_hot_path.py
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from canx.api.app import create_app
from canx.api.dbc import MAX_BATCH_FRAMES, DbcFramePayload, _decode_batch, _decode_frames
from canx.api.frame import frame_to_wire
from canx.dbc.decoder_cache import DECODER_CACHE
from canx.dbc.project_service import ProjectDbcService
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectService
from fastapi.testclient import TestClient

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "dbc"

#: Messages in the synthetic document. A mid-size real vehicle DBC, not a fixture.
SYNTHETIC_MESSAGES = 300
#: Signals per synthetic message (8 eight-bit signals fill one 8-byte payload).
SYNTHETIC_SIGNALS = 8

FIXTURE_ENGINE_DATA = bytes([0xB8, 0x0B, 0x50, 0x80]) + bytes(4)
SYNTHETIC_DATA = bytes(8)


def synthetic_dbc(
    messages: int,
    signals: int,
    *,
    base_frame_id: int = 0x200,
    prefix: str = "",
) -> str:
    """Build a well-formed DBC text with ``messages`` messages of ``signals`` signals.

    ``base_frame_id`` and ``prefix`` exist so two *genuinely different* documents can be
    produced by one generator. That matters: a benchmark that called two assets "two
    DBCs" while they held the same bytes would measure one document read twice, and the
    decoder cache keys on verified content identity — so it would answer from a single
    entry and report a saving it had not made.
    """
    lines = ['VERSION ""', "", "NS_ :", "", "BS_:", "", "BU_: ECU1 ECU2", ""]
    for index in range(messages):
        lines.append(f"BO_ {base_frame_id + index} {prefix}Msg_{index}: 8 ECU1")
        for signal in range(signals):
            lines.append(
                f' SG_ Sig_{index}_{signal} : {signal * 8}|8@1+ (1,0) [0|255] "" ECU2'
            )
        lines.append("")
    return "\n".join(lines)


@dataclass(frozen=True)
class AssetSpec:
    """One project-owned asset, plus the frame that decodes against it."""

    key: str
    asset_id: str
    arbitration_id: int
    is_extended: bool
    data: bytes


@dataclass(frozen=True)
class Scenario:
    """One request pattern a live viewport can produce."""

    key: str
    description: str
    requests: int
    frames_per_request: int


@dataclass(frozen=True)
class Row:
    """One measured cell of the table."""

    project: str
    mode: str
    scenario: str
    requests: int
    frames: int
    elapsed_s: float
    parses: int
    compiles: int
    project_opens: int
    asset_reads: int

    @property
    def frames_per_second(self) -> float:
        return self.frames / self.elapsed_s if self.elapsed_s > 0 else float("inf")

    @property
    def ms_per_request(self) -> float:
        return 1000.0 * self.elapsed_s / self.requests if self.requests else 0.0


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("1-one-channel", "one channel, one DBC, 20-frame requests", 200, 20),
    Scenario("2-shared-dbc", "two channels bound to the same DBC, 20-frame requests", 200, 20),
    Scenario("3-two-dbc", "two channels, two DBCs, interleaved, 10-frame requests", 200, 10),
    Scenario("4-worst-case", "two channels alternating frame by frame (1 frame/request)", 400, 1),
    Scenario("5-large-batch", "one channel, 1000-frame requests", 50, 1000),
    Scenario("6-repeat-small", "repeated one-frame requests against an unchanged asset", 200, 1),
)

#: Request-count divisor for the large project. One request against the synthetic
#: document is ~40x one against a fixture, so the same request counts would turn a
#: measurement into a coffee break. Coverage is unchanged; only the repetition thins.
LARGE_REQUEST_DIVISOR = 10
#: Never thin a scenario below this many requests, or its timing stops being stable.
LARGE_REQUEST_FLOOR = 10


def scaled_for_large(scenario: Scenario) -> Scenario:
    """Return the same scenario with a request count the large project can afford."""
    return Scenario(
        scenario.key,
        scenario.description,
        max(LARGE_REQUEST_FLOOR, scenario.requests // LARGE_REQUEST_DIVISOR),
        scenario.frames_per_request,
    )


class NullDecoderCache:
    """The decoder cache with nothing to remember: every lookup misses.

    Substituted for :data:`~canx.dbc.decoder_cache.DECODER_CACHE` to measure the
    pre-cache behaviour on this tree, so the BEFORE and AFTER columns come from one
    script, one machine and one run. It is the same hot path — the integrity read
    and hash still run every request — with the one difference that each request
    re-parses and re-compiles, exactly as the code without a cache did.
    """

    def get(self, key: object) -> None:
        """Always miss."""
        return None

    def put(self, key: object, decoder: object) -> None:
        """Drop what would have been stored."""
        return None

    def __len__(self) -> int:
        return 0

    def clear(self) -> None:
        """Nothing to forget."""
        return None


#: The BEFORE column's cache: a stand-in that never remembers anything.
NULL_CACHE = NullDecoderCache()


class Probe:
    """Count the static steps without changing what they do."""

    def __init__(self) -> None:
        self._originals: tuple[object, ...] | None = None
        self.reset()

    def reset(self) -> None:
        self.parses = 0
        self.compiles = 0
        self.project_opens = 0
        self.asset_reads = 0

    def install(self) -> None:
        """Wrap the functions that do the per-request static work."""
        import cantools
        import canx.dbc.decode as decode_module
        import canx.dbc.parser as parser_module
        import canx.dbc.project_service as project_service_module

        real_load_string = cantools.database.load_string
        real_init = decode_module.DbcDecoder.__init__
        real_open = ProjectService.open
        real_read = project_service_module._read_asset_bytes
        self._originals = (real_load_string, real_init, real_open, real_read)
        probe = self

        def load_string(*args: object, **kwargs: object) -> object:
            probe.parses += 1
            return real_load_string(*args, **kwargs)

        def decoder_init(decoder: object, *args: object, **kwargs: object) -> None:
            probe.compiles += 1
            real_init(decoder, *args, **kwargs)  # type: ignore[arg-type]

        def project_open(service: object, *args: object, **kwargs: object) -> object:
            probe.project_opens += 1
            return real_open(service, *args, **kwargs)  # type: ignore[arg-type]

        def read_asset_bytes(path: Path, asset: object) -> bytes:
            probe.asset_reads += 1
            return real_read(path, asset)  # type: ignore[arg-type]

        cantools.database.load_string = load_string  # type: ignore[assignment]
        decode_module.DbcDecoder.__init__ = decoder_init  # type: ignore[method-assign]
        parser_module.cantools.database.load_string = load_string  # type: ignore[assignment]
        ProjectService.open = project_open  # type: ignore[method-assign]
        project_service_module._read_asset_bytes = read_asset_bytes  # type: ignore[assignment]

    def uninstall(self) -> None:
        """Restore every wrapped function."""
        if self._originals is None:
            return
        import cantools
        import canx.dbc.decode as decode_module
        import canx.dbc.parser as parser_module
        import canx.dbc.project_service as project_service_module

        load_string, init, open_, read = self._originals
        cantools.database.load_string = load_string  # type: ignore[assignment]
        parser_module.cantools.database.load_string = load_string  # type: ignore[assignment]
        decode_module.DbcDecoder.__init__ = init  # type: ignore[method-assign]
        ProjectService.open = open_  # type: ignore[method-assign]
        project_service_module._read_asset_bytes = read  # type: ignore[assignment]
        self._originals = None


def payload_for(spec: AssetSpec, sequence: int, channel_id: str = "can0") -> DbcFramePayload:
    """Build the wire payload that decodes against ``spec`` at ``sequence``."""
    frame = Frame(
        sequence=sequence,
        channel_id=channel_id,
        arbitration_id=spec.arbitration_id,
        is_extended=spec.is_extended,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=len(spec.data),
        data=spec.data,
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=1.0,
        normalized_timestamp=0.0,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )
    return DbcFramePayload(**frame_to_wire(frame).model_dump())


def run_scenario(
    project: str,
    scenario: Scenario,
    project_path: Path,
    assets: tuple[AssetSpec, ...],
    probe: Probe,
    *,
    use_cache: bool,
) -> Row:
    """Run one scenario on the real ``_decode_batch`` body and time it.

    ``use_cache`` chooses which cache the hot path consults: the real
    :data:`~canx.dbc.decoder_cache.DECODER_CACHE` for the AFTER column, or
    :data:`NULL_CACHE` for the BEFORE column. The integrity read and hash are
    unaffected either way.
    """
    import canx.dbc.project_service as project_service_module

    decoder_cache = DECODER_CACHE if use_cache else NULL_CACHE
    project_service_module.DECODER_CACHE = decoder_cache
    decoder_cache.clear()
    sequence = 0
    decoded = 0
    probe.reset()
    started = time.perf_counter()
    for request_index in range(scenario.requests):
        spec = assets[request_index % len(assets)]
        payloads = []
        for _ in range(scenario.frames_per_request):
            sequence += 1
            payloads.append(payload_for(spec, sequence))
        batch = _decode_batch(str(project_path), spec.asset_id, "benchmark", payloads)
        decoded += batch.frame_count
    elapsed = time.perf_counter() - started
    return Row(
        project=project,
        mode="cache" if use_cache else "no-cache",
        scenario=scenario.key,
        requests=scenario.requests,
        frames=decoded,
        elapsed_s=elapsed,
        parses=probe.parses,
        compiles=probe.compiles,
        project_opens=probe.project_opens,
        asset_reads=probe.asset_reads,
    )


def import_asset(root: Path, name: str, content: bytes) -> str:
    """Import ``content`` as a project-owned asset and return its id."""
    staging = root.parent / f"inbox-{name}"
    staging.mkdir(parents=True, exist_ok=True)
    source = staging / f"{name}.dbc"
    source.write_bytes(content)
    return ProjectDbcService(root).import_asset(source).asset_id


def build_project(
    tmp_path: Path, name: str, contents: tuple[tuple[str, bytes], ...]
) -> tuple[Path, tuple[str, ...]]:
    """Create a real project, import every asset, and close it again."""
    root = tmp_path / f"{name}.canx"
    asset_ids: list[str] = []
    with ProjectService().create(root, display_name=name) as handle:
        for asset_name, content in contents:
            staging = tmp_path / f"inbox-{name}-{asset_name}"
            staging.mkdir(parents=True, exist_ok=True)
            source = staging / f"{asset_name}.dbc"
            source.write_bytes(content)
            asset_ids.append(ProjectDbcService(handle.root).import_asset(source).asset_id)
    return root, tuple(asset_ids)


def environment() -> str:
    """One line naming the machine the numbers were taken on."""
    return (
        f"{platform.system()} {platform.release()} ({platform.machine()}), "
        f"Python {platform.python_version()}, "
        f"{os.cpu_count()} CPUs"
    )


def print_table(rows: list[Row]) -> None:
    """Print the measured table, one block per project."""
    header = (
        f"{'scenario':<16} {'mode':<9} {'req':>5} {'frames':>8} {'elapsed_s':>9} "
        f"{'ms/req':>8} {'frames/s':>12} {'parse':>6} {'compile':>7} "
        f"{'proj-open':>9} {'read':>5}"
    )
    for project in dict.fromkeys(row.project for row in rows):
        print(f"\n== project: {project} ==")
        print(header)
        for row in rows:
            if row.project != project:
                continue
            print(
                f"{row.scenario:<16} {row.mode:<9} {row.requests:>5} {row.frames:>8} "
                f"{row.elapsed_s:>9.3f} {row.ms_per_request:>8.2f} "
                f"{row.frames_per_second:>12,.0f} {row.parses:>6} {row.compiles:>7} "
                f"{row.project_opens:>9} {row.asset_reads:>5}"
            )


# --- request fragmentation: how many requests one viewport costs ------------
#
# The defect this section measures is not inside any one decode. It is in *how many
# requests* a viewport is cut into. A DBC decode is frame-local — the decoder answers
# "what does this frame mean" and never looks at a neighbouring sequence — but the
# request used to be shaped like a capture batch: one asset, consecutive sequences. A
# live viewport that alternates two channels therefore had to be submitted one frame at
# a time, and with one HTTP request in flight that caps throughput at whatever one
# single-frame request costs.
#
# BEFORE: the plan the Desktop used to submit (`partitionDecodeRuns`, contiguous runs).
# AFTER:  the plan the Desktop submits now (`partitionDecodeWorkSets`, one per asset).
#
# Both plans are pure functions over `(channel, sequence)`, re-stated here in Python
# because the Desktop's implementation is TypeScript. The shapes they produce are the
# shapes `apps/desktop/src/workspace/decode-partition.test.ts` pins — including the
# headline case below, an alternating viewport that must cost two requests and not one
# per frame.
#
# Nothing below is pre-filled: every number in the table is measured on this machine in
# this run, and BEFORE and AFTER run against the *real* handlers (`_decode_batch` and
# `_decode_frames`) over the same project, the same frames and the same decoder cache.

#: The label for the plan the Desktop used to submit.
BEFORE_PLAN = "BEFORE 1/run"
#: The label for the plan the Desktop submits now.
AFTER_PLAN = "AFTER 1/asset"

#: One viewport, and how it is bound.
@dataclass(frozen=True)
class ViewportScene:
    """One live viewport: how many frames, on which channels, and for how many rounds."""

    key: str
    description: str
    project: str
    frames: int
    channels: tuple[str, ...]
    bindings: tuple[tuple[str, str], ...]
    repeats: int = 1


@dataclass(frozen=True)
class FragmentationRow:
    """One measured cell of the fragmentation table."""

    scene: str
    plan: str
    assets: int
    frames: int
    requests: int
    elapsed_s: float
    parses: int
    compiles: int
    reads: int

    @property
    def frames_per_request(self) -> float:
        return self.frames / self.requests if self.requests else 0.0

    @property
    def frames_per_second(self) -> float:
        return self.frames / self.elapsed_s if self.elapsed_s > 0 else float("inf")


@dataclass(frozen=True)
class HttpRow:
    """One measured cell of the same plan driven over real HTTP."""

    scene: str
    plan: str
    frames: int
    requests: int
    elapsed_s: float

    @property
    def frames_per_second(self) -> float:
        return self.frames / self.elapsed_s if self.elapsed_s > 0 else float("inf")

    @property
    def ms_per_request(self) -> float:
        return 1000.0 * self.elapsed_s / self.requests if self.requests else 0.0


@dataclass(frozen=True)
class ProjectFixture:
    """One prepared project, with the assets a scene can bind a channel to."""

    label: str
    root: Path
    specs: Mapping[str, AssetSpec]


FRAGMENTATION_SCENES: tuple[ViewportScene, ...] = (
    ViewportScene(
        "1-one-channel",
        "400 frames, one channel, one DBC",
        "small",
        400,
        ("can0",),
        (("can0", "basic"),),
    ),
    ViewportScene(
        "2-two-ch-shared",
        "400 frames alternating, both channels on the SAME DBC",
        "small",
        400,
        ("can0", "can1"),
        (("can0", "basic"), ("can1", "basic")),
    ),
    ViewportScene(
        "3-two-ch-two-dbc",
        "400 frames alternating, two genuinely DIFFERENT DBCs",
        "small",
        400,
        ("can0", "can1"),
        (("can0", "basic"), ("can1", "extended")),
    ),
    ViewportScene(
        "4-large-viewport",
        "4000 frames alternating, two DIFFERENT large DBCs",
        "large",
        4000,
        ("can0", "can1"),
        (("can0", "synthetic_a"), ("can1", "synthetic_b")),
    ),
    ViewportScene(
        "5-repeated-viewport",
        "the 400-frame alternation, one viewport decoded 10 times",
        "small",
        400,
        ("can0", "can1"),
        (("can0", "basic"), ("can1", "extended")),
        repeats=10,
    ),
)

#: The one scene whose plan is also driven over real HTTP, so the measurement includes
#: request validation, JSON serialization and response rendering rather than only the
#: handler body. Kept to the fixtures: a large document would turn 400 real requests into
#: a coffee break without changing what is being asked.
HTTP_SCENE_KEY = "3-two-ch-two-dbc"


def viewport(scene: ViewportScene) -> list[tuple[str, int]]:
    """The scene's frames as ``(channel_id, sequence)``, in realtime order."""
    return [
        (scene.channels[index % len(scene.channels)], index + 1)
        for index in range(scene.frames)
    ]


def contiguous_runs(
    frames: Sequence[tuple[str, int]],
    bindings: Mapping[str, str],
    max_frames: int = MAX_BATCH_FRAMES,
) -> list[tuple[str, list[tuple[str, int]]]]:
    """The plan the Desktop used to submit: one request per contiguous single-asset run.

    A run ends at anything that is not "same asset, next sequence": another channel's
    frame, an unbound channel, or the bound. This is `partitionDecodeRuns` — the function
    the Desktop's `decode-partition.ts` used to export.
    """
    runs: list[tuple[str, list[tuple[str, int]]]] = []
    current: tuple[str, list[tuple[str, int]]] | None = None
    last_sequence: int | None = None
    for channel, sequence in frames:
        asset_id = bindings.get(channel)
        if asset_id is None:
            current = None
            last_sequence = None
            continue
        continues = (
            current is not None
            and current[0] == asset_id
            and last_sequence is not None
            and last_sequence + 1 == sequence
            and len(current[1]) < max_frames
        )
        if continues and current is not None:
            current[1].append((channel, sequence))
        else:
            current = (asset_id, [(channel, sequence)])
            runs.append(current)
        last_sequence = sequence
    return runs


def work_sets(
    frames: Sequence[tuple[str, int]],
    bindings: Mapping[str, str],
    max_frames: int = MAX_BATCH_FRAMES,
) -> list[tuple[str, list[tuple[str, int]]]]:
    """The plan the Desktop submits now: one request per asset, cut only at the bound.

    This is `partitionDecodeWorkSets`. Gaps are not a reason to start a new request; the
    only reasons are a different asset or a full set.
    """
    grouped: dict[str, list[list[tuple[str, int]]]] = {}
    for channel, sequence in frames:
        asset_id = bindings.get(channel)
        if asset_id is None:
            continue
        sets = grouped.setdefault(asset_id, [])
        if not sets or len(sets[-1]) >= max_frames:
            sets.append([])
        sets[-1].append((channel, sequence))
    return [
        (asset_id, one_set) for asset_id, sets in grouped.items() for one_set in sets
    ]


def execute_plan(
    fixture: ProjectFixture,
    plan: Sequence[tuple[str, list[tuple[str, int]]]],
    probe: Probe,
    *,
    use_cache: bool,
    endpoint: str,
    repeats: int = 1,
) -> tuple[int, float, int, int, int]:
    """Run one plan against the real handler and time it.

    Returns the decoded frame count, the elapsed seconds and the parse / compile /
    asset-read counts the probe observed — so "the work really happened" and "the
    static work was reused" are both numbers rather than claims.
    """
    import canx.dbc.project_service as project_service_module

    decoder_cache = DECODER_CACHE if use_cache else NULL_CACHE
    project_service_module.DECODER_CACHE = decoder_cache
    decoder_cache.clear()
    decoded = 0
    probe.reset()
    started = time.perf_counter()
    for _ in range(repeats):
        for asset_key, entries in plan:
            spec = fixture.specs[asset_key]
            payloads = [payload_for(spec, sequence, channel) for channel, sequence in entries]
            if endpoint == "batch":
                decoded += _decode_batch(
                    str(fixture.root), spec.asset_id, "benchmark", payloads
                ).frame_count
            else:
                decoded += _decode_frames(
                    str(fixture.root), spec.asset_id, "benchmark", payloads
                ).frame_count
    return decoded, time.perf_counter() - started, probe.parses, probe.compiles, probe.asset_reads


def measure_fragmentation(
    fixtures: Mapping[str, ProjectFixture], probe: Probe
) -> list[FragmentationRow]:
    """Measure both plans on every scene, on the real handlers, in this run."""
    rows: list[FragmentationRow] = []
    for scene in FRAGMENTATION_SCENES:
        fixture = fixtures[scene.project]
        frames = viewport(scene)
        bindings = dict(scene.bindings)
        for label, plan, endpoint in (
            (BEFORE_PLAN, contiguous_runs(frames, bindings), "batch"),
            (AFTER_PLAN, work_sets(frames, bindings), "frames"),
        ):
            decoded, elapsed, parses, compiles, reads = execute_plan(
                fixture, plan, probe, use_cache=True, endpoint=endpoint, repeats=scene.repeats
            )
            rows.append(
                FragmentationRow(
                    scene=scene.key,
                    plan=label,
                    assets=len({asset_id for asset_id, _ in plan}),
                    frames=decoded,
                    requests=len(plan) * scene.repeats,
                    elapsed_s=elapsed,
                    parses=parses,
                    compiles=compiles,
                    reads=reads,
                )
            )
    return rows


def measure_over_http(
    fixtures: Mapping[str, ProjectFixture], probe: Probe
) -> list[HttpRow]:
    """Drive the same two plans through the real ASGI app, not the handler body.

    The handler-body measurement above deliberately excludes the HTTP surface: request
    validation, JSON decoding, frame reconstruction and response serialization. This one
    includes all of it, so the throughput claim is not an artifact of calling a Python
    function directly. `TestClient` runs the real FastAPI application in process; it is
    not a mock and not a second implementation of the endpoints.
    """
    scene = next(one for one in FRAGMENTATION_SCENES if one.key == HTTP_SCENE_KEY)
    fixture = fixtures[scene.project]
    frames = viewport(scene)
    bindings = dict(scene.bindings)
    client = TestClient(create_app())
    rows: list[HttpRow] = []
    for label, plan, path in (
        (BEFORE_PLAN, contiguous_runs(frames, bindings), "decode-batch"),
        (AFTER_PLAN, work_sets(frames, bindings), "decode-frames"),
    ):
        decoded = 0
        started = time.perf_counter()
        for asset_key, entries in plan:
            spec = fixture.specs[asset_key]
            body = {
                "project_path": str(fixture.root),
                "stream_id": "benchmark",
                "frames": [
                    payload_for(spec, sequence, channel).model_dump()
                    for channel, sequence in entries
                ],
            }
            response = client.post(f"/dbc/assets/{spec.asset_id}/{path}", json=body)
            response.raise_for_status()
            decoded += response.json()["frame_count"]
        elapsed = time.perf_counter() - started
        rows.append(
            HttpRow(
                scene=scene.key,
                plan=label,
                frames=decoded,
                requests=len(plan),
                elapsed_s=elapsed,
            )
        )
    probe.reset()
    return rows


def print_fragmentation(rows: list[FragmentationRow]) -> None:
    """Print the measured fragmentation table, BEFORE and AFTER side by side."""
    header = (
        f"{'scene':<20} {'plan':<14} {'assets':>6} {'frames':>7} {'req':>6} "
        f"{'frames/req':>10} {'elapsed_s':>9} {'frames/s':>12} "
        f"{'parse':>6} {'compile':>7} {'read':>5}"
    )
    print("\n== request fragmentation (one viewport, real handlers) ==")
    print(header)
    for row in rows:
        print(
            f"{row.scene:<20} {row.plan:<14} {row.assets:>6} {row.frames:>7} "
            f"{row.requests:>6} {row.frames_per_request:>10.1f} {row.elapsed_s:>9.3f} "
            f"{row.frames_per_second:>12,.0f} {row.parses:>6} {row.compiles:>7} "
            f"{row.reads:>5}"
        )
    for scene in dict.fromkeys(row.scene for row in rows):
        before = next(row for row in rows if row.scene == scene and row.plan == BEFORE_PLAN)
        after = next(row for row in rows if row.scene == scene and row.plan == AFTER_PLAN)
        print(
            f"  {scene:<20} requests {before.requests:>6} -> {after.requests:>4}"
            f"  ({before.requests / after.requests:.0f}x fewer)"
            f"  frames/s {before.frames_per_second:>10,.0f} -> {after.frames_per_second:>10,.0f}"
        )


def print_http(rows: list[HttpRow]) -> None:
    """Print the same plan measured through the real FastAPI application."""
    header = (
        f"{'scene':<20} {'plan':<14} {'frames':>7} {'req':>6} "
        f"{'elapsed_s':>9} {'ms/req':>8} {'frames/s':>12}"
    )
    print("\n== request fragmentation over real HTTP (FastAPI/ASGI, in process) ==")
    print(header)
    for row in rows:
        print(
            f"{row.scene:<20} {row.plan:<14} {row.frames:>7} {row.requests:>6} "
            f"{row.elapsed_s:>9.3f} {row.ms_per_request:>8.2f} {row.frames_per_second:>12,.0f}"
        )


def main() -> None:
    """Measure every scenario against the tiny fixtures and a synthetic document."""
    print(f"environment: {environment()}")
    probe = Probe()
    probe.install()
    rows: list[Row] = []
    fragmentation_rows: list[FragmentationRow] = []
    http_rows: list[HttpRow] = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            small_root, small_ids = build_project(
                tmp_path,
                "small",
                (
                    ("basic_standard", (FIXTURES / "basic_standard.dbc").read_bytes()),
                    ("extended", (FIXTURES / "extended.dbc").read_bytes()),
                ),
            )
            # Two *different* documents for the two-asset scenes. Two assets holding the same
            # bytes would make "two DBCs" a name for one document — and the decoder cache keys
            # on verified content identity, so the second asset would be answered from the
            # first asset's entry and the measurement would report a saving it never made.
            synthetic_a = synthetic_dbc(SYNTHETIC_MESSAGES, SYNTHETIC_SIGNALS).encode("utf-8")
            synthetic_b = synthetic_dbc(
                SYNTHETIC_MESSAGES,
                SYNTHETIC_SIGNALS,
                base_frame_id=0x600,
                prefix="Alt",
            ).encode("utf-8")
            assert synthetic_a != synthetic_b, "the two synthetic documents must differ"
            large_root, large_ids = build_project(
                tmp_path,
                "large",
                (("synthetic_a", synthetic_a), ("synthetic_b", synthetic_b)),
            )

            small_assets = (
                AssetSpec("basic", small_ids[0], 0x123, False, FIXTURE_ENGINE_DATA),
                AssetSpec("extended", small_ids[1], 0x123, True, bytes(8)),
            )
            large_assets = (
                AssetSpec("synthetic_a", large_ids[0], 0x200, False, SYNTHETIC_DATA),
                AssetSpec("synthetic_b", large_ids[1], 0x600, False, SYNTHETIC_DATA),
            )

            for scenario in SCENARIOS:
                for use_cache in (True, False):
                    rows.append(
                        run_scenario(
                            "small (fixtures)",
                            scenario,
                            small_root,
                            small_assets,
                            probe,
                            use_cache=use_cache,
                        )
                    )
            for scenario in SCENARIOS:
                for use_cache in (True, False):
                    rows.append(
                        run_scenario(
                            "large (synthetic)",
                            scaled_for_large(scenario),
                            large_root,
                            large_assets,
                            probe,
                            use_cache=use_cache,
                        )
                    )

            fixtures = {
                "large": ProjectFixture(
                    "large (synthetic)",
                    large_root,
                    {"synthetic_a": large_assets[0], "synthetic_b": large_assets[1]},
                ),
                "small": ProjectFixture(
                    "small (fixtures)",
                    small_root,
                    {"basic": small_assets[0], "extended": small_assets[1]},
                ),
            }
            fragmentation_rows = measure_fragmentation(fixtures, probe)
            http_rows = measure_over_http(fixtures, probe)
    finally:
        probe.uninstall()
        import canx.dbc.project_service as project_service_module

        project_service_module.DECODER_CACHE = DECODER_CACHE

    print_table(rows)
    print_fragmentation(fragmentation_rows)
    print_http(http_rows)
    total = (
        sum(row.elapsed_s for row in rows)
        + sum(row.elapsed_s for row in fragmentation_rows)
        + sum(row.elapsed_s for row in http_rows)
    )
    print(f"\ntotal wall time: {total:.2f}s")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
