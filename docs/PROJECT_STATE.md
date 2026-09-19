# CAN-X — Current Project State

> **Document**: `docs/PROJECT_STATE.md`
> **Purpose**: Compact current-state snapshot — the mandatory startup context for every agent task.
> **Updated**: 2026-09-19 (CI-03 — Change-Aware Tiered Quality Gate — Final Acceptance: PASS ·
> Status: CLOSED after protected integration: accepted head 6edac09, PR #16, merge 11d362e,
> post-merge FULL main CI 35370751537 SUCCESS. DOC-GOV-01 — Documentation & Agent Context
> Governance — Final Acceptance: PASS · Status: CLOSED. **V0.3-12 — Desktop Project Open
> Foundation — IMPLEMENTED · SELF-VERIFIED · AWAITING INDEPENDENT ACCEPTANCE**, delivered
> together with the **first real AGENT-02 native pilot**: 1 Main Agent + 3 native Tianshu
> workers EXECUTED and self-verified on the same branch.)
> **Current Phase**: V0.3 — Professional Trace & DBC Foundation
> **Owner**: CAN-X sole author · **Model**: Document-Driven Development

This answers **"where is CAN-X now?"**, not "what happened at every step". The full narrative —
review rounds, remediations, RED→GREEN records, CI logs and merge records — is preserved verbatim
in `docs/project-state/`; per-phase acceptance evidence is in `docs/acceptance/` (§13). Task routing
is in `docs/CONTEXT_INDEX.md`; loading rules in `docs/engineering/AGENT_CONTEXT_GOVERNANCE.md`.

**Order of reading:** §3 and §5 orient fastest. §7 says what exists today. §9 before touching
DBC / desktop / hardware territory. §12 before implementing.

---

## 1. Project Identity

**CAN-X** is an **Agent-native Professional CAN Engineering Workbench**: a Professional CAN
Workbench + Engineering Automation Runtime + CAN Engineering Agent + Engineering Memory.

It is **not** a CAN-Space rename, CAN-Space V2, a CanLab fork continuation, or a PyQt UI refactor.
**CAN-Space is in a Frozen Reference State** — usable only as legacy / algorithm / behavior
reference and as a candidate code source, subject to `AGENTS.md` §5–§7 and `SPEC.md`. Full product
intent is in `PRD.md`.

---

## 2. Source of Truth

| Document | Responsibility |
| --- | --- |
| `PRD.md` | Product intent, scope, users, version direction |
| `SPEC.md` | Technical architecture, data structures, safety boundaries, technology constraints |
| `AGENTS.md` | AI / agent development execution rules |
| `docs/PROJECT_STATE.md` | Where CAN-X is now (this file) |
| `docs/CONTEXT_INDEX.md` | Context router — which authority to load per task. **Never an authority itself** |
| `docs/engineering/AGENT_CONTEXT_GOVERNANCE.md` | How agent context is layered and loaded |
| `docs/project-state/` | How CAN-X got here (lossless history archive, read on demand) |
| `docs/acceptance/` | Per-phase acceptance evidence (from V0.3-10 onward) |
| `docs/ADR/` | Architecture decision records |

Authority order is fixed: `PRD` = product intent · `SPEC` = technical contract · `AGENTS` =
execution rules · `PROJECT_STATE` = current state. `CONTEXT_INDEX` **routes**; it never overrides
any of them.

```text
code vs SPEC → fix code    requirement change → update PRD first
architecture change → update SPEC or create an ADR first
```

`docs/REUSE_LEDGER.md` does not exist yet — created on the first actual legacy-code reuse
(`AGENTS.md` §6).

---

## 3. Frozen Architecture Invariants

These hold today. Do not violate them; do not assume a superseded rule still holds.

**Layer separation** — `UI ≠ CAN Runtime ≠ Agent Runtime ≠ Storage`. The Tauri / React desktop does
**not** own the CAN Runtime data plane, the recorder, the Agent executor, or any protocol state
machine. The Python Runtime is the authority for CAN / DBC / query logic, and `cantools` is confined
to the single DBC parser adapter (`runtime/canx/dbc/parser.py`).

**Realtime data path** — `CAN → Runtime → batching → Binary WebSocket (MessagePack) → Worker →
bounded frontend store → virtualized UI`. React does **not** hold the full CAN history (no
`setFrames([...all, frame])`). Capture / record / timestamp / sequence correctness outranks UI
responsiveness: under pressure, UI FPS is reduced — never Recorder data.

**Domain models** — `Frame` is a canonical domain object/schema; APIs do not return `pandas.Series`,
`QTableWidgetItem`, or a UI-invented shape. Timestamps are never a bare `float`: the core Frame keeps
`hardware_timestamp` / `host_timestamp` / `normalized_timestamp` / `clock_domain` /
`timestamp_quality`. Device capabilities are read via `adapter.capabilities`, never
`if brand == "Vector"`.

**Persistence** — `SQLite = metadata / project state` · `Parquet = large datasets` · `DuckDB =
analytical queries`.

**Error contract** — every cross-API error uses the structured five-field envelope `code` /
`message` / `details` / `recoverable` / `source`. Domain semantic validation → `400` (`query.*` /
`project.*` / `dbc.*`); framework request schema/type validation → `422`
(`api.request_validation_failed`, `source = api`). The two are never downgraded into each other.

**DBC / project identity** — the external selected DBC's **absolute filesystem path must not** enter
the renderer, the Tauri IPC payload, or the Runtime HTTP contract; only `source_name` (basename) +
raw bytes cross those boundaries. `projectPath` and the external source DBC path are different
concepts, and `projectPath` is always an explicit parameter — there is **no** global current project,
**no** global active DBC, and **no** channel ↔ DBC binding. A DBC asset's canonical content has
exactly one source of truth, the `.dbc` file; SQLite stores only registry / provenance / integrity
metadata.

**Safety (implemented as the Runtime Safety Kernel — SAFETY-01, §17).** All real TX must pass
`TX Policy → ARM State → Permission → Approval → Adapter.send → Audit`; there is no bypass path. The
Runtime Safety Kernel (`runtime/canx/safety/`) is the **policy authority** (S2), and dangerous
operations — `TX` / `DIAGNOSTIC_MUTATION` / `ACTUATION` / `ECU_MUTATION` / `CRITICAL` — default to
`DENY` (S1). An operation is authorised only when the risk is classified, the runtime is armed within
a scoped and unexpired `ArmScope`, the session holds the matching capability, and a conforming
approval is presented and consumed. A caller may **request** danger and may not arm the runtime,
issue an approval, widen its own permission set, or reach `Adapter.send` (S3, S4); Agent-generated
Python runs in an isolated Sandbox Worker with no raw CAN device handle, no direct `python-can` bus,
no TX credentials and no unrestricted host filesystem. Authority-increasing actions commit only when
their **complete audit transaction** succeeds (S20); audit reference fields are typed
**identifiers**, never caller free text (S21); the emergency stop is an **epoch boundary**, not a
pause (S22–S24), and its metadata is non-authoritative — an unrecordable reason never vetoes the
reduction (S25). There is **no real TX path in this tree**, asserted by regression test
(`tests/unit/safety/test_device_transmit_boundary.py`) rather than promised here.

