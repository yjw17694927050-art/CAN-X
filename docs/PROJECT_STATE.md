# CAN-X — Current Project State

> **Document**: `docs/PROJECT_STATE.md`
> **Purpose**: Compact current-state snapshot — the mandatory startup context for every agent task.
> **Updated**: 2026-09-17 (V0.3-11 independently accepted — Final Acceptance: PASS, Status: CLOSED; Maintenance CI-01 continuous-integration baseline independently accepted — Final Acceptance: PASS, Status: CLOSED; Maintenance CI-02 protected-integration gate foundation independently accepted — Final Acceptance: PASS, Status: CLOSED, see §15; SAFETY-01 Safety Architecture & Risk Control Foundation implemented and self-verified — awaiting independent acceptance, see §17)
> **Current Phase**: V0.3 — Professional Trace & DBC Foundation
> **Project Owner**: CAN-X sole author
> **Development Model**: Document-Driven Development

This document answers **"where is CAN-X now?"** — not "what happened at every step".
Detailed history lives in `docs/project-state/`; per-phase acceptance evidence lives in
`docs/acceptance/`. See §13 for references.

**How to read this file:** §3 and §5 are the fastest orientation (boundaries + current
position). §7 tells you what exists today. §9 before you touch DBC / desktop / hardware
territory. §12 before you start implementing anything.

---

## 1. Project Identity

**CAN-X** is an:

> **Agent-native Professional CAN Engineering Workbench**

CAN-X is **not** a CAN-Space rename, CAN-Space V2, a CanLab fork continuation, or a PyQt UI
refactor. **CAN-Space is in a Frozen Reference State** — it may only be used as legacy /
algorithm / behavior reference and as a candidate code source, subject to the audit rules in
`AGENTS.md` §5–§7 and `SPEC.md`.

Long-term product shape:

```text
Professional CAN Workbench
+ Engineering Automation Runtime
+ CAN Engineering Agent
+ Engineering Memory
```

Full product intent is in `PRD.md` — do not duplicate it here.

---

## 2. Source of Truth

| Document | Responsibility |
| --- | --- |
| `PRD.md` | Product intent, scope, users, version direction |
| `SPEC.md` | Technical architecture, data structures, safety boundaries, technology constraints |
| `AGENTS.md` | AI / agent development execution rules |
| `docs/PROJECT_STATE.md` | Where CAN-X is now (this file) |
| `docs/project-state/` | How CAN-X got here (lossless history archive, read on demand) |
| `docs/acceptance/` | Per-phase acceptance evidence (from V0.3-10 onward) |
| `docs/ADR/` | Architecture decision records |

Conflict rules:

```text
code vs SPEC           → code is presumed wrong (fix code)
requirement changes    → update PRD first
architecture changes   → update SPEC or create an ADR first
```

`docs/REUSE_LEDGER.md` does not exist yet — it is created on the first actual legacy-code
reuse (see `AGENTS.md` §6).

---

## 3. Frozen Architecture Invariants

These hold today. Do not violate them; do not assume a superseded rule still holds.

**Layer separation**

```text
UI  ≠  CAN Runtime  ≠  Agent Runtime  ≠  Storage
```

- The Tauri / React desktop does **not** own the CAN Runtime data plane, the recorder, the
  Agent executor, or any protocol state machine.
- The Python Runtime is the authority for CAN / DBC / query engineering logic.
- `cantools` is confined to the single DBC parser adapter module
  (`runtime/canx/dbc/parser.py`) and does **not** cross that boundary.

**Realtime data path**

```text
CAN
→ Runtime
→ batching
→ Binary WebSocket (MessagePack)
→ Worker
→ bounded frontend store
→ virtualized UI
```

- React does **not** hold the full CAN history; no `setFrames([...all, frame])`.
- Capture / record / timestamp / sequence correctness outranks UI responsiveness. Under
  pressure, UI FPS is reduced — never Recorder data.

**Domain models**

- `Frame` is a canonical domain object/schema. APIs do not return `pandas.Series`,
  `QTableWidgetItem`, or a UI-invented shape.
- Timestamps are never a bare `float`: the core Frame keeps
  `hardware_timestamp` / `host_timestamp` / `normalized_timestamp` / `clock_domain` /
  `timestamp_quality`.
- Device capabilities are read via `adapter.capabilities`, never `if brand == "Vector"`.

**Persistence**

```text
SQLite  = metadata / project state
Parquet = large datasets
DuckDB  = analytical queries
```

**Error contract**

- Every cross-API error uses the structured five-field envelope:
  `code` / `message` / `details` / `recoverable` / `source`.
- Domain semantic validation → `400` (`query.*` / `project.*` / `dbc.*`); framework request
  schema/type validation → `422` (`api.request_validation_failed`, `source = api`). The two
  are never downgraded into each other.

**DBC / project identity**

- The external selected DBC's **absolute filesystem path must not** enter the renderer, the
  Tauri IPC payload, or the Runtime HTTP contract. Only `source_name` (basename) + raw bytes
  cross those boundaries.
- `projectPath` and the external source DBC path are **different concepts**.
- `projectPath` is always an explicit function parameter. There is currently **no** global
  current project, **no** global active DBC, and **no** channel ↔ DBC binding.
- A DBC asset's canonical content has exactly one source of truth: the `.dbc` file.
  SQLite stores only asset registry / provenance / integrity metadata.

**Safety (implemented as the Runtime Safety Kernel — SAFETY-01, §17)**

- All real TX must pass: `TX Policy → ARM State → Permission → Approval → Adapter.send →
  Audit`. No bypass path. This is now executable policy, not only prose: see
  `docs/architecture/SAFETY_ARCHITECTURE.md`.
- The Runtime Safety Kernel (`runtime/canx/safety/`) is the **policy authority**
  (invariant S2). Dangerous operations — `TX` / `DIAGNOSTIC_MUTATION` / `ACTUATION` /
  `ECU_MUTATION` / `CRITICAL` — default to `DENY` (invariant S1). An operation is
  authorised only when the risk is classified, the runtime is armed within a scoped
  and unexpired `ArmScope`, the session holds the matching capability, and a
  conforming approval is presented and consumed.
- Agent auto-executes `READ` / `COMPUTE` / `WRITE_PROJECT`; the dangerous levels are
  Runtime-gated and never bypassed on AI request. An Agent, script or automation rule
  may **request** any operation and may not arm the runtime, issue an approval, widen
  its own permission set or reach `Adapter.send` (invariants S3, S4).
- Agent-generated Python runs in an isolated Sandbox Worker with no raw CAN device
  handle, no direct `python-can` bus, no TX credentials, no unrestricted host
  filesystem. The kernel exposes no execution primitive a sandbox could reach.
- There is **no real TX path in this tree**. The absence is asserted by regression
  test (`tests/unit/safety/test_device_transmit_boundary.py`) rather than promised
  here, so adding one cannot happen quietly.

**Forbidden legacy patterns**

