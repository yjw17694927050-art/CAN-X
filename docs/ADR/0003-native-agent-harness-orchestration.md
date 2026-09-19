# ADR 0003: Native Agent Harness Orchestration

- Status: Accepted
- Date: 2026-09-19
- Decision owners: CAN-X sole author and repository integration policy
- Scope: AGENT-02 (the real multi-agent pilot) — the boundary between the host Agent
  Harness and CAN-X Agent Governance

## Context

AGENT-01 built a *governance* foundation: TaskContracts, protected/public-truth/safety
path classes, a conflict classifier, a dependency DAG, Git-backed ownership evidence, a
stale-base predicate, serial protected integration, and independent acceptance. It
deliberately built **no** execution runtime — `MULTI_AGENT_PROTOCOL.md` §4 says so in
writing ("no orchestration daemon, no scheduler, no server").

AGENT-02-PREP-01 then designed the first real pilot (1 Main Agent + 2 Sub-Agents, C0,
ownership-disjoint, both contracts `PLANNED`) and audited R01–R50 against real
executable behaviour. The audit returned `READY FOR PILOT` and the work is
`Engineering Acceptance: PASS · Integration: HOLD`.

While preparing to actually run that pilot, the execution environment itself was
inspected. **The host Agent Harness is Tianshu, and it already supplies multi-agent
orchestration natively.** Verified on this machine (Tianshu `3.22.0`, `product:
tianshu-desktop`, `mode: code`, licence tier `pro`):

```text
command surface      "/team" × 6 · "/scout" × 6 · "/council" × 5 · "/plan" · "/galaxy" · "/starflow"
                     (literal command strings in the shipped runtime)
worker runtime       rivet-runtime/agent/worker-process/child.js —
                     DelegationCoordinator · runWorkerSession · buildWorkerRuntime
worker count         resolveMaxWorkers(): config.workers.maxWorkers → process.env.RIVET_MAX_WORKERS → 3
tool scoping         filterToolRegistry(registry, allowedTools) — a worker gets a filtered registry
worker profiles      profileRegistry.writeProfiles() — read-only vs write worker profiles
context isolation    subagentPromptBlocks(); the child builds its own PromptEngine, it does
                     not inherit the Main Agent's frozen prefix
model routing        config.workers.{profiles,routing,patcherTier,escalationCap}
                     e.g. code_edit → cheap-flash, planning → capable
worker results       a structured envelope: workOrderId · status · summary · findings ·
                     artifacts · changedFiles · risks · nextActions · evidenceStatus · failureReason
persisted sessions   TianshuData/.rivet/subagents — 100 work-order records (wo_*.json + *.session.jsonl)
worker isolation     workerIsolationMode() ← RIVET_WORKER_ISOLATION (default enabled); the runtime
                     builds an "isolated snapshot worktree" and treats in-place verification as a
                     caveat; Session Manager also has an isolatedWorktree session mode
                     (per-session worktree + branch + baseline HEAD, squash-merged back)
nested delegation    maxDelegationDepth; steering (steerSeed / steer drain); checkpoint / priorMessages
```

The question this ADR must answer:

> If the host already *is* a multi-agent orchestrator, what is CAN-X still for?

## Decision

**CAN-X adopts the host Agent Harness for execution and orchestration. CAN-X does not
implement a competing Agent Runtime. CAN-X retains its repository-specific governance,
Safety, evidence and integration controls.**

The boundary is a division of labour, not a layer to be re-implemented twice:

```text
Tianshu Harness owns                    CAN-X owns
──────────────────────────────────      ──────────────────────────────────────────────
agent runtime                           repository identity
main / orchestrator execution           Task ownership policy (allowed/forbidden paths)
sub-agent / worker spawning             protected_paths · public_truth_paths
worker sessions + lifecycle             Safety path classification (the one RiskLevel)
parallel scheduling                     C0–C4 conflict policy where still useful
context isolation between workers       Git historical evidence (history_touched_paths)
runtime model routing                   branch / base evidence + stale-base protection
native /team /scout /council            handoff verification
conversation runtime                    Quality Gate + protected integration
                                        independent acceptance boundary
```

The flow the pilot will follow:

```text
User / independent reviewer
        ↓
Tianshu Main Agent
        ↓
/team
        ↓
Worker A        Worker B
   ↓               ↓
CAN-X ownership / protection constraints   (TaskContract fed as the worker's brief)
   ↓               ↓
tests + Git evidence
   └──────┬────────┘
          ↓
Main Agent validation      (validate-handoff → check-integration)
          ↓
serial protected integration   (one PR at a time, onto the current head — ADR-0002)
          ↓
independent acceptance
```