The frozen set **S1–S25**, the risk taxonomy and every contract live in
`docs/architecture/SAFETY_ARCHITECTURE.md` — that file is the safety authority, not this summary.

**Forbidden legacy patterns** — no PyQt6 / Qt / QML, no Electron, no .NET, no all-Rust backend
rewrite; no giant mutable singleton `AppState`; no `frames = pandas.DataFrame` as canonical model.
Rust is used only for Tauri, OS integration, sidecar lifecycle, capabilities, window management and
proven performance bottlenecks.

**Performance & scale priorities (frozen ordering)** — `1 CAN RX integrity · 2 Recorder integrity ·
3 Timestamp integrity · 4 Safety · 5 Runtime responsiveness · 6 UI interaction · 7 Trace refresh ·
8 Plot refresh · 9 animation`. Under pressure the UI degrades — never the capture / recorder path.
V1 targets 10–50 GB of engineering data; the architecture reserves 100 GB+ via streaming, chunking,
indexing, query-on-demand and downsampling. Never materialize an entire log into a `DataFrame`.

---

## 4. Technology Baseline

```text
Desktop   Tauri 2 + Rust (bounded scope)
Frontend  React 19 · TypeScript · Vite · Dockview · Zustand · TanStack Query · TanStack Virtual ·
          ECharts · Monaco Editor · i18next
Runtime   Python 3.13 · FastAPI · python-can · cantools 44.0.0
IPC       Control: HTTP + JSON · Realtime: WebSocket + binary MessagePack
Data      SQLite (metadata) · Parquet (pyarrow 21.0.0) · DuckDB 1.5.5 (analytics)
Packaging Windows: scripts\package-windows.cmd → canx-runtime.exe (PyInstaller sidecar) + MSI
```

Do not swap these without a SPEC change.

---

## 5. Current Phase

```text
Current Phase:  V0.3 — Professional Trace & DBC Foundation
Latest CLOSED numbered step:    V0.3-11 — Project Runtime Read Model API Foundation   PASS · CLOSED
Previous CLOSED numbered step:  V0.3-10 — Read-Only DBC Workspace UI Foundation      PASS · CLOSED
Current numbered step:          V0.3-12 — Desktop Project Open Foundation
                                IMPLEMENTED · SELF-VERIFIED · AWAITING INDEPENDENT ACCEPTANCE
                                branch feature/v0.3-12-desktop-project-open-foundation, from
                                main 451352f · evidence docs/acceptance/v0.3-12-…md

Closed engineering infrastructure (not numbered product phases) — independently accepted, CLOSED:
  Maintenance CI-01   Continuous Integration Baseline Foundation .............. §14
  Maintenance CI-02   Protected Integration Gate Foundation ................. §15
  SAFETY-01           Safety Architecture & Risk Control Foundation ......... §17  (S1–S25 frozen)
  AGENT-01            Multi-Agent Orchestration Foundation .................. §18
  RELIABILITY-01      Capture / DataSession Finalization Timing ............. §19
  DOC-GOV-01          Documentation & Agent Context Governance .............. §12
                      PASS · CLOSED (independent re-acceptance, head 38914b6 → main e183c53)
  AGENT-CONTEXT-PROTECTION  Context Governance Protected Truth Surface ...... §12
                      PASS · CLOSED (external acceptance, head cd44abc → main d652b4c)
  AGENT-02-PREP-01    Readiness Audit + Real Pilot Design ................... §12
                      PASS · CLOSED (external acceptance, head cd44abc → main d652b4c)
  AGENT-02-NATIVE-HARNESS-PIVOT  Native Tianshu Harness Orchestration ....... §12
                      PASS · CLOSED (external acceptance, head cd44abc → main d652b4c)
                      Real AGENT-02 pilot: **EXECUTED**, self-verified, awaiting independent
                      acceptance · V0.3-12: **IMPLEMENTED**, awaiting independent acceptance
                      (both: docs/acceptance/v0.3-12-…md, §20)
```

Closed-phase ledger — accepted heads, merges and post-merge CI, kept for traceability (full
narrative in the archive, §13):

```text
Phase           Verdict                        Accepted head / merge / post-merge CI
V0.3-10         PASS · CLOSED (independent)    evidence docs/acceptance/v0.3-10-…md
V0.3-11         PASS · CLOSED (independent)    post-fix regression on a18a4f3; evidence v0.3-11-…md
CI-01           PASS · CLOSED (independent)    run IDs in §14
CI-02           PASS · CLOSED (independent)    ruleset main-protected-integration (23600372), active
SAFETY-01       PASS · CLOSED (independent)    main c05debf9; post-merge main CI 35295673078 green
AGENT-01        PASS · CLOSED (independent)    head 2eab62b; PR #7 → main 951e202; post-merge CI 35315019912
RELIABILITY-01  PASS · CLOSED (independent)    head 07a95f6; PR #10 → main d22e989; post-merge CI 35349031072
DOC-GOV-01      PASS · CLOSED (independent)    head 38914b6; PR #13 → main e183c53; post-merge CI 35361083707
```

The `Final Acceptance: PASS` / `Status: CLOSED` verdicts here are **project-owner / independent
acceptance results**, not agent conclusions. A development agent implements, self-verifies and
records `Awaiting independent acceptance`; it never writes `Final Acceptance: PASS` for its own work
(§11). Every closed phase had at least one independent review round; every `NOT PASS` verdict and its
remediation is preserved verbatim in the archive (§13).

---

## 6. Completed Phase Summary

All numbered phases V0.1 → V0.3-11 are CLOSED. Full detail: V0.1 → V0.3-09 in
`docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_V0.3-09.md`; V0.3-10 onward in `docs/acceptance/`;
maintenance-phase narrative in `docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_RELIABILITY-01.md`.

