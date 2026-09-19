# CAN-X — Multi-Agent Orchestration Protocol

> **Document**: `docs/engineering/MULTI_AGENT_PROTOCOL.md`
> **Applies To**: the Main Agent and every Sub-Agent working on CAN-X
> **In force from**: AGENT-01 (Multi-Agent Orchestration Foundation)
> **Owner**: CAN-X sole author
> **Machine-checked by**: `tools/agent/` (config, contracts, graph, conflicts, validation, worktree, handoff, CLI)

This document is the protocol half of AGENT-01. The enforcement half is
`tools/agent/`, and the two are not interchangeable: a rule that is only written
here is a rule a tired agent can skip, which is why every gate below names the
validator that refuses it.

---

## 1. What AGENT-01 is

AGENT-01 does not add a way to run four AI agents. It adds the *contracts,
isolation and evidence* that make running up to four agents safe to attempt
later, in AGENT-02. Nothing in this phase spawns an agent, schedules a process,
brokers a message or executes a command taken from a task or handoff file.

The frozen principle:

> **Parallelism is earned by isolation.**

```text
no clear ownership             → no parallelism
no stable contract             → no parallelism
a high probability of conflict → serial
shared core truth              → Main Agent only
```

Development may be parallel. **Integration is not.**

---

## 2. Roles

### 2.1 Project owner

Owns the repository, the ruleset and the acceptance verdict. The only role that
may write `Final Acceptance: PASS` / `Status: CLOSED`, or use break-glass.

### 2.2 Main Agent

Owns:

```text
task decomposition        dependency analysis        the task DAG
ownership assignment       contract freezing          branch allocation
worktree allocation        handoff validation         integration order
conflict resolution        final status synthesis
```

The Main Agent does **not** implement every subtask — otherwise the structure
buys nothing. It is the only identity that may own a protected path
(`.agent/config.json` → `protected_paths`), and the only one that may run the
worktree and integration commands against the integration branch.

### 2.3 Sub-Agent

Owns exactly one task:

```text
one task        one bounded ownership surface     one branch
one worktree    implementation                     unit tests
integration tests (where applicable)              lint / typecheck
one PR                                                 one handoff
```

A Sub-Agent must never:

```text
widen its scope                     redefine a public contract
edit a path it does not own         edit another agent's ownership
merge its own PR                    self-accept its own work
use break-glass
```

The last three are not etiquette. There is no code path for a Sub-Agent to
merge, accept or disable the gate.

### 2.4 Independent reviewer

`development ≠ acceptance` still holds. A Main Agent may conclude
`READY_FOR_INTEGRATION`; it may never conclude `Final Acceptance: PASS`
(`docs/engineering/INTEGRATION_POLICY.md` §15,
`docs/PROJECT_STATE.md` §11).

### 2.5 Maximum parallelism

```text
1 Main Agent + up to 4 Sub-Agents
```

`.agent/config.json` pins `max_sub_agents: 4` and `AgentConfig.from_dict` refuses
anything outside `1..4`. Since FIX-2, `plan()` enforces the cap against the
Sub-Agents **already running**, not against the number of new candidates:

```text
active_execution_ids   tasks whose status occupies a Sub-Agent slot
                       EXECUTION_SLOT_STATUSES == { IN_PROGRESS }
available_slots        max(0, max_sub_agents - len(active_execution_ids))
selected_new_dispatch  the first available_slots candidates, in integration order
invariant              the planner adds at most available_slots, so
                       len(active) + len(newly dispatched)
                          <= max(max_sub_agents, len(active))
                       and an already over-dispatched set is reported as
                       capacity.active_over_capacity rather than hidden
```

The wave is cut deterministically from the integration order, and four deferral
reasons stay distinguishable in the machine output
(`plan().to_dict()` and `TaskPlanEntry.deferral_kind()`):

```text
blocked by dependency   the dependency is not DONE
deferred by conflict    an earlier conflicting task still holds the surface
deferred by capacity    ready and conflict-free, but past the available slots
requires re-plan        an earlier conflicting owner FAILED / CANCELLED
```

"Up to four" is a ceiling, not a target: the Main Agent starts the number of
Sub-Agents the DAG actually justifies.

---

## 3. Lifecycle

```mermaid
stateDiagram-v2
    [*] --> PLANNED
    PLANNED --> READY: every dependency DONE
    PLANNED --> BLOCKED: a dependency is not DONE
    PLANNED --> CANCELLED: re-planned away
    READY --> IN_PROGRESS: dispatched into its worktree
    READY --> BLOCKED: a dependency regressed
    READY --> CANCELLED
    IN_PROGRESS --> HANDOFF_READY: implementation and tests complete
    IN_PROGRESS --> BLOCKED: needs a contract change
    IN_PROGRESS --> FAILED
    IN_PROGRESS --> CANCELLED
    HANDOFF_READY --> INTEGRATING: Main Agent validated the handoff
    HANDOFF_READY --> BLOCKED
    HANDOFF_READY --> FAILED
    BLOCKED --> READY: unblocked
    BLOCKED --> FAILED
    BLOCKED --> CANCELLED
    INTEGRATING --> DONE: merged through the protected workflow
    INTEGRATING --> BLOCKED
    INTEGRATING --> FAILED
    DONE --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
```

`DONE` means *integrated into the integration branch*. It does not mean *phase
accepted*. `FAILED` and `CANCELLED` are terminal; rework is a new task/revision,
not a revived terminal one.

The transition table lives in `tools/agent/lifecycle.py:ALLOWED_TRANSITIONS`; an
edge that is not in it raises `agent.task_invalid`.

### 3.1 Exit criteria

```text
HANDOFF_READY  implementation complete · required tests all PASS · ownership valid
               branch matches the contract · handoff schema valid
INTEGRATING    dependencies satisfied · no blocking conflict · task is HANDOFF_READY
               base is the current integration head · Git-backed evidence valid
               Quality Gate green on that head (GitHub, not this tooling)
DONE           the accepted head reached the integration branch
```