- No PyQt6 / Qt / QML, no Electron, no .NET, no all-Rust backend rewrite.
- No giant mutable singleton `AppState`; no `frames = pandas.DataFrame` as canonical model.
- Rust is used only for Tauri, OS integration, sidecar lifecycle, capabilities, window
  management, and proven performance bottlenecks.

**Performance & scale priorities (frozen ordering)**

```text
1 CAN RX integrity   2 Recorder integrity   3 Timestamp integrity   4 Safety
5 Runtime responsiveness   6 UI interaction   7 Trace refresh   8 Plot refresh
9 Decorative animation
```

Under pressure the UI degrades (refresh rate, downsampling, non-critical animation) — never
the capture / recorder path. Data-scale target: V1 handles 10–50 GB of engineering data;
the architecture reserves 100 GB+ — via streaming, chunking, indexing, query-on-demand and
downsampling. Never materialize an entire log into a `DataFrame`, or all frames into React
state. The timestamp system must eventually span CAN / CAN FD / LIN / DoIP / Automotive
Ethernet / Video / Sensor on one timeline.

---

## 4. Technology Baseline

```text
Desktop        Tauri 2 + Rust (bounded scope)
Frontend       React 19 · TypeScript · Vite · Dockview · Zustand · TanStack Query ·
               TanStack Virtual · ECharts · Monaco Editor · i18next
Runtime        Python 3.13 · FastAPI · python-can · cantools 44.0.0
IPC            Control plane: HTTP + JSON
               Realtime plane: WebSocket + binary MessagePack batches
Data           SQLite (metadata) · Parquet (large datasets, pyarrow 21.0.0) ·
               DuckDB 1.5.5 (analytical queries)
Packaging      Windows: scripts\package-windows.cmd → canx-runtime.exe (PyInstaller sidecar)
               + Tauri MSI
```

Do not swap these without a SPEC change.

---

## 5. Current Phase

```text
Current Phase:
V0.3 — Professional Trace & DBC Foundation

Latest CLOSED step:
V0.3-11 — Project Runtime Read Model API Foundation
  (Final Acceptance: PASS, Status: CLOSED — independent acceptance)

Previous CLOSED step:
V0.3-10 — Read-Only DBC Workspace UI Foundation   (Final Acceptance: PASS, Status: CLOSED)

Current step:
None. No V0.3-12 implementation has been started.

Engineering infrastructure (not numbered product phases):
Maintenance CI-01 — Continuous Integration Baseline Foundation
  Final Acceptance: PASS · Status: CLOSED   (independent acceptance)
  Adds .github/workflows/ci.yml only — no product scope change

Maintenance CI-02 — Protected Integration Gate Foundation
  Final Acceptance: PASS · Status: CLOSED   (independent acceptance)
  Adds a GitHub Repository Ruleset and docs/engineering/INTEGRATION_POLICY.md —
  no product scope change

Safety Foundation (SAFETY-01) — Safety Architecture & Risk Control Foundation
  Implementation complete · self-verification complete · Awaiting independent acceptance
  Adds runtime/canx/safety/ and docs/architecture/SAFETY_ARCHITECTURE.md —
  safety domain, policy, contracts and tests only. It introduces no dangerous
  execution capability. See §17.
```

The `Final Acceptance: PASS / Status: CLOSED` verdicts recorded here are **project-owner /
independent acceptance results** — not self-granted agent conclusions. This document only
*records* an acceptance result that already happened; it did not produce it.

V0.3-10 was implemented and self-verified by a development agent, whose evidence is in
`docs/acceptance/v0.3-10-read-only-dbc-workspace-ui-foundation.md`. The PASS / CLOSED verdict
now recorded for it is an **independent acceptance result** (project owner / independent
reviewer — P0: 0, P1: 0, Blocking P2: 0), supplied after that self-verification; the agent did
not write `Final Acceptance: PASS` for its own work.

V0.3-11 was implemented and self-verified by a development agent; its evidence is in
`docs/acceptance/v0.3-11-project-runtime-read-model-api-foundation.md`. It was then
independently reviewed and **not accepted on its first submission**:

```text
Independent acceptance source:
Project owner / independent reviewer

Final Acceptance: NOT PASS
Status: OPEN

P0: 0
P1: 1
P2: 0
```

The P1 finding was correct — an empty `project_path` was interpreted by `Path("")` as the
Runtime working directory, so `GET /project/inspect?project_path=` answered `200` with whatever
project the runtime happened to be running in. It is fixed by **V0.3-11-FINAL**, which refuses
an empty path at the FastAPI request-validation boundary (`422
api.request_validation_failed`, `source = api`) before the project domain is reached. The fix
was then covered by **V0.3-11-FINAL-2**, a post-fix full regression recorded on `a18a4f3`.

After that fix and its regression evidence, the phase was independently re-accepted:

```text
Independent acceptance source:
Project owner / independent reviewer

Final Acceptance: PASS
Status: CLOSED

P0: 0
P1: 0
Blocking P2: 0
```

This verdict is an **external result**, supplied by the project owner / independent reviewer.
The development agent did not write `Final Acceptance: PASS` for its own work at any point —
not on the initial submission (`NOT PASS`), not after V0.3-11-FINAL, and not after
V0.3-11-FINAL-2. V0.3-11 is now CLOSED and `V0.3-12` has not been started.

The complete history — initial `NOT PASS` with the P1 finding, the V0.3-11-FINAL fix, and the
V0.3-11-FINAL-2 post-fix regression — remains in
`docs/acceptance/v0.3-11-project-runtime-read-model-api-foundation.md` unedited.

This document's own maintenance task (*Maintenance — PROJECT_STATE Documentation Compaction*)
is a documentation-only task, **not** a numbered development phase, and did not touch any
product code.

---

## 6. Completed Phase Summary

Each closed phase keeps only a short summary here. Full detail is in
`docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_V0.3-09.md`.

**V0.1 — Technology Proof**
Final Acceptance: Conditional PASS (2026-09-15)
Result: Tauri + React shell · Python Runtime · Virtual CAN · Frame / FrameBatch · Capture ·
Recorder (MsgPack) · binary WebSocket · frontend Worker · virtualized Trace · basic Plot ·
Tool Registry · `trace.summary` · benchmark. A genuinely new architecture, not a CAN-Space
continuation.

**V0.1.1 — Acceptance Hardening**
Final Acceptance: Conditional PASS → approved to enter V0.2
Result: Recorder backpressure P0 RESOLVED via ADR 0001 · shared frontend realtime stream ·
saturation + soak tests · packaged Python runtime proof · single Windows packaging entry.
Carried PARTIAL / NOT VERIFIED items: windowed desktop launch (NOT VERIFIED),
realtime/UI latency (PARTIAL), real CAN hardware (NOT VERIFIED), macOS (NOT VERIFIED).
Evidence: `docs/V0.1.1_ACCEPTANCE_REPORT.md`.

