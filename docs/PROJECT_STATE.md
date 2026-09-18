# CAN-X — Current Project State

> **Document**: `docs/PROJECT_STATE.md`
> **Purpose**: Compact current-state snapshot — the mandatory startup context for every agent task.
> **Updated**: 2026-09-18 (V0.3-11 independently accepted — Final Acceptance: PASS, Status: CLOSED; Maintenance CI-01 continuous-integration baseline independently accepted — Final Acceptance: PASS, Status: CLOSED; Maintenance CI-02 protected-integration gate foundation independently accepted — Final Acceptance: PASS, Status: CLOSED, see §15; SAFETY-01 Safety Architecture & Risk Control Foundation — first independent acceptance NOT PASS (P0: 3, P1: 2, P2: 1), remediated by SAFETY-01-FIX-1; **second** independent acceptance NOT PASS (P0: 1, P1: 1, P2: 1), remediated by SAFETY-01-FIX-2; **third** independent acceptance NOT PASS (P0: 1, P1: 1, P2: 0), remediated by SAFETY-01-FIX-3; **fourth** independent acceptance NOT PASS (P0: 1, P1: 0, P2: 0), remediated by SAFETY-01-FIX-4; **final** independent acceptance PASS (P0: 0, P1: 0, P2: 0), merged to `main` as c05debf9 with post-merge `main` CI green — Final Acceptance: PASS, Status: CLOSED, see §17; AGENT-01 Multi-Agent Orchestration Foundation — first independent acceptance NOT PASS (P0: 2, P1: 3, P2: 1), remediated by AGENT-01-FIX-1; **second** independent acceptance NOT PASS (P0: 2, P1: 2, P2: 1), remediated by AGENT-01-FIX-2 — implementation and self-verification complete, awaiting independent re-acceptance, see §18)
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
- Authority-increasing actions are committed only when their **complete audit
  transaction** succeeds — event preparation, event construction and the sink write
  — and any failure in that chain rolls the authority back first (invariant S20).
  A rollback that also fails is a distinct, stronger fault, not a softer one.
- Safety Audit reference fields are **identifiers**, not arbitrary caller text;
  the contract lives in `runtime/canx/safety/identifiers.py` and is enforced by
  every domain type that reaches the trail and again by the event itself
  (invariant S21).
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
  Implementation complete · remediation complete (SAFETY-01-FIX-1) ·
  hardening complete (SAFETY-01-FIX-2) ·
  emergency-stop epoch hardening complete (SAFETY-01-FIX-3) ·
  emergency-stop metadata hardening complete (SAFETY-01-FIX-4) ·
  Final Acceptance: PASS · Status: CLOSED   (independent acceptance —
  project owner / independent reviewer)
  First independent acceptance: NOT PASS (P0: 3, P1: 2, P2: 1) — all six fixed by
  SAFETY-01-FIX-1; the first verdict is preserved in §17.
  Second independent acceptance: NOT PASS (P0: 1, P1: 1, P2: 1) — all three fixed
  by SAFETY-01-FIX-2; the second verdict is preserved in §17 too.
  Third independent acceptance: NOT PASS (P0: 1, P1: 1, P2: 0) — both fixed by
  SAFETY-01-FIX-3; the third verdict is preserved in §17 as well.
  Fourth independent acceptance: NOT PASS (P0: 1, P1: 0, P2: 0) — fixed by
  SAFETY-01-FIX-4; the fourth verdict is preserved in §17 too.
  Final independent acceptance: PASS (P0: 0, P1: 0, P2: 0) — merged to `main` as
  c05debf9 with post-merge `main` CI green. The PASS is external; the development
  agent did not write it (§17.15).
  Adds runtime/canx/safety/ and docs/architecture/SAFETY_ARCHITECTURE.md —
  safety domain, policy, contracts and tests only. It introduces no dangerous
  execution capability. See §17.

AGENT-01 — Multi-Agent Orchestration Foundation
  Implementation complete · remediation complete (AGENT-01-FIX-1) ·
  hardening complete (AGENT-01-FIX-2) · self-verification complete
  First independent acceptance: NOT PASS (P0: 2, P1: 3, P2: 1) — preserved in §18.11
  Second independent acceptance: NOT PASS (P0: 2, P1: 2, P2: 1) — preserved in §18.13
  Awaiting independent re-acceptance (§18.12, §18.14)
  Adds .agent/ and tools/agent/, docs/engineering/MULTI_AGENT_PROTOCOL.md,
  docs/ADR/0002-parallel-development-serial-integration.md and
  INTEGRATION_POLICY.md §17 — engineering tooling only, no product scope change.
  See §18.
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
  decision engine, audit contract, audit-safe
  identifier contract, emergency stop contract) ✅ (authorises only — no executor)
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
          Remediation complete (SAFETY-01-FIX-1)
          Hardening complete (SAFETY-01-FIX-2)
          Emergency-stop epoch hardening complete (SAFETY-01-FIX-3)
          Emergency-stop metadata hardening complete (SAFETY-01-FIX-4)
          Final Acceptance: PASS · Status: CLOSED   (independent acceptance)
          First independent acceptance: NOT PASS (P0: 3, P1: 2, P2: 1) — preserved in §17
          Second independent acceptance: NOT PASS (P0: 1, P1: 1, P2: 1) — preserved in §17
          Third independent acceptance: NOT PASS (P0: 1, P1: 1, P2: 0) — preserved in §17
          Fourth independent acceptance: NOT PASS (P0: 1, P1: 0, P2: 0) — preserved in §17
          Final independent acceptance: PASS (P0: 0, P1: 0, P2: 0) — merged to `main`
          (c05debf9) with post-merge `main` CI green — preserved in §17
          Adds runtime/canx/safety/ + docs/architecture/SAFETY_ARCHITECTURE.md (§17)

AGENT-01 — Multi-Agent Orchestration Foundation   (not a numbered phase)
          First independent acceptance: NOT PASS (P0: 2, P1: 3, P2: 1) — preserved
          in §18.11
          AGENT-01-FIX-1 implementation complete · self-verification complete
          Second independent acceptance: NOT PASS (P0: 2, P1: 2, P2: 1) — preserved
          in §18.13
          AGENT-01-FIX-2 implementation complete · self-verification complete
          Awaiting independent re-acceptance (§18.12, §18.14)
          Level 4 is NOT marked DONE — only an external verdict may do that
          Adds .agent/ + tools/agent/ + docs/engineering/MULTI_AGENT_PROTOCOL.md
          + docs/ADR/0002-parallel-development-serial-integration.md
          + INTEGRATION_POLICY.md §17 + one ci.yml mypy-scope line
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
the canonical `RiskLevel`. SAFETY-01-FIX-2 also hardened the audit contract inside that package:
the audit *transaction* now covers event preparation and construction, not only the sink write
(invariant S20), and every audit reference field is a validated identifier rather than a
non-empty string (invariant S21). SAFETY-01-FIX-3 then closed the two findings of the third
independent review inside the same package: the emergency stop is now an epoch boundary rather
than a pause — `arm`, `confirm_arm` and `grant_approval` are all refused while it is engaged
(invariant S22) and a successful release leaves the runtime `DISARMED` with no outstanding
approval (S23) — and the cancellation boundary it reports through is typed (S24). SAFETY-01-FIX-4
then closed the fourth review's single finding on the same path: the raw reason is digested
best-effort, so a reason that cannot be UTF-8 encoded degrades the *attribution* to `None` instead
of vetoing the stop (invariant S25). It adds **no**
dangerous capability: no TX, no replay send, no
injection, no diagnostic request, no ECU mutation, no new endpoint and no new UI control. Its own
verdict is external as well; §17 records what was implemented and what remains unverified.