`tools/agent/validation.py:evaluate_integration` returns the whole blocker set,
not just the first, so an integration loop converges instead of ping-ponging.
It reports `ready` only when it could actually prove every local requirement:
with no repository, no task set **or no orchestration plan** it reports
`agent.integration_context_incomplete` rather than quietly skipping the check.
A caller cannot bypass the conflict gate with `include_plan=False` and still
obtain `ready: true` (FIX-2 §29).

`evaluate_integration` also collects its own Git evidence: it takes a
**repository path** and never an evidence object. There is no `evidence=`
parameter, so an internally consistent dataclass cannot stand in for Git, and a
verdict asked for without a repository is `agent.integration_context_incomplete`
rather than `ready`. The sub-agent report, the handoff JSON and any caller-built
object are all untrusted input; the repository is the only authority
(FIX-4 §3-§9).

### 3.2 Local verdict versus platform gate

The two halves answer different questions and neither claims the other's job:

```text
local IntegrationReadiness   handoff schema · Git-backed evidence · ownership
                             base/current-head · dependency completion
                             conflict/deferral state · task readiness
GitHub Ruleset               Quality Gate green · protected PR workflow
merge eligibility            local READY
                             AND platform Quality Gate success on this head
                             AND base still current immediately before merge
```

`check-integration` does **not** verify the GitHub Quality Gate — `to_dict()`
says so explicitly (`github_gate.checked_here = false`). The ruleset requires it
independently; this tooling does not embed a GitHub client.

---

## 4. Ownership

> **Anything not explicitly owned is not editable.**

Ownership is a predicate over repository-relative paths, not an instruction to a
willing agent:

```text
allowed_paths      what this task may touch           (required, non-empty)
forbidden_paths    explicit holes inside that surface (they win over allowed)
```

Glob dialect, deliberately small and un-expandable:

```text
*    one path segment
**   zero or more path segments
?    one character, never a separator
```

Character classes are not supported and are matched literally, so no ownership
pattern can smuggle in a regular expression.

### 4.1 Path contract

Every path crossing the boundary is normalised first
(`tools/agent/paths.py:normalize_repo_path`). Refused:

```text
absolute paths            Windows drive prefixes        backslash separators
'..' segments             '.' or empty segments         NUL bytes
a trailing '/' on a pattern (use '/**' to own a subtree)
```

This is what makes traversal (`../../etc`), Windows/POSIX aliasing and
symlink-style surprises unrepresentable rather than merely discouraged.

### 4.2 Protected and high-contention paths

`.agent/config.json` names three classes. They are different questions and must
not be merged into one list.

| Class | Meaning | Effect |
| --- | --- | --- |
| `protected_paths` | Main-Agent-only surface: root manifests, lockfiles, CI workflows, `docs/PROJECT_STATE.md`, the AGENT-01 schemas, the central safety policy | A task whose `allowed_paths` touch one is refused with `agent.protected_path_conflict` unless the owner is `main` |
| `public_truth_paths` | Shared contracts: domain models, API surface, tool registry schema, safety package, the AGENT-01 schemas | Two tasks overlapping one classify at least C3 and may not be auto-integrated |
| `safety_paths` | `runtime/canx/safety/**`, `docs/architecture/SAFETY_ARCHITECTURE.md` | A task touching one is classified C4 and always requires serial review |

Protected paths are not permanently forbidden. They are *not parallel*: the Main
Agent either owns the change itself or re-plans the DAG so exactly one task
touches it.

### 4.3 Ownership enforcement

Ownership is decided by **the paths Git says were touched**, not by the list the
handoff reports. `validate_handoff` ignores the `ownership_compliance` flag a
handoff reports about itself — a self-assessment is not evidence — and, since
FIX-2, the authoritative set is the task's **history**: the union of every path
touched by *every* commit in `<base>..<head>`, derived per commit with
`git diff-tree --root --no-commit-id --name-status -r -m -M -C <commit>`. The
final net tree diff (`git diff --name-status -M -C base..head`) is collected too
(`net_changed_paths`), but it is not what ownership is decided on. A touched path
that is not covered by `allowed_paths`, or that hits a `forbidden_paths` entry,
raises `agent.ownership_violation`.

This closes two real holes:

- **(FIX-1 §35)** before the gate existed, a Sub-Agent could change `SPEC.md`,
  report only `runtime/canx/foo/a.py`, and the gate would validate the reported
  list against itself and pass.
- **(FIX-2 §21-§25)** before the history set existed, a Sub-Agent could change
  `SPEC.md` in one commit and restore it byte-for-byte in a later one: the net
  diff no longer mentioned `SPEC.md`, so a net-diff-only ownership gate passed —
  while both commits still land on the integration branch under a merge.

```text
base:  SPEC.md = ORIGINAL
c1:    SPEC.md = MUTATED            (touches a protected file)
c2:    SPEC.md = ORIGINAL, + owned work
head:  net diff = only the owned work        -> net-changed_paths misses SPEC.md
       history_touched_paths still contains SPEC.md -> agent.ownership_violation
```

`build_handoff()` derives `changed_files` from the **same** history helper the
verifier uses, so an honest handoff and the gate agree by construction rather
than by convention.

### 4.4 Pattern versus pattern — the `**` hole

There are two different questions, and they need two different functions:

```text
a concrete file     vs an ownership pattern     matches_pattern / matching_pattern
an ownership pattern vs a protected pattern     patterns_overlap / overlapping_pattern
```

Using the first for the second is how a task owning `**` looked as though it
never touched `SPEC.md`: `SPEC.md` does not match the *literal text* `**`.
Protected ownership is therefore checked with overlap semantics — *can this
ownership surface reach a protected path?* — and a Sub-Agent task whose surface
can reach one is refused with `agent.protected_path_conflict`.

