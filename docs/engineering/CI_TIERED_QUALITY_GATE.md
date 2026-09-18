# CAN-X — Change-Aware Tiered Quality Gate (CI-03)

> **Document**: `docs/engineering/CI_TIERED_QUALITY_GATE.md`
> **Scope**: How CI decides which validation jobs a change requires.
> **Status**: AWAITING INDEPENDENT RE-ACCEPTANCE (CI-03-FIX-1 — cross-domain
> dependency and fail-closed hardening).
> **Related**: `docs/engineering/INTEGRATION_POLICY.md` (the protected-integration
> policy this sits under), `.github/workflows/ci.yml` (the workflow),
> `tools/ci/classify_changes.py` (the classifier), `tools/ci/evaluate_gate.py`
> (the gate decision).

## 1. The problem

Before CI-03 every event ran every job. A one-line documentation change paid for
2 600+ Python tests, a Node install plus lint/typecheck/test/build, and a from-scratch
Tauri build with a PyInstaller sidecar staging step. The measured wall time of a
full run was ~400 s, and most of it had no relationship to the change under review.

CI-03 does not reduce what is *validated*. It stops running validations that the
change cannot invalidate — and it keeps every fail-closed property of the gate.

## 2. The shape

```text
                    Change Classification
                    (always runs, produces the routing decision)
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
  Runtime / Python     Frontend / TypeScript   Desktop System / Rust
  (only if required)   (only if required)      (only if required)
        └─────────────────────┼─────────────────────┘
                              ▼
                        Quality Gate
                        (ALWAYS RUNS — the only required status check)
```

`Quality Gate` runs on every event on every repository state. A docs-only change
produces a `Quality Gate` result of `success`, not a *missing* check. That
distinction is the whole design: the CI-02 ruleset requires the check to exist, so
a workflow that skipped itself for documentation would block every docs PR forever.

## 3. Routing

Routing is a tested Python function (`tools/ci/classify_changes.py`), not a set of
YAML path expressions. The workflow carries no routing logic of its own.