AGENT-01 is not a numbered product phase either. It is engineering infrastructure: no product
behaviour, no schema, no API contract, no dependency and no runtime code changes. It adds `.agent/`
(contracts, schemas, examples, prompt templates), `tools/agent/` (the standard-library-only
enforcement tooling), `docs/engineering/MULTI_AGENT_PROTOCOL.md`,
`docs/ADR/0002-parallel-development-serial-integration.md`, a §17 in
`docs/engineering/INTEGRATION_POLICY.md`, two extending lint/type config lines
(`pyproject.toml`, `.github/workflows/ci.yml`) and `scripts/agent.cmd`. Its own verdict is external
too — the development agent did not write `Final Acceptance: PASS` for it, and Level 4 is **not**
`DONE`. §18 records what was implemented, what was verified and what remains unverified.

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
    SAFETY-01 safety architecture. The frozen invariants S1–S25, the risk taxonomy,
    operation / caller / ARM / scope / permission / approval / audit /
    audit-safe-identifier / emergency-stop (epoch-boundary) / cancellation contracts,
    the audit transaction semantics, the emergency-stop metadata priority, the Agent
    and script safety boundaries, the adapter boundary, restart and concurrency
    semantics, the future-integration rules, and the list of items that remain
    NOT VERIFIED — see §17.

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
Safety Foundation (SAFETY-01)                  DONE · CLOSED
                                               (Final Acceptance: PASS — independent
                                               acceptance, merged to `main` as
                                               c05debf9, post-merge CI green)
Level 4  Multi-Agent Orchestration             NOT DONE — AGENT-01 first independent
                                               acceptance NOT PASS (P0:2 P1:3 P2:1),
                                               second independent acceptance NOT PASS
                                               (P0:2 P1:2 P2:1); AGENT-01-FIX-2
                                               implemented and self-verified, awaiting
                                               independent re-acceptance (§18.11–§18.14)
Level 5  Controlled Delivery / Qualification   NOT STARTED
```

A Level 3 verdict — like every acceptance verdict here — is external. The `DONE` on Level 3 is the
project owner / independent reviewer's CI-02 result (P0: 0, P1: 0, P2: 2 non-blocking, see §15),
not a conclusion this document reached on its own. The Safety Foundation row reads `DONE · CLOSED`
because its own independent acceptance returned PASS *and* the accepted tree then reached `main`
through the protected workflow with a green post-merge CI run (§17.15) — the row records work that
was accepted and integrated, it is not a conclusion this document reached on its own.

The Level 4 row records something weaker, and deliberately so. AGENT-01's implementation and
self-verification are complete (§18), but no external verdict on it exists yet, so Level 4 is
**not** `DONE`, and `Level 5` remains `NOT STARTED`. This record says what the tree contains, not
that anything beyond SAFETY-01 has been accepted.

---

## 17. Safety Architecture & Risk Control Foundation (SAFETY-01)

SAFETY-01 is the safety foundation that had to exist **before** CAN-X gains any
capability that can change a vehicle. It is not a numbered product phase, and it
adds no product capability.

```text
First independent acceptance:
Project-owner / independent reviewer

Final Acceptance: NOT PASS
Status: AWAITING FIX

P0 = 3
P1 = 2
P2 = 1
```

All six findings were correct. SAFETY-01-FIX-1 remediated them (§17.8 below). The
first `NOT PASS` is kept rather than replaced — it is the record of what the review
found, and the phase is judged on the tree that exists now, not on the one that was
submitted.

```text
Second independent acceptance (after SAFETY-01-FIX-1):
Project-owner / independent reviewer

Final Acceptance: NOT PASS
Status: AWAITING FIX-2

P0 = 1
P1 = 1
P2 = 1
```

All three findings were correct as well. They are remediated by SAFETY-01-FIX-2
(§17.9 below). This verdict is preserved unedited too, for the same reason: the
acceptance history is the record of what reviewers found, and rewriting it would
destroy the only evidence that the process was adversarial.

```text
Third independent acceptance (after SAFETY-01-FIX-2):
Project-owner / independent reviewer

Final Acceptance: NOT PASS
Status: AWAITING FIX-3

P0 = 1
P1 = 1
P2 = 0
```

Both findings were correct as well. They are remediated by SAFETY-01-FIX-3
(§17.11 below). This verdict is preserved unedited as well:

```text
P0  Emergency Stop could be used as pause/resume: ARM state and approval could be
    rebuilt while the stop was engaged, and survived the release.
P1  OperationCanceller returned tuple[str], and that tuple reached the audit
    event's detail through EmergencyStopState.describe() — arbitrary subsystem
    text outside the typed identifier contract, and the raw operator reason
    fanned out to every canceller by the same route.
```

```text
Fourth independent acceptance (after SAFETY-01-FIX-3):
Project-owner / independent reviewer

Final Acceptance: NOT PASS
Status: AWAITING FIX-4

P0 = 1
P1 = 0
P2 = 0
```

The finding was correct as well. It is remediated by SAFETY-01-FIX-4 (§17.13
below). This verdict is preserved unedited too:

```text
P0  Emergency Stop metadata could veto the safety reduction:
    SafetyKernel.engage_emergency_stop digested the raw reason before the stop
    engaged, and digest_reason encodes to UTF-8 — so a reason that is a legal
    Python str but not UTF-8 encodable ("\ud800") raised UnicodeEncodeError and
    the stop never ran, leaving an ARMED runtime and a live approval in place
    while the operator believed they had pulled the stop.
```

```text
Final independent acceptance (after SAFETY-01-FIX-4):
Project-owner / independent reviewer

Final Acceptance: PASS
Status: APPROVED FOR MERGE

P0 = 0
P1 = 0
P2 = 0

FIX-1 protections       PASS
FIX-2 protections       PASS
FIX-3 protections       PASS
FIX-4 protections       PASS

Latest PR head CI       PASS
Ruleset                 ACTIVE
Real TX added           NO
Real UDS added          NO
Dangerous execution     NO
```

**Status: implementation complete · remediation complete (FIX-1) · hardening
complete (FIX-2) · emergency-stop epoch hardening complete (FIX-3) ·
emergency-stop metadata hardening complete (FIX-4) · self-verification complete ·
independently accepted · merged to `main` (merge commit c05debf9) · post-merge
`main` CI green · Final Acceptance: PASS · Status: CLOSED.** The development agent
did not write a `Final Acceptance: PASS` for this work at any point — not on the
first submission, and not after any of the four remediations. The PASS recorded
here is the external verdict; §17.15 is the record of the merge and of the
post-merge `main` CI that had to pass before this document was allowed to write
`CLOSED`.

### 17.1 The question it answers

> Can a caller gain a dangerous vehicle capability by not going through the runtime's
> safety authority?

Before SAFETY-01 the honest answer was "the rule is written down but nothing enforces it".
After SAFETY-01 there is a Runtime-owned **Safety Kernel** that is the policy authority
(invariant S2), dangerous operations default to `DENY` (S1), and the absence of a transmit
path is asserted by regression test rather than promised in prose (S12).

### 17.2 What was added

```text
runtime/canx/safety/            a new Runtime domain package (14 modules)
  risk.py        RiskLevel · Capability · OperationClass · deterministic classification
  caller.py      CallerKind · CallerIdentity (caller_id is an identifier) · who may supply authority
  identifiers.py the audit-safe identifier + digest contract (FIX-2; CancellerId added by FIX-3)
  scope.py       OperationTarget · ArmScope · NaN-safe has_lapsed
  arm.py         ArmState · ArmController · explicit transition table
  permission.py  PermissionGrant · PermissionSet (no mutator; empty by default)
  approval.py    Approval · ApprovalIssuer · ApprovalStore (atomic single-use consumption)
  operation.py   OperationRequest (parameter digest, never parameters)
  decision.py    DecisionOutcome · SafetyReason · PolicyDecision
  policy.py      SafetyPolicy · SafetyContext · ApprovalRequirement
  audit.py       SafetyAuditEvent · SafetyAuditSink · InMemoryAuditSink ·
                 digest_reason (strict) · digest_reason_best_effort (FIX-4)
  emergency.py   EmergencyStopController · EmergencyStopState · OperationCanceller ·
                 CancellationFailure · CancellationFailureCode (FIX-3: typed cancellation)
  kernel.py      SafetyKernel — the authority · the audit commit guard ·
                 the emergency-stop authority gate (FIX-3) ·
                 best-effort reason digesting (FIX-4)
  errors.py      SafetyError family, all codes prefixed `safety.`