```text
allowed_paths = ["**"]                       owner = sub-a   -> REFUSED
allowed_paths = ["**/*.md"]                  owner = sub-a   -> REFUSED
allowed_paths = ["docs/**"]                  owner = sub-a   -> REFUSED
allowed_paths = ["runtime/canx/alpha/**"]    owner = sub-a   -> accepted
allowed_paths = ["**"]                       owner = main    -> accepted
```

The fix is correct overlap semantics, **not** banning globs: a genuinely
disjoint surface is still accepted.

**RED → GREEN #1 (`test_a_handoff_that_changes_a_file_outside_allowed_paths_is_refused`).**
Before a handoff's ownership was re-derived at all, one could claim
`ownership_compliance: true` and change `SPEC.md` while its task owned only
`runtime/canx/foo/**`. The validator accepted it (RED: `DID NOT RAISE
OwnershipViolationError`). With the gate wired in, the same input is refused
(GREEN).

---

## 5. Branch and worktree isolation

### 5.1 Branch convention

```text
Sub-Agent:   agent/<task-id>-<lower-kebab-slug>
Main Agent:  any valid ref (CAN-X uses maintenance/<slug>, docs/<slug>, …)
```

A Sub-Agent branch must encode its own task id, so a task cannot be developed on
the wrong branch by accident and then discovered at review time.
`validate_task` refuses a branch that does not start with `branch_prefix`, does
not carry the task id, or whose slug is not lower-case kebab-case.

### 5.2 One branch, one agent, one task

```text
Sub-Agent A + Sub-Agent B → the same branch     FORBIDDEN
```

`create_worktree` refuses a branch that already exists in `refs/heads`, and
refuses a branch that is checked out in another worktree.

### 5.3 Worktree layout

```text
repo-main/
  .worktrees/
    agent-02-a/     one branch · one agent · one task
    agent-02-b/
```

`.worktrees/` is gitignored, and it must stay that way: `create_worktree`
refuses a repository with uncommitted work, and an untracked worktrees directory
would make the repository dirty the moment the first task worktree exists.

### 5.4 The helper

```text
python -m tools.agent.cli worktree create  --task-id AGENT-02-B \
        --branch agent/AGENT-02-B-handoff-validator \
        --path .worktrees/agent-02-b --base <40-hex>
python -m tools.agent.cli worktree list
python -m tools.agent.cli worktree validate --path .worktrees/agent-02-b \
        --branch agent/AGENT-02-B-handoff-validator
python -m tools.agent.cli worktree remove  --path .worktrees/agent-02-b
```

There is no orchestration daemon, no scheduler and no server: four subcommands
over `git worktree`, which is the whole platform requirement.

### 5.5 Fail-closed rules

Before creating, the helper confirms:

```text
the directory is a git work tree, with the expected origin
the base is a full 40-hex commit that resolves
the branch is not already spoken for, and not checked out elsewhere
the target path is inside the repository and is not already a worktree
the repository has no uncommitted work
```

Before removing, it confirms:

```text
the path is a registered worktree of this repository, and not the main one
the worktree has no uncommitted work
the worktree HEAD is already contained in the integration branch
      (unless --allow-unmerged is passed explicitly)
```

Removal goes through `git worktree remove`. There is no `rm -rf` anywhere in the
module, and branch deletion uses `git branch -d`, never `-D`. A refused cleanup
leaves the evidence in place: a dirty worktree is never destroyed to tidy up.

---

## 6. Dependencies

```text
task_id
depends_on[]      other task ids that must be DONE first
blocks[]          derived: the inverse relation
status            one of the lifecycle states
```

Readiness is *derived*, never trusted: a task that has not started is `READY`
only when every dependency is `DONE`. A task already labelled `READY` whose
dependency is incomplete is demoted to `BLOCKED` — the label does not buy
scheduling. A `FAILED` or `CANCELLED` dependency is reported as unsatisfiable so
the Main Agent re-plans instead of waiting forever.

`TaskGraph.build` refuses a duplicate id, an unknown dependency, a self
dependency and a cycle (`agent.dependency_invalid`, `agent.dag_cycle`).

This is a dependency model, not a workflow engine. There is no retry policy, no
cron, no persistence and no queue.

---

## 7. Handoff

### 7.1 What a handoff contains

```text
task_id · agent · branch · base_sha · head_sha · commits · changed_files
ownership_compliance · tests[name, command, result] · lint · typecheck · ci
dependencies · known_issues · deferred_items · ready_for_integration
```

A handoff stores **facts, evidence and decisions**. It has no field for private
reasoning, no scratchpad and no chain-of-thought, and the `command` strings it
carries are text for a human to read: **nothing executes a command taken out of
a handoff file.**

`result` is one of `passed / failed / skipped / not_run`. `not_run` is a
first-class answer — "we did not run it" must be reportable as itself, never
smuggled in as a pass.

### 7.2 Git-backed verification (FIX-1)

A handoff is a **report format**. Git repository state is the **authority**. The
Main Agent's verifier independently compares the two:

```text
TaskContract            what is permitted
real repository path    the only entry point to evidence (FIX-4)
real worktree / branch  the strongest local source of truth
real base..head history what actually happened
real changed paths      the authority for ownership
local IntegrationReadiness  the verdict
```

`tools/agent/evidence.py` collects, and
`tools/agent/validation.py:verify_repository_evidence` compares:

```text
repository identity     == config.repository           agent.git_state_error
actual worktree branch  == task.branch                 agent.branch_mismatch
actual worktree path    == task.worktree               agent.worktree_conflict
actual HEAD             == handoff.head_sha            agent.handoff_invalid
handoff.base_sha        == task.base_sha               agent.handoff_invalid
base is an ancestor of head (git merge-base)           agent.base_not_ancestor
worktree clean                                         agent.git_state_error
actual history paths    -> ownership validation        agent.ownership_violation
handoff.changed_files   == actual history paths        agent.handoff_evidence_mismatch
handoff.commits         <=> actual base..head range    agent.handoff_evidence_mismatch
                        (one-to-one, bijective)
```