**V0.2-01 — Project Foundation**
Final Acceptance: PASS (Conditional PASS → V0.2-01-FINAL → PASS)
Result: Project model · `project.json` manifest · SQLite `project.db` · lifecycle
create/open/close/reopen · identity survives reopen · corruption detection · create never
overwrites user data. FINAL fixed display-name validation and `close()` handle ordering.

**V0.2-02 — Data Session & Parquet Segment Persistence**
Final Acceptance: PASS
Result: SQLite schema v2 · session lifecycle · bounded segment writer · atomic segment commit ·
canonical Frame Parquet (schema v1) · explicit recovery · integrity inspection · pyarrow
21.0.0. FINAL fixed `start()` atomicity and V2 schema completeness.

**V0.2-03 — DuckDB Query Foundation & Bounded Historical Query Service**
Final Acceptance: PASS
Result: query domain models · typed query errors · structured-only query (no arbitrary SQL) ·
bounded pagination (`FETCH limit+1`, no OFFSET) · summary / arbitration-id counts · segment
planning · duckdb 1.5.5. FINAL closed an unsafe timestamp segment pruning (silent false
negative); timestamp pruning remains OFF.

**V0.2-04 — Project-Backed Capture Persistence Integration**
Final Acceptance: PASS (after V0.2-04-FINAL, FINAL-2, FINAL-3)
Result: ProjectRecorder · DataSessionWriter failure semantics · blocking-IO offload ·
sequence-gap rejection · packaged `canx-runtime.exe` runs the real Parquet write path
(Packaged Parquet execution path: VERIFIED). The FINAL chain fixed cleanup-timeout lifecycle
race, terminal-commit / timeout atomicity (pending finalization), and transient false
`FAILED` runtime status. Recorder terminal protocol is frozen from V0.3-01 on.

**V0.3-01 — Trace Query & Filtering Foundation**
Final Acceptance: PASS · Status: CLOSED
Result: `POST /trace/query` · `POST /trace/summary` · CAN ID range + ID mask filters ·
`(id & mask) == (value & mask)` semantics · `sequence ASC` cursor pagination · queries run in a
worker thread · Packaged DuckDB query path: VERIFIED. FINAL added the shared HTTP request
validation envelope (`422 api.request_validation_failed`).

**V0.3-02 — DBC Domain Foundation**
Final Acceptance: PASS · Status: CLOSED
Result: CAN-X canonical DBC domain model · single `cantools` adapter boundary · read-only
import/parse/validation · typed DBC errors · strict encoding policy (`utf-8-sig`, no silent
repair) · 9 license-clean fixtures. `Legacy reuse = none`.

**V0.3-03 — DBC Project Registry & Persistence Foundation**
Final Acceptance: PASS · Status: CLOSED (first NOT PASS → V0.3-03-FINAL → PASS)
Result: project-owned immutable DBC copies (`<project>/dbc/<asset_id>.dbc`) · SQLite schema
V3 `dbc_assets` registry · sha256 provenance · path containment · import / list / get / load
across close → reopen. FINAL closed asset path identity & containment.

**V0.3-04 — DBC Decode Foundation**
Final Acceptance: PASS · Status: CLOSED
Result: Frame → signal decode · bit extraction · byte order (Intel/Motorola) · physical
conversion · signed/unsigned · multiplexing foundation · float signal audit · differential
verification as an acceptance hard requirement.

**V0.3-05 — DBC Runtime Read & Decode API Foundation**
Final Acceptance: PASS · Status: CLOSED (Conditional PASS 1×P1 → V0.3-05-FINAL → PASS)
Result: `GET /dbc/assets` · `GET /dbc/assets/{asset_id}` · `GET /dbc/assets/{asset_id}/database`
· `POST /dbc/assets/{asset_id}/decode` · `POST /dbc/assets/{asset_id}/decode-batch` · shared
frame wire model · error mapping · worker-thread offload. FINAL tightened HTTP frame input
strictness.

**V0.3-06 — Safe DBC Content Import API Foundation**
Final Acceptance: technical PASS (V0.3-06-FINAL → V0.3-06-FINAL-2); process CONDITIONAL PASS
due to the stage-ordering anomaly below.
Result: `POST /dbc/assets` content import · Base64 policy · HTTP size bound · source-name
invariant · shared persistence. FINAL offloaded Base64 decode from the event loop;
FINAL-2 verified event-loop responsiveness under GIL-enabled CPython 3.13.

**V0.3-07 — Tauri Safe DBC File Bridge Foundation**
Final Acceptance: PASS · Status: CLOSED
Result: native OS file dialog opened by Rust · bounded exact-byte read · `SelectedDbcContent
{ source_name, content_base64 }` · no path crosses IPC · renderer capability not expanded
(still `core:default`). V0.3-07-FINAL added the packaged native-dialog E2E smoke.

> **Historical sequence anomaly (recorded, resolved):** V0.3-07 was implemented before
> V0.3-06-FINAL-2 was formally closed. The archive records it as a process issue, later
> reconciled; the technical records were preserved unchanged.

**V0.3-08 — Desktop DBC Import Orchestration Foundation**
Final Acceptance: PASS · Status: CLOSED
Result: native dialog → Tauri bridge → typed IPC → Desktop orchestration → Runtime content
import → project persistence. External absolute DBC path does not cross IPC or HTTP.
V0.3-08-FINAL closed packaged Cancel-path truthfulness and made the smoke fail closed.

**V0.3-09 — Desktop DBC Read Model Client Foundation**
Final Acceptance: PASS · Status: CLOSED
Result: `listDbcAssets(projectPath)` · `getDbcAsset(projectPath, assetId)` ·
`getDbcDatabase(projectPath, assetId)` · deep unknown → typed validation · URL-safe
project/asset addressing · preserved structured Runtime errors. Read APIs are explicit
`projectPath` parameters; no global current project was introduced.

**V0.3-10 — Read-Only DBC Workspace UI Foundation**
Final Acceptance: PASS · Status: CLOSED (independent acceptance — P0: 0, P1: 0, Blocking P2: 0)
Result: a production-visible, project-scoped, read-only DBC workspace — a real Dockview `DBC`
panel (a sibling tab of `Trace`), a project-scoped `DbcWorkspace` taking an explicit
`{ projectPath: string | null }`, read-only asset browsing plus message / signal inspection
(virtualized message list, signal definition table), formal loading / empty / no-project /
failure states, and full `en` + `zh-CN` i18n. Built entirely on the already-accepted V0.3-09
typed read client: no new Python endpoint, no SQLite migration, no new dependency, and no
global current project. Evidence:
`docs/acceptance/v0.3-10-read-only-dbc-workspace-ui-foundation.md`.