docs/architecture/SAFETY_ARCHITECTURE.md   the frozen contract (27 sections, S1–S25)
tests/unit/safety/                         refusal paths, fault injection, cross-caller
                                           matrices, anti-escalation, the audit
                                           transaction's failure points, the
                                           identifier contract, boundary guards
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
Emergency stop     an epoch boundary, not a pause (S22–S24): globally disarm ·
                   clear approvals · block arm/confirm_arm/grant_approval while
                   engaged · request cancellations with a reason digest · audit.
                   Release leaves DISARMED + no outstanding approval.
                   Metadata is non-authoritative (S25): reason encoding, clock,
                   canceller reporting and audit failures degrade attribution or
                   observability, never the stop
Audit              ALLOW and DENY both recorded; references are typed identifiers and
                   digests, never free text; the whole commit path is the transaction
                   (S20, S21); unrecordable ⇒ fault. Cancellation feedback obeys the
                   same contract (S24)
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

### 17.5 Verification (historical — the initial SAFETY-01 local runs)

These are the runs made when SAFETY-01 was first implemented. They are **not** this
tree's current numbers; §17.10 records the current ones. Kept because the FIX-1 and
FIX-2 evidence below is expressed as a progression from them.

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
Audit rollback-failure fail-safe *actuation*  CONTRACT ONLY — the typed fault is raised, and
                                              the runtime reports that its state can no longer
                                              be trusted; with no device attached there is
                                              nothing to fail safe into
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

### 17.8 Remediation (SAFETY-01-FIX-1)

The first independent review found six defects. All were real, and each is
recorded in `docs/architecture/SAFETY_ARCHITECTURE.md` §24 with its RED → GREEN
evidence.

```text
P0-1  READ effect risk and physical TX authority were conflated
      diagnostic.read landed on Capability.READ alone, so a future could have let
      it bypass the ARM state, the CAN_TX grant and the audit trail
      → effect risk and required capabilities split into two axes (S15)

P0-2  Approval provenance could be forged
      the issuer was carried by the approval payload and the store checked only
      that the grantor may issue approvals, never that the label matched it, so
      the host could speak as the operator
      → ApprovalSpec has no issuer field; issuer_for derives it; the store
        independently checks the match (S16)

P0-3  Authority could survive a failed audit
      arm / confirm / grant / e-stop release mutated authority before writing the
      trail, so a sink failure left authority that no record accounted for
      → _record_or_rollback, rolling back to a reducing action only (S17)

P1-1  The audit trail still had caller-controlled text
      caller_name; message, which carried the operator's reason verbatim; and
      detail, which rendered the emergency stop's reason
      → reason_digest replaces raw reasons; a control event's message is kernel
        text; caller_name is a bounded label (S19)

P1-2  The dangerous-permission expiry contract was documentation only
      PermissionGrant allowed expires_at=None while its docstring claimed policy
      enforced otherwise, and policy had no such check
      → enforced at construction, where a grant that cannot be built cannot be
        handed to a consumer that forgot to ask (S18)

P2-1  The PR handoff metadata was stale
      → regenerated from live GitHub state
```

The remediation added S15–S19 to the frozen invariant set, replaced
`ToolDefinition.permissions` (strings) with `ToolDefinition.required_capabilities`
(typed, and checked for vehicle transmission authority), and touched no CI
configuration, no ruleset and no test in a weakening direction. It introduced
**no** dangerous capability: still no TX, no replay send, no injection, no
diagnostic request, no ECU mutation, no new endpoint and no new UI control.

SAFETY-01 stops here. It does **not** start AGENT-01, AGENT-02, CD-01 or V0.3-12.

### 17.9 Remediation (SAFETY-01-FIX-2)

The second independent review found three defects. All three were real, and each
is recorded in `docs/architecture/SAFETY_ARCHITECTURE.md` §25 with its RED → GREEN
evidence.

```text
P0  The audit transaction was still not fully fail-safe
    _record_or_rollback caught only SafetyAuditError, so any exception raised
    *before* the sink — event-id generation, the clock read, event construction,
    detail rendering — escaped the guard. Authority survived an action that
    reported failure: confirm_arm left the runtime ARMED.
    → the transaction is now the whole commit path (S20). _record_control and
      _record_decision normalise every preparation fault into SafetyAuditError
      with __cause__ preserved; _commit_authority_change_with_audit catches
      broadly, rolls back through a reducing action, then propagates.
    → the new edge it exposed: if the audit fails AND the rollback fails, that is
      SafetyRollbackError — deliberately not a SafetyAuditError, because catching
      the ordinary fault must not swallow the unknown one. No FAULTED arm state
      was invented; the loud fault is the contract.

P1  Four audit references were still "any non-empty string"
    operation_id, approval_id, device_id and channel only had to be non-empty, so
    free-form caller text could be stored as an identifier (caller_name had been
    bounded by FIX-1, but only by a local rule).
    → every reference field now has a formal domain contract (S21) in
      runtime/canx/safety/identifiers.py: a 1–64 character [A-Za-z0-9._:-]
      identifier, typed value objects, runtime-minted ids, validated digests, and
      enforcement both at each domain type and again by SafetyAuditEvent.
      CallerIdentity.name became caller_id, and the event's caller_name became
      caller_id. The rule is an alphabet, not a secret detector.

P2  Documentation had drifted from the implementation
    S1–S14 was still the stated invariant range in places, the acceptance history
    did not record the second NOT PASS, and the recorded test counts were not this
    tree's counts.
    → reconciled across SAFETY_ARCHITECTURE.md, AGENTS.md §16, SPEC.md §32 and
      this document. The first AND second independent verdicts are both preserved.
```

The remediation added S20–S21 to the frozen invariant set. It touched no CI
configuration, no ruleset, and no test in a weakening direction — the two FIX-1
clock tests were re-expressed against the stronger contract, and the properties
they pinned (expiry fails closed; an unauditable verdict is not returned) are
still asserted directly. It introduced **no** dangerous capability: still no TX, no
replay send, no injection, no diagnostic request, no ECU mutation, no new endpoint
and no new UI control.

```text
P0 RED → GREEN
  RED    safety.confirm_arm() raised the injected RuntimeError and left
         arm_state == ARMED — an unaudited authority reported as a failure
         (195 failed / 48 passed across the new fault-injection matrix)
  GREEN  SafetyAuditError + arm_state == DISARMED
         (522 passed across tests/unit/safety)

P1 RED → GREEN
  RED    OperationRequest(operation_id="operator entered emergency because the
         rig was smoking") was constructed, evaluated and persisted verbatim
  GREEN  refused at construction as a safety.invalid_identifier fault, and again
         by SafetyAuditEvent itself
```

### 17.10 Verification (current tree, SAFETY-01-FIX-2)

Local runs against the tree this section describes. The FIX-1 and initial
SAFETY-01 numbers in §17.5 are **historical** — they are the runs that were made
then, and they have deliberately not been restated as current.

```text
python -m pytest tests/unit/safety -q     522 passed
python -m pytest -q                       2188 passed, 1 skipped in 152.71 s
                                          (the skip is a Windows directory-link
                                          privilege in an unrelated DBC test —
                                          the same pre-existing skip as before)
python -m ruff check runtime tests tools  All checks passed
python -m mypy runtime                    Success: no issues found in 80 source files
```

For reference, the historical progression is `286` safety tests before FIX-2
(the FIX-1 tree) and `183` at the end of the initial SAFETY-01 implementation
(§17.5).

```text
GitHub Quality Gate on the FIX-2 head
  Runtime / Python          required
  Frontend / TypeScript     required
  Desktop System / Rust     required
  Quality Gate              required — must be `success`
```