Every row above is read from the repository. `evaluate_integration` accepts no
evidence object from its caller: a self-consistent dataclass proves nothing, and
a final `ready: true` built from one would be a verdict about the caller's typing
rather than about the code. `RepositoryEvidence.repository_identity` is likewise
**diagnostic** metadata - the binding is enforced by
`validate_repository(expected_remote=config.repository)` reading the real origin,
never by comparing that field (FIX-4 §8).

Repository identity validation fails closed on an origin shape it does not
recognise, credential-bearing URLs such as
`https://user:token@github.com/owner/repo.git` included. It never echoes the URL
back: the failure carries `origin_supported: false` and nothing else, because
that error travels onward into CLI output, CI logs and captured structured
errors. A recognised-but-wrong repository is the other case - a canonical
`owner/repo` contains no credentials, so it is reported as itself
(FIX-4 §12-§18).

Three consequences worth stating plainly:

- **A handoff cannot invent a base.** `handoff.base_sha` must equal
  `task.base_sha`; a legitimate rebase updates the *task contract* and re-issues
  it. Typing the current `main` into JSON does not make the delivery based on it.
- **Ancestry is proved, not assumed.** `git merge-base --is-ancestor` decides it;
  two values looking like commit ids proves nothing.
- **Both sides of a rename or copy count, and so does a reverted edit.** Paths
  come from the full commit history, so a protected file renamed into an owned
  subtree still appears under its old path, a deleted protected path is still a
  protected-path modification, and a transient edit that a later commit reverts
  is still a touched path.

The commit comparison is **one-to-one** (FIX-2 §30): every reported token must
resolve to exactly one actual commit — not zero (unknown token), not two
(ambiguous prefix) — no token may repeat, and the mapping must cover every
actual commit. Prefix membership plus equal counts is not enough: two tokens that
both prefix the *same* commit satisfy a membership test while a second real
commit is never represented.

When no task worktree is registered, evidence falls back to the task *branch
ref* — still Git, still required to resolve to the claimed head — and the
fallback is recorded in `evidence.source` rather than hidden. In that case
`clean` means *"no live task worktree exists to contain uncommitted changes"*,
**not** "a worktree was inspected and found clean" (FIX-2 §20).

The task worktree itself is located by resolving `task.worktree` against the
repository's **primary** worktree root (`git worktree list`, main worktree first),
never against whichever worktree invoked the tool. `--repo` may be the main
repository root or the task's own worktree; both must find the same registered
task worktree, and a dirty one must block readiness from either entry point
(FIX-2 §15-§19).

The contract freezes **both** identities - the branch and the worktree path - and
Git must agree with both. Every registered worktree is examined and the state is
classified (AGENT-01-FIX-3 §4-§12):

```text
A  declared path + branch both match          -> use that worktree
B  declared path exists, branch differs       -> agent.worktree_conflict
C  branch registered at a different path      -> agent.worktree_conflict
D  ambiguous metadata (same path/branch x2)   -> agent.worktree_conflict
E  neither the path nor the branch registered -> branch-ref fallback
```

A mismatch is **never silently repaired**. Following the branch to a different
worktree would make `task.worktree` non-authoritative, and falling back to the
branch ref would ignore a live worktree that may hold dirty work: with the
declared path absent and the branch registered elsewhere, the old collector
reported `source = branch-ref`, `clean = true` without ever inspecting the live
worktree. The Main Agent decides whether a contract mismatch needs a new task
revision.

`agent.worktree_conflict` carries `task_id`, `expected_branch`,
`expected_worktree`, `actual_worktree`, `actual_branch` and `declared_path`.

Repository-bound evidence (AGENT-01-FIX-3 §16-§20): the final integration gate
collects evidence with `expected_repository = config.repository`, so "some valid
Git repository" is never enough. A wrong owner/repo, a lookalike name, an
unsupported host or a missing origin is `agent.git_state_error` and the verdict is
not ready. `worktree create` already checked identity on the write path; the
read-only integration path now does too. `RepositoryEvidence.repository_identity`
records the canonical identity for diagnostics. `build_handoff()` stays a
producer convenience and is deliberately not identity-bound - the consumer is the
trust boundary.

`build_handoff()` remains the convenient producer and is explicitly **not** a
trust boundary: the consumer assumes the JSON may have been edited after
generation. The same principle the Safety kernel already uses — a caller's
report is not authority — applies here.

### 7.3 Required tests

For `ready_for_integration == true`, **every** name in `task.required_tests` must
be present exactly once and reported as `passed`. `failed`, `skipped` and
`not_run` remain valid *reports* — a handoff that is honest about a test it did
not run is more useful than one that lies — but none of them is an
integration-passing result for a required test. Duplicate test names are refused
outright, so a passing copy cannot shadow a failing one.

### 7.4 Validation

`validate_handoff` refuses:

```text
a missing required field                    (agent.handoff_invalid)
a handoff for a different task              (agent.handoff_invalid)
a branch other than the task's branch       (agent.branch_mismatch)
a base or head sha that is not 40 hex       (agent.handoff_invalid)
head_sha == base_sha (no commit was made)   (agent.handoff_invalid)
zero reported tests                         (agent.handoff_invalid)
changed_files outside allowed_paths         (agent.ownership_violation)
changed_files inside forbidden_paths        (agent.ownership_violation)
"ready" while a required test is missing    (agent.handoff_invalid)
"ready" while a reported test failed        (agent.handoff_invalid)
"ready" while the handoff declares non-compliance (agent.handoff_invalid)
```

### 7.3 Where handoffs live

