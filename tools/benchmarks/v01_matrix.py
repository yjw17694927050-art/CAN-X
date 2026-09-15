"""Run the complete CAN-X V0.1 benchmark matrix sequentially."""

import argparse
import asyncio
import json
from pathlib import Path

from canx.benchmarks.v01_pipeline import matrix_configs, run_benchmark


async def run(output: Path, *, seed: int, warmup: float, duration: float) -> None:
    """Preserve one raw result per matrix point and a combined index."""
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    for index, config in enumerate(
        matrix_configs(seed=seed, warmup_seconds=warmup, duration_seconds=duration), start=1
    ):
        stem = f"{index:03d}-{config.channels}ch-{config.mode}-{config.load}-b{config.batch_size}"
        result = await run_benchmark(config, recording_path=output / f"{stem}.canxmsg")
        raw = result.model_dump(mode="json")
        (output / f"{stem}.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")
        results.append(raw)
    (output / "matrix.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", default=1, type=int)
    parser.add_argument("--warmup", default=0.05, type=float)
    parser.add_argument("--duration", default=0.25, type=float)
    args = parser.parse_args()
    asyncio.run(
        run(
            args.output,
            seed=args.seed,
            warmup=args.warmup,
            duration=args.duration,
        )
    )


if __name__ == "__main__":
    main()
