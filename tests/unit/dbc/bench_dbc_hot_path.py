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
from dataclasses import dataclass
from pathlib import Path

from canx.api.dbc import DbcFramePayload, _decode_batch
from canx.api.frame import frame_to_wire
from canx.dbc.decoder_cache import DECODER_CACHE
from canx.dbc.project_service import ProjectDbcService
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectService

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "dbc"

#: Messages in the synthetic document. A mid-size real vehicle DBC, not a fixture.
SYNTHETIC_MESSAGES = 300
#: Signals per synthetic message (8 eight-bit signals fill one 8-byte payload).
SYNTHETIC_SIGNALS = 8

FIXTURE_ENGINE_DATA = bytes([0xB8, 0x0B, 0x50, 0x80]) + bytes(4)
SYNTHETIC_DATA = bytes(8)


def synthetic_dbc(messages: int, signals: int) -> str:
    """Build a well-formed DBC text with ``messages`` messages of ``signals`` signals."""
    lines = ['VERSION ""', "", "NS_ :", "", "BS_:", "", "BU_: ECU1 ECU2", ""]
    for index in range(messages):
        lines.append(f"BO_ {0x200 + index} Msg_{index}: 8 ECU1")
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


def payload_for(spec: AssetSpec, sequence: int) -> DbcFramePayload:
    """Build the wire payload that decodes against ``spec`` at ``sequence``."""
    frame = Frame(
        sequence=sequence,
        channel_id="can0",
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


def main() -> None:
    """Measure every scenario against the tiny fixtures and a synthetic document."""
    print(f"environment: {environment()}")
    probe = Probe()
    probe.install()
    rows: list[Row] = []
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
            synthetic = synthetic_dbc(SYNTHETIC_MESSAGES, SYNTHETIC_SIGNALS).encode("utf-8")
            large_root, large_ids = build_project(
                tmp_path,
                "large",
                (("synthetic_a", synthetic), ("synthetic_b", synthetic)),
            )

            small_assets = (
                AssetSpec("basic", small_ids[0], 0x123, False, FIXTURE_ENGINE_DATA),
                AssetSpec("extended", small_ids[1], 0x123, True, bytes(8)),
            )
            large_assets = (
                AssetSpec("synthetic_a", large_ids[0], 0x200, False, SYNTHETIC_DATA),
                AssetSpec("synthetic_b", large_ids[1], 0x200, False, SYNTHETIC_DATA),
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
    finally:
        probe.uninstall()
        import canx.dbc.project_service as project_service_module

        project_service_module.DECODER_CACHE = DECODER_CACHE

    print_table(rows)
    print(f"\ntotal wall time: {sum(row.elapsed_s for row in rows):.2f}s")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