The exact run id and head SHA live in the PR #4 body rather than here: a CI run id
is only knowable *after* a push, and writing it into this file would make this file
stale the moment it changed the head. Live PR metadata belongs in the PR
(a FIX-1 finding, §17.8 P2-1). No new Quality Gate job was added — the FIX-2 tests
live in `tests/` and are covered by the existing gate, so `.github/workflows/ci.yml`
and the `main` ruleset are untouched.

### 17.11 Remediation (SAFETY-01-FIX-3)

The **third** independent review returned `NOT PASS` with one P0 and one P1
(§17 above). Both were correct. Neither was a redesign — the review found two
places where the frozen contract said one thing and the code did another — and the
remediation is frozen as invariants S22, S23 and S24.

#### P0 — the emergency stop was a pause, not an epoch boundary

Engaging the stop already did the right three things in the right order (disarm,
clear approvals, stay engaged), and `evaluate` refused dangerous work for as long
as it was engaged. What was missing was the other half: `arm`, `confirm_arm` and
`grant_approval` were not gated on the stop at all.

```text
E-stop engaged → arm() → confirm_arm() → grant_approval()
→ release E-stop → runtime already ARMED, approval already present
```

so the next dangerous operation proceeded without anyone rebuilding anything. The
stop did not remove authority so much as park it, and the release was a resume.
Reproduced before any edit with a `.rivet/scratch/` probe:

```text
RED     engaged: True
        P0 arm during e-stop: SUCCEEDED (defect)
        P0 confirm_arm during e-stop: SUCCEEDED armed
        P0 grant_approval during e-stop: SUCCEEDED; outstanding: 1
        after release: arm_state= armed outstanding= 1 engaged= False
```

Fixed in two layers rather than at whichever was easier:

```text
Layer 1  SafetyKernel._require_emergency_stop_released(action=…)
         called by arm · confirm_arm · grant_approval, before any mutation and
         inside the kernel lock, raising SafetyEmergencyStopError
         (`safety.emergency_stop_active`)
Layer 2  release_emergency_stop disarms and clears approvals as part of the
         release, outside the audit commit guard, and rolls back only the stop's
         engagement when the audit cannot be written
```

`confirm_arm` carries its own gate because it is a real bypass, not a duplicate:
a runtime that was already `ARMING` when the stop engaged reaches `ARMED` through
the confirmation and never calls `arm` again. The gate is a typed fault rather than
a `PolicyDecision.DENY` because these are control-plane authority mutations that
never reach `evaluate` — there is no verdict for a `DENY` to be.

Neither layer may itself become a way for the stop to fail (S22): a clock that
cannot be read records `engaged_at = None`, and a reason digest that is not a digest
records `reason_digest = None`. The loss is visible as `null` in the state and the
trail; the stop still engages.

```text
GREEN   P0 arm during e-stop: refused -> SafetyEmergencyStopError safety.emergency_stop_active
        P0 confirm_arm during e-stop: refused -> SafetyEmergencyStopError
        P0 grant_approval during e-stop: refused -> SafetyEmergencyStopError
        after release: arm_state= disarmed outstanding= 0 engaged= False
```

#### P1 — the cancellation boundary was outside the identifier contract

`OperationCanceller.cancel_active_operations` returned `tuple[str, ...]` and that
tuple went into `EmergencyStopState.requested_cancellations` unvalidated, then into
the audit event's `detail` through `EmergencyStopState.describe()`. The failure list
was prose assembled at the failure site — `f"{ClassName}: {ExceptionName}"`. So a
subsystem could put anything into Safety Audit's `detail` by returning it, which is
the back door S21 closed for `operation_id` / `approval_id` / `device_id` /
`channel`, reopened one layer up. The raw operator reason fanned out to every
canceller by the same route.

```text
RED     requested_cancellations: ('tx-1', 'operator secret is hunter2')
        audit detail: {"cancellation_failures":[],"engaged":true,…,
                       "requested_cancellations":["tx-1","operator secret is hunter2"]}
        secret in detail: True
        canceller saw reason: ['bench secret xyz']
```

Fixed by giving the cancellation boundary the same typed contract as the rest of
the trail (S24):

```text
OperationCanceller       cancel_active_operations(reason_digest=…) -> tuple[OperationId, …]
registration             register_canceller(CancellerId, canceller) — not type(x).__name__
revalidation             every returned value re-runs through OperationId(), because the
                         return type is a typing promise rather than a runtime guarantee
CancellationFailure      canceller_id · failure_code · failure_type — all bounded, no message text
CancellationFailureCode  canceller.raised · canceller.invalid_reference ·
                         canceller.contract_violation (closed vocabulary)
EmergencyStopState       __post_init__ re-checks every audit-facing field, the same defence
                         in depth SafetyAuditEvent applies to itself
```

Two properties were preserved and are asserted: **the stop still engages** — a
malformed answer is a failure to *report*, never a veto, so the identifiers it did
name are kept, the rest become a structured failure, and the stop engages either
way — and **nothing silently disappears**, because S13/S14 require an operator to
see which operation cancellation was requested and which subsystem did not answer.
The answer is a structured bounded `detail`, not `detail=None`.

```text
GREEN   requested_cancellations: ('tx-1',)
        cancellation_failures: CancellationFailure(canceller_id='tx.periodic',
            failure_code=CANCELLER_INVALID_REFERENCE, failure_type='str')
        secret in detail: False
        canceller saw reason_digest only: True
        e-stop still engaged: True
```

#### A test that encoded the defect, corrected

`test_the_stop_survives_a_re_arm_attempt_during_the_emergency` — and the
architecture prose next to it — documented re-arming during the stop as
*permitted*. The third review found the expected behaviour itself unsafe, so the
test is now `test_rearming_is_forbidden_while_emergency_stop_is_engaged` and the
prose was replaced. This is a corrected contract, not a weakened test: the old
assertion pinned a pause, the new one pins an epoch boundary. No other safety test
was deleted, skipped or loosened.

#### What the remediation did not change

```text
S1–S21       unchanged, and none weakened
FIX-1        every FIX-1 protection intact — two-axis authority, derived provenance,
             finite dangerous-permission expiry, reason digests
FIX-2        every FIX-2 protection intact — the whole-transaction audit commit,
             SafetyRollbackError, the identifier contract
tests        none deleted, skipped or loosened; the one E-stop expectation that
             encoded the defect was corrected
ci.yml       untouched
ruleset      untouched
TX / UDS     still absent — the boundary is enforced; no capability was added
```

A general *authority epoch* (one counter invalidating old authority on stop,
restart, device reconnect or channel change alike) is recorded in
`SAFETY_ARCHITECTURE.md` §21 as the natural generalisation of S22/S23 and as
**deferred**. `DISARMED` + no approvals expresses the whole requirement the third
review set, so FIX-3 stops there rather than inventing a fourth piece of authority
state.

### 17.12 Verification (current tree, SAFETY-01-FIX-3)

Local runs against the tree this section describes. §17.5 and §17.10 are
**historical** — they are the runs that were made then, and they have deliberately
not been restated as current.

```text
python -m pytest tests/unit/safety/test_emergency_stop.py -q    54 passed
python -m pytest tests/unit/safety -q                           555 passed
python -m pytest -q                                             2221 passed, 1 skipped in 151.53 s
                                                                (the skip is the same pre-existing
                                                                Windows directory-link privilege in an
                                                                unrelated DBC test)
python -m ruff check runtime tests tools                        All checks passed
python -m mypy runtime                                          Success: no issues found in 80 source files
```

For reference, the historical progression is `522` safety tests before FIX-3 (the
FIX-2 tree), `286` before FIX-2 (the FIX-1 tree) and `183` at the end of the initial
SAFETY-01 implementation (§17.5).

```text
GitHub Quality Gate on the FIX-3 head
  Runtime / Python          required
  Frontend / TypeScript     required
  Desktop System / Rust     required
  Quality Gate              required — must be `success`
```