**V0.3-11 — Project Runtime Read Model API Foundation**
Final Acceptance: PASS · Status: CLOSED (independent acceptance — P0: 0, P1: 0, Blocking P2: 0)
Result: one read-only Runtime endpoint `GET /project/inspect?project_path=…`, built as a thin
adapter over the existing `ProjectService` (the project domain stays the authority), returning a
typed immutable read model (`project_id` / `display_name` / `schema_version` / `created_at` /
`updated_at`) with no filesystem path in the response, a request-scoped handle lifecycle, and
the blocking open/read/close sequence off the event loop. No global current project, no DBC
coupling, no schema change, no Desktop work. Its first independent review returned `NOT PASS`
with one P1 — an empty `project_path` was read by `Path("")` as the Runtime working directory;
**V0.3-11-FINAL** refuses an empty path at the request-validation boundary
(`422 api.request_validation_failed`, `source = api`), and **V0.3-11-FINAL-2** recorded the
post-fix full regression on `a18a4f3`. Evidence, including the initial `NOT PASS` and the
RED → GREEN record:
`docs/acceptance/v0.3-11-project-runtime-read-model-api-foundation.md`.

All V0.1 → V0.3-09 detail: `docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_V0.3-09.md`.
V0.3-10 onward: `docs/acceptance/`.

---

## 7. Current Capability Matrix

Verified against the current working tree (Runtime routers, desktop modules, Agent tools).

```text
Project foundation                         ✅
Project Runtime read-model API (HTTP)      ✅
SQLite project metadata                    ✅
Parquet session persistence                ✅
DuckDB query foundation                    ✅

Trace query / filter (HTTP)                ✅

Safety Kernel foundation (risk taxonomy,
  caller model, ARM state machine, scope,
  capability permissions, approvals, policy
  decision engine, audit contract,
  emergency stop contract)                 ✅ (authorises only — no executor)
Realtime stream (virtual CAN → batching →
  binary WebSocket → Worker → bounded
  frontend store → virtualized Trace/Plot) ✅
Virtual CAN capture                        ✅
Recorder (project-backed persistence)      ✅
Agent tool registry + trace.summary        ✅

DBC canonical domain                       ✅
DBC parser (cantools adapter)              ✅
DBC project registry                       ✅
DBC project persistence                    ✅
DBC decode (Runtime domain)                ✅
DBC Runtime HTTP read / decode API         ✅
Safe DBC content import (HTTP)             ✅
Desktop native DBC file bridge (Tauri)     ✅
Desktop DBC import orchestration           ✅
Desktop DBC read-model client              ✅

DBC Workspace UI (read-only)               ✅
DBC asset browser UI (read-only)           ✅
DBC editor                                 ❌
active DBC                                 ❌
channel ↔ DBC binding                      ❌
Trace decoded signal columns               ❌
Plot signal binding                        ❌
Frontend decode-batch integration          ❌
Agent dbc.* tools                          ❌

real CAN hardware (Vector/PCAN/Kvaser/ZLG) NOT VERIFIED
macOS real-machine validation              NOT VERIFIED
real CAN TX safety (SAFETY-01)             NOT VERIFIED — no TX path exists
real vehicle behaviour / UDS mutation /
  hardware fail-safe / vehicle
  qualification (SAFETY-01)                NOT VERIFIED — no dangerous capability
emergency stop against real hardware       NOT VERIFIED — contract only
device reconnect / channel change /
  transport fault auto-disarm (SAFETY-01)  CONTRACT ONLY — no device lifecycle,
                                           channel binding or transport exists
audit durability across restart /
  tamper evidence (SAFETY-01)              NOT IMPLEMENTED — in-memory trail
```

The two DBC Workspace rows were annotated while V0.3-10 was still awaiting independent
acceptance; the phase has since been independently accepted (**Final Acceptance: PASS ·
Status: CLOSED**), so they now carry the same ✅ as the rows inherited from earlier CLOSED
phases.

The `Project Runtime read-model API (HTTP)` row was annotated while V0.3-11 was still awaiting
independent re-acceptance. The phase has since been independently accepted (**Final
Acceptance: PASS · Status: CLOSED** — after V0.3-11-FINAL and the V0.3-11-FINAL-2 post-fix
regression), so the row now carries the same ✅ as the phases CLOSED before it.

---

## 8. Current Architecture / Boundary Summary

**Python Runtime** (`runtime/canx/`) — the engineering-logic authority:

```text
canx/project/     project model, manifest, SQLite schema + migration
canx/data/        data session model, bounded Parquet segment writer
canx/query/       structured bounded query (DuckDB), typed query errors
canx/recorder/    ProjectRecorder, DataSessionWriter failure semantics
canx/capture/     capture pipeline, virtual adapter
canx/devices/     adapter abstraction (python-can)
canx/dbc/         canonical domain, parser (cantools boundary), service, registry,
                  project_service, asset model
canx/transport/   MessagePack realtime codec
canx/agent/       tool registry + trace.summary (ToolRisk is the canonical
                  safety RiskLevel, not a second taxonomy)
canx/safety/      Safety Kernel: risk taxonomy, caller model, ARM state machine,
                  scope, capability permissions, approvals, policy engine,
                  audit contract, emergency stop contract — authorises, does
                  not execute (SAFETY-01, §17)
canx/api/         FastAPI app + routers (app.py, trace.py, dbc.py, project.py,
                  frame.py, errors.py)
canx/runtime/     RuntimeService (capture lifecycle, status truthfulness)
```

**Runtime HTTP surface (current):**

```text
GET  /health
GET  /runtime/status
POST /capture/start
POST /capture/stop
GET  /metrics
POST /tools/execute
POST /runtime/shutdown
WS   /stream/frames

GET  /project/inspect

POST /trace/query
POST /trace/summary

POST /dbc/assets
GET  /dbc/assets
GET  /dbc/assets/{asset_id}
GET  /dbc/assets/{asset_id}/database
POST /dbc/assets/{asset_id}/decode
POST /dbc/assets/{asset_id}/decode-batch
```

**Desktop** (`apps/desktop/src/`):

```text
desktop/        OS / Tauri IPC boundary      (dbc-file-bridge.ts)
runtime/        Python Runtime HTTP clients  (runtime-client, capture-client,
                 realtime-stream, decode-frame-batch [MessagePack, not DBC],
                 dbc-client [read + write])
orchestration/  flow coordination between the two boundaries (dbc-import.ts)
components/     UI panels (workspace/DockWorkspace, trace, plot)
workers/        WebSocket MessagePack worker
smoke/          build-gated smoke harness (not in normal production builds)
```

The desktop renders a Dockview workspace with Trace, Plot, a placeholder Agent panel and a
read-only **DBC** panel. The DBC read-model client (`runtime/dbc-client.ts`) now has a real
production caller — `components/dbc/` — while the DBC **import** client
(`orchestration/dbc-import.ts` + `desktop/dbc-file-bridge.ts`) still has no production caller
and is exercised only by tests and the smoke harness.

**Persisted schema baseline (current):**

```text
SQLite project schema            DATABASE_SCHEMA_VERSION = 3 (legacy floor = 1)
Parquet frame schema             FRAME_PARQUET_SCHEMA_VERSION = 1
Frame canonical schema           unchanged (timestamp provenance preserved)
project.json manifest schema     unchanged (format / schema_version / project_id)
DBC content                      the .dbc file is the single source of truth;
                                 SQLite stores only dbc_assets registry metadata
```