```text
.agent/handoffs/<task-id>.json    machine-readable
.agent/handoffs/<task-id>.md      human-readable
```

Both are produced by `tools/agent/handoff.py:write_handoff`, which builds the
handoff from the *live* repository state — branch, head, commits and changed
files are read from git, not typed in by the agent.

---

## 8. Conflicts

```text
C0  no overlap                          auto-integratable
C1  textual, low-risk overlap           auto-integratable
C2  semantic / shared-contract overlap  stop
C3  public-truth overlap                stop
C4  safety / migration / schema overlap stop
```

The level is computed from the *declared ownership surfaces*, before either
branch exists, and it errs upward: a false C2 costs one review, a false C0 costs
a broken `main`. Only C0/C1 may be resolved by the Main Agent under a fixed
rule; **C2 and above stop automatic integration** — the Main Agent re-plans, or
issues a serial task, or takes the change itself.

Two independent rules sit on top of the classification:

```text
a SAFETY_CRITICAL development task is serialised regardless of overlap
a task whose ownership touches a safety path is serialised regardless of overlap
```

When two tasks conflict above the ceiling, the later one waits for the earlier
one — *the task later in integration order defers to the earlier one* — so the
verdict is reproducible rather than a judgement call made under time pressure.

Since FIX-2 the wait is a **serialisation lease held across status transitions**,
not a snapshot of "both happen to be READY". The later task may not become
runnable while the earlier one's status is in
`CONFLICT_LEASE_STATUSES = { PLANNED, READY, IN_PROGRESS, HANDOFF_READY, BLOCKED,
INTEGRATING }`:

```text
earlier            later     result
READY              READY     later deferred
IN_PROGRESS        READY     later deferred   <- dispatching an owner does not release it
HANDOFF_READY      READY     later deferred   <- handing off does not consume a slot, still a lease
INTEGRATING        READY     later deferred
BLOCKED            READY     later deferred
DONE               READY     later may run   <- only landing releases the surface
FAILED / CANCELLED READY     later is NOT silently runnable; re-plan required
```

"Not currently executing" is **not** "the conflict is resolved". A `FAILED` /
`CANCELLED` owner is deliberately fail-closed: the later task is flagged
`replan_required_by`, exposed in the plan output as
`deferral_kind == "replan"` and by `replan_required_ids()`, so the Main Agent
decides whether the later task becomes the new owner, the contract changes, or a
new revision is issued. Public-truth ownership is never silently transferred.

Execution capacity and conflict serialisation are **separate predicates**
(`EXECUTION_SLOT_STATUSES` versus `CONFLICT_LEASE_STATUSES`), so a task can hold
a lease while consuming no slot — `HANDOFF_READY` and `INTEGRATING` do exactly
that.

---

## 9. Integration

### 9.1 The one rule

> **Integration is serial, and it happens only through a PR with a green
> `Quality Gate` on the current integration head.**

```text
contract first  →  implementations second  →  consumers third
```

The Main Agent orders merges by the dependency DAG, then by whether a task
freezes a public-truth contract, then by task id. Producers of a contract always
land before its consumers.

### 9.2 Stale bases

A PR that was green on an old base is not known to be green on the new one.
`check_base` refuses any handoff whose `base_sha` is not the current integration
head (`agent.base_stale`). The remedy is the protocol's, not the agent's:

```text
update the branch onto the current integration head  →  full CI re-runs  →  then merge
```

**RED → GREEN #2 (`test_a_handoff_based_on_a_stale_commit_is_refused`).** Before
this gate existed, `check_base` validated the *shape* of the two shas and
returned; a handoff built on an obsolete `main` was integrated as if it had been
built on the current one (RED: `DID NOT RAISE BaseStaleError`). The comparison is
now the whole function (GREEN).

### 9.3 Individually-green, jointly-broken

See `docs/ADR/0002-parallel-development-serial-integration.md` for the full
decision. In one paragraph: CAN-X does **not** build a merge queue. Development
is parallel; merge is not. The Main Agent integrates one PR at a time, and each
PR must be based on the current integration head with a green gate on that head
— which `check_base` enforces as a machine predicate rather than a habit. The
ruleset's `strict_required_status_checks_policy` is currently `false`; the ADR
records turning it on as the platform-level backstop, to be applied as its own
deliberate, verified configuration change (§12).

### 9.4 The integration checklist

`READY_FOR_INTEGRATION` requires all of:

```text
handoff schema valid
ownership re-derived from the Git change set and valid
Git-backed evidence valid (branch, head, base, ancestry, clean worktree)
handoff.base_sha == task.base_sha and == the current integration head
dependencies satisfied (every dependency DONE)
no blocking conflict with an in-flight task
task status is HANDOFF_READY
every required test reported as passed
known limitations disclosed
GitHub Quality Gate green on this head          <- the platform, not this tooling
base still current immediately before the merge <- re-checked at merge time
```

---

## 10. GitHub CI

CI is the second, independent gate. It is required, it is automatic, and it is
not negotiable:

```text
PR must be green       "there is no CI run" is NOT a pass
red CI blocks the merge, always
```

The required check is `Quality Gate`, which already fails closed unless all
three domain jobs report `success`. AGENT-01 extends the Python job's lint and
type scope to the new tooling (`tools/agent`) so the tooling cannot rot outside
the gate the rest of the repository is held to. No test was skipped, deleted or
weakened, and nothing was added to `continue-on-error`.

---

## 11. Acceptance

```text
Local Verification   ≠   GitHub CI   ≠   Protected Merge   ≠   Independent Acceptance
```

A green gate means "safe to integrate". It has never meant "accepted", and no
agent may write `Final Acceptance: PASS` for its own work. The AGENT-01 phase
was subsequently accepted externally (`PASS`, P0 = P1 = P2 = 0), merged through
the protected PR workflow, and passed post-merge `main` CI. Its acceptance
history remains in
`docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_RELIABILITY-01.md` §18.11–§18.21 (the
`docs/PROJECT_STATE.md` §18 entry is the summary); the real AGENT-02
pilot has not started.

