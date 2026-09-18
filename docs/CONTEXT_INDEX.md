# CAN-X — Context Index (Router)

> **Document**: `docs/CONTEXT_INDEX.md`
> **Role**: **Router only.** It tells a task *which authority to load*. It is **not** an authority:
> it never states product intent, technical contracts, safety semantics or acceptance verdicts.
> Where this file and an authority disagree, the authority wins.
> **Authority order**: `PRD.md` (product intent) · `SPEC.md` (technical contract) · `AGENTS.md`
> (execution rules) · `docs/PROJECT_STATE.md` (current state).
> **Load rules**: `docs/engineering/AGENT_CONTEXT_GOVERNANCE.md`.

## Bootstrap — every task, every time (Tier 0)

```text
AGENTS.md                 execution rules
docs/PROJECT_STATE.md     where CAN-X is now
docs/CONTEXT_INDEX.md     this router
```

Then classify the task below and load its additional authority (Tier 1). Load **evidence**
(Tier 2: `docs/acceptance/**`) only when the task needs it; load **history** (Tier 3:
`docs/project-state/**`) only for an investigation.

**More context is not automatically more correct.** Load what is *sufficient*, *authoritative* and
*task-relevant* — not everything that is merely related. `PRD.md` and `SPEC.md` are **not** part of
the mandatory bootstrap; they are task authority — read the relevant sections, and read the whole
document when the task scope genuinely spans the whole authority.

## How to use this section

1. Read Tier 0.
2. Find the task class (a task may match two — take the union).
3. Load the listed Tier 1 authorities — the **relevant sections**, not necessarily whole files.
4. Add Tier 2 / Tier 3 only if that class's "load evidence / history when" applies.

## Task classes

### Frontend / UI — React, Tauri, Dockview, i18n, Trace, Plot
`Tier 1`: `SPEC.md` frontend + realtime sections · `AGENTS.md` §19–§20, §29–§30, §34–§35
*Evidence when* a component has an acceptance report (e.g. V0.3-10) and the task changes its surface.
*Not needed*: Safety architecture · AGENT-01 remediation history · unrelated acceptance reports.

### DBC — domain, parser, registry, import, workspace
`Tier 1`: `SPEC.md` DBC sections · `AGENTS.md` §21, §23 · `docs/acceptance/v0.3-10-…` **only if** the
task changes what that report accepted.
*Not needed*: `SAFETY_ARCHITECTURE.md` · `MULTI_AGENT_PROTOCOL.md`.

### Runtime / CAN — capture, devices, transport, HTTP API
`Tier 1`: `SPEC.md` Runtime / API / data-path sections · `AGENTS.md` §19–§24, §31 · the relevant
`docs/ADR/*`.
*History when* investigating a Runtime regression.

### Recorder / DataSession / persistence
`Tier 1`: `docs/ADR/0001-recorder-pressure-policy.md` · `SPEC.md` persistence sections ·
`AGENTS.md` §20, §24.
*Evidence / history when* a finalization or timing regression is suspected →
`docs/engineering/RELIABILITY_CAPTURE_FINALIZATION.md` and `PROJECT_STATE` §19.

### Safety — any TX / DIAGNOSTIC_MUTATION / ACTUATION / ECU_MUTATION / CRITICAL work
`Tier 1 (mandatory, full text — never a summary)`: `docs/architecture/SAFETY_ARCHITECTURE.md` ·
`AGENTS.md` §16–§18 · the relevant `runtime/canx/safety/**` module.
*History when* an acceptance dispute → the archive §17 (all four `NOT PASS` rounds).
**The safety summary in `PROJECT_STATE` §3 is orientation only; it never replaces the authority.**

### Agent orchestration — AGENT-01 / AGENT-02, tasks, handoffs, integration
`Tier 1`: `docs/engineering/MULTI_AGENT_PROTOCOL.md` ·
`docs/ADR/0002-parallel-development-serial-integration.md` · `docs/engineering/INTEGRATION_POLICY.md`
§17 · `.agent/config.json` · the relevant `.agent/schemas/*`.
*History when* a handoff dispute → the archive §18.
*Not needed*: DBC and capture history.

### CI / protected integration
`Tier 1`: `.github/workflows/ci.yml` · `docs/engineering/INTEGRATION_POLICY.md` · `AGENTS.md` §14.
*History when* investigating a CI-only failure → `RELIABILITY_CAPTURE_FINALIZATION.md` / the archive.

### Acceptance / verification
`Tier 1`: the **implementation authority** for the phase under acceptance (`SPEC.md` / `AGENTS.md` /
the phase's own documents) + `docs/acceptance/<phase>.md`.
*History when* the acceptance is disputed or its evidence must be reconstructed → the archive.
**Rule**: an agent never writes `Final Acceptance: PASS` — see `PROJECT_STATE` §11, `AGENTS.md` §42.

### Historical investigation / archaeology
`Tier 1`: `docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_V0.3-09.md` (V0.1 → V0.3-09) ·
`docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_RELIABILITY-01.md` (verbatim §17 SAFETY-01,
§18 AGENT-01, and the CI / merge records).
Defer everything else until the investigation localizes the question.

## Escalation

If the task class is not listed, or two classes conflict, load the authority named by the task's own
brief and record the gap in the task's handoff. Do **not** infer a class's authority from this file.

## What this file must never become

- It must not restate `PRD` / `SPEC` / `AGENTS` / `PROJECT_STATE` content.
- It must not grow into a mega-document; it stays a router.
- It must never be loaded **as** a substitute for an authority.