**Typed error codes** (five-field envelope, `source` = domain):

```text
project.*   validation / identity / schema / close failures
data.*      session / segment / storage / integrity failures
query.*     filter / pagination / segment / execution failures
capture.*   configuration / recording-target / finalization failures
dbc.*       file_not_found · read_failed · unsupported_format · decode_failed ·
            parse_failed · invalid_model · invalid_asset · asset_not_found ·
            asset_storage_failed · asset_registry_failed · asset_integrity_failed ·
            source_changed · message_not_found · frame_type_mismatch ·
            payload_too_short · decode_unsupported · signal_decode_failed
api.*       request_validation_failed (422, source = api)
```

**DBC import transport bound:** `MAX_DBC_IMPORT_BYTES = 16 MiB`, shared (by a cross-boundary
test) between the Rust desktop bridge and the Runtime HTTP guard. It is a transport bound,
not a domain limit.

---

## 9. Known Limitations / NOT VERIFIED

Still in force — do not silently drop these when reading only history.

```text
real CAN hardware (Vector/PCAN/Kvaser/ZLG)     NOT VERIFIED
macOS real-machine validation                  NOT VERIFIED
windowed desktop launch                        NOT VERIFIED (from V0.1.1)
realtime/UI latency                            PARTIAL (worker decode measured;
                                               UI/runtime latency NOT VERIFIED)
10–50 GB engineering dataset                   NOT VERIFIED
```

Desktop / Runtime / DBC limitations still open:

- **Near-limit Desktop HTTP serialization / renderer starvation (P2, unresolved).**
  Importing a DBC at the transport limit (~16 MiB → ~22 MB Base64 body) blocks the renderer
  main thread for ≈51–53 ms (≈22.5 ms of it synchronous `JSON.stringify`). It is a
  user-initiated, one-shot cost off the realtime path; measured relative to baseline, not an
  absolute cross-machine threshold. Not fixed — no Worker, no transport-contract change.
- Timestamp segment pruning remains **disabled** (V0.2-03 decision) until persistence can
  provide reliable per-segment min/max timestamp metadata.
- `max_frames_per_segment` defaults to 65536 (a foundation constant, not a SPEC value).
- DBC registry and filesystem cannot form a single transaction: a crash between "asset file
  written" and "registry row committed" can leave an unregistered orphan file. Registry state
  is authoritative; unregistered files under `project/dbc/` are not trusted. No automatic
  orphan cleanup.
- `dbc/`-as-symlink rejection is skipped on Windows (needs extra privileges); path-escape
  evidence comes from malicious `relative_path` tests instead.
- DBC client-side validation is **contract-shape** validation, not a domain-semantics
  replica (e.g. `signal.length` only requires a non-negative integer; uniqueness and
  `frame_id`/`is_extended` relations are Runtime-domain guarantees).

Project process gaps recorded honestly: until Maintenance CI-01 there was **no** `.github/` in
this repository, so no CI existed and every test number recorded in this document and under
`docs/acceptance/` is a **local run**, not a CI result. Maintenance CI-01 added
`.github/workflows/ci.yml`, so a real Windows quality gate now exists for
`pull_request → main`, `push → main` and `workflow_dispatch`. Two boundaries stay attached to
that gate:

- CI is an **automatic quality gate**, not a substitute for Independent Acceptance. A green CI
  run never grants a phase `Final Acceptance: PASS`.
- CI runs on **`windows-latest` only**. It verifies neither macOS nor Linux nor real CAN
  hardware, so the rows above stay `NOT VERIFIED`. A cross-platform CI matrix is a later
  maintenance task.

Maintenance CI-02 then turned that gate into a **protected integration gate**: `main` now
carries a GitHub Repository Ruleset (`main-protected-integration`) that requires a Pull Request
and a `success` `Quality Gate` before any normal merge, blocks force-push and deletion, and
grants no bypass actor. See §15. Two limits stay attached to it:

- The ruleset is **repository-side configuration**, not a product feature. Changing it needs
  repository-administration rights — the break-glass path in
  `docs/engineering/INTEGRATION_POLICY.md` §13. Protection removes the *normal* merge bypass; it
  cannot remove the owner's inherent ability to edit the configuration itself.
- Protection covers **`main` only**. macOS / Linux and real CAN hardware stay `NOT VERIFIED`, and
  a cross-platform CI matrix is still a later maintenance task.

---

## 10. Deferred Capabilities

Not implemented, in scope for later increments (actual repo state, not aspiration):

```text
DBC import button / project picker / project create-open UI
DBC editor
active DBC
channel ↔ DBC binding
Trace decoded signal columns / live decode UI
decode-batch frontend integration
Plot signal binding
asset rename / delete / replace
drag & drop / multi-file import
Rust DBC domain
arbitrary renderer filesystem access
Agent dbc.* tools
```

Not in V0.x at all: account system, subscription, license server, cloud sync.

---

## 11. Current Development Rules

The binding rules are in `AGENTS.md` (architecture, safety, testing, scope, naming). The
startup read policy is in `AGENTS.md` §2. Key points that apply to every task:

- One coherent increment per task / commit; no "design ten modules then write thousands of
  lines then test" (AGENTS.md §12–13).
- Test before claim — if you did not run it, write `NOT VERIFIED` (§14).
- No fake compatibility — no real Mac / real hardware claims without evidence (§15).
- Capture integrity precedes UI optimisation (§20).
- Never weaken tests, skip tests, or lower assertions to make CI green (§43).
- Do not overbuild; do not commit placeholder production code (§45–46).
- Protection order: architecture first, then correctness, then speed (§50).

### PROJECT_STATE compaction rule

Once a phase is formally accepted:

```text
working phase section
   → Final Acceptance: PASS
   → detail archived / acceptance report finalized
   → PROJECT_STATE.md keeps only a concise summary
```

Long implementation logs, raw test stdout, RED→GREEN dumps and multi-round FINAL detail for
CLOSED phases must **not** permanently accumulate in `docs/PROJECT_STATE.md`.

### Acceptance & verification model

An agent **does not** self-grant a phase `Final Acceptance: PASS` / `Status: CLOSED`. It
implements, self-verifies, and records `Awaiting independent acceptance`. The acceptance
verdict is an external result (the project owner / independent reviewer). When an agent writes
an acceptance result into this document, it must attribute it — e.g. "Project-owner /
independent acceptance result" — and must not present its own conclusion as that verdict.

---

## 12. Immediate Next Action