| Change | Runtime | Frontend | Rust | Classification |
| --- | --- | --- | --- | --- |
| `docs/**`, any `*.md` | — | — | — | `docs_only` |
| `runtime/**`, `tests/**` (not below) | ● | — | — | `runtime` |
| `runtime/canx/api/**` (except the control factory below), `runtime/canx/domain/**`, `runtime/canx/transport/**` | ● | ● | — | `runtime+frontend` |
| `runtime/canx/api/app.py` — the control-plane factory the Rust sidecar consumes | ● | ● | ● | `runtime+frontend+rust` |
| `tools/agent/**`, `.agent/**` (not below) | ● | — | — | `runtime` |
| `apps/desktop/src/**`, frontend config | — | ● | — | `frontend` |
| `apps/desktop/src/desktop/**`, `apps/desktop/src/runtime/runtime-client.ts`, `apps/desktop/src/smoke/**` — the Tauri IPC bridges | — | ● | ● | `frontend+rust` |
| `apps/desktop/src-tauri/src/**` — the Rust half of the Tauri IPC contract | — | ● | ● | `frontend+rust` |
| `apps/desktop/src-tauri/tests/**`, icons, packaging resources, `tauri.conf.json` | — | — | ● | `rust` |
| `runtime/canx/safety/**`, `docs/architecture/**` | ● | ● | ● | `full` |
| `.github/workflows/**`, `tools/ci/**`, `scripts/**` | ● | ● | ● | `full` |
| `pyproject.toml`, `package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `Cargo.toml`, `Cargo.lock` | ● | ● | ● | `full` |
| `AGENTS.md`, `PRD.md`, `SPEC.md` | ● | ● | ● | `full` |
| `.agent/config.json`, `.agent/schemas/**` | ● | ● | ● | `full` |
| `docs/engineering/INTEGRATION_POLICY.md`, `docs/engineering/MULTI_AGENT_PROTOCOL.md` | ● | ● | ● | `full` |
| **anything unrecognised** | ● | ● | ● | `full` |
| `workflow_dispatch` | ● | ● | ● | `full` |

Five of these deserve their reasoning stated, because they are the ones a reader
will question:

- **A Runtime API / domain / transport change also runs the frontend job.** A
  Python-green change to an HTTP or MessagePack contract can still break a
  non-Python consumer, and the Python suite cannot see that. Routing by file
  extension alone would miss it.
- **The Runtime control-plane factory also runs the Rust job.**
  `runtime/canx/api/app.py` defines `GET /health` and `POST /runtime/shutdown`
  together with the models they answer with, and the desktop sidecar
  (`apps/desktop/src-tauri/src/runtime_sidecar.rs`) consumes both — asserting
  `service == "canx-runtime"`, `schema_version == 1` and an HTTP 202. A change here
  that is green in Python and TypeScript can still break the Rust sidecar, so all
  three domain jobs run. The rule is deliberately the single control-plane factory
  rather than the whole `runtime/canx/api/**` package: Rust consumes the control
  plane and nothing else, so the frontend-only routers stay a Python + frontend
  contract. The anchor is the application factory — the stable root module — and
  moving the control routes to another module means adding that module here in the
  same change.
- **Tauri IPC is a two-language boundary, not a Rust-internal one.** The commands
  registered in `apps/desktop/src-tauri/src/**` are invoked from the renderer by
  exact command name, and the frontend's own drift test
  (`apps/desktop/src/desktop/dbc-file-bridge.test.ts`) reads those Rust sources to
  prove the two halves have not diverged. A Rust-source change must therefore run
  the frontend job, or that cross-language contract test would be skipped; and a
  change to a TypeScript IPC bridge must run the Rust job for the same reason.
  Rust's own tests, icons, packaging resources and `tauri.conf.json` have no
  TypeScript consumer and stay Rust-only. The HTTP Runtime clients
  (`capture-client`, `dbc-client`, `realtime-stream`) speak to the Python runtime,
  not to Rust, and are not escalated.
- **`scripts/**` is FULL.** The build and verification scripts
  (`build-runtime.cmd`, `package-windows.cmd`, `rust-check.cmd`) are authorities
  over both the Python sidecar and the Rust bundle; a change there invalidates the
  premise of any selective run.

## 4. Fail-closed rules

```text
unknown path                    → FULL CI
diff cannot be established      → FULL CI
workflow_dispatch               → FULL CI
classifier internal error       → FULL CI (and a ::warning:: in the run log)
```

The classifier **never** chooses "skip everything" as a way out of not knowing.
Its two inputs are derived like this:

```text
pull_request   merge-base(base, head)..head   the PR's effective diff, not the last commit
push → main    event.before..github.sha       what this integration actually introduced
```

`git diff --name-only --no-renames` is used deliberately: with rename detection on,
`git` reports only the new name for a rename, so a critical file could be *moved
out* of its guarded location unobserved. With `--no-renames` a rename is a delete
of the old path plus an add of the new one, and both sides are classified.

An unresolvable commit (an absent or all-zero `before`, a SHA the checkout does
not have) is not worked around — it escalates to FULL CI.

## 5. The gate

`tools/ci/evaluate_gate.py` decides the required check. The rule is:

```text
classifier result must be `success`            otherwise FAIL
`full_required` must be `true` or `false`      otherwise FAIL
required domain job must be `success`          `skipped` FAILS
not-required domain job may be `skipped` or `success`
anything else (failure, cancelled, missing)    FAILS — even for a not-required job
`classification` label present & consistent    otherwise FAIL
```

The fourth line is deliberate: a red job is evidence of a problem whether or not
the classifier required it, so the gate does not ignore it.

`full_required` is authoritative, and it is fail-closed. When the classifier says
FULL, all three domain jobs are required regardless of the individual flags. And
because a value that cannot be read must never be *defaulted away*, a
`full_required` that is missing, empty or not `true`/`false` fails the gate on its
own: silently falling back to the individual flags would let a corrupt FULL
classification run a selective gate.

The `classification` label is part of classification integrity, not decoration.
The gate re-derives the label the requirement flags describe (`full`, `docs_only`,
`no_changes`, or a `+`-joined union of the three domains) and fails closed when the
label is missing, empty or contradicts the flags it is about to act on.

## 6. What CI-03 does not change

```text
Repository Ruleset        main-protected-integration — unchanged
Required status check     Quality Gate — the exact same context string
PR requirement            unchanged
Direct push to main       still blocked
Force push                still blocked
Bypass actors             none
Triggers                  pull_request → main · push → main · workflow_dispatch
Job display names         Runtime / Python · Frontend / TypeScript · Desktop System / Rust
```

`Local Verification ≠ GitHub CI ≠ Protected Merge ≠ Independent Acceptance` is
unchanged. CI-03 changes *which validation jobs run before the gate*, and nothing
else.

## 7. Full CI on demand

`workflow_dispatch` always classifies as FULL. That is the supported way to force
complete validation — for a suspected cross-domain problem, or when a selective
run is under suspicion. No label, bot or comment-command system was introduced.

## 8. Evidence

All numbers below are wall time (`createdAt` → `updatedAt`) of real GitHub Actions
runs on this repository, `windows-latest`.

```text
Docs-only change — pull request, run 35364782061, head d767dd5
  Change Classification   pass      14 s
  Runtime / Python        skipped   —
  Frontend / TypeScript   skipped   —
  Desktop System / Rust   skipped   —
  Quality Gate            pass      16 s
  run wall time                      40 s

Full baseline — the CI-01/CI-02 behaviour, three consecutive runs
  35361993908  pull_request   success   wall 402 s
  35362711111  push → main    success   wall 406 s
  35359780497  pull_request   success   wall 406 s
```

```text
docs-only:  ~405 s → 40 s   ≈ 90 % reduction
```

This is a *structural* result: the docs-only run genuinely skipped all three
domain jobs and still produced a `Quality Gate` check run.

**How the docs-only observation was obtained.** A pull request whose *entire* diff
is documentation cannot target `main` before CI-03 is merged — the workflow that
classifies is introduced by CI-03 itself, and the pre-CI-03 workflow on `main` has
no classifier. The observation was therefore taken on a temporary stacked pull
request (PR #15, base = a throwaway branch whose only difference from the CI-03
branch was the `pull_request.branches` filter, head = one added markdown file). The
classifier and gate under observation were the shipped ones. The probe branches were
deleted and the probe PR was closed unmerged; nothing from it reached `main`.

A frontend-only, Rust-only or agent-tooling-only routing has **not** been observed
on GitHub Actions. Those rows of §3 are proven by unit test
(`tests/unit/ci/test_classify_changes.py`), not by a measured run, and this document
does not claim a speed-up for them. The rename, delete, multi-commit and
merge-base derivations of §4 are proven against real *temporary* Git repositories
by `tests/unit/ci/test_git_backed_diff.py` — no test touches this repository's
checkout.

## 9. Where the code lives

```text
tools/ci/classify_changes.py            routing: path set + event → classification
tools/ci/evaluate_gate.py               decision: classification + job results → pass/fail
tests/unit/ci/test_classify_changes.py  the routing matrix + fail-closed paths
tests/unit/ci/test_evaluate_gate.py     required / authorised / red / missing matrix + CLI
tests/unit/ci/test_workflow_contract.py the invariants §6 lists, pinned
tests/unit/ci/test_git_backed_diff.py   rename / delete / multi-commit / merge-base, real temp repos
```

Both modules are standard library only and are type-checked by the `Runtime /
Python` job (`mypy runtime tools/agent tools/ci`). Changing a routing rule means
changing `classify_changes.py` and its test in the same commit — and since
`tools/ci/**` is itself a FULL-CI path, that change cannot be validated by a
selective run.
