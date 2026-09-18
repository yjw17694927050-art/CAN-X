# CAN-X — Agent Context Governance

> **Document**: `docs/engineering/AGENT_CONTEXT_GOVERNANCE.md`
> **Scope**: How much context an agent loads, in what order, and why.
> **Status**: in force from DOC-GOV-01 (2026-09-18).
> **Related**: `AGENTS.md` §2 (the rule), `docs/CONTEXT_INDEX.md` (the router),
> `docs/PROJECT_STATE.md` §11 (compaction rule).

## 1. The problem this solves

Every agent task used to start by loading the whole project state file — by 2026-09-18 it had grown
to 140 KB / 2 829 lines, most of it the blow-by-blow history of review rounds that had already been
accepted and closed. Two costs followed:

- **Cost per task.** The mandatory startup read had become larger than the work most tasks needed.
- **Cost to the *current* truth.** When the file holds every historical round, the statement that is
  true *now* is hard to find among the statements that were true *then*.

The fix is not to delete history. It is to **route** it: keep every fact, load it when the task
needs it, and keep the mandatory read small.

## 2. Four layers of context

```text
L0  Bootstrap context      — every task, every time
L1  Task authority         — loaded by task class
L2  Evidence / acceptance  — loaded only when the task needs evidence
L3  Historical / archive   — loaded only for an investigation
```

### L0 — Bootstrap context

The minimum an agent must know before it can act at all. Answers: *where is CAN-X now, what are the
rules, what is next, and which authority do I load for this task?*

```text
AGENTS.md                    execution rules             (the rule)
docs/PROJECT_STATE.md        where CAN-X is now          (current state)
docs/CONTEXT_INDEX.md        which authority to load     (the router)
```

### L1 — Task authority

Loaded **by task class**, relevant sections first. This is `PRD.md`, `SPEC.md`, the ADRs,
`docs/architecture/**`, `docs/engineering/**` and any domain-specific authority named by the router.

**`PRD.md` and `SPEC.md` are L1, not L0.** They are the product intent and the technical contract,
and a task reads the parts that bear on it. "Read PRD and SPEC" does not mean "read both in full on
every task" — but reading a whole authority **is** allowed, and is necessary, when the task genuinely
spans it. There is no "never read a full authority" rule; the standard is *sufficient*,
*authoritative*, *task-relevant*.

### L2 — Evidence / acceptance context

`docs/acceptance/**`, RELIABILITY-01 evidence, CI evidence, per-phase verification. Loaded when the
task is an acceptance, a regression investigation or a fault investigation — **not** by default.

### L3 — Historical / archaeology context

`docs/project-state/**` — the lossless archives. Loaded only for a historical audit, a regression
investigation, an architecture archaeology question, an acceptance dispute, or a decision
reconstruction.

## 3. Load order

```text
1. Load L0 (bootstrap).
2. Classify the task (see docs/CONTEXT_INDEX.md).
3. Load that class's L1 authority — the relevant sections first; load a whole authority when the
   task scope genuinely spans it.
4. Load L2 evidence only if the task requires evidence.
5. Load L3 history only if the task requires an investigation.
```

The order is a *sufficiency filter*, not a completeness checklist. An agent that has read L0 and its
class's L1 authority and can answer the question should stop reading and start working.

## 4. The one principle

> **More context is not automatically more correct.**

Context is loaded to be **sufficient**, **authoritative** and **task-relevant** — not to be complete.
Loading an authority that does not bear on the task adds tokens and, worse, invites a decision to be
made from the wrong document.

Corollary — **a summary never replaces an authority.** `PROJECT_STATE` §3 summarises the safety
invariants; a task that touches a dangerous operation must still read
`docs/architecture/SAFETY_ARCHITECTURE.md`. The router points at the authority; it is not one.

## 5. Authority order (unchanged by routing)

```text
PRD.md                    = product intent
SPEC.md                   = technical architecture / contract
AGENTS.md                 = execution rules
docs/PROJECT_STATE.md     = current state
docs/CONTEXT_INDEX.md     = router only — it never overrides any of the four above
```

Routing reduces *how much* is read. It changes no responsibility and weakens no rule. If the router
and an authority disagree, **the authority wins** and the router is the thing that is wrong.

## 6. Who states the policy

The policy is stated once and mirrored, not re-invented:

```text
AGENTS.md §2                         the binding rule and the tiers
docs/CONTEXT_INDEX.md                the per-task-class routing table
docs/engineering/AGENT_CONTEXT_GOVERNANCE.md   this file — the layer definitions and load order
.agent/prompts/main-agent.prompt.md  the Main Agent's startup read (points at the three above)
.agent/prompts/sub-agent.prompt.md   the Sub Agent's startup read (scoped to its own task)
```

These five must not drift. A prompt that restates an old "read everything" list creates a second,
contradictory policy — exactly what DOC-GOV-01 removed.

**A Sub-Agent does not inherit the Main Agent's history context.** It loads its own task contract,
the bootstrap, and the authority relevant to its scope; nothing else.

## 7. What this does not do

- It does not reduce the *observable API token usage* of a running agent. That would need real
  per-session usage measurement, which this repository does not yet have. DOC-GOV-01 measures the
  **structural** context a task must load, not observed consumption.
- It does not delete anything. Every fact removed from `PROJECT_STATE.md` exists verbatim in
  `docs/project-state/` (§8).
- It does not change the safety authorities, the technical contract, the acceptance history or the
  protected-integration rules.

## 8. Where the history went

```text
docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_V0.3-09.md
    the full PROJECT_STATE.md through the V0.3-09 close
docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_RELIABILITY-01.md
    the full PROJECT_STATE.md as it stood at the RELIABILITY-01 close, immediately before the
    DOC-GOV-01 compaction — including the verbatim §17 (SAFETY-01) and §18 (AGENT-01) review /
    remediation narrative and the CI / merge records
```

Both are byte copies with recorded MD5s; see `docs/project-state/README.md`.

## 9. Adding a new authority

When a task class or authority is added:

```text
1. Add the authority document to its real location (docs/ADR, docs/engineering, …).
2. Add the task class (or extend one) in docs/CONTEXT_INDEX.md — Tier 1 line + "not needed" line.
3. Do not add it to L0 unless every task genuinely needs it. Almost nothing does.
4. If it is a Safety authority, it is read in full by every task in its class — never by summary.
```