The Harness performs **execution**. CAN-X performs **governance and evidence**.

## Responsibilities — stated at the level that keeps them from drifting

```text
Harness side (do not rebuild in CAN-X)
  how a worker is spawned, which model it runs on, how many run at once,
  how their contexts are kept apart, how a worker session is persisted,
  how the native commands (/team /scout /council) behave.

CAN-X side (do not delegate to the Harness)
  what a worker is allowed and required to do (TaskContract),
  which paths are protected / public-truth / safety,
  whether the history actually touched only owned paths (Git evidence),
  whether the base is the integration head (stale-base predicate),
  whether the change may be integrated (Quality Gate + protected merge),
  who may declare the phase done (independent acceptance, never the author).
```

The key asymmetry, in one line: **the Harness decides *how* a worker executes; CAN-X
decides *what* a worker may do and *whether the result is true*.**

## AGENT-01 component classification (audit, not deletion)

AGENT-01 is not wasted work. Each component is classified. **No large deletion or
refactor is performed in this task** — the boundary is established first.

```text
KEEP — CAN-X governance (repository-specific, the Harness cannot supply it)
  .agent/config.json                         repository identity, path classes, caps
  tools/agent/paths.py                       pattern overlap (protected vs owned)
  tools/agent/conflicts.py                   C0–C4 classification
  tools/agent/graph.py · orchestration.py    dependency DAG, capacity accounting
  tools/agent/lifecycle.py                   conflict leases vs execution slots
  tools/agent/evidence.py · gitcmd.py        Git-backed evidence (history_touched_paths)
  tools/agent/validation.py                  ownership, stale-base, integration readiness
  tools/agent/worktree.py                    fail-closed git worktree lifecycle
  .agent/tasks/*.json                        the TaskContract a worker is handed

ADAPT — integrate with native orchestration (still CAN-X, reshaped)
  .agent/prompts/{main,sub}-agent.prompt.md  become the brief fed *to* a native worker,
                                             not a dispatch mechanism of our own
  tools/agent/handoff.py                     consume/validate native worker output rather
                                             than owning a second handoff runtime
  tools/agent/cli.py                         the governance commands a Main Agent runs
                                             around a native /team dispatch
  docs/engineering/AGENT_02_PILOT_PLAN.md    rewritten around native /team (see below)

HARNESS-OWNED — must not be expanded further inside CAN-X
  agent runtime / worker spawning            provided by the Harness
  scheduler / capacity daemon                provided by the Harness
  message broker / conversation runtime      provided by the Harness
  model router                               provided by the Harness (config.workers.routing)
  worker context manager                     provided by the Harness (subagentPromptBlocks)

DEPRECATE-LATER — duplicated by the Harness, no deletion yet
  any CAN-X-side assumption that it must *produce* worker sessions / dispatch prompts
  any CAN-X-side scheduler or worktree *allocation of runtime capacity* (as opposed to
  the governance adapter below, which is kept)
```

`docs/engineering/MULTI_AGENT_PROTOCOL.md` §4 already says CAN-X has "no orchestration
daemon, no scheduler and no server" — this ADR makes that a positive boundary rather than
an accident.

## The two open questions the pivot changes

### Worktree isolation → CAN-X worktree helper retained, as a **governance adapter**

Observed, not assumed: the Harness *does* supply filesystem isolation
(`RIVET_WORKER_ISOLATION` → "isolated snapshot worktree"; Session Manager
`isolatedWorktree` → per-session worktree + branch + baseline, squash-merged back). But
the native worker delegation path builds its coordinator with `sharedWorktree: true`, and
the Harness's worktree machinery carries **no CAN-X concept of a task id, an
`agent/<task-id>-<slug>` branch name, an `allowed_paths` surface, a `base_sha` or a
fail-closed cleanup predicate**.

So `tools/agent/worktree.py` is **kept**, reclassified from "runtime allocation" to
**governance adapter**: it encodes the CAN-X branch convention, binds a worktree to a
task, and refuses unsafe states (`agent.worktree_dirty`, `agent.branch_unmerged`) before
removal. That is governance the Harness has no reason to implement and CAN-X cannot
delegate. Revisit only if a future Harness version exposes branch-naming and ownership
hooks.

### Handoff → consume native output, retain a minimal CAN-X validation envelope

