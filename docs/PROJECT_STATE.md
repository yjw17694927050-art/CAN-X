# CAN-X — Current Project State

> **Document**: `docs/PROJECT_STATE.md`
> **Purpose**: Compact current-state snapshot — the mandatory startup context for every agent task.
> **Updated**: 2026-09-17 (V0.3-11 independently accepted — Final Acceptance: PASS, Status: CLOSED; Maintenance CI-01 continuous-integration baseline added)
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

**Safety (when TX / mutation exists)**

- All real TX must pass: `TX Policy → ARM State → Permission → Approval → Adapter.send →
  Audit`. No bypass path.
- Agent auto-executes `READ` / `COMPUTE` / `WRITE_PROJECT`; `TX` / `ECU_MUTATION` /
  `CRITICAL` are Runtime-gated and never bypassed on AI request.
- Agent-generated Python runs in an isolated Sandbox Worker with no raw CAN device handle,
  no direct `python-can` bus, no TX credentials, no unrestricted host filesystem.

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

Engineering infrastructure (not a numbered product phase):
Maintenance CI-01 — Continuous Integration Baseline Foundation
  Implementation complete · self-verification complete · Awaiting independent acceptance
  Adds .github/workflows/ci.yml only — no product scope change
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
canx/agent/       tool registry + trace.summary
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

Project process gaps recorded honestly: no `.github/` exists, so no CI is claimed for any
regression number; all recorded test numbers are local runs.

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
V0.3-10 — Final Acceptance: PASS · Status: CLOSED
V0.3-11 — Project Runtime Read Model API Foundation
          Final Acceptance: PASS · Status: CLOSED   (independent acceptance —
          P0: 0, P1: 0, Blocking P2: 0)
          History: first independent review NOT PASS (P0: 0, P1: 1, P2: 0)
          → V0.3-11-FINAL (empty project_path contract hardening)
          → V0.3-11-FINAL-2 (post-fix full regression recorded on a18a4f3)
          → independent re-acceptance: PASS / CLOSED
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
explicit task brief; no V0.3-12 implementation exists in this tree.

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

docs/ADR/0001-recorder-pressure-policy.md
    Normative recorder backpressure decision (V0.1.1).

docs/V0.1.1_ACCEPTANCE_REPORT.md, docs/V0.1_VALIDATION_REPORT.md,
docs/V0.1_TECH_VALIDATION.md, docs/V0.1.1_HANDOFF_AUDIT.md, docs/DEPENDENCIES.md
    Earlier phase-specific reports.
```

The dependency baseline is tracked in `docs/DEPENDENCIES.md`.