The exact run id and head SHA live in the PR #4 body rather than here (a FIX-1
finding, §17.8 P2-1). `.github/workflows/ci.yml` and the `main` ruleset are
untouched: the FIX-3 tests live in `tests/` and are covered by the existing gate,
and the explicit instruction for this round was to leave both alone.

### 17.13 Remediation (SAFETY-01-FIX-4)

The **fourth** independent review returned `NOT PASS` with one P0 and nothing else
(§17 above). It was correct, and it reopened none of FIX-1/2/3: S22–S24 were
already right, and this is the property that made them *unconditional* rather than
merely *available*.

#### P0 — optional metadata could veto the safety reduction

`SafetyKernel.engage_emergency_stop` digested the raw reason **before** the stop
engaged:

```python
reason_digest = digest_reason(reason)          # reason.encode("utf-8")
state = self._emergency.engage(caller=caller, reason_digest=reason_digest)
```

`digest_reason` encodes to UTF-8, and a Python `str` may hold a lone surrogate —
`"\ud800"` is legal `str` and illegal UTF-8. So the first line raised
`UnicodeEncodeError` and the second never ran:

```text
operator requests E-stop
→ reason encoding fails
→ exception
→ E-stop NOT engaged
→ ARM may remain ARMED, approval may remain active
```

Reproduced on the **public kernel path** with the runtime armed under `CAN_TX` and
holding an approval — before any edit:

```text
RED     BEFORE: arm_state=armed approvals=1 engaged=False
        EXCEPTION: UnicodeEncodeError 'utf-8' codec can't encode character
                   '\ud800' in position 0: surrogates not allowed
        AFTER : arm_state=armed approvals=1 engaged=False
                scope=ArmScope(...) canceller_calls=[]
```

The stop did not happen and the canceller was never called. An optional piece of
attribution metadata, describing a reduction that had not yet been performed, had
vetoed the reduction.

Fixed in three small pieces (invariant **S25**):

```text
Kernel    engage_emergency_stop processes the reason through
          digest_reason_best_effort, which returns None instead of raising
Contract  OperationCanceller.reason_digest is str | None, and cancellers are
          handed the *normalised* value: None means "attribution unavailable",
          never "skip the fan-out"
Priority  reduce authority · establish the stop · clear approvals · request
          cancellation   all before   best-effort attribution · audit
```

```text
GREEN   engage returned normally
        AFTER : arm_state=disarmed approvals=0 engaged=True
                scope=None canceller_calls=[None]
```

The fallback is `None` and specifically **not** a substituted encoding:
`errors="ignore"` would make two different reasons hash alike, and
`errors="replace"` would produce a digest of text nobody supplied. A fabricated
digest is worse than an absent one — it claims a reason was recorded when the one
recorded is not the one given. `reason_digest: null` beside `engaged: true` says
"the stop happened, the reason was not recordable", which a reviewer can act on.

`digest_reason` itself is **unchanged and still strict**, and a test pins that: on
an authority-*increasing* path a reason that cannot be digested is a caller bug
worth knowing about loudly. The leniency is confined to the reducing path, the
same asymmetry S17 draws.

No raw reason is retained as a fallback (§10): the degrade direction is
`None`, never the text, so S19 stays intact.

#### Compound failures

The four metadata failures are independent, and the brief asked for the
combinations rather than the sum of their parts. All four are asserted:

```text
unencodable reason + failing audit sink
    → caller may receive SafetyAuditError
    → E-stop ENGAGED · ARM DISARMED · active_scope None · approvals empty

unencodable reason + clock that fails the engaged_at read
    → the call returns normally
    → engaged True · engaged_at None · reason_digest None
    → E-stop ENGAGED · ARM DISARMED · approvals empty

unencodable reason + malformed canceller output
    → E-stop ENGAGED · requested_cancellations keeps the valid OperationId
    → neither the prose nor the surrogate reaches the trail

unencodable reason + release
    → the stop releases on the normal path afterwards (FIX-3 behaviour intact)
```

#### What the remediation did not change

```text
S1–S24       unchanged, and none weakened
FIX-1        every FIX-1 protection intact — two-axis authority, derived provenance,
             finite dangerous-permission expiry, reason digests
FIX-2        every FIX-2 protection intact — the whole-transaction audit commit,
             SafetyRollbackError, the identifier contract
FIX-3        every FIX-3 protection intact — the epoch boundary, the arm/approval
             gates, the release postconditions, the typed cancellation boundary
tests        none deleted, skipped or loosened; twelve added
ci.yml       untouched
ruleset      untouched
TX / UDS     still absent — the boundary is enforced; no capability was added
```

### 17.14 Verification (current tree, SAFETY-01-FIX-4)

Local runs against the tree this section describes. §17.5, §17.10 and §17.12 are
**historical** — they are the runs that were made then, and they have deliberately
not been restated as current.

```text
python -m pytest tests/unit/safety/test_emergency_stop.py -q         66 passed
python -m pytest tests/unit/safety/test_audit_secret_boundary.py -q   15 passed
python -m pytest tests/unit/safety/test_authority_audit_atomicity.py -q  22 passed
python -m pytest tests/unit/safety/test_audit_identifiers.py -q      222 passed
python -m pytest tests/unit/safety -q                                567 passed
python -m pytest -q                                                  2233 passed, 1 skipped in 150.98 s
python -m ruff check runtime tests tools                             All checks passed
python -m mypy runtime                                               Success: no issues found in 80 source files
```

The single skip is the same pre-existing Windows directory-link privilege skip in
an unrelated DBC test.

The RED was checked two ways. The `.rivet/scratch/` probe above shows the public
path failing before the edit. Separately, because a fix that is never re-broken is
a fix nobody has tested, the pins were re-run with the kernel's
`digest_reason_best_effort` global rebound to the strict `digest_reason` — the
exact pre-FIX-4 code path, with no tracked file touched:

```text
4 pins run against the pre-fix code path: 4 raised UnicodeEncodeError
(headline stop · cancellation fan-out · +failing audit · +broken clock)
```

For reference, the historical progression is `555` safety tests before FIX-4 (the
FIX-3 tree), `522` before FIX-3 (the FIX-2 tree), `286` before FIX-2 (the FIX-1
tree) and `183` at the end of the initial SAFETY-01 implementation (§17.5).

```text
GitHub Quality Gate on the FIX-4 head
  Runtime / Python          required
  Frontend / TypeScript     required
  Desktop System / Rust     required
  Quality Gate              required — must be `success`
```

The exact run id and head SHA live in the PR #4 body rather than here (a FIX-1
finding, §17.8 P2-1). `.github/workflows/ci.yml` and the `main` ruleset are
untouched: the FIX-4 tests live in `tests/` and are covered by the existing gate,
and the explicit instruction for this round was to leave both alone.

**Known residue, recorded rather than fixed.** `SafetyKernel.disarm(reason=…)`
still digests strictly, so an unencodable reason there raises `UnicodeEncodeError`
instead of a typed fault. It does **not** veto anything: `self._arm.disarm()` runs
before the digest, so the reduction has already happened and the exception only
costs the audit record and the return value — the safe direction, exactly what S17
describes. It is out of FIX-4's scope (the review's finding and the frozen S25 both
concern the emergency-stop path, and §6 of the brief is explicit that the
best-effort helper must not be applied globally), and it is a diagnosability
defect rather than a safety one. Recorded so the next review sees it was
considered rather than missed.

### 17.15 Merge and post-merge verification (SAFETY-01-CLOSE)

The independent verdict was `PASS / APPROVED FOR MERGE` on head `ca2d6f8`. What
this subsection records is the second half of that sentence: the accepted tree
actually reached `main` through the protected workflow, and `main` passed its own
CI afterwards. Those are two separate facts from "the PR was reviewed", and the
`CLOSED` above is only written because all three hold.