```text
V0.3-11 — Project Runtime Read Model API Foundation
          Final Acceptance: PASS · Status: CLOSED   (independent acceptance —
          P0: 0, P1: 0, Blocking P2: 0)
          History: first independent review NOT PASS (P0: 0, P1: 1, P2: 0)
          → V0.3-11-FINAL (empty project_path contract hardening)
          → V0.3-11-FINAL-2 (post-fix full regression recorded on a18a4f3)
          → independent re-acceptance: PASS / CLOSED

V0.3-12 — NOT STARTED. No V0.3-12 implementation exists in this tree.

Maintenance CI-01 — Continuous Integration Baseline Foundation   (not a numbered phase)
          Final Acceptance: PASS · Status: CLOSED   (independent acceptance)
          Real GitHub Actions runs executed, RED → GREEN (§14)

Maintenance CI-02 — Protected Integration Gate Foundation   (not a numbered phase)
          Final Acceptance: PASS · Status: CLOSED   (independent acceptance)
          main protected by a real GitHub Repository Ruleset (§15)

Safety Foundation (SAFETY-01) — Safety Architecture & Risk Control Foundation
          Implementation complete
          Self-verification complete
          AWAITING INDEPENDENT ACCEPTANCE
          Adds runtime/canx/safety/ + docs/architecture/SAFETY_ARCHITECTURE.md (§17)
```

V0.3-11 is CLOSED. It added one read-only Runtime endpoint —
`GET /project/inspect?project_path=…` — built as a thin adapter over the existing
`ProjectService`. Its first independent review found one P1 (an empty `project_path` was read by
`Path("")` as the Runtime working directory); V0.3-11-FINAL refuses an empty path at the
request-validation boundary instead, and V0.3-11-FINAL-2 supplied the post-fix regression
evidence. The full RED → GREEN record is in
`docs/acceptance/v0.3-11-project-runtime-read-model-api-foundation.md`, and none of the earlier
`NOT PASS` / FINAL / FINAL-2 history was rewritten.

Closing V0.3-11 does **not** begin V0.3-12. The next numbered phase must arrive as its own
explicit task brief.

Maintenance CI-01 is engineering infrastructure, not a product phase. It changes no behaviour, no
schema, no API contract and no dependency, and it makes no product claim. Its own verdict is
still external — the development agent did not write `Final Acceptance: PASS` for it. See §14 for
the CI baseline and its real run record.

Maintenance CI-02 is engineering infrastructure too. It changes no behaviour, no schema, no API
contract, no dependency and no test. It adds repository-side protection and one policy document,
and it makes no product claim. Like CI-01, its verdict is external — the development agent did
not write `Final Acceptance: PASS` for it; the PASS / CLOSED now recorded was supplied by the
project owner / independent reviewer. See §15 for the protected-integration record and
`docs/engineering/INTEGRATION_POLICY.md` for the policy itself.

Safety Foundation (SAFETY-01) is not a numbered product phase either. It adds
`runtime/canx/safety/` (a Runtime domain package) and `docs/architecture/SAFETY_ARCHITECTURE.md`,
and it changes one existing module — `canx/agent/tools.py`, where `ToolRisk` became an alias of
the canonical `RiskLevel`. It adds **no** dangerous capability: no TX, no replay send, no
injection, no diagnostic request, no ECU mutation, no new endpoint and no new UI control. Its own
verdict is external as well; §17 records what was implemented and what remains unverified.

---

## 13. Historical / Acceptance References

```text
docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_V0.3-09.md
    Lossless archive of the full PROJECT_STATE.md through V0.3-09.
    md5 (pre-compaction): df3b73fb1ce51afc00ad0b63a4e84551

docs/project-state/README.md
    Purpose and going-forward convention for the history archive.

docs/acceptance/README.md
    Policy for per-phase acceptance reports (in force from V0.3-10 onward).
    Historical phases were NOT back-filled as a bulk rewrite — the archive is the record.

docs/acceptance/v0.3-10-read-only-dbc-workspace-ui-foundation.md
    V0.3-10 acceptance evidence. Final Acceptance: PASS · Status: CLOSED
    (independent acceptance — project owner / independent reviewer).

docs/acceptance/v0.3-11-project-runtime-read-model-api-foundation.md
    V0.3-11 acceptance evidence. Final Acceptance: PASS · Status: CLOSED
    (independent acceptance — project owner / independent reviewer,
    P0: 0, P1: 0, Blocking P2: 0). The initial independent review was NOT PASS
    (P0: 0, P1: 1, P2: 0); the P1 was fixed by V0.3-11-FINAL, and §19 records the
    V0.3-11-FINAL-2 post-fix full regression on a18a4f3. History preserved unedited.

.github/workflows/ci.yml
    Maintenance CI-01 continuous-integration baseline. Three independent Windows quality jobs
    (Runtime / Python, Frontend / TypeScript, Desktop System / Rust) plus a fail-closed Quality
    Gate. The operational record of its first real GitHub Actions runs is in §14.

docs/engineering/INTEGRATION_POLICY.md
    Maintenance CI-02 integration policy. The protected-integration rules around `main`: the
    required `Quality Gate`, force-push / deletion / conversation-resolution rules, the
    break-glass policy, agent restrictions, and the boundary
    Local Verification ≠ GitHub CI ≠ Protected Merge ≠ Independent Acceptance. Enforced (not
    merely described) by the `main-protected-integration` GitHub Repository Ruleset — see §15.

docs/architecture/SAFETY_ARCHITECTURE.md
    SAFETY-01 safety architecture. The frozen invariants S1–S14, the risk taxonomy,
    operation / caller / ARM / scope / permission / approval / audit / emergency-stop
    contracts, the Agent and script safety boundaries, the adapter boundary, restart
    and concurrency semantics, the future-integration rules, and the list of items
    that remain NOT VERIFIED — see §17.

docs/ADR/0001-recorder-pressure-policy.md
    Normative recorder backpressure decision (V0.1.1).

docs/V0.1.1_ACCEPTANCE_REPORT.md, docs/V0.1_VALIDATION_REPORT.md,
docs/V0.1_TECH_VALIDATION.md, docs/V0.1.1_HANDOFF_AUDIT.md, docs/DEPENDENCIES.md
    Earlier phase-specific reports.
```

The dependency baseline is tracked in `docs/DEPENDENCIES.md`.

---

## 14. Continuous Integration Baseline (Maintenance CI-01)

`.github/workflows/ci.yml` is CAN-X's first real CI. Before this task the repository had no
`.github/` at all, so every test number recorded in this document and under `docs/acceptance/`
is a local run.

**Shape.** Triggers are `pull_request → main`, `push → main` and `workflow_dispatch`, with no
path filtering. `permissions: contents: read` only — no write, release, publish, deploy or
secret. Superseded runs on the same ref are cancelled. Four jobs, all on `windows-latest`:

```text
Runtime / Python        pytest -q · ruff check runtime tests tools · mypy runtime
Frontend / TypeScript   pnpm install --frozen-lockfile · lint · typecheck · test · build
Desktop System / Rust   cargo fmt --check · clippy --all-targets --all-features --locked
                        -- -D warnings · test --locked
Quality Gate            needs all three, if: always(); non-zero unless every one succeeded
```

Versions and dependencies are never re-declared in the workflow: Python comes from
`pyproject.toml` (interpreter pinned to 3.13.15), pnpm from `package.json`'s `packageManager`
field via corepack, and Rust from `Cargo.lock` (`--locked`). Node is pinned to 24.18.0. There is
no `continue-on-error`, no `|| true`, and no skipped or weakened test. CI-01 publishes, signs and
releases nothing.