The Harness already emits a structured worker result (`workOrderId · status · summary ·
findings · artifacts · changedFiles · risks · nextActions · evidenceStatus ·
failureReason`). CAN-X therefore does **not** build a second handoff runtime. It keeps a
**minimal validation envelope**: `validate-handoff` + `check-integration`, because these
verify Git-backed ownership and base ancestry — facts the Harness's result envelope
describes but does not *authorise* against a repository. The R29 finding ("the handoff
*write* half has no CLI command") is reframed: the write half is now the native worker's
result, and CAN-X's job is to validate it, not to produce a competing one.

## Rejected alternative: "build our own complete Agent runtime"

The alternative is to keep AGENT-01 growing toward a self-contained Agent Runtime —
worker spawning, a scheduler, a message bus, a model router, a session store — so CAN-X
owns the whole stack.

Rejected. It would duplicate, inside a CAN engineering workbench, machinery the host
already runs, against three concrete costs:

1. **Two sources of truth for the same runtime.** A CAN-X scheduler and the Harness
   scheduler would both decide "how many workers, which model, which context" — and the
   one CAN-X wrote would be the less exercised of the two.
2. **It is not the project.** CAN-X is an *Agent-native professional CAN engineering
   workbench* (`PRD.md`). Its differentiated work is CAN capture, DBC, Safety, evidence
   and protected integration — not an LLM orchestration runtime. Building the latter is
   `AGENTS.md` §45 overbuild.
3. **It cannot win on the axis that matters — safety.** A home-grown runtime would still
   have to prove the properties the Harness already has (context isolation, worker
   lifecycle, cancellation), while CAN-X's real safety obligations (the single
   `RiskLevel`, TX/approval gates) live in `canx/safety/` regardless of who spawns the
   worker.

The honest framing: **the Harness is an environment, not a competitor.** The correct
engineering move is to define the seam with it, not to re-implement it behind the seam.

## Consequences

- **AGENT-01's governance work is preserved and re-aimed.** TaskContracts, path classes,
  conflicts, the DAG, Git evidence, stale-base and the worktree helper all remain
  load-bearing — they now govern workers the Harness spawns.
- **The pilot's execution mode changes** from "CAN-X dispatches Sub-Agents" to
  "Tianshu `/team` dispatches workers, under CAN-X TaskContracts". See
  `docs/engineering/AGENT_02_PILOT_PLAN.md`.
- **A stated non-goal becomes a hard refusal.** CAN-X must not add a provider-specific
  Agent runtime, a sub-agent spawning engine, a message broker, a scheduler daemon, a
  parallel-worker runtime, a model router, a conversation runtime, or a duplicate
  Harness context manager.
- **`RIVET_WORKER_ISOLATION` and `sharedWorktree` become named runtime facts**, recorded
  here and in the readiness doc, with the observed default attached — so a later reader
  does not re-derive them from scratch (or, worse, guess from marketing text).
- **A real pilot is still unproven.** This ADR changes the *architecture of the pilot*,
  not its status: `REAL PILOT NOT STARTED`. No worker has run against a CAN-X
  TaskContract yet, and nothing here may be cited as a pilot result.
- **Integrated through protected PR #18.** Head `cd44abc` → `main` `d652b4c` (entry: protected merge,
  no bypass), post-merge main CI `35415938987` SUCCESS (classification `full`). The ADR's own status is
  `Accepted`; the real pilot it enables remains **NOT STARTED**.

## Migration / compatibility implications

```text
no product code changes            this pivot is documentation + boundary, not a rewrite
no CI classifier change            docs/** stays docs-only; .agent/config.json is untouched
no test weakening                  the AGENT-CONTEXT-PROTECTION tests are preserved exactly
TaskContracts retained             .agent/tasks/AGENT-02-{A,B}.json remain the governance
                                   contract fed to a native worker — CAN-X defines WHAT, the
                                   Harness decides HOW. They are not a spawning mechanism.
worktree helper retained           as a governance adapter (above)
handoff layer retained, reduced    as a validation envelope (above)
deprecation deferred               duplicated-by-Harness pieces are named, not deleted; a
                                   future task may retire them once the boundary has held
```

Revisit this ADR when either (a) a real `/team` pilot has run and its evidence is
reviewed, or (b) a Harness version exposes ownership/branch hooks that make part of the
CAN-X governance adapter redundant.

## Native execution mode — the pilot's first architectural finding (V0.3-12-FIX-1)

### What the pilot measured

The first real AGENT-02 pilot ran three native workers, and it **measured** the
per-worker filesystem model rather than inferring it: a 2 s sampler over the
whole dispatch window saw a **constant worktree set** — no new worktree, no new
branch — and all three workers' artifacts landing in the same working tree, on
the same branch, in the same `git status`. The host Harness runs its default
execution mode: **one shared worktree**.