```text
Pre-merge (re-read live, not taken from the handoff brief)
  main before merge       4ecc6cde8999e4ea8be53a63c12f17f2899e8189
  PR #4                   OPEN, not draft, MERGEABLE, mergeStateStatus CLEAN
  PR head                 ca2d6f8fab2ee930724d212c3666d04164d94245
                          == the independently accepted head; no commit after it
  PR head check-runs      Runtime / Python      success
                          Frontend / TypeScript success
                          Desktop System / Rust success
                          Quality Gate          success
  ruleset                 main-protected-integration, enforcement active,
                          required check "Quality Gate", bypass_actors [],
                          current_user_can_bypass "never"
  bypass used             NO

Merge
  method                  merge commit (preserves the FIX-1 → FIX-4 audit trail;
                          the ruleset allows merge / squash / rebase)
  merge commit            c05debf9faa625810b27a391d5db979d30bea1e2
  parents of the merge    4ecc6cde (old main) + ca2d6f8 (PR head)
  merged at               2026-09-18T01:31:38Z
  merged by               yjw17694927050-art
  new main HEAD           c05debf9faa625810b27a391d5db979d30bea1e2
  PR #4 state             MERGED
```

`ca2d6f8` being an *ancestor* of the new `main` HEAD was checked with
`git merge-base --is-ancestor` rather than inferred from the PR's own state — a
merge commit that lost the branch's commits would still report `MERGED`.

```text
Post-merge CI (a different run from the PR run, as it must be)
  run id                  35295673078
  event                   push
  branch                  main
  head                    c05debf9faa625810b27a391d5db979d30bea1e2
  conclusion              success

  Runtime / Python        success
    pytest                2228 passed, 6 skipped in 182.87s   (0 failed)
    ruff                  All checks passed!
    mypy                  Success: no issues found in 80 source files
  Frontend / TypeScript   success
  Desktop System / Rust   success   (31 tests, 0 failed)
  Quality Gate            success

  the 6 skips             all tests/integration/test_packaged_runtime_smoke.py,
                          reason "packaged canx-runtime.exe has not been built" —
                          the pre-existing packaged-runtime skips, none added by
                          SAFETY-01
  rerun / flake           none — the run was green on its first and only attempt
```

The `2228 passed / 6 skipped` figures are read from *this* run's log. The PR run
reported a different split (`2233 passed / 1 skipped` locally) because the local
environment has a `canx-runtime.exe` console script so the packaged smoke tests
execute rather than skip; the totals agree at 2234 collected, and neither number
is reused as the other's evidence.

```text
Ruleset after the merge    unchanged
  enforcement              active
  required status check    Quality Gate
  bypass_actors            []
  current_user_can_bypass  never
  main ref                 protected: true
```

**No `admin` bypass, no force push, no temporary ruleset disable, no direct push
to `main`.** The merge went `PR → Quality Gate → ruleset → protected merge`, and
the `CLOSED` status was written only after the post-merge `main` CI came back
green, in a separate docs-only change.

Scope of this closeout: documentation only. It adds no product code, no agent
code and no capability. AGENT-01 has **not** started, V0.3-12 has **not** started,
and CD-01 has **not** started — each waits on its own explicit task brief.

#### Post-closeout observation — a main CI flake (recorded, not repaired)

