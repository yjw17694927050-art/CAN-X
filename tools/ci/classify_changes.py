"""CI-03 — change classification for the tiered quality gate.

The classifier answers one question: *which validation jobs must run for this
change?* It is deliberately small, standard-library only and unit-testable, so
that the workflow's job conditions are consequences of a tested function rather
than of YAML path expressions that drift.

Fail-closed rules, in order:

* ``workflow_dispatch`` always requests FULL CI;
* an unknown path always escalates the whole change set to FULL CI;
* a diff that cannot be established always escalates to FULL CI;
* any internal error escalates to FULL CI and is reported as a warning — the
  classifier never chooses "skip everything" as a way out of not knowing.

FULL CI means every domain job runs. It is never "no validation".
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

FULL = "full"
DOCS_ONLY = "docs_only"
NO_CHANGES = "no_changes"

_ZERO_SHA = "0" * 40

# --------------------------------------------------------------------------
# Rule table
#
# Ordering below is the *documentation order*; the evaluation order is fixed in
# `classify_paths`: critical (FULL) first, then unknown-path escalation, then
# the per-domain flags as a union.
# --------------------------------------------------------------------------

#: Authorities whose change invalidates the premise of a selective run. Any hit
#: forces FULL CI: Safety, the CI control plane itself, the global build and
#: dependency authorities, the project authorities, and Agent shared truth.
CRITICAL_PATTERNS: tuple[str, ...] = (
    ".github/workflows/**",
    "tools/ci/**",
    "scripts/**",
    "runtime/canx/safety/**",
    "docs/architecture/**",
    "AGENTS.md",
    "PRD.md",
    "SPEC.md",
    "pyproject.toml",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "apps/desktop/src-tauri/Cargo.toml",
    "apps/desktop/src-tauri/Cargo.lock",
    ".agent/config.json",
    ".agent/schemas/**",
    "docs/engineering/INTEGRATION_POLICY.md",
    "docs/engineering/MULTI_AGENT_PROTOCOL.md",
)

#: Rust / Tauri surface.
RUST_PATTERNS: tuple[str, ...] = ("apps/desktop/src-tauri/**",)

#: Frontend surface. `apps/desktop/src/**` is deliberately distinct from
#: `apps/desktop/src-tauri/**` — the two are sibling directories and a prefix
#: test on the raw string would not distinguish them.
FRONTEND_PATTERNS: tuple[str, ...] = (
    "apps/desktop/src/**",
    "apps/desktop/index.html",
    "apps/desktop/package.json",
    "apps/desktop/vite.config.ts",
    "apps/desktop/tsconfig.json",
    "apps/desktop/eslint.config.js",
)

#: Python Runtime and the engineering tooling it verifies.
RUNTIME_PATTERNS: tuple[str, ...] = (
    "runtime/**",
    "tests/**",
    "tools/agent/**",
    ".agent/**",
)

#: Cross-boundary contracts. A Python-green change here can still break a
#: non-Python consumer, so these require the frontend job as well.
SHARED_CONTRACT_PATTERNS: tuple[str, ...] = (
    "runtime/canx/api/**",
    "runtime/canx/domain/**",
    "runtime/canx/transport/**",
)

#: The Runtime control-plane HTTP contract, which has a **Rust** consumer as well.
#:
#: The desktop sidecar (`apps/desktop/src-tauri/src/runtime_sidecar.rs`) calls
#: `GET /health` — asserting `service == "canx-runtime"` and
#: `schema_version == 1` — and `POST /runtime/shutdown`, asserting HTTP 202. Both
#: routes, and the `HealthResponse` / `ShutdownResponse` models they answer with,
#: are defined by the FastAPI application factory in this one module. The renderer
#: consumes the same HTTP surface and the Python runtime implements it, so a change
#: here has three real consumers and requires all three domain jobs.
#:
#: Kept as an explicit single-file rule rather than escalating the whole
#: `runtime/canx/api/**` package: the Rust sidecar consumes the control plane and
#: nothing else, so the frontend-only routers (`project`, `dbc`, `trace`, `errors`)
#: remain a Python + frontend contract. The anchor is the application factory — the
#: stable root module of the package — and if the control routes are ever moved to
#: another module, that module must be added here in the same change.
DESKTOP_CONTROL_API_PATTERNS: tuple[str, ...] = ("runtime/canx/api/app.py",)

#: Tauri IPC is a boundary between Rust and TypeScript, not a Rust-internal one.
#:
#: The commands registered by `apps/desktop/src-tauri/src/lib.rs` and
#: `.../dbc_file_bridge.rs` are invoked from the renderer by exact command name,
#: and the frontend's own drift test (`apps/desktop/src/desktop/dbc-file-bridge.test.ts`)
#: reads these Rust sources to prove the two halves have not diverged. A change to
#: Rust source must therefore run the frontend job as well, or that cross-language
#: contract test would be skipped. `tests/**`, icons, packaging resources and
#: `tauri.conf.json` are not IPC sources and stay Rust-only.
TAURI_IPC_RUST_PATTERNS: tuple[str, ...] = ("apps/desktop/src-tauri/src/**",)

#: The TypeScript half of the Tauri IPC boundary: the modules that `invoke` a Rust
#: command. Changing one can change the contract the Rust side must satisfy, so the
#: Rust job is required too. Deliberately a precise list, not `apps/desktop/src/**`:
#: the HTTP Runtime clients (`capture-client`, `dbc-client`, `realtime-stream`) speak
#: to the Python runtime and have no Rust consumer, so they are not escalated.
TAURI_IPC_FRONTEND_PATTERNS: tuple[str, ...] = (
    "apps/desktop/src/desktop/**",
    "apps/desktop/src/runtime/runtime-client.ts",
    "apps/desktop/src/smoke/**",
)

#: Documentation-only surfaces. Nothing here needs a domain job.
DOCS_PATTERNS: tuple[str, ...] = ("docs/**", "*.md")


def _match(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")
    if "*" in pattern:
        return fnmatchcase(path, pattern)
    return path == pattern


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(_match(path, pattern) for pattern in patterns)


def _normalise(raw: str) -> str:
    """Normalise a repository-relative path to forward slashes, no leading './'."""

    cleaned = raw.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


@dataclass(frozen=True)
class Classification:
    """The routing decision for one change set."""

    runtime_required: bool
    frontend_required: bool
    rust_required: bool
    full_required: bool
    classification: str
    reason: str
    paths: tuple[str, ...] = ()
    unmatched: tuple[str, ...] = field(default=())

    @property
    def all_required(self) -> bool:
        return self.runtime_required and self.frontend_required and self.rust_required

    def as_dict(self) -> dict[str, object]:
        return {
            "runtime_required": self.runtime_required,
            "frontend_required": self.frontend_required,
            "rust_required": self.rust_required,
            "full_required": self.full_required,
            "classification": self.classification,
            "reason": self.reason,
            "paths": list(self.paths),
            "unmatched": list(self.unmatched),
        }


def _full(reason: str, paths: Sequence[str] = ()) -> Classification:
    return Classification(
        runtime_required=True,
        frontend_required=True,
        rust_required=True,
        full_required=True,
        classification=FULL,
        reason=reason,
        paths=tuple(paths),
    )


def classify_paths(paths: Iterable[str]) -> Classification:
    """Classify an explicit set of repository-relative changed paths."""

    normalised = tuple(_normalise(raw) for raw in paths)
    considered = tuple(path for path in normalised if path)

    if not considered:
        return Classification(
            runtime_required=False,
            frontend_required=False,
            rust_required=False,
            full_required=False,
            classification=NO_CHANGES,
            reason="no changed paths",
        )

    critical = tuple(path for path in considered if _matches_any(path, CRITICAL_PATTERNS))
    if critical:
        return _full(
            "critical authority changed: " + ", ".join(critical),
            considered,
        )

    unmatched: list[str] = []
    runtime = frontend = rust = False

    for path in considered:
        known = False
        if _matches_any(path, RUST_PATTERNS):
            rust = True
            known = True
        if _matches_any(path, FRONTEND_PATTERNS):
            frontend = True
            known = True
        if _matches_any(path, RUNTIME_PATTERNS):
            runtime = True
            known = True
        if _matches_any(path, SHARED_CONTRACT_PATTERNS):
            runtime = True
            frontend = True
            known = True
        if _matches_any(path, DESKTOP_CONTROL_API_PATTERNS):
            runtime = True
            frontend = True
            rust = True
            known = True
        if _matches_any(path, TAURI_IPC_RUST_PATTERNS):
            frontend = True
            rust = True
            known = True
        if _matches_any(path, TAURI_IPC_FRONTEND_PATTERNS):
            frontend = True
            rust = True
            known = True
        if _matches_any(path, DOCS_PATTERNS):
            known = True
        if not known:
            unmatched.append(path)

    if unmatched:
        return Classification(
            runtime_required=True,
            frontend_required=True,
            rust_required=True,
            full_required=True,
            classification=FULL,
            reason="unrecognised path escalated to FULL CI: " + ", ".join(unmatched),
            paths=considered,
            unmatched=tuple(unmatched),
        )

    labels = [
        name
        for name, required in (("runtime", runtime), ("frontend", frontend), ("rust", rust))
        if required
    ]
    return Classification(
        runtime_required=runtime,
        frontend_required=frontend,
        rust_required=rust,
        full_required=False,
        classification="+".join(labels) if labels else DOCS_ONLY,
        reason="classified from " + str(len(considered)) + " changed path(s)",
        paths=considered,
    )


# --------------------------------------------------------------------------
# Git-backed change detection
# --------------------------------------------------------------------------


class ClassificationError(RuntimeError):
    """Raised when the change set cannot be established; callers escalate to FULL."""


def _git(repo: str, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", repo, *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ClassificationError(
            "git " + " ".join(args) + " failed: " + (completed.stderr.strip() or "no stderr")
        )
    return completed.stdout


def _require_commit(repo: str, sha: str, label: str) -> str:
    candidate = (sha or "").strip()
    if not candidate or set(candidate) == {"0"}:
        raise ClassificationError(f"{label} is absent or all-zero")
    _git(repo, "cat-file", "-e", f"{candidate}^{{commit}}")
    return candidate


def changed_paths(repo: str, base: str, head: str) -> tuple[str, ...]:
    """The changed paths in ``base..head``.

    ``--no-renames`` is deliberate: a rename is reported as a delete of the old
    path *and* an add of the new one, so both sides are classified. With rename
    detection on, ``git`` would report only the new name and a critical file
    could be moved out of its guarded location unobserved.
    """

    output = _git(repo, "diff", "--name-only", "--no-renames", base, head)
    return tuple(line.strip() for line in output.splitlines() if line.strip())


def merge_base(repo: str, base: str, head: str) -> str:
    output = _git(repo, "merge-base", base, head).strip()
    if not output:
        raise ClassificationError("no merge base between base and head")
    return output


def classify_event(event: str, *, repo: str, base: str, head: str) -> Classification:
    """Classify a GitHub Actions event.

    Never raises: an event whose change set cannot be established is FULL CI.
    """

    try:
        return _classify_event(event, repo=repo, base=base, head=head)
    except Exception as exc:
        return _full(f"classifier_error: {type(exc).__name__}: {exc}")


def _classify_event(event: str, *, repo: str, base: str, head: str) -> Classification:
    if event == "workflow_dispatch":
        return _full("workflow_dispatch requests full validation")

    if event == "pull_request":
        base_sha = _require_commit(repo, base, "pull_request base")
        head_sha = _require_commit(repo, head, "pull_request head")
        # The effective diff of a PR is merge-base(base, head)..head — never
        # base..head, which would also report the base branch's own progress.
        root = merge_base(repo, base_sha, head_sha)
        paths = changed_paths(repo, root, head_sha)
        return classify_paths(paths)

    if event == "push":
        before = _require_commit(repo, base, "push before")
        after = _require_commit(repo, head, "push head")
        return classify_paths(changed_paths(repo, before, after))

    raise ClassificationError(f"unsupported event {event!r}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

_OUTPUT_KEYS = (
    "runtime_required",
    "frontend_required",
    "rust_required",
    "full_required",
    "classification",
)


def _write_outputs(destination: str, result: Classification) -> None:
    payload = result.as_dict()
    lines = [
        f"{key}={'true' if payload[key] is True else payload[key]}"
        if isinstance(payload[key], bool)
        else f"{key}={payload[key]}"
        for key in _OUTPUT_KEYS
    ]
    with open(destination, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _resolve_inputs(args: argparse.Namespace) -> tuple[str, str, str]:
    if args.paths is not None:
        return "", "", ""

    event = args.event or os.environ.get("CANX_CI_EVENT", "")
    if event == "pull_request":
        base = args.base if args.base is not None else os.environ.get("CANX_CI_PR_BASE", "")
        head = args.head if args.head is not None else os.environ.get("CANX_CI_PR_HEAD", "")
    elif event == "push":
        base = args.base if args.base is not None else os.environ.get("CANX_CI_PUSH_BEFORE", "")
        head = args.head if args.head is not None else os.environ.get("CANX_CI_PUSH_HEAD", "")
    else:
        base = head = ""
    return event, base, head


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify a change for the tiered quality gate.")
    parser.add_argument("--event", default=None, help="GitHub event name")
    parser.add_argument("--base", default=None, help="base sha (PR base / push before)")
    parser.add_argument("--head", default=None, help="head sha (PR head / push sha)")
    parser.add_argument("--repo", default=".", help="repository path")
    parser.add_argument("--github-output", default=None, help="file to append job outputs to")
    parser.add_argument("--paths", nargs="*", default=None, help="explicit paths (local use)")
    args = parser.parse_args(argv)

    event, base, head = _resolve_inputs(args)
    if args.paths is not None:
        result = classify_paths(args.paths)
    else:
        result = classify_event(event, repo=args.repo, base=base, head=head)

    payload = result.as_dict()
    print(json.dumps(payload, indent=2, sort_keys=True))

    if result.reason.startswith("classifier_error:"):
        print(f"::warning::{result.reason}", file=sys.stderr)

    destination = args.github_output or os.environ.get("GITHUB_OUTPUT", "")
    if destination:
        _write_outputs(destination, result)

    # Always exit 0: a classifier that cannot decide escalates to FULL CI, which
    # still validates the change. Only a broken pipeline (job failure) is caught
    # by the gate, which is itself fail-closed.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