`AGENT_02_PILOT_PLAN.md` §6 named this as the thing the pilot had to observe.
The observation is now a recorded fact, and it invalidated an assumption the
AGENT-01 execution model was built on.

### The gap

AGENT-01 modelled a Sub-Agent as *a branch plus a registered worktree*. The task
contract froze both, and the final gate proved a delivery by resolving that
worktree back through Git (`tools/agent/evidence.py:resolve_task_worktree`).
Under native shared execution **neither coordinate exists** — so the contract
was free to declare a branch the run never created, and the collector failed
closed before it could look at the delivery at all:

```text
declared branch    agent/V0.3-12-A-desktop-runtime-project-client
declared worktree  .worktrees/v0.3-12-a
actual             everything on feature/v0.3-12-… in the main worktree
result             agent.git_state_error
                   fatal: Needed a single revision
```

This is a real architectural gap, not an operator mistake: the governance model
had no way to *describe* the execution mode the host actually uses. Its
consequence in the pilot was that all three tasks stayed `PLANNED` and no
`validate-handoff` / `check-integration` was ever run.

### Decision

A task declares **how its worker executes**, and the governance gates read the
delivery accordingly. Nothing about *ownership* changes.

```text
execution_mode: "isolated-worktree"   the AGENT-01 model, unchanged and still the default
                                      branch + worktree are required and frozen; the
                                      delivery is resolved through the task worktree

execution_mode: "native-shared"       the host Harness's default
                                      the contract declares NEITHER coordinate, because
                                      declaring one would be a lie Git cannot resolve;
                                      the delivery is the real commit range
                                      base_sha..head_sha on the integration branch
```

Three consequences, each deliberate:

* **`base_sha` means "the integration head this delivery was applied to".** In
  isolated mode that is the head the branch was cut from. In native-shared mode
  the integration head advances as each unit lands, so `base_sha` is the head
  immediately *before* that unit — which makes the delivery range exactly the
  task's own commits, and keeps `history_touched_paths(base, head)` the single
  ownership authority.
* **`check_base`'s "base == integration head" equality is the isolated
  predicate, not a universal one.** A worker branch must be rebased onto the
  head before it merges; a native-shared delivery is *already* on the branch, so
  equality is impossible for every unit but the first. It is replaced by a
  stronger, repository-proved relation: `base_sha` **and** `head_sha` must both
  be ancestors of the caller's integration head. The refusal code stays
  `agent.base_stale`, and the repository stage remains mandatory — without a
  repository the verdict is `agent.integration_context_incomplete`, never
  `ready`.
* **`integration_paths` names Main-Agent-owned paths inside a worker's delivery
  commit.** In a shared worktree the Main Agent's cross-boundary integration test
  lands in the same commit as the worker's module. The field exists so that is
  declared rather than smuggled: it is refused if it can reach a protected,
  public-truth or safety path, or the task's own `forbidden_paths`, and it
  participates in conflict classification exactly like `allowed_paths`.

### What is NOT relaxed

The mode changes *where the delivery lives*. It does not change *who may touch
what*, or *what counts as proof*:

```text
ownership                    allowed_paths + forbidden_paths, decided on Git history
protected / public-truth /
  safety classification      unchanged, and integration_paths cannot reach them
Git history authority        history_touched_paths, never the net tree delta
stale base                   still fails closed, now proved by ancestry rather than equality
self-reported compliance     still not evidence - the flag is re-derived
integration authority        still the Main Agent's; there is no `evidence=` parameter
Harness scope                spawns and runs workers; it decides nothing about permission
```

A worker still cannot claim another task's paths, cannot reach a governed path
(including through a transient edit that the net diff hides), and cannot obtain
integration readiness by asserting it. Each of those is asserted by a test in
`tests/unit/agent_tools/test_native_shared_execution.py` and
`tests/integration/test_agent_native_shared_evidence.py`, on real throwaway
repositories.

### A commit that no task claims

In native-shared mode the **Main Agent** commits; a worker cannot. A commit on
the integration branch that no task's range covers is therefore Main-Agent work,
governed by the Main Agent's own obligations (protected paths, public truth, the
PR review) rather than by a worker contract. The gate's job is to prove *no
worker reached outside its surface*, and it does that over each task's own
range. This is stated so a later reader does not mistake it for an unenforced
assumption: it is a property of who is allowed to commit, not a hole in the
check.

### Revisit when

(a) the Harness exposes per-task worktree/branch creation with ownership hooks —
then the isolated mode may become the native one; or (b) a Harness version
changes its default isolation mode, which would make this section's measured
fact stale and the pilot's recorded observation must be re-taken.