**Real runs.** The first run is the objective infrastructure RED baseline — the workflow did not
exist before this task, so its first execution is that baseline. Every gate is green from the run
that follows:

```text
35217771155  pull_request       @9aff3fa  FAILURE   three jobs failed; Quality Gate failed closed
35219911643  pull_request       @75693f6  SUCCESS   1658 passed / 6 skipped · 162 frontend tests
                                                     · 28 + 3 Rust tests · ruff and mypy clean
35220767729  push → main        @75693f6  SUCCESS   all four jobs success
35221583265  workflow_dispatch  @75693f6  SUCCESS   all four jobs success
```

The Rust job runs the cross-boundary sidecar test for real rather than letting it return early:
it sets `CANX_TEST_PYTHON` to the repository-root `.venv` interpreter, and the test that
silently no-ops when that variable is absent (2.27 s with it, 0.00 s without, measured locally)
reports `ok` in 1.34 s on the runner.

The first run also exposed three pre-existing environment / portability defects that no local run
had ever shown: `bundle.externalBin` pointing at a gitignored PyInstaller artifact that a fresh
checkout does not have, a jsdom canvas gap that made `pnpm test` exit non-deterministically, and
a capture test whose drain budget assumed a machine faster than the runner. Each was reproduced
locally, repaired minimally, and re-run. None was repaired by skipping, deleting or weakening a
test, and no product behaviour, schema, API contract or dependency changed. The repairs and their
RED → GREEN evidence are in the commit messages for `75693f6`, `efb90cb` and `fc98f8e`.

**What CI does not establish.** CI is an automatic quality gate, not Independent Acceptance — a
green CI never grants `Final Acceptance: PASS`. It runs on `windows-latest` only, so macOS, Linux
and real CAN hardware stay `NOT VERIFIED`. It does not build the MSI, the updater or a release.
The Rust toolchain is the runner's preinstalled stable (1.98.1 at the time of these runs) and is
not pinned by this repository, unlike Python, Node and pnpm.
`tests/integration/test_packaged_runtime_smoke.py` (6 tests) skips on CI because no packaged
`canx-runtime.exe` is staged there; it runs locally after `scripts\package-windows.cmd`.

---

## 15. Protected Integration Workflow (Maintenance CI-02)

`.github/workflows/ci.yml` (CI-01) answered *"does CI check the code?"*. Maintenance CI-02 adds
the stronger boundary: **code that the required gate has not passed cannot reach `main` through
the normal process.**

> **No green required Quality Gate = no normal merge into `main`.**

**Mechanism.** A GitHub **Repository Ruleset** named `main-protected-integration` (id `23600372`,
target: the default branch `main`, enforcement `active`, `bypass_actors: []`). This is real
platform enforcement, not documentation:

```text
deletion                block deleting main
non_fast_forward        block force-push to main
pull_request            require a PR; 0 required approvals (sole-author);
                        all review conversations must be resolved
required_status_checks  require the "Quality Gate" check to report `success`
```

**Required check.** Only `Quality Gate` is required — it already fails closed unless all three
domain jobs report `success`, so the aggregate is sufficient and cannot be satisfied by a partial
run. Its exact context string (`Quality Gate`) was read from the real check runs already present
on `main`, not assumed.

**Policy.** `docs/engineering/INTEGRATION_POLICY.md` states the rules on top of the mechanism —
branch / PR flow, red-CI and missing-CI handling, stale-PR and conflict handling, force-push and
deletion policy, the break-glass policy, agent restrictions, and the
`Local Verification ≠ GitHub CI ≠ Protected Merge ≠ Independent Acceptance` boundary.

**Verified.** Every item below was produced against the real GitHub repository, not simulated
locally — by the development agent, as self-verification. The phase was then independently
reviewed:

```text
Independent acceptance source:
Project-owner / independent reviewer

Final Acceptance: PASS
Status: CLOSED

P0: 0
P1: 0
P2: 2 non-blocking
```

The two non-blocking P2 items are a timing-related recorder-cleanup flake observed once during
CI-02's own review and the ruleset's lack of a strict "branch must be up to date with `main`"
requirement. Neither is fixed, and neither was fixed inside SAFETY-01 (§9, §17). This verdict is
an **external result**: the development agent did not write `Final Acceptance: PASS` for its own
work at any point.

```text
main protection      un-protected (HTTP 404 "Branch not protected") before
                     → ruleset main-protected-integration active after
                     (verified with GET /repos/{owner}/{repo}/rules/branches/main)
direct push to main  REJECTED — "remote: error: GH013: Repository rule violations found for
                     refs/heads/main" — the repository owner is not exempt; remote main unchanged
merge blocked        a temporary probe PR carrying an intentionally failing check could not be
                     merged — blocked by "the base branch policy prohibits the merge" while the
                     required check was not `success`
post-merge CI        a merge into `main` is itself a `push → main` event and produces a fresh CI
                     run on the merge commit
```

The run IDs, PR numbers and exact probe output for these checks are recorded in the CI-02 task
handoff record and remain queryable in the GitHub Actions history; they are deliberately not
duplicated here as self-referential numbers.

**What CI-02 does not do.** It adds no CD, release, packaging, signing, updater or any product
capability, and it starts no SAFETY-01, AGENT-01, V0.3-12 or CD work. The ruleset is repository
configuration — owner-editable by design, which is exactly the break-glass path in the policy —
not product code.

---

## 16. Engineering Maturity Record

```text
Level 1  Local Automated Verification          DONE
Level 2  Repository Continuous Integration     DONE
Level 3  Protected Integration Workflow        DONE
Safety Foundation (SAFETY-01)                  IMPLEMENTED · AWAITING INDEPENDENT ACCEPTANCE
Level 4  Multi-Agent Orchestration             NOT STARTED
Level 5  Controlled Delivery / Qualification   NOT STARTED
```

A Level 3 verdict — like every acceptance verdict here — is external. The `DONE` on Level 3 is the
project owner / independent reviewer's CI-02 result (P0: 0, P1: 0, P2: 2 non-blocking, see §15),
not a conclusion this document reached on its own. The Safety Foundation row becomes `DONE` only
after its own independent acceptance; until then it stays `IMPLEMENTED · AWAITING INDEPENDENT
ACCEPTANCE`. This record says what the tree contains, not that it has been accepted.

---

## 17. Safety Architecture & Risk Control Foundation (SAFETY-01)

SAFETY-01 is the safety foundation that had to exist **before** CAN-X gains any
capability that can change a vehicle. It is not a numbered product phase, and it
adds no product capability.

**Status: implementation complete · self-verification complete · AWAITING INDEPENDENT
ACCEPTANCE.** The development agent did not write a `Final Acceptance: PASS` for this work.

### 17.1 The question it answers

> Can a caller gain a dangerous vehicle capability by not going through the runtime's
> safety authority?