```text
V0.1    Technology Proof ..................... Conditional PASS (2026-09-15)
V0.1.1  Acceptance Hardening ................. Conditional PASS → V0.2 (ADR 0001)
V0.2-01 Project Foundation ................... PASS
V0.2-02 Data Session & Parquet Persistence ... PASS
V0.2-03 DuckDB Query Foundation .............. PASS (timestamp pruning OFF)
V0.2-04 Project-Backed Capture Persistence ... PASS (after FINAL / FINAL-2 / FINAL-3)
V0.3-01 Trace Query & Filtering .............. PASS · CLOSED
V0.3-02 DBC Domain Foundation ................ PASS · CLOSED (legacy reuse = none)
V0.3-03 DBC Project Registry & Persistence ... PASS · CLOSED (first NOT PASS → FINAL)
V0.3-04 DBC Decode Foundation ................ PASS · CLOSED
V0.3-05 DBC Runtime Read & Decode API ........ PASS · CLOSED (1×P1 → FINAL)
V0.3-06 Safe DBC Content Import API .......... technical PASS; process CONDITIONAL PASS
V0.3-07 Tauri Safe DBC File Bridge ........... PASS · CLOSED
V0.3-08 Desktop DBC Import Orchestration ..... PASS · CLOSED
V0.3-09 Desktop DBC Read Model Client ........ PASS · CLOSED
V0.3-10 Read-Only DBC Workspace UI ........... PASS · CLOSED (independent)
V0.3-11 Project Runtime Read Model API ....... PASS · CLOSED (independent)
```

What each phase added is in §7 (capability matrix) and §8 (architecture). V0.3-07 preceded
V0.3-06-FINAL-2's formal close — a recorded, later reconciled process anomaly.

---

## 7. Current Capability Matrix

Verified against the current working tree (Runtime routers, desktop modules, Agent tools).

```text
✅ Project foundation · Project Runtime read-model API (HTTP) · SQLite project metadata ·
   Parquet session persistence · DuckDB query foundation · Trace query / filter (HTTP)
✅ Safety Kernel foundation (risk taxonomy, caller model, ARM state machine, scope, capability
   permissions, approvals, policy decision engine, audit contract, audit-safe identifier
   contract, emergency stop contract) — authorises only, no executor
✅ Realtime stream (virtual CAN → batching → binary WebSocket → Worker → bounded frontend store →
   virtualized Trace/Plot) · Virtual CAN capture · Recorder (project-backed persistence) ·
   Agent tool registry + trace.summary
✅ DBC canonical domain · DBC parser (cantools adapter) · DBC project registry · DBC project
   persistence · DBC decode (Runtime domain) · DBC Runtime HTTP read/decode API · Safe DBC content
   import (HTTP) · Desktop native DBC file bridge (Tauri) · Desktop DBC import orchestration ·
   Desktop DBC read-model client · DBC Workspace UI (read-only) · DBC asset browser UI (read-only)
✅ Desktop project open foundation (V0.3-12) — Desktop Runtime project read-model client
   (`inspectProject`) · native Tauri project-directory bridge (`select_project_directory`, zero
   caller-supplied filesystem arguments) · typed TS directory bridge · project-open orchestration ·
   cross-boundary integration test. No Project Picker UI, no global current project, no recent
   projects, no project mutation. None of these has a production UI caller yet — a foundation, in
   the same state `orchestration/dbc-import.ts` was left in.

❌ DBC editor · active DBC · channel ↔ DBC binding · Trace decoded signal columns · Plot signal
   binding · Frontend decode-batch integration · Agent dbc.* tools
```

Runtime / hardware / platform capability that is **not** verified is listed once, in §9.

---

## 8. Current Architecture / Boundary Summary

**Python Runtime** (`runtime/canx/`) — the engineering-logic authority:

```text
canx/project/   project model, manifest, SQLite schema + migration
canx/data/      data session model, bounded Parquet segment writer
canx/query/     structured bounded query (DuckDB), typed query errors
canx/recorder/  ProjectRecorder, DataSessionWriter failure semantics
canx/capture/   capture pipeline, virtual adapter
canx/devices/   adapter abstraction (python-can)
canx/dbc/       canonical domain, parser (cantools boundary), service, registry, asset model
canx/transport/ MessagePack realtime codec
canx/agent/     tool registry + trace.summary (ToolRisk aliases canx.safety.risk.RiskLevel)
canx/safety/    Safety Kernel — authorises, does not execute (SAFETY-01, §17)
canx/api/       FastAPI app + routers (app, trace, dbc, project, frame, errors)
canx/runtime/   RuntimeService (capture lifecycle, status truthfulness)
```

**Runtime HTTP surface:** `GET /health` · `GET /runtime/status` · `POST /capture/start` ·
`POST /capture/stop` · `GET /metrics` · `POST /tools/execute` · `POST /runtime/shutdown` ·
`WS /stream/frames` · `GET /project/inspect` · `POST /trace/query` · `POST /trace/summary` ·
`POST /dbc/assets` · `GET /dbc/assets` · `GET /dbc/assets/{asset_id}` ·
`GET /dbc/assets/{asset_id}/database` · `POST /dbc/assets/{asset_id}/decode` ·
`POST /dbc/assets/{asset_id}/decode-batch`

**Desktop** (`apps/desktop/src/`): `desktop/` OS / Tauri IPC boundary (`dbc-file-bridge.ts`) ·
`runtime/` Runtime HTTP clients (`runtime-client`, `capture-client`, `realtime-stream`,
`decode-frame-batch` [MessagePack], `dbc-client` [read + write]) · `orchestration/` flow coordination
(`dbc-import.ts`) · `components/` UI panels (`workspace/DockWorkspace`, `trace`, `plot`, `dbc`) ·
`workers/` WebSocket MessagePack worker · `smoke/` build-gated harness (not in production builds).

The desktop renders a Dockview workspace with Trace, Plot, a placeholder Agent panel and a read-only
**DBC** panel. The DBC read-model client (`runtime/dbc-client.ts`) has a real production caller —
`components/dbc/` — while the DBC **import** client (`orchestration/dbc-import.ts` +
`desktop/dbc-file-bridge.ts`) still has no production caller and is exercised only by tests and the
smoke harness.

**Persisted schema baseline:** `DATABASE_SCHEMA_VERSION = 3` (legacy floor 1) ·
`FRAME_PARQUET_SCHEMA_VERSION = 1` · Frame canonical schema unchanged (timestamp provenance
preserved) · `project.json` manifest schema unchanged · DBC content: the `.dbc` file is the single
source of truth, SQLite stores only `dbc_assets` registry metadata.

**Typed error codes** (five-field envelope, `source` = domain):