---

## 12. Safety interaction

AGENT-01 adds no dangerous capability: no TX, no replay send, no injection, no
diagnostic request, no ECU mutation, no new endpoint and no new UI control. It
does not touch `runtime/canx/safety/**`, and S1–S25 are unchanged.

What it does add is a *classification*: a task whose ownership surface reaches a
safety path, or whose `risk_class` is `SAFETY_CRITICAL`, is marked
`serial_review_required` and may never be integrated in parallel with another
task. A future safety-touching Sub-Agent task is therefore routed to serial
review automatically, not by remembering to ask.

`RiskClass` (development risk) is deliberately a different type from the
Runtime's `canx.safety.risk.RiskLevel` (vehicle-operation risk). A high-risk
development task is not a vehicle operation and the two must never be
conflated.

---

## 13. Failure recovery

| Situation | Status | Main Agent action |
| --- | --- | --- |
| Agent stalls or crashes | `FAILED` | Keep the branch and any partial handoff as evidence; cleanup only by explicit decision |
| Dependency fails | `BLOCKED` | Re-plan the DAG; do not "temporarily work around" the dependency |
| Base moved under a handoff | `HANDOFF_READY` | Update onto the current head, re-run CI, then integrate |
| Ownership conflict discovered | `BLOCKED` | Re-scope the task or reassign; never widen `allowed_paths` after the fact without a revision bump |
| A shared contract is wrong | `BLOCKED` + change request | The Main Agent owns the contract; no Sub-Agent silently redefines it |
| CI red on a PR | `FAILED` or fix forward | Never merge; never weaken the gate |

A crash means "the task stopped", not "the task is delisted": the worktree is
kept until the Main Agent decides, and evidence is not deleted to tidy up.

### 13.1 Cancellation

```text
status = CANCELLED
branch preserved · partial handoff preserved if it exists
worktree cleanup only after an explicit decision
```

Evidence is never auto-deleted.

### 13.2 Cleanup

`worktree remove` refuses a dirty worktree and refuses an unmerged branch
without an explicit `--allow-unmerged`. Cleanup is the last step of a task, not
a reflex.

---

## 14. Task immutability

Once a task is under way, three fields are frozen:

```text
objective        allowed_paths        shared_contracts
```

Changing one requires a revision bump, or a re-issued task.
`assert_frozen_unchanged` refuses a silent change; a status change never needs a
bump, because status is not a property of the assignment.

The point is that a Sub-Agent must not be able to discover, halfway through,
that its target moved.

---

## 15. Tooling map

```text
.agent/config.json                  the knobs: cap, path classes, branch prefix
.agent/schemas/task.schema.json     the task interchange contract
.agent/schemas/handoff.schema.json  the handoff interchange contract
.agent/examples/                    one valid task, one valid handoff, one
                                    ownership violation, one 4-task plan
.agent/prompts/                     the Main-Agent and Sub-Agent prompt templates
.agent/handoffs/                    where produced handoffs land
tools/agent/                        the enforcement (standard library only)
scripts/agent.cmd                   the Windows entry point
tests/unit/agent_tools/             the protocol's unit tests
tests/integration/                  the worktree lifecycle and the simulation
```

Exit codes are the contract: `0` success, `2` validation, `3` ownership /
conflict, `4` git state, `5` internal. Every failure prints
`{"code", "message", "details"}` — never a bare stack trace.

---

## 16. What AGENT-01 deliberately does not do

```text
no agent runtime, no provider SDK, no model name anywhere in the contract
no message broker, scheduler, database queue, daemon or dashboard
no real four-agent product pilot — that is AGENT-02
no product capability of any kind
```

`simulated multi-task orchestration verified` is the strongest claim the
AGENT-01 foundation may make. `four-agent parallel development verified` is not
available to it, and will not be until AGENT-02 runs against this protocol for
real.

---

## 17. Remediation — AGENT-01-FIX-2

The **second** independent acceptance of AGENT-01 returned `NOT PASS`
(P0 = 2, P1 = 2, P2 = 1) and is recorded, unchanged, in
`docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_RELIABILITY-01.md` §18.13. FIX-2 is a narrow orchestration-lifecycle and
evidence-completeness hardening: no redesign of the accepted architecture, no
AGENT-02, no product capability, no Safety change.

The invariant it restores:

> A planning cycle must reason about work already in flight, not only the tasks
> that happen to be `READY` at this instant.

### 17.1 The two policies, stated once

```text
execution slot occupancy   who is currently consuming one of max_sub_agents?
conflict ownership lease   which earlier task still owns a shared surface?
```

`tools/agent/lifecycle.py` defines `EXECUTION_SLOT_STATUSES`,
`CONFLICT_LEASE_STATUSES` and `CONFLICT_REPLAN_STATUSES` in one place, with
`occupies_sub_agent_slot()`, `holds_conflict_lease()` and
`requires_conflict_replan()` as the only predicates over them. No other module
hard-codes a status list.

### 17.2 RED → GREEN (every case reproduced against the tree *before* the fix)