The closeout itself (this document's change, merged as `6dd29842`) produced the
first post-merge `main` CI run that was **not** green on its first attempt:

```text
run 35296791223   event push   branch main   head 6dd29842
  attempt 1   FAILURE   Runtime / Python · Quality Gate
              tests/unit/api/test_capture_project_target.py::
              test_a_project_capture_reports_the_data_session_it_created
              AssertionError: DataSessionState.FAILED is not COMPLETED
              1 failed, 2227 passed, 6 skipped in 272.08s
  attempt 2   SUCCESS   2228 passed, 6 skipped in 154.79s
                        ruff All checks passed · mypy Success (80 files)
```

Recorded rather than smoothed over, and **not repaired here**, because it is not
a SAFETY-01 regression and this task was not authorised to change it:

```text
the merge 6dd29842 changed exactly one file vs c05debf9: docs/PROJECT_STATE.md
the failing test's file was last touched by 025e1ad, a pre-existing main commit
the same code passed twice: run 35295673078 (main @ c05debf9, 182.87s) and
  run 35296298635 (closeout head 5cad067) — both green on their first attempt
20 consecutive local runs of that file: 20 × 7 passed, 0 failures
```

The test starts a 2 kHz capture, sleeps 0.1 s, stops it, and then requires the
persisted session to be `COMPLETED`. That is a *timing budget*, and attempt 1 ran
48% slower than attempt 2 on the same code — the signature of a contended runner.
It is the same class of CI-only failure §14 already records once: `fc98f8e`
("size the recorder drain budget for this module's segment churn") sized the same
kind of budget for `tests/integration/test_project_backed_capture.py`. This is a
second instance of that class, in a sibling file.

```text
repair attempted here        NO — no test was skipped, deleted, loosened or edited
closure impact                NONE on the evidence: SAFETY-01's own post-merge CI
                              (run 35295673078, head c05debf9) was green on its
                              first and only attempt, and `main` is green now
owner / reviewer decision     OPEN — whether to give this budget more headroom is
                              a separate task, and a latent race in capture
                              finalization cannot be ruled out from one sample
```

Flagged rather than fixed, and flagged rather than hidden: a test that fails only
on a slow runner is exactly the kind of thing that becomes "we do not know why CI
is red" six months later.

---

## 18. Multi-Agent Orchestration Foundation (AGENT-01)

AGENT-01 is the foundation that had to exist **before** CAN-X runs more than one
agent. It is not a numbered product phase, and it adds no product capability.

### 18.1 The question it answers

> How can CAN-X safely parallelise engineering work without losing ownership,
> contract integrity, test evidence, integration safety, or independent
> acceptance?

The answer is stated in `docs/engineering/MULTI_AGENT_PROTOCOL.md` and enforced by
`tools/agent/`: **parallel development, serial protected integration, clear
machine-checkable ownership, machine-verifiable handoff, and a protected CI gate**.

```text
no clear ownership             → no parallelism
no stable contract             → no parallelism
a high probability of conflict → serial
shared core truth              → Main Agent only
```

This is not "how to call four AI agents at once". AGENT-01 spawns no agent, adds
no provider SDK, and names no model anywhere in its contracts.

### 18.2 What was added

```text
.agent/
  README.md                     how to use the contracts
  config.json                   parallelism cap, path classes, branch prefix
  schemas/task.schema.json      task interchange contract (JSON Schema 2020-12)
  schemas/handoff.schema.json   handoff interchange contract
  examples/                     one valid task · one valid handoff ·
                                one ownership violation · one four-task plan
  prompts/                      Main-Agent and Sub-Agent prompt templates
  handoffs/README.md            where produced handoffs land

tools/
  agent/                        errors · config · paths · contracts · lifecycle ·
                                graph · conflicts · validation · gitcmd ·
                                worktree · handoff · orchestration · cli
scripts/agent.cmd               the Windows entry point

docs/engineering/MULTI_AGENT_PROTOCOL.md
docs/ADR/0002-parallel-development-serial-integration.md
docs/engineering/INTEGRATION_POLICY.md            §17 added

tests/unit/agent_tools/                           6 files, 169 tests
tests/integration/test_agent_worktree_lifecycle.py   18 tests
tests/integration/test_multi_agent_orchestration_simulation.py  13 tests
```

Two existing files changed, and one deliberately not:

```text
pyproject.toml             pytest pythonpath and mypy_path also resolve tools/agent
.github/workflows/ci.yml   mypy now type-checks runtime AND tools/agent
.gitignore                 unchanged — .worktrees/ was already ignored
```

The `tools/agent/` package is engineering tooling, not product: it is not
imported by `canx` and is not packaged into the wheel (`pyproject.toml` packages
only `runtime/canx`). It is standard library only — no new dependency was added.

### 18.3 The required design decision

> How does CAN-X prevent individually-green but jointly-broken parallel PRs?

Answered in `docs/ADR/0002-parallel-development-serial-integration.md`: **no merge
queue**. Development is parallel; integration is not. The Main Agent merges one PR
at a time, and every handoff must be based on the current integration head, which
`tools/agent/validation.py:check_base` enforces as a returned error code
(`agent.base_stale`) rather than an instruction in a prompt.

The ADR evaluates the three alternatives (enable the ruleset's strict up-to-date
flag, a long-lived integration branch, a merge queue) and records enabling
`strict_required_status_checks_policy` as the recommended platform-level backstop
— to be applied as its own deliberate, verified configuration change, not folded
into this one. The ruleset is **unchanged** by AGENT-01.

### 18.4 RED → GREEN evidence

Two orchestration contracts were pinned by a test that fails before the gate
exists and passes after it.

```text
#1  changed file outside allowed_paths
    before   a handoff could claim ownership_compliance: true and change SPEC.md
             while its task owned runtime/canx/foo/**; validate_handoff accepted it
             RED    4 failed, 153 passed   (tests/unit/agent_tools, 157 collected;
                    test_validation.py: DID NOT RAISE OwnershipViolationError)
    after   validate_handoff re-derives compliance from changed_files and raises
             agent.ownership_violation
             GREEN  2 failed, 155 passed  (the ownership tests pass; the stale-base
                    test is still red because that gate is not in yet)

#2  stale base
    before   check_base validated the shape of the two shas and returned; a handoff
             built on an obsolete main was integrated as if it were current
             RED    test_validation.py: DID NOT RAISE BaseStaleError
    after   check_base refuses any base_sha that is not the current integration head
             GREEN  157 passed  (tests/unit/agent_tools, at that point)
```

The two `GREEN` counts differ because the examples/schema suite was added after
both gates landed; the final count for that directory is 169.

Both gates were then rolled back one final time to confirm the tests are not
vacuous — with the two checks removed, `tests/unit/agent_tools/test_validation.py`
returns `4 failed, 44 passed`; restored, it is green. A test that stays green
with its fix reverted protects nothing.

### 18.5 Simulated orchestration

`tests/integration/test_multi_agent_orchestration_simulation.py` runs the §74
scenario end to end against the contracts:

```text
A  independent                                  → RUNNABLE
B  independent, freezes a public-truth contract → RUNNABLE
C  depends on A                                 → BLOCKED
D  races B on that same public-truth path       → DEFERRED, B~D classified C3

A's handoff with an unowned file                → REJECTED (agent.ownership_violation)
A's clean handoff on the current head           → ACCEPTED
A integrated                                    → C becomes RUNNABLE
B integrated                                    → D's deferral is released
a handoff built before the head moved           → REJECTED (agent.base_stale)
```

The scenario also verifies the serial-integration rule directly: after A
integrates, B's handoff on the old base is refused, and the same work rebased onto
the new head is accepted.

**This is `protocol/tooling verified in simulation`.** It is not
`four-agent parallel development verified` — no agent was spawned, and no product
pilot was run. That is AGENT-02.

### 18.6 Verification (local, this tree)

```text
pytest        2433 passed, 1 skipped in 157.50s   (2434 collected; baseline
              before AGENT-01 was 2234 collected — the delta is the 200 new tests)
              the 1 skip is tests/unit/dbc/test_dbc_asset.py:274, "this environment
              cannot create a directory link" — pre-existing, none added
ruff          All checks passed!  (runtime tests tools)
mypy          Success: no issues found in 94 source files  (runtime tools/agent)
frontend      eslint --max-warnings 0 clean · tsc -b clean · 162 tests passed
              (12 files) · vite build ok
rust          cargo fmt --check clean · clippy --all-targets --all-features
              --locked -D warnings clean · 31 tests passed
```

Frontend and Rust are unchanged by AGENT-01; they were run as a regression check.
The packaged-runtime smoke tests skip locally only when no `canx-runtime.exe` is
staged — on this tree it is staged, so they ran.

### 18.7 What was deliberately NOT added

```text
no agent runtime, no provider SDK, no model name in any contract
no message broker, scheduler, database queue, daemon or dashboard
no FastAPI product endpoint, no Tauri command, no React UI
no new dependency — tools/agent is standard library only
no real four-agent pilot
```

### 18.8 Safety

`runtime/canx/safety/**` is untouched; S1–S25 are unchanged; no dangerous
capability was added. What AGENT-01 does add is a classification: a task whose
ownership surface reaches a safety path, or whose `risk_class` is
`SAFETY_CRITICAL`, is marked `serial_review_required` and may never be integrated
in parallel with another task. `RiskClass` (development risk) is a different type
from the Runtime's `canx.safety.risk.RiskLevel` (vehicle-operation risk) and the
two are never conflated.

### 18.9 Known limitations and NOT VERIFIED

- **Simulation only.** No real multi-agent pilot has run. The protocol has been
  exercised by tests, not by four concurrent agents against a live repository.
- **The ruleset's strict up-to-date flag is still `false`.** The parallel-case
  integration rule is enforced by the protocol and `check_base`, not by the
  platform. `docs/ADR/0002-*` records this as an open follow-up.
- **Windows only.** Every number above is from this Windows tree; CI runs on
  `windows-latest`. macOS and Linux remain `NOT VERIFIED`.
- **No merge queue.** Deliberate; see §18.3.
- **`worktree remove` needs a gitignored worktrees directory.** Documented as a
  precondition, not worked around.

### 18.10 Deferred items

```text
enabling strict_required_status_checks_policy on ruleset 23600372, with its
  negative case verified — its own change (ADR 0002, alternative A)
the reliable handling of the capture/finalization timing flake before AGENT-02
  (see §17.15, carried forward unchanged and not repaired here)
the real 1 Main + up to 4 Sub-Agent pilot — AGENT-02
```

### 18.11 First independent acceptance — NOT PASS

The first independent review of AGENT-01 did **not** pass, and the verdict is
preserved here rather than smoothed over:

```text
Independent acceptance source:
Project owner / independent reviewer

Final Acceptance: NOT PASS
Status: AWAITING AGENT-01-FIX-1

P0 = 2
P1 = 3
P2 = 1
```

The reviewer's framing was explicit, and correct: the architecture was accepted
in direction, but **several claimed machine gates still trusted agent-supplied
metadata instead of independently deriving repository facts**. That is a fair
reading of the tree at `5b7e1d6`:

```text
P0-1  protected paths could be bypassed by a broad ownership glob. `**` or
      `docs/**` was accepted for a Sub-Agent because the check asked whether the
      *literal text* of the glob is a protected file name, not whether the glob
      can *reach* one.
P0-2  handoff facts were self-reported. `check-integration` trusted
      `changed_files`, `base_sha` and `head_sha` from the JSON, so a lying
      handoff could omit a changed `SPEC.md`, or invent the current `main` as its
      base, and still come back ready.
P1-1  a required test reported `skipped` or `not_run` still allowed readiness.
P1-2  `plan()` did not use `max_sub_agents`; five independent tasks all came
      back runnable.
P1-3  the local gate did not check dependencies, conflict deferral or task
      status, yet READY_FOR_INTEGRATION is documented as requiring them.
P2    repository identity was a substring test, so
      `github.com/evil/<owner>/<repo>-copy.git` passed as `<owner>/<repo>`.
```

Every one of those was reproduced against `5b7e1d6` before any fix was written
(the RED record is summarised in §18.12). The verdict is external; the
development agent did not write it and does not dispute it.

### 18.12 Remediation — AGENT-01-FIX-1

A narrow remediation: no redesign of the accepted architecture, no AGENT-02, no
product capability, no Safety change.

```text
RED, all seven, against 5b7e1d6
  validate_task(allowed_paths=("**",), owner=sub-a)          ACCEPTED
  validate_task(allowed_paths=("**/*.md",), owner=sub-a)     ACCEPTED
  validate_task(allowed_paths=("docs/**",), owner=sub-a)     ACCEPTED
  lying handoff omitting a changed SPEC.md                   ready=True
  handoff inventing the current main as its base             ready=True
  handoff inventing a head_sha                               ready=True
  ready handoff with an uncommitted worktree                 ready=True
  required 'integration' reported not_run / skipped          ACCEPTED
  two test records named 'unit', both passed                 ACCEPTED
  five independent tasks, max_sub_agents=4                   runnable len=5
  dependent C ready while A is READY                         ready=True
  deferred-by-conflict D                                     ready=True
  origin github.com/evil/<owner>/<repo>-copy.git             accepted
```

What changed:

```text
tools/agent/paths.py       overlapping_pattern() — pattern-versus-pattern overlap,
                           kept distinct from matching_pattern() (path versus pattern)
tools/agent/config.py      protected_overlap / public_truth_overlap / safety_overlap
tools/agent/validation.py  protected ownership by overlap; required tests must all
                           pass with duplicates refused; check_base also requires
                           handoff.base_sha == task.base_sha; IntegrationContext;
                           verify_repository_evidence; evaluate_integration reports
                           ready only when it could prove every local requirement
tools/agent/evidence.py    NEW — RepositoryEvidence + collect_repository_evidence,
                           the Git-backed source of truth
tools/agent/gitcmd.py      canonical_repository() exact identity; diff_name_status /
                           touched_paths (both sides of renames and copies);
                           commit_shas()
tools/agent/orchestration.py  plan() enforces max_sub_agents and reports
                           capacity_deferred distinctly
tools/agent/worktree.py    validate_repository compares canonical identities exactly
tools/agent/cli.py         check-integration takes --repo and --plan
```

The corrected boundary, stated once:

```text
Sub-Agent report      untrusted evidence
Git repository state  authoritative evidence
Task contract         what is permitted
Main Agent verifier   independently compares the two
```

New tests: 293 focused tests across the agent suites (unit + integration),
including throwaway-git fixtures for the omitted-file, fake-base, fake-head,
dirty-worktree, rename/copy and commit-evidence attacks. The real CAN-X
repository is never mutated by pytest.

```text
Implementation complete
Self-verification complete
Awaiting independent re-acceptance
```

Level 4 remains **not** `DONE`; no `Final Acceptance: PASS` is written by the
development agent.

Level 4 is **not** `DONE`, `Final Acceptance` was **not** written by the
development agent, and `AGENT-02`, `V0.3-12` and `CD-01` have **not** started.

### 18.13 Second independent acceptance — NOT PASS

The **second** independent review of AGENT-01 did **not** pass either, and its
verdict is preserved here exactly as the first one was:

```text
AGENT-01 Re-Acceptance

Final Acceptance: NOT PASS
Status: AWAITING AGENT-01-FIX-2

P0 = 2
P1 = 2
P2 = 1
```

The first verdict stays unedited above (§18.11) — history is not rewritten. The
reviewer accepted the architecture in direction and confirmed the FIX-1 findings
as fixed, then named a narrower set of remaining defects:

```text
P0-1  the planner's blocking-conflict deferral disappeared after dispatch. It
      fired only while both tasks were READY, so dispatching the earlier owner
      (READY -> IN_PROGRESS) released the later task onto the same public-truth
      surface the deferral existed to serialise.
P0-2  max_sub_agents capped *new* READY candidates, not the Sub-Agents already
      running: 3 IN_PROGRESS + 4 READY (max 4) returned 4 new runnable tasks,
      i.e. 7 effective concurrent agents against a promise of 1 + <= 4.
P1-1  the evidence collector resolved task.worktree against whatever Git root the
      caller supplied, so invoking it from the task worktree produced
      <task-worktree>/.worktrees/<task>, fell back to branch-ref, and could
      report clean = true while the real worktree held uncommitted work.
P1-2  ownership used the net tree diff (base vs head). A protected file edited in
      one commit and restored in a later one left the net diff clean, so the
      task's own history could touch a protected surface undetected.
P2    the Main-Agent prompt showed an incomplete check-integration command,
      omitting --repo and --plan, which the FIX-1 gate requires.
```

The verdict is external; the development agent did not write it and does not
dispute it.

### 18.14 Remediation — AGENT-01-FIX-2

A narrow lifecycle-and-evidence hardening: no redesign of the accepted
architecture, no AGENT-02, no product capability, no Safety change. The ruleset
is unchanged.

```text
RED, all six, against the FIX-1 tree (861e9e0)
  B READY, D READY -> D deferred; B -> IN_PROGRESS -> D runnable       DEFECT
  3 IN_PROGRESS + 4 READY, max 4 -> runnable len 4 (7 effective)       DEFECT
  --repo <task worktree> -> source=branch-ref, clean=true (dirty tree) DEFECT
  modify SPEC.md, restore it before head -> net diff clean, ready=true DEFECT
  IntegrationContext(include_plan=False) -> ready=true                 DEFECT
  two commit tokens both prefixing one commit -> ready=true            DEFECT
  over-dispatched: 5 IN_PROGRESS, max 4 -> sum 5 > 4 with no signal    DEFECT
  no registered worktree, leftover dirty dir at the declared path
    -> source=branch-ref, clean=true                                   DEFECT
```

(The last two were found by an adversarial self-review of the finished change
set, reproduced against the tree, and fixed here — the FIX-2 pass is not only the
reviewer's list.)

What changed:

```text
tools/agent/lifecycle.py      EXECUTION_SLOT_STATUSES / CONFLICT_LEASE_STATUSES /
                              CONFLICT_REPLAN_STATUSES and their three predicates —
                              capacity and conflict serialisation stay separate
                              policies, defined once
tools/agent/orchestration.py  leases held across status transitions; DONE releases;
                              FAILED/CANCELLED -> replan_required_by; capacity is
                              max(0, max_sub_agents - active) and the planner adds
                              at most the free slots; the capacity block reports
                              active / active_over_capacity / available_slots /
                              selected
tools/agent/gitcmd.py         history_touched_paths — a per-commit diff-tree union,
                              both sides of renames/copies, `-m` for merge commits
tools/agent/evidence.py       net_changed_paths vs history_touched_paths; the task
                              worktree is resolved against the primary worktree root
tools/agent/validation.py     ownership and handoff equality use the history set;
                              an orchestration plan is required for ready; commit
                              evidence must be a one-to-one bijection; a terminated
                              conflict owner is exposed as replan_required
tools/agent/handoff.py        build_handoff derives changed_files from the same
                              history helper the verifier uses
tools/agent/worktree.py       primary_worktree()
.agent/prompts/main-agent.prompt.md   the complete check-integration command, and
                              the local-gate vs GitHub-Quality-Gate boundary
```

New tests: `tests/unit/agent_tools/test_orchestration_lifecycle.py`
(conflict-lease matrix, capacity matrix, slot predicates),
`tests/unit/agent_tools/test_evidence_hardening.py` (plan-required, one-to-one
commit evidence, replan visibility),
`tests/integration/test_agent_worktree_evidence.py` (a real linked worktree, both
entry points, the dirty-state proof), the transient-edit and rename/delete history
tests in `tests/integration/test_agent_git_backed_handoff.py`, and the multi-cycle
serial-integration simulation in
`tests/integration/test_multi_agent_orchestration_simulation.py`. Every fix was
rolled back once to confirm its tests turn red — a test that stays green with its
fix reverted protects nothing.

One thing FIX-2 deliberately did **not** change, recorded so a later reviewer
does not have to rediscover it: `check-integration` does not pass
`expected_remote` to `validate_repository`, so the local *read-only* gate does not
verify the repository's origin identity. The identity check lives on the write
path (`worktree create`). Wiring it into `check-integration` would fail every
remote-less test fixture and is not part of FIX-2; the platform gate and the
protected PR workflow remain the boundary that matters.

```text
AGENT-01-FIX-2 implementation complete
Self-verification complete
Awaiting independent re-acceptance
```

Level 4 remains **not** `DONE`; `Final Acceptance: PASS` / `Status: CLOSED` are
**not** written by the development agent. `AGENT-02`, `V0.3-12` and `CD-01` have
**not** started.