```text
project.*   validation / identity / schema / close failures
data.*      session / segment / storage / integrity failures
query.*     filter / pagination / segment / execution failures
capture.*   configuration / recording-target / finalization failures
dbc.*       file_not_found · read_failed · unsupported_format · decode_failed · parse_failed ·
            invalid_model · invalid_asset · asset_not_found · asset_storage_failed ·
            asset_registry_failed · asset_integrity_failed · source_changed · message_not_found ·
            frame_type_mismatch · payload_too_short · decode_unsupported · signal_decode_failed
api.*       request_validation_failed (422, source = api)
```

**DBC import transport bound:** `MAX_DBC_IMPORT_BYTES = 16 MiB`, shared (by a cross-boundary test)
between the Rust desktop bridge and the Runtime HTTP guard. A transport bound, not a domain limit.

---

## 9. Known Limitations / NOT VERIFIED

Still in force — do not silently drop these when reading only history.

```text
real CAN hardware (Vector/PCAN/Kvaser/ZLG)     NOT VERIFIED
macOS real-machine validation                  NOT VERIFIED
windowed desktop launch                        NOT VERIFIED (from V0.1.1)
realtime/UI latency                            PARTIAL (worker decode measured; UI latency NOT VERIFIED)
10–50 GB engineering dataset                   NOT VERIFIED
real CAN TX safety (SAFETY-01)                 NOT VERIFIED — no TX path exists
real vehicle behaviour / UDS mutation /
  hardware fail-safe / vehicle qualification   NOT VERIFIED — no dangerous capability
emergency stop against real hardware           NOT VERIFIED — contract only
device reconnect / channel change / transport
  fault auto-disarm (SAFETY-01)                CONTRACT ONLY — no device lifecycle
audit durability across restart / tamper
  evidence (SAFETY-01)                         NOT IMPLEMENTED — in-memory trail
external execution atomic with the decision    NOT IMPLEMENTED — the kernel's lock covers only
                                               its own state
multi-agent behaviour at real scale            PARTIAL — the first real AGENT-02 pilot ran
                                               (1 Main + 3 native workers); the V0.3-12-FIX-1
                                               section of
                                               docs/acceptance/v0.3-12-desktop-project-open-foundation.md
                                               carries the measured concurrency evidence;
                                               scale beyond 1 + 3 and long-duration context
                                               endurance stay NOT VERIFIED
ruleset strict_required_status_checks_policy   false — recorded gap (ADR-0002 alt. A)
```

Desktop / Runtime / DBC limitations still open:

- **Near-limit Desktop HTTP serialization / renderer starvation (P2, unresolved).** Importing a DBC
  at the transport limit (~16 MiB → ~22 MB Base64 body) blocks the renderer main thread for
  ≈51–53 ms (≈22.5 ms of it synchronous `JSON.stringify`). User-initiated, one-shot, off the realtime
  path; measured relative to baseline, not an absolute cross-machine threshold. Not fixed.
- Timestamp segment pruning remains **disabled** (V0.2-03 decision) until persistence can provide
  reliable per-segment min/max timestamp metadata.
- `max_frames_per_segment` defaults to 65536 (a foundation constant, not a SPEC value).
- DBC registry and filesystem cannot form a single transaction: a crash between "asset file written"
  and "registry row committed" can leave an unregistered orphan file. Registry state is
  authoritative; unregistered files under `project/dbc/` are not trusted. No automatic cleanup.
- `dbc/`-as-symlink rejection is skipped on Windows (needs extra privileges); path-escape evidence
  comes from malicious `relative_path` tests instead.
- DBC client-side validation is **contract-shape** validation, not a domain-semantics replica (e.g.
  `signal.length` only requires a non-negative integer; uniqueness and `frame_id` / `is_extended`
  relations are Runtime-domain guarantees).
- Capture / DataSession finalization timing is resolved by RELIABILITY-01 (§19); its residual
  uncertainty is recorded in `docs/engineering/RELIABILITY_CAPTURE_FINALIZATION.md`.

Two boundaries stay attached to the CI gate: CI is an **automatic quality gate**, not a substitute
for Independent Acceptance (a green CI never grants `Final Acceptance: PASS`); and CI runs on
**`windows-latest` only**, so macOS, Linux and real CAN hardware stay `NOT VERIFIED`. Until
Maintenance CI-01 the repository had no `.github/` at all, so every earlier test number recorded
anywhere is a **local run**, not a CI result.

---

## 10. Deferred Capabilities

Not implemented, in scope for later increments (actual repo state, not aspiration):

```text
DBC import button / project picker / project create-open UI · DBC editor · active DBC ·
channel ↔ DBC binding · Trace decoded signal columns / live decode UI · decode-batch frontend
integration · Plot signal binding · asset rename / delete / replace · drag & drop / multi-file
import · Rust DBC domain · arbitrary renderer filesystem access · Agent dbc.* tools ·
durable / tamper-evident Safety audit sink · an execution path that crosses the kernel (TX,
replay, injection, diagnostics) · a general safety "authority epoch" beyond the emergency-stop
case · enabling strict_required_status_checks_policy on the main ruleset (ADR-0002 alt. A) ·
a cross-platform CI matrix
```

Not in V0.x at all: account system, subscription, license server, cloud sync.

---

## 11. Current Development Rules

The binding rules are in `AGENTS.md` (architecture, safety, testing, scope, naming). The startup read
policy is `AGENTS.md` §2, with routing in `docs/CONTEXT_INDEX.md` and layer definitions in
`docs/engineering/AGENT_CONTEXT_GOVERNANCE.md`. Key points for every task:

- One coherent increment per task / commit; no "design ten modules then write thousands of lines then
  test" (AGENTS.md §12–13).
- Test before claim — if you did not run it, write `NOT VERIFIED` (§14).
- No fake compatibility — no real Mac / real hardware claims without evidence (§15).
- Capture integrity precedes UI optimisation (§20).
- Never weaken tests, skip tests, or lower assertions to make CI green (§43).
- Do not overbuild; do not commit placeholder production code (§45–46).
- Protection order: architecture first, then correctness, then speed (§50).

### PROJECT_STATE compaction rule

Once a phase is formally accepted: `working phase section → Final Acceptance: PASS → detail archived
/ acceptance report finalized → PROJECT_STATE.md keeps only a concise summary + a reference`. Long
implementation logs, raw test stdout, RED→GREEN dumps and multi-round FINAL detail for CLOSED phases
must **not** permanently accumulate here. DOC-GOV-01 applied this to the SAFETY-01 / AGENT-01 /
RELIABILITY-01 sections that had accumulated; their text moved verbatim into `docs/project-state/`,
it was not deleted (§13).

### Acceptance & verification model

