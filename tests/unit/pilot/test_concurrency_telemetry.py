"""The committed concurrency evidence must recompute to what the report claims.

The AGENT-02 instrumented rerun's raw artefacts — three per-worker heartbeat files
and one sampler log — lived under the gitignored ``.rivet/``, so an independent
reviewer could not recompute the run's numbers from the pull request. FIX-2
transcribes them, verbatim, into ``.agent/telemetry/v0.3-12-concurrency-rerun.json``;
this module re-derives every figure the acceptance report quotes *from those raw
sequences* and refuses a disagreement.

What is pinned here is the arithmetic, not the wording: a worker's activity windows
close when the gap between consecutive heartbeats exceeds the artifact's own
threshold, "max simultaneously active" is a peak over those windows, and the strict
three-way overlap is the *longest continuous* interval in which all three are inside
one. A number that only appears in prose cannot be checked; these can.

The report's own figures, which the recomputation must reproduce:

```text
A  first 04:00:10  last 04:02:49  span 159 s  max gap 36 s
B  first 04:00:10  last 04:02:42  span 152 s  max gap 67 s
C  first 04:00:10  last 04:02:04  span 114 s  max gap 33 s
max simultaneously active workers      3
strict three-way overlap               25 s
```

"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
ARTIFACT_PATH = REPO_ROOT / ".agent" / "telemetry" / "v0.3-12-concurrency-rerun.json"
WORKERS = ("A", "B", "C")

#: What `docs/acceptance/v0.3-12-desktop-project-open-foundation.md` §10.3 reports.
REPORTED_MAX_SIMULTANEOUS = 3
REPORTED_STRICT_THREE_WAY_OVERLAP_SECONDS = 25
REPORTED_CONDITIONAL_THREE_WAY_OVERLAP_SECONDS = 114
REPORTED_WORKER_SPANS = {"A": 159, "B": 152, "C": 114}
REPORTED_WORKER_MAX_GAPS = {"A": 36, "B": 67, "C": 33}


def load_artifact() -> dict[str, Any]:
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


def active_intervals(heartbeats: list[int], threshold: int) -> list[list[int]]:
    """Consecutive heartbeats merged while the gap stays within ``threshold``."""

    windows: list[list[int]] = []
    start = previous = heartbeats[0]
    for beat in heartbeats[1:]:
        if beat - previous > threshold:
            windows.append([start, previous])
            start = beat
        previous = beat
    windows.append([start, previous])
    return windows


def _intersect(left: list[list[int]], right: list[list[int]]) -> list[list[int]]:
    overlaps: list[list[int]] = []
    for left_start, left_end in left:
        for right_start, right_end in right:
            start, end = max(left_start, right_start), min(left_end, right_end)
            if start <= end:
                overlaps.append([start, end])
    return sorted(overlaps)


def three_way_intervals(sets: dict[str, list[list[int]]]) -> list[list[int]]:
    """The intervals in which all three workers are simultaneously active."""

    return _intersect(_intersect(sets["A"], sets["B"]), sets["C"])


def max_simultaneously_active(sets: dict[str, list[list[int]]]) -> int:
    """The peak number of overlapping activity windows."""

    edges: list[tuple[int, int]] = []
    for windows in sets.values():
        for start, end in windows:
            edges.append((start, 1))
            edges.append((end + 1, -1))
    edges.sort()
    current = peak = 0
    for _, delta in edges:
        current += delta
        peak = max(peak, current)
    return peak


def test_the_committed_artifact_parses_and_declares_itself() -> None:
    artifact = load_artifact()

    assert artifact["schema_version"] == 1
    assert artifact["artifact"] == "v0.3-12-concurrency-rerun"
    assert artifact["run"]["main_agents"] == 1
    assert artifact["run"]["workers"] == 3
    assert artifact["run"]["execution_mode"] == "native-shared"
    assert artifact["run"]["harness_max_workers"] == 3
    assert artifact["run"]["concurrency_configuration_modified"] is False


def test_every_worker_carries_its_raw_heartbeat_sequence() -> None:
    artifact = load_artifact()

    assert set(artifact["workers"]) == set(WORKERS)
    for name in WORKERS:
        beats = artifact["workers"][name]["heartbeats"]
        assert len(beats) >= 2
        assert beats == sorted(beats), "a heartbeat sequence must be monotonic"


def test_the_derived_first_and_last_heartbeats_are_recomputable() -> None:
    artifact = load_artifact()

    for name in WORKERS:
        beats = artifact["workers"][name]["heartbeats"]
        assert artifact["workers"][name]["first_heartbeat"] == min(beats)
        assert artifact["workers"][name]["last_heartbeat"] == max(beats)
        assert artifact["workers"][name]["span_seconds"] == max(beats) - min(beats)


def test_the_reported_per_worker_spans_and_gaps_are_recomputable() -> None:
    artifact = load_artifact()

    for name in WORKERS:
        beats = artifact["workers"][name]["heartbeats"]
        reported = artifact["workers"][name]
        assert reported["span_seconds"] == REPORTED_WORKER_SPANS[name]
        assert reported["max_gap_seconds"] == REPORTED_WORKER_MAX_GAPS[name]
        gaps = [later - earlier for earlier, later in itertools.pairwise(beats)]
        assert reported["max_gap_seconds"] == max(gaps)


def test_the_recorded_active_intervals_recompute_from_the_heartbeats() -> None:
    artifact = load_artifact()
    threshold = artifact["activity_gap_threshold_seconds"]

    recomputed = {
        name: active_intervals(artifact["workers"][name]["heartbeats"], threshold)
        for name in WORKERS
    }

    for name in WORKERS:
        assert artifact["workers"][name]["active_intervals"] == recomputed[name]

    # The threshold is load-bearing: worker B's single 67 s gap is what splits it in two.
    assert recomputed["B"] != [[recomputed["B"][0][0], recomputed["B"][-1][1]]]
    assert len(recomputed["A"]) == 1


def test_the_reported_max_simultaneously_active_workers_recomputes() -> None:
    artifact = load_artifact()
    threshold = artifact["activity_gap_threshold_seconds"]
    windows = {
        name: active_intervals(artifact["workers"][name]["heartbeats"], threshold)
        for name in WORKERS
    }

    assert max_simultaneously_active(windows) == REPORTED_MAX_SIMULTANEOUS
    assert artifact["derived"]["max_simultaneously_active_workers"] == REPORTED_MAX_SIMULTANEOUS


def test_the_reported_strict_three_way_overlap_recomputes() -> None:
    artifact = load_artifact()
    threshold = artifact["activity_gap_threshold_seconds"]
    windows = {
        name: active_intervals(artifact["workers"][name]["heartbeats"], threshold)
        for name in WORKERS
    }

    strict = three_way_intervals(windows)
    longest = max(end - start for start, end in strict)

    assert longest == REPORTED_STRICT_THREE_WAY_OVERLAP_SECONDS
    assert artifact["derived"]["strict_three_way_overlap_seconds"] == longest
    assert artifact["derived"]["strict_three_way_overlap_intervals"] == strict
    assert artifact["derived"]["strict_three_way_overlap_total_seconds"] == sum(
        end - start for start, end in strict
    )


def test_the_conditional_reading_bundles_each_worker_into_one_window() -> None:
    """The 114 s figure is the same arithmetic with a gap-blind activity window."""

    artifact = load_artifact()
    bundled = {}
    for name in WORKERS:
        beats = artifact["workers"][name]["heartbeats"]
        bundled[name] = [[min(beats), max(beats)]]

    conditional = sum(end - start for start, end in three_way_intervals(bundled))

    assert conditional == REPORTED_CONDITIONAL_THREE_WAY_OVERLAP_SECONDS
    assert artifact["derived"]["conditional_three_way_overlap_seconds"] == conditional


def test_the_sampler_samples_agree_with_the_derived_activity_windows() -> None:
    artifact = load_artifact()
    threshold = artifact["activity_gap_threshold_seconds"]
    windows = {
        name: active_intervals(artifact["workers"][name]["heartbeats"], threshold)
        for name in WORKERS
    }
    samples = artifact["sampler_samples"]

    assert samples
    assert artifact["dispatch_window"]["sample_count"] == len(samples)
    assert artifact["dispatch_window"]["first_sample"] == samples[0]["timestamp"]
    assert artifact["dispatch_window"]["last_sample"] == samples[-1]["timestamp"]

    for sample in samples:
        moment = sample["timestamp"]
        for name in WORKERS:
            expected = any(start <= moment <= end for start, end in windows[name])
            assert sample["worker_active"][name] is expected


def test_the_sampler_never_observed_more_than_three_active_workers() -> None:
    artifact = load_artifact()

    peak = max(
        sum(1 for name in WORKERS if sample["worker_active"][name])
        for sample in artifact["sampler_samples"]
    )

    assert peak == REPORTED_MAX_SIMULTANEOUS


def test_the_dispatch_window_and_process_observation_are_self_consistent() -> None:
    artifact = load_artifact()
    window = artifact["dispatch_window"]
    processes = artifact["process_observation"]

    assert window["duration_seconds"] == window["last_sample"] - window["first_sample"]
    assert processes["node_processes_baseline"] < processes["node_processes_peak"]
    observed = [sample["node_processes"] for sample in artifact["sampler_samples"]]
    assert processes["node_processes_peak"] == max(observed)
    assert processes["node_processes_baseline"] == min(observed)


def test_the_artifact_carries_no_private_or_absolute_locator() -> None:
    """A committed artefact must not smuggle a user directory or a credential in."""

    artifact = load_artifact()
    raw = ARTIFACT_PATH.read_text(encoding="utf-8")

    assert re.search(r"[A-Za-z]:[\\/]", raw) is None, "no Windows drive path"
    assert "\\" not in raw, "no backslash path separator"
    assert "Users" not in raw
    assert re.search(r"(?i)username|token|secret|password|api[_-]?key", raw) is None
    # Every locator it does name is repository-relative (the raw .rivet evidence it
    # was transcribed from), so a reviewer can see where the numbers came from.
    assert set(artifact["source_evidence"].values()) == {
        ".rivet/scratch/rerun-heartbeat/{A,B,C}.hb (gitignored)",
        ".rivet/scratch/rerun-telemetry.log (gitignored)",
        "verbatim; the derived block below is recomputed by the test",
    }