```text
#1  a C3 waiter escaped the lease when its owner left READY
    before  B READY, D READY -> D deferred; B -> IN_PROGRESS -> D runnable
    after   D stays conflict-deferred through IN_PROGRESS / HANDOFF_READY /
            INTEGRATING; DONE releases it; FAILED / CANCELLED require a re-plan

#2  max_sub_agents counted only new candidates
    before  3 IN_PROGRESS + 4 READY, max 4 -> 4 runnable, 7 effective agents
    after   active = 3, available_slots = 1, runnable = 1; the planner adds at
            most the free slots, and an over-dispatched set is reported as
            capacity.active_over_capacity

#3  a task worktree read from the task worktree lost its dirty state
    before  from the worktree root: source = branch-ref, clean = true (dirty!)
    after   both entry points: source = worktree, clean = false

#4  a modify/restore of a protected file escaped the net-diff ownership gate
    before  net diff = the owned work only -> ready = true
    after   history_touched_paths contains SPEC.md -> agent.ownership_violation

#5  a context without an orchestration plan could skip the conflict gate
    before  include_plan=False -> ready = true
    after   agent.integration_context_incomplete, ready = false

#6  duplicate / ambiguous commit evidence passed
    before  two tokens both prefixing one commit -> ready = true
    after   one-to-one matching; ambiguous, unknown, duplicate and
            unrepresented commits are all refused
```

Each fix was also rolled back and its tests re-run to confirm they turn red — a
test that stays green with its fix reverted protects nothing.

### 17.3 What was added

```text
tools/agent/lifecycle.py      EXECUTION_SLOT_STATUSES · CONFLICT_LEASE_STATUSES ·
                              CONFLICT_REPLAN_STATUSES · holds_conflict_lease ·
                              occupies_sub_agent_slot · requires_conflict_replan
tools/agent/orchestration.py  leases held across statuses; capacity counted from
                              active slots; replan_required_by; capacity block
                              reports active / active_over_capacity /
                              available_slots / selected
tools/agent/gitcmd.py         history_touched_paths (per-commit diff-tree union)
tools/agent/evidence.py       net_changed_paths vs history_touched_paths;
                              canonical primary-worktree resolution
tools/agent/validation.py     ownership + handoff equality on history paths;
                              orchestration plan required for ready; one-to-one
                              commit evidence; replan_required in conflict details
tools/agent/handoff.py        build_handoff derives changed_files from the same
                              history helper the verifier uses
tools/agent/worktree.py       primary_worktree()
```

New tests: `tests/unit/agent_tools/test_orchestration_lifecycle.py`,
`tests/unit/agent_tools/test_evidence_hardening.py`,
`tests/integration/test_agent_worktree_evidence.py`, plus the multi-cycle
lifecycle simulation in
`tests/integration/test_multi_agent_orchestration_simulation.py`.

### 17.4 What FIX-2 did not touch

```text
the ruleset            unchanged, still active, still bypass_actors: []
the required check     unchanged — still exactly "Quality Gate"
ci.yml                 unchanged
runtime/canx/safety/   untouched; S1–S25 unchanged
product surface        none — no UI, endpoint, Tauri command or dependency
AGENT-02 / V0.3-12 / CD-01   not started
```

`Final Acceptance` is **not** written by the development agent; the phase remains
`awaiting independent re-acceptance`.

---

## 18. Remediation — AGENT-01-FIX-3

The **third** independent acceptance returned `NOT PASS` with **no P0**
(P1 = 2, P2 = 1) and is recorded, unchanged, in
`docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_RELIABILITY-01.md` §18.15.
The architecture is substantially accepted; FIX-3 is the narrow evidence
identity-binding remediation before the final re-acceptance.

The invariant it restores:

> A TaskContract declares exactly `branch` + `worktree` + (implicitly)
> `repository`, and all three must agree with reality. Branch-ref fallback is
> allowed only when **no** registered task worktree exists - not when the
> declared path merely failed to match while another live worktree did not.

### 18.1 RED → GREEN (both reproduced against the FIX-2 tree)

```text
#1  the task branch registered at a wrong worktree path
    setup   .worktrees/wrong-location registered on the task branch, dirty;
            the contract declares .worktrees/agent-02-a
    before  source = branch-ref, clean = true, worktree = None
            (the live dirty worktree was never inspected)
    after   agent.worktree_conflict; branch-ref is not used; the final gate
            refuses and the dirty worktree is never laundered

#2  integration evidence not bound to config.repository
    before  other-owner/other-repo  -> ready: true
            .../CAN-X-copy.git      -> ready: true
            no origin               -> ready: true
    after   each is agent.git_state_error, ready: false
            correct CAN-X HTTPS/SSH -> still accepted
```

Both fixes were rolled back once and the suites re-run: nine tests turn red.

### 18.2 What was added

```text
tools/agent/evidence.py     resolve_task_worktree() full A/B/C/D/E classification;
                            collect_repository_evidence(expected_repository=...);
                            RepositoryEvidence.repository_identity
tools/agent/validation.py   evaluate_integration() binds config.repository
tools/agent/cli.py          the docstring's check-integration example is complete
                            (--plan, --repo) with the fail-closed note
```

### 18.3 What FIX-3 did not touch

```text
tools/agent/orchestration.py   unchanged — slots, leases, capacity, replan
historical ownership           unchanged — history_touched_paths is authoritative
commit evidence                unchanged — still one-to-one
required-test gate             unchanged — every required test must be `passed`
protected-path overlap         unchanged — pattern vs pattern
worktree cleanup safety        unchanged — no rm -rf, git branch -d, fail-closed
the ruleset                    unchanged, still active, still bypass_actors: []
runtime/canx/safety/           untouched; S1–S25 unchanged
AGENT-02 / V0.3-12 / CD-01     not started
```

`Final Acceptance` is **not** written by the development agent; the phase remains
`awaiting independent final re-acceptance`.

---

## 19. Closeout — external acceptance and protected integration

AGENT-01-FIX-4 was independently accepted externally on
`2eab62bfc53867f44996dafc08c32b84fabea7fd` with `P0 = 0`, `P1 = 0`, `P2 = 0`.
The protected integration then merged PR #7 into `main` as merge commit
`951e20272211d2fd3934f4c67063985d64229d7a`. Post-merge `main` CI run
`35315019912` succeeded on its first attempt; the six skips were the known
packaged-runtime smoke tests. AGENT-01 is therefore `Final Acceptance: PASS`,
`Status: CLOSED` in the external record, while AGENT-02, V0.3-12 and CD-01 remain
not started.