An agent **does not** self-grant a phase `Final Acceptance: PASS` / `Status: CLOSED`. It implements,
self-verifies, and records `Awaiting independent acceptance`. The acceptance verdict is an external
result (the project owner / independent reviewer). When an agent writes an acceptance result into
this document it must attribute it, and must not present its own conclusion as that verdict.

---

## 12. Immediate Next Action

```text
Just DELIVERED — V0.3-12, Desktop Project Open Foundation
  Developer status: IMPLEMENTED · SELF-VERIFIED · AWAITING INDEPENDENT ACCEPTANCE · NOT MERGED
  Branch feature/v0.3-12-desktop-project-open-foundation (from main 451352f). No `Final
  Acceptance: PASS` is claimed. Evidence: docs/acceptance/v0.3-12-desktop-project-open-foundation.md

Also DELIVERED — AGENT-02, the first real native multi-agent pilot
  Developer status: EXECUTED · SELF-VERIFIED · AWAITING INDEPENDENT ACCEPTANCE
  1 Main Agent + 3 native Tianshu workers (native default maxWorkers = 3, NOT reconfigured).
  Filesystem model MEASURED, not assumed: SHARED worktree — no per-worker worktree appeared.
  302 s of verified three-worker concurrency. Ownership re-derived from Git: overlap NONE,
  forbidden-path violations NONE, every touched path classifies C1. Same evidence file.

Earlier CLOSED — CI-03, Change-Aware Tiered Quality Gate
  Final Acceptance: PASS · Status: CLOSED (external / independent reviewer) · CI-03-FIX-1 accepted
  at head 6edac0982c78a6e2cc1c17d63f4937269db552fd · PR #16 · merge 11d362e
  → `main` 11d362efeb78d369b0ab5a5dbb6f2350186cc0a9 (protected merge, merge commit, no bypass).
  Accepted CI: 35368535974 (classification full; Runtime / Frontend / Rust / Quality Gate SUCCESS).
  Post-merge FULL main CI: 35370751537 attempt 1 SUCCESS on 11d362e (classification full;
  pytest 2715 passed / 6 skipped · ruff clean · mypy 98 files · frontend 162 · Rust 28 unit + 3 integration).
  CI control plane only — no product / runtime / safety / schema / API / dependency change, and the
  ruleset is unchanged. CI-03 first returned NOT PASS (P0: 0 · P1: 2 · P2: 2) and was closed by
  CI-03-FIX-1, which routed `runtime/canx/api/app.py` to Runtime + Frontend + Rust, routed
  `src-tauri/src/**` and the TypeScript IPC bridges to Frontend + Rust, made `full_required` (and the
  classification label) fail closed, and added real temporary-repo rename/delete/multi-commit/merge-base
  tests. Design + evidence: docs/engineering/CI_TIERED_QUALITY_GATE.md; policy: INTEGRATION_POLICY.md §18.

Earlier CLOSED — DOC-GOV-01, Documentation & Agent Context Governance
  Final Acceptance: PASS · Status: CLOSED (external / independent reviewer) · PR #13 · head 38914b6
  → `main` e183c53 (protected merge, no bypass) · post-merge CI 35361083707 SUCCESS on attempt 1.
  Docs-only: no product code, runtime, safety, schema, API, dependency or CI-semantics change.

Earlier CLOSED — AGENT-CONTEXT-PROTECTION, Context Governance Protected Truth Surface
  Final Acceptance: PASS · Status: CLOSED (external / independent reviewer) · PR #18 · head cd44abc
  → `main` d652b4c (protected merge, no bypass) · post-merge main CI 35415938987 SUCCESS on attempt 1
  (classification full).
  docs/CONTEXT_INDEX.md and docs/engineering/AGENT_CONTEXT_GOVERNANCE.md now enter
  .agent/config.json protected_paths **and** public_truth_paths — two independent invariants over one
  surface, exact paths only (no `docs/**` widening). Regression coverage:
  tests/unit/agent_tools/test_context_protection.py (A1–A9) and
  tests/integration/test_agent_context_protection.py (git-backed transient-touch: a modify-then-restore
  leaves the net diff clean and is still refused). The verdict is external, never self-asserted.

Earlier CLOSED — AGENT-02-PREP-01, Readiness Audit + Real Pilot Design
  Final Acceptance: PASS · Status: CLOSED (external / independent reviewer) · PR #18 · head cd44abc
  → `main` d652b4c (protected merge, no bypass) · post-merge main CI 35415938987 SUCCESS on attempt 1.
  Readiness audited R01–R50 against real executable behaviour: 48 READY · 2 PARTIAL matrix rows (R29 the
  handoff write half is untested and unwired; R49 the stale local `main` ref can refuse a legitimate
  cleanup) · 4 P2-like observations (O-1…O-4) · 0 BLOCKED · 0 NOT IMPLEMENTED · no P0/P1-like blocker.
  Pilot designed as 1 Main Agent + 2 workers, C0 verified with the shipped classifier, ownership-disjoint,
  both contracts `PLANNED` in .agent/tasks/. See docs/engineering/AGENT_02_READINESS.md.
  REAL PILOT NOT STARTED.

Earlier CLOSED — AGENT-02-NATIVE-HARNESS-PIVOT, Adopt Tianshu Native Multi-Agent Orchestration
  Final Acceptance: PASS · Status: CLOSED (external / independent reviewer) · PR #18 · head cd44abc
  → `main` d652b4c (protected merge, no bypass) · post-merge main CI 35415938987 SUCCESS on attempt 1
  (classification full).
  NATIVE TIANSHU HARNESS SELECTED FOR ORCHESTRATION · REAL PILOT NOT STARTED.
  The execution environment is itself a multi-agent orchestrator (Tianshu 3.22.0, product
  `tianshu-desktop`, mode `code`): native `/team` · `/scout` · `/council`, worker sessions, parallel
  scheduling, context isolation and model routing — verified by reading the installed runtime, not the
  product description. CAN-X therefore adopts the host Harness for execution/orchestration and does NOT
  implement a competing Agent Runtime; CAN-X retains repository identity, Task ownership policy,
  protected/public-truth/safety path classification, conflict policy, Git-backed handoff evidence,
  stale-base protection, the Quality Gate, protected integration and the independent acceptance
  boundary. AGENT-01 is not deleted: its components are classified KEEP / ADAPT / HARNESS-OWNED /
  DEPRECATE-LATER, and no large deletion is performed in this task. The pilot is re-specified to run
  through native `/team` (1 Main Agent + 2 native workers) while still using the C0, disjoint
  .agent/tasks/AGENT-02-{A,B}.json contracts as the worker governance brief.
  Long-duration context endurance: NOT VERIFIED. Integrated via protected PR #18 (no bypass).
  Decision: docs/ADR/0003-native-agent-harness-orchestration.md ·
  Plan: docs/engineering/AGENT_02_PILOT_PLAN.md · Audit: docs/engineering/AGENT_02_READINESS.md §3.

Architecture in force — native orchestration boundary:
  Tianshu Harness = agent runtime · orchestration · worker spawning · scheduling · context isolation ·
                    model routing · native /team · /scout · /council
  CAN-X          = repository governance · Task ownership · protected/public-truth/safety classes ·
                    Git evidence · stale-base protection · Quality Gate · protected integration ·
                    independent acceptance boundary

Immediate next engineering task — none. AGENT-02's real native-harness pilot has been EXECUTED.
  Status: **EXECUTED · SELF-VERIFIED · AWAITING INDEPENDENT ACCEPTANCE**. The pilot's own
  governance closure — the native execution mode, the three real handoffs and their
  integration verdicts, and the concurrency evidence — is addressed by **V0.3-12-FIX-1** on
  the same branch. It ran **1 Main Agent + 3 native Tianshu
  workers** — the host's default worker concurrency, NOT the `1 + 2` this plan originally
  designed, and the Harness concurrency configuration was not modified. The executed-run record
  is `docs/engineering/AGENT_02_PILOT_PLAN.md` §15; the evidence is
  `docs/acceptance/v0.3-12-desktop-project-open-foundation.md`.
  Dispatch used the harness's **native worker delegation**; the literal `/team` slash command was
  not invoked from the pilot session. Recorded as it happened.
  **Historical, not current:** the two `Earlier CLOSED` entries above that say
  `REAL PILOT NOT STARTED` / `pilot DESIGNED only` / `/team NOT used` describe the state at
  `AGENT-02-PREP-01` and `AGENT-02-NATIVE-HARNESS-PIVOT` close, before this pilot ran. They are
  retained as history and must not be read as the present state.
  Read first for any AGENT-02 follow-up: docs/ADR/0003-native-agent-harness-orchestration.md,
  docs/engineering/MULTI_AGENT_PROTOCOL.md, docs/ADR/0002-parallel-development-serial-integration.md,
  docs/engineering/INTEGRATION_POLICY.md, docs/engineering/AGENT_02_READINESS.md,
  docs/engineering/AGENT_02_PILOT_PLAN.md.

AWAITING INDEPENDENT ACCEPTANCE: V0.3-12 · AGENT-02 (real pilot).
NOT STARTED: CD-01.
Neither V0.3-12 nor the pilot is CLOSED, and no `Final Acceptance: PASS` is claimed for either —
an independent reviewer decides both, on a protected merge with a green Quality Gate.
Closing a phase does not begin the next one; the next phase must arrive as its own explicit brief.
```

---

## 13. Historical / Acceptance References

```text
docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_V0.3-09.md
    Lossless archive of the full PROJECT_STATE.md through the V0.3-09 close.
    md5 (pre-compaction): df3b73fb1ce51afc00ad0b63a4e84551
docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_RELIABILITY-01.md
    Lossless archive of the full PROJECT_STATE.md as it stood at the RELIABILITY-01 close,
    immediately before the DOC-GOV-01 compaction of 2026-09-18. Carries the verbatim §17
    (SAFETY-01) and §18 (AGENT-01) review / remediation narrative, the CI and merge records and
    every earlier section. Source: main e522324f7bc5202b202cef13c8f9077307e1c0ff.
    md5 (pre-compaction): f42339a3fda85298616878a9e6e30158
docs/project-state/README.md · docs/acceptance/README.md   archive + acceptance-report policy
docs/acceptance/v0.3-10-…md · docs/acceptance/v0.3-11-…md   phase evidence — PASS · CLOSED
    (V0.3-11 keeps its initial NOT PASS, V0.3-11-FINAL and FINAL-2 unedited)
docs/acceptance/v0.3-12-desktop-project-open-foundation.md   V0.3-12 + the first real AGENT-02
    native pilot — IMPLEMENTED · SELF-VERIFIED · AWAITING INDEPENDENT ACCEPTANCE
docs/engineering/RELIABILITY_CAPTURE_FINALIZATION.md   RELIABILITY-01 record (§19)
docs/CONTEXT_INDEX.md · docs/engineering/AGENT_CONTEXT_GOVERNANCE.md   context routing + layers
.github/workflows/ci.yml · docs/engineering/INTEGRATION_POLICY.md   CI baseline §14 + policy §15
docs/architecture/SAFETY_ARCHITECTURE.md   SAFETY-01 authority — S1–S25 (§17)
docs/engineering/MULTI_AGENT_PROTOCOL.md   AGENT-01 authority (§18)
docs/ADR/0001-recorder-pressure-policy.md · docs/ADR/0002-parallel-development-serial-integration.md
docs/V0.1.1_ACCEPTANCE_REPORT.md · docs/V0.1_VALIDATION_REPORT.md · docs/V0.1_TECH_VALIDATION.md
docs/V0.1.1_HANDOFF_AUDIT.md · docs/DEPENDENCIES.md   earlier reports + dependency baseline
```

---

## 14. Continuous Integration Baseline (Maintenance CI-01)

`.github/workflows/ci.yml` is CAN-X's first real CI — before it the repository had no `.github/`, so
every earlier test number is a local run. Triggers `pull_request → main`, `push → main`,
`workflow_dispatch`; `permissions: contents: read` only; superseded runs cancelled. Five jobs, all on
`windows-latest`:

```text
Change Classification   routing decision — always runs (CI-03)
Runtime / Python        pytest -q · ruff check runtime tests tools · mypy runtime tools/agent tools/ci
Frontend / TypeScript   pnpm install --frozen-lockfile · lint · typecheck · test · build
Desktop System / Rust   cargo fmt --check · clippy --all-targets --all-features --locked
                        -- -D warnings · test --locked
Quality Gate            needs all four, if: always(); fails closed on an unauthorised skip (CI-03)
```

The three domain jobs run only when the change classifier requires them; `Quality Gate` still runs on
every event and is still the only required status check
(`docs/engineering/CI_TIERED_QUALITY_GATE.md`).

Versions are never re-declared in the workflow: Python from `pyproject.toml` (pinned 3.13.15), pnpm
from `package.json`'s `packageManager` via corepack, Rust from `Cargo.lock` (`--locked`), Node
24.18.0. No `continue-on-error`, no `|| true`, no skipped or weakened test.

**Real runs** (the first is the infrastructure RED baseline — the workflow did not exist before):

```text
35217771155  pull_request       @9aff3fa  FAILURE   three jobs failed; Quality Gate failed closed
35219911643  pull_request       @75693f6  SUCCESS   1658 passed / 6 skipped · 162 frontend · 28+3 Rust
35220767729  push → main        @75693f6  SUCCESS   all four jobs success
35221583265  workflow_dispatch  @75693f6  SUCCESS   all four jobs success
```

That first run exposed three pre-existing environment defects no local run had shown; each was
reproduced locally, repaired minimally and re-run — never by skipping, deleting or weakening a test
(commits `75693f6`, `efb90cb`, `fc98f8e`).

**Change-aware since CI-03.** A docs-only change now costs ~40 s instead of ~405 s (run `35364782061`
vs `35361993908`): the three domain jobs are conditional on a tested classifier, while `Quality Gate`
is unchanged, runs on every event, and fails closed on an unauthorised skip. **CI-03-FIX-1** routed
the two real cross-domain dependencies that CI-03 missed — the Rust sidecar's consumption of the
Runtime control API (`runtime/canx/api/app.py` → Runtime + Frontend + Rust) and the Tauri IPC boundary
(`src-tauri/src/**` → Frontend + Rust; the TypeScript IPC bridges → Frontend + Rust) — and made an
unreadable `full_required` (and classification label) fail the gate rather than be defaulted away.
Rules and evidence: `docs/engineering/CI_TIERED_QUALITY_GATE.md`. The ~90 % reduction is measured for
the **docs-only** case only: Runtime-only, Frontend-only and Rust-only routings have no real GitHub
Actions benchmark, and no speed-up is claimed for them.

**What CI does not establish** — see §9. `tests/integration/test_packaged_runtime_smoke.py` (6 tests)
skips on CI (no packaged `canx-runtime.exe` staged) and runs locally after
`scripts\package-windows.cmd`.

---

## 15. Protected Integration Workflow (Maintenance CI-02)

CI-01 answered *"does CI check the code?"*; CI-02 adds the stronger boundary: **code that the required
gate has not passed cannot reach `main` through the normal process.**

> **No green required Quality Gate = no normal merge into `main`.**

**Mechanism** — a GitHub **Repository Ruleset** `main-protected-integration` (id `23600372`, target
`main`, enforcement `active`, `bypass_actors: []`), real platform enforcement, not documentation:

```text
deletion                block deleting main
non_fast_forward        block force-push to main
pull_request            require a PR; 0 required approvals (sole-author);
                        all review conversations must be resolved
required_status_checks  require the "Quality Gate" check to report `success`
                        strict_required_status_checks_policy: false   ← recorded gap (ADR-0002)
```

Only `Quality Gate` is required — it fails closed unless all three domain jobs report `success`, so
the aggregate is sufficient; its context string was read from the real check runs on `main`.

**Policy** — `docs/engineering/INTEGRATION_POLICY.md`: branch / PR flow, red-CI and missing-CI
handling, stale-PR and conflict handling, force-push and deletion policy, the break-glass policy,
agent restrictions, and the `Local Verification ≠ GitHub CI ≠ Protected Merge ≠ Independent
Acceptance` boundary.

**Verified** against the real repository, then independently accepted (P0: 0, P1: 0, P2: 2
non-blocking): protection was HTTP 404 before and the ruleset active after; a direct push to `main`
was REJECTED (`GH013`, owner not exempt); a probe PR with a failing required check could not be
merged; a merge into `main` is itself a `push → main` event and produces a fresh CI run. The two
non-blocking P2 items are a recorder-cleanup flake observed once during CI-02's review, and the
missing strict up-to-date requirement above (the gap closed by ADR-0002 alternative A, deferred).

**What CI-02 does not do** — no CD, release, packaging, signing, updater or product capability; it
starts no SAFETY-01, AGENT-01, V0.3-12 or CD work. The ruleset is repository configuration —
owner-editable by design, exactly the break-glass path in the policy — not product code.

---

```text
Level 1  Local Automated Verification          DONE
Level 2  Repository Continuous Integration     DONE
Level 3  Protected Integration Workflow        DONE
Safety Foundation (SAFETY-01)                  DONE · CLOSED (§17)
Level 4  Multi-Agent Orchestration             GOVERNANCE FOUNDATION CLOSED — AGENT-01 externally
                                               accepted (merged as 951e202); the AGENT-02 governance
                                               foundation (context protection · readiness · native
                                               harness pivot) is externally accepted and CLOSED
                                               (head cd44abc → main d652b4c); the real AGENT-02
                                               pilot is EXECUTED and SELF-VERIFIED but NOT yet
                                               accepted, so the level is not fully validated
Level 5  Controlled Delivery / Qualification   NOT STARTED
```

Every row is an external verdict, not a conclusion this document reached on its own. The Level 4 row
records that the AGENT-01 *foundation* and the AGENT-02 *governance foundation* are accepted, merged and
closed; it does **not** claim the full level is validated — the real AGENT-02 pilot (native multi-worker
dispatch under CAN-X governance) has been executed and self-verified but not independently
accepted. It ran **1 Main Agent + 3 native workers** — the pilot design named in §18 of
`AGENT_02_PILOT_PLAN.md` said 2 workers; the run used the host's native default of 3. See
`docs/acceptance/v0.3-12-desktop-project-open-foundation.md`.

---

## 17. Safety Architecture & Risk Control Foundation (SAFETY-01)

SAFETY-01 is the safety foundation that had to exist **before** CAN-X gains any capability that can
change a vehicle. It adds no product capability.

**Current status:** implementation complete · SAFETY-01-FIX-1…FIX-4 complete · independently accepted
(P0: 0, P1: 0, P2: 0) · merged to `main` as `c05debf9` (PR #4) with post-merge `main` CI run
`35295673078` green · **Final Acceptance: PASS · Status: CLOSED**. The development agent did not write
that PASS; it is the external verdict.

**What exists:** `runtime/canx/safety/` (14 modules — risk taxonomy, caller model, identifier
contract, ArmScope, ARM state machine, permission set, approvals, operation request, policy decision
engine, audit contract, emergency-stop contract, kernel, errors) and
`docs/architecture/SAFETY_ARCHITECTURE.md` (the frozen contract, invariants S1–S25). One existing
module changed: `canx/agent/tools.py`'s `ToolRisk` is now an alias of the canonical `RiskLevel`.

**What it deliberately does not add:** real CAN TX · periodic transmit · replay send · frame
injection · diagnostic requests · UDS · ECU mutation · any `POST /tx` `/inject` `/uds` endpoint · any
dangerous UI control · SQLite migration. The kernel **authorises**; it does not execute.

**Review history — four NOT PASS rounds, all remediated:**

```text
1st NOT PASS (P0: 3, P1: 2, P2: 1) → FIX-1 → S15–S19
2nd NOT PASS (P0: 1, P1: 1, P2: 1) → FIX-2 → S20–S21
3rd NOT PASS (P0: 1, P1: 1, P2: 0) → FIX-3 → S22–S24
4th NOT PASS (P0: 1, P1: 0, P2: 0) → FIX-4 → S25
final PASS   (P0: 0, P1: 0, P2: 0)
```

Every verdict, every RED → GREEN record and the merge / post-merge record are preserved verbatim in
`docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_RELIABILITY-01.md` §17; the per-fix remediation is
in `docs/architecture/SAFETY_ARCHITECTURE.md` §24–§27. The development agent did not write any PASS
verdict for this work at any point.

**Authority:** any task touching a dangerous operation (`TX` / `DIAGNOSTIC_MUTATION` / `ACTUATION` /
`ECU_MUTATION` / `CRITICAL`) must read `docs/architecture/SAFETY_ARCHITECTURE.md` directly. This
section is orientation only and never replaces it.

**NOT VERIFIED / CONTRACT ONLY** (also §9): real CAN TX safety · real vehicle behaviour · UDS
mutation safety · emergency stop against real hardware · hardware fail-safe · vehicle qualification ·
device reconnect / channel change / transport-fault auto-disarm (contract only) · audit durability
across restart and tamper evidence (not implemented — in-memory, bounded trail) · external execution
atomic with the decision. A known residue: `SafetyKernel.disarm(reason=…)` still digests strictly, so
an unencodable reason raises there instead of a typed fault; it does not veto anything (the disarm
runs before the digest) and is a diagnosability defect, not a safety one.

---

## 18. Multi-Agent Orchestration Foundation (AGENT-01)

AGENT-01 is the foundation that had to exist **before** CAN-X runs more than one agent. It adds no
product capability.

**Current status:** implementation complete · AGENT-01-FIX-1…FIX-4 complete · independently accepted
on head `2eab62bfc53867f44996dafc08c32b84fabea7fd` (P0: 0, P1: 0, P2: 0) · merged to `main` as
`951e20272211d2fd3934f4c67063985d64229d7a` through protected PR #7 · post-merge `main` CI run
`35315019912` green · **Final Acceptance: PASS · Status: CLOSED**. Real AGENT-02 pilot:
**EXECUTED** — 1 Main Agent + 3 native workers, self-verified and awaiting independent
acceptance (`docs/acceptance/v0.3-12-desktop-project-open-foundation.md`).

**What exists:** `.agent/` (task + handoff JSON schemas, examples, Main-Agent and Sub-Agent prompt
templates, `handoffs/`, `config.json`), `tools/agent/` (standard-library-only enforcement tooling),
`scripts/agent.cmd`, `docs/engineering/MULTI_AGENT_PROTOCOL.md`,
`docs/ADR/0002-parallel-development-serial-integration.md`, `INTEGRATION_POLICY.md` §17, and one
`ci.yml` mypy-scope line. No agent runtime, no provider SDK, no model name, no new dependency, no
product endpoint or UI. The real CAN-X repository is never mutated by pytest.

**The design decision (ADR-0002):** parallel development, **serial** integration — no merge queue.
Every handoff must be based on the current integration head (`tools/agent/validation.py:check_base` →
`agent.base_stale`); a task whose ownership surface reaches a safety path or whose `risk_class` is
`SAFETY_CRITICAL` is `serial_review_required`; the final readiness verdict is derived only from real
Git evidence, never from a caller-supplied object; repository-identity validation refuses an unsafe
remote **without disclosing it**.

**Review history — four NOT PASS rounds, all remediated:**

```text
1st NOT PASS (P0: 2, P1: 3, P2: 1) → FIX-1  ownership by pattern overlap; Git-backed evidence
2nd NOT PASS (P0: 2, P1: 2, P2: 1) → FIX-2  lifecycle leases; capacity; historical ownership
3rd NOT PASS (P0: 0, P1: 2, P2: 1) → FIX-3  worktree state resolution; repo identity binding
4th NOT PASS (P0: 0, P1: 2, P2: 0) → FIX-4  final authority must be real Git; no secret echo
final PASS   (P0: 0, P1: 0, P2: 0)
```

Every verdict, every RED → GREEN record and the closeout are preserved verbatim in
`docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_RELIABILITY-01.md` §18; the per-fix remediation is
in `docs/engineering/MULTI_AGENT_PROTOCOL.md` §17–§19. The development agent did not write any PASS
verdict for this work.

**Authority:** `docs/engineering/MULTI_AGENT_PROTOCOL.md` (roles, lifecycle, ownership, handoff,
conflicts, integration), `docs/ADR/0002-parallel-development-serial-integration.md` and
`docs/engineering/INTEGRATION_POLICY.md`. This section is orientation only.

**Safety boundary:** `runtime/canx/safety/**` is untouched; S1–S25 unchanged; no dangerous capability
added. `RiskClass` (development risk) is a different type from the Runtime's
`canx.safety.risk.RiskLevel` (vehicle-operation risk); the two are never conflated.

**Known limitations** (also §9): verified in simulation only (no real pilot); the ruleset's
`strict_required_status_checks_policy` is still `false` (recorded gap, ADR-0002 alternative A);
Windows-only evidence; no merge queue (deliberate, ADR-0002 alternative C).

---

## 19. Reliability — Capture / DataSession Finalization (RELIABILITY-01)

RELIABILITY-01 resolved a capture / DataSession finalization timing failure that had appeared as a
rare CI-only `DataSessionState.FAILED is not COMPLETED` on a contended runner. It is engineering
infrastructure; it changes no production Runtime behaviour, with the production cleanup default
unchanged at 1.0 s and assertion strength unchanged.

**Current status:** implementation complete · RELIABILITY-01-FIX-1 and FIX-2 complete · independently
accepted on head `07a95f675527cb14c742edc93f108f5c1260ef06` (P0: 0, P1: 0, P2: 1 PR metadata,
resolved before the merge) · merged to `main` as `d22e989910ae896d40f59e718a502993126dd055` through
protected PR #10 · 3× same-SHA stability gate runs `35341733910` / `35342314866` / `35342873157` all
SUCCESS · post-merge `main` CI run `35349031072` green · **Final Acceptance: PASS · Status: CLOSED**.
The AGENT-02 reliability prerequisite is **CLEARED**.

**Authority:** `docs/engineering/RELIABILITY_CAPTURE_FINALIZATION.md` — the authoritative record
(symptom, root cause, fix, verification, remaining uncertainty), plus test-harness hardening in three
test modules. No production Runtime change.