Before SAFETY-01 the honest answer was "the rule is written down but nothing enforces it".
After SAFETY-01 there is a Runtime-owned **Safety Kernel** that is the policy authority
(invariant S2), dangerous operations default to `DENY` (S1), and the absence of a transmit
path is asserted by regression test rather than promised in prose (S12).

### 17.2 What was added

```text
runtime/canx/safety/            a new Runtime domain package (13 modules)
  risk.py        RiskLevel · Capability · OperationClass · deterministic classification
  caller.py      CallerKind · CallerIdentity · who may supply authority
  scope.py       OperationTarget · ArmScope · NaN-safe has_lapsed
  arm.py         ArmState · ArmController · explicit transition table
  permission.py  PermissionGrant · PermissionSet (no mutator; empty by default)
  approval.py    Approval · ApprovalIssuer · ApprovalStore (atomic single-use consumption)
  operation.py   OperationRequest (parameter digest, never parameters)
  decision.py    DecisionOutcome · SafetyReason · PolicyDecision
  policy.py      SafetyPolicy · SafetyContext · ApprovalRequirement
  audit.py       SafetyAuditEvent · SafetyAuditSink · InMemoryAuditSink
  emergency.py   EmergencyStopController · EmergencyStopState · OperationCanceller
  kernel.py      SafetyKernel — the authority
  errors.py      SafetyError family, all codes prefixed `safety.`

docs/architecture/SAFETY_ARCHITECTURE.md   the frozen contract (23 sections, S1–S14)
tests/unit/safety/                         refusal paths, fault injection, cross-caller
                                           matrices, anti-escalation, boundary guards
```

Plus one minimal change to an existing module: `canx/agent/tools.py`'s `ToolRisk` is now an
**alias** of the canonical `RiskLevel` rather than a second six-level enum. Two enums meaning
the same thing drift the first time one is edited, and the failure mode is a tool whose
`risk_level` reads "safe" to the executor and "dangerous" to the policy (AGENTS.md §21).

```text
Risk levels        READ · COMPUTE · WRITE_PROJECT · TX · DIAGNOSTIC_MUTATION
                   · ACTUATION · ECU_MUTATION · CRITICAL   (first three safe)
ARM states         DISARMED → ARMING → ARMED; disarm() idempotent; no DISARMED → ARMED edge
Permissions        capability-based, scoped, empty by default, no widening method
Approvals          one capability, expiring, single-use where demanded, atomic consumption
Scope              device / channel / target address; a blank request coordinate is NOT a wildcard
Emergency stop     globally disarm · deny new dangerous work · request cancellations · audit
Audit              ALLOW and DENY both recorded; no field can carry a secret; unrecordable ⇒ fault
```

### 17.3 What was deliberately NOT added

```text
real CAN transmit · periodic transmit · replay send · frame injection
diagnostic requests · UDS · clear DTC · ECU reset · routine control · security access
flash / download · IO control
POST /tx · POST /inject · POST /uds · any new endpoint
Send / Inject / Clear DTC / ECU Reset UI controls
SQLite schema migration
```

The kernel **authorises**; it does not execute. There is no `execute`, no `dispatch`, no
adapter handle and no bus object anywhere in the package. `tests/unit/safety/test_device_transmit_boundary.py`
fails the moment a transmit primitive appears on `CanAdapter`, a `send`-like method appears on
`VirtualAdapter`, an execution verb appears on `SafetyKernel`, the safety package imports a
device or transport module, or the HTTP surface grows a dangerous endpoint.

### 17.4 RED → GREEN evidence

The decision chain was implemented against a test suite written first. The suite was run before
`SafetyPolicy._decide` existed, and the recorded progression is:

```text
56 failed / 127 passed      first run — the chain was not implemented
22 failed / 161 passed      after the approval-refusal mapping was added
12 failed / 171 passed      after the cross-caller and boundary test expectations were corrected
 0 failed / 183 passed      final — all safety tests green
```

Two real defects were found and fixed by that red run, not by inspection:

- **Approval refusals escaped the decision chain.** `_require_approval` consumed the approval
  store without translating the store's typed faults, so `SafetyApprovalExpiredError`,
  `SafetyApprovalReusedError` and an unresolvable reference all fell through to the outermost
  fail-closed handler and were reported as `safety.policy_failure` — the right verdict for the
  wrong reason, which would have hidden *why* an approval was unusable from the operator who has
  to issue another one. Fixed by mapping each fault onto its own reason code
  (`safety.approval_expired` / `_reused` / `_invalid` / `safety.scope_violation`).
- **Expiry was fail-open on a broken clock.** The first draft wrote the expired check as
  `now >= expires_at`. IEEE-754 makes every comparison with `NaN` false, so `NaN >= expires_at`
  answers "not expired" — a corrupted clock reading would *extend* an authority, which is the one
  direction expiry must never fail in. Every expiry check now routes through `has_lapsed`, written
  `not (now < expires_at)`, and a test pins the behaviour.

### 17.5 Verification (local runs, this tree)

```text
python -m pytest -q            1846 passed, 1 skipped in 149.57 s
                               (the skip is a Windows directory-link privilege, not a safety test)
python -m pytest tests/unit/safety -q     183 passed
python -m ruff check runtime tests tools  All checks passed
python -m mypy runtime                    Success: no issues found in 79 source files
```

These are **local** runs. CI-01's Quality Gate re-runs the same three Python checks in the
`Runtime / Python` job, and the new safety tests are inside `tests/`, so they are covered by the
existing gate with no change to `.github/workflows/ci.yml` — no separate Safety Gate was added
(SAFETY-01 §32). A green gate is an automatic quality check and is **not** independent acceptance.

### 17.6 Known limitations and NOT VERIFIED items

```text
Real CAN TX safety                            NOT VERIFIED — no TX path exists
Real vehicle behaviour                        NOT VERIFIED
UDS mutation safety                           NOT VERIFIED
Emergency stop against real hardware          NOT VERIFIED — contract and state only
Hardware fail-safe                            NOT VERIFIED
Vehicle qualification                         NOT VERIFIED
Device reconnect / channel change /
  transport fault auto-disarm                 CONTRACT ONLY — no device lifecycle yet
Audit durability across restart               NOT IMPLEMENTED — in-memory, bounded trail
Audit tamper evidence                         NOT IMPLEMENTED
External execution atomic with the decision   NOT IMPLEMENTED — the kernel's lock covers the
                                              kernel's own state only; a future caller that
                                              evaluates and then acts still has a gap
```

### 17.7 Deferred items

```text
a durable / tamper-evident audit sink (would need a storage decision, not a schema guess)
an execution path that crosses the kernel (TX, replay, injection, diagnostics)
atomic execution with the decision (an authorization token, reservation, or in-lock execution)
device lifecycle events driving auto-disarm
a cross-platform CI matrix (macOS / Linux remain NOT VERIFIED)
CI-02's two non-blocking P2 items (§9) — untouched by SAFETY-01
```

SAFETY-01 stops here. It does **not** start AGENT-01, AGENT-02, CD-01 or V0.3-12.