---

## 20. Execution modes — isolated worktree and native shared

> Added by **V0.3-12-FIX-1**, after the first real AGENT-02 pilot measured its
> own execution model. The decision record is `docs/ADR/0003-native-agent-harness-orchestration.md`;
> this section is the protocol half.

§1 states the frozen principle: **parallelism is earned by isolation.** What the
pilot added is that "isolation" has two shapes, and the governance layer has to
be able to *name which one it is looking at* instead of assuming one.

```text
isolated-worktree     the AGENT-01 model (default)
                      one Sub-Agent, one branch, one registered worktree;
                      the contract freezes branch + worktree and the delivery is
                      resolved through that worktree

native-shared         the host Harness's default
                      the worker executes inside the Main Agent's single worktree;
                      no task branch and no task worktree exist, and the delivery
                      is a real commit range on the integration branch
```

### 20.1 The contract

```text
execution_mode      "isolated-worktree" | "native-shared"     default: isolated-worktree
branch / worktree   required for isolated; MUST be absent (null) for native-shared
integration_paths   native-shared only; default []
```

`validate_task` refuses a native-shared task that declares a branch or a
worktree, and refuses an isolated task that declares `integration_paths`. The
rule is not stylistic: a declared branch Git cannot resolve is exactly the
defect that made the pilot's first three handoffs fail with
`agent.git_state_error` before the delivery was ever inspected.

`integration_paths` names the additional files a reviewer permitted to sit inside a
worker's delivery **range** — in a shared worktree the Main Agent's cross-boundary
integration test shares the worker's commit. Each entry must be an **exact
repository-relative file** (a glob is refused: the field says "these named files may
coexist", and `apps/desktop/src/**` says something much larger). It is refused when
it can reach a protected, public-truth or safety path, or when it overlaps the task's
own `forbidden_paths`; and `classify_pair` classifies two tasks using each one's
full **ownership surface**, so a delivery that reaches another task's surface is
visible instead of reading as C0.

What the field does and does not prove (V0.3-12-FIX-2):

```text
allowed_paths        the worker's declared permission surface
integration_paths    Main-Agent-reviewed additional paths permitted to coexist in
                     the task's delivery range
Git gate proves      the delivery range's composition, and that no governed path
                     was reached
Git gate does NOT    prove per-file writer provenance inside a shared worktree - a
                     commit range records that a file is present, not who wrote it
```

**Preferred rule for future native-shared development.** The worker's implementation
commit comes first, and the Main Agent adds its integration changes in a **separate
Main-Agent commit**, so the worker's delivery range contains only worker paths and no
`integration_paths` declaration is needed. The field is retained for the compatibility
case it was introduced for — V0.3-12-C's historical commit — which is kept as history
and is not read as evidence of who wrote each file.

### 20.2 What `base_sha` means

```text
isolated       the integration head the task branched from (§9.2, unchanged)
native-shared  the integration head immediately before this delivery landed,
               so base..head is exactly this task's own commits
```

`base_sha` is not a frozen field: a task re-issued with different execution
coordinates bumps `revision`. The base a contract carried at dispatch is
preserved in git history, and the handoff still may not invent a base —
`handoff.base_sha == task.base_sha` is unchanged in both modes.

### 20.3 The stale-base rule, per mode

`check_base` answers "is this delivery based on the current integration head?".
The predicate differs because the question does:

```text
isolated       handoff.base_sha == integration_head          (equality)
               a worker branch must be rebased before it merges

native-shared  base_sha is an ancestor of the integration head
               AND head_sha is an ancestor of the integration head
               (proved from Git, `git merge-base --is-ancestor`)
```

The native-shared form is not weaker: the delivery is *already* on the
integration branch, so what has to be proved is that its commits are in the
head's history — which ancestry proves and equality cannot express. The refusal
code is `agent.base_stale` in both modes. `verify_repository_evidence` is where
the ancestry is checked, and it is reached only through
`collect_repository_evidence`, so a verdict without a repository remains
`agent.integration_context_incomplete`.

### 20.4 What the mode does not change

```text
ownership              allowed_paths + forbidden_paths, decided on
                       history_touched_paths over base..head (§4.3, §7.2)
protected / public-truth / safety     classified identically (§4.2)
handoff schema and required tests     identical (§7.1, §7.3)
self-reported compliance              still not evidence (§4.3)
conflicts, capacity, lifecycle        identical (§6, §8)
integration authority                 still the Main Agent's (§9); no evidence= parameter
acceptance boundary                  still external (§11)
```

The Harness decides *how* a worker executes. CAN-X decides *what* it may do and
*whether the result is true*. Moving the execution surface does not move that
line.

### 20.5 A commit no task claims

In native-shared mode only the Main Agent commits. A commit on the integration
branch that no task's range covers is therefore Main-Agent work, governed by the
Main Agent's own obligations, not by a worker contract. The gate's duty is to
prove no *worker* reached outside its surface, and it discharges that over each
task's own delivery range.

### 20.6 Tooling map additions

```text
tools/agent/contracts.py    ExecutionMode · TaskContract.execution_mode ·
                            TaskContract.integration_paths ·
                            TaskContract.ownership_surface()
tools/agent/validation.py   _validate_execution_coordinates · ownership_surface()
                            · mode-aware validate_handoff / check_base /
                            verify_repository_evidence
tools/agent/evidence.py     SOURCE_NATIVE_SHARED · _collect_native_shared ·
                            integration_head_sha / base_on_integration_head /
                            head_on_integration_head
tools/agent/conflicts.py    classify_pair over the full ownership surface
.agent/schemas/task.schema.json   execution_mode · integration_paths; branch and
                            worktree become mode-conditional
tests/unit/agent_tools/test_native_shared_execution.py
tests/integration/test_agent_native_shared_evidence.py
```
