# CAN-X — Integration Policy

> **Document**: `docs/engineering/INTEGRATION_POLICY.md`
> **Applies To**: every change that reaches `main` — human or agent
> **In force from**: Maintenance CI-02 (Protected Integration Gate Foundation)
> **Owner**: CAN-X sole author
> **Enforced by**: a GitHub Repository Ruleset (the platform, not this file, is the authority)

This document states how code is allowed to reach `main`. The ruleset is the
mechanism; this file explains the policy behind it and what to do when the
mechanism cannot be used.

---

## 1. Why this exists

CAN-X now has three levels of verification. Each answers a different question.

```text
Level 1  Local Automated Verification   pytest · ruff · mypy · cargo · pnpm   DONE
Level 2  Repository Continuous Integration   .github/workflows/ci.yml         DONE
Level 3  Protected Integration Workflow   this policy + the GitHub ruleset    ACTIVE
```

Level 2 answers *"does CI check the code?"*. Level 3 answers the stronger
question: *"can code that CI has not passed reach `main` through the normal
process?"* The answer must be **no**.

CAN-X is being built toward a future real test-vehicle environment — real CAN /
CAN FD capture, DBC, diagnostics, UDS, TX, ECU-mutating operations and
agent-assisted workflows. An unverified change that reaches `main` is an
unverified change that can later reach a vehicle. This policy makes that
boundary real now, while the code is still harmless.

## 2. The one rule

> **No green required Quality Gate = no normal merge into `main`.**

Everything below follows from this.

## 3. `main` is the integration branch

- `main` is the branch a release candidate would be cut from. It must stay green.
- `main` is protected by a GitHub **Repository Ruleset** named
  `main-protected-integration` (target: the default branch, enforcement:
  `active`). The ruleset — not convention or trust — enforces this policy.
- Product work does **not** happen on `main`.

```text
main branch role     integration branch — protected, always green
branch role          all development (feature / fix / maintenance / docs)
```

## 4. Branches for development, Pull Requests for integration

1. Branch off `main` (`feature/…`, `fix/…`, `maintenance/…`, `docs/…`).
2. Commit there and push the branch.
3. Open a Pull Request into `main`.
4. CI runs on the PR.
5. Merge only when the required gate is green.

The ruleset's `pull_request` rule rejects any direct update to `main` — including
from a repository administrator/owner — so a PR is the only normal path.

## 5. Required status check

The required check is the aggregated gate produced by `.github/workflows/ci.yml`:

```text
Runtime / Python        pytest -q · ruff check · mypy
Frontend / TypeScript   lint · typecheck · test · build
Desktop System / Rust   cargo fmt --check · clippy -D warnings · test --locked
        ↓
Quality Gate            REQUIRED — must be `success`
```

Only **`Quality Gate`** is configured as a required status check. It already
fails closed unless all three domain jobs report `success`, so requiring the
aggregate is both sufficient and stronger than requiring the three separately
(it cannot be satisfied by a partial run).

`Quality Gate` is a **strict-success** check: only `success` passes. All of the
following block the merge:

```text
failure   cancelled   timed_out   skipped   neutral   queued   in_progress   missing
```

## 6. Red CI is a blocking failure

- A red CI run is a blocked merge, not an inconvenience to work around.
- "There is no CI run" is **not** a pass.
- It is forbidden to make CI green by weakening the gate: `continue-on-error:
  true`, `|| true`, swallowed exit codes, deleted or skipped tests, or lowered
  assertions (AGENTS.md §14, §43).

## 7. When CI does not start (missing check)

Because the check is a *required* context, a PR whose `Quality Gate` never
reports stays blocked, with the check shown as *expected*. A missing gate blocks
exactly like a red one. If CI fails to trigger, that is an infrastructure
incident to fix — never a reason to bypass.

## 8. Post-merge CI

A merge into `main` is itself a `push → main` event, so CI runs again on the merge
commit. `main` is expected to stay green; a red post-merge run is a regression to
fix immediately — and to revert if it cannot be fixed promptly.

## 9. Stale Pull Requests and merge conflicts

- A PR whose head has drifted far behind `main`, or that carries unresolved
  conflicts, is not merged as-is. Rebase or merge `main` into the branch and let
  CI re-run on the updated head.
- The ruleset does **not** require the branch to be strictly up to date with
  `main`, so an otherwise-green PR is not force-rebased on every unrelated `main`
  commit. A green `Quality Gate` on the PR head is the bar.
- That is the rule for a single contributor. Once work is parallel (one Main Agent
  plus Sub-Agents), the integration precondition is stricter: the PR must be based
  on the current integration head. See §17.
- Stale/unwanted PRs and their branches are closed and deleted, not left open.

## 10. Force push and branch deletion

- **Force push to `main` is blocked** (ruleset rule `non_fast_forward`).
- **Deleting `main` is blocked** (ruleset rule `deletion`).
- Force push remains allowed on short-lived *feature* branches (nothing protects
  them): rewriting an unpublished branch before review is normal.

## 11. Conversation resolution

The ruleset requires **all review conversations to be resolved before merging**.
On a sole-author repository this cannot deadlock — the author resolves their own
threads — and it exists so that a review comment, especially an automated or
future multi-agent one, cannot be silently swallowed by a merge.

## 12. Administrator / owner bypass — the truth

The ruleset declares **no bypass actors** (`bypass_actors: []`). Observed on this
repository: a direct push to `main` is rejected for the owner too —

```text
remote: error: GH013: Repository rule violations found for refs/heads/main.
! [remote rejected] … -> main (push declined due to repository rule violations)
```

Under normal operation **no role is exempt from the gate**. The only way to change
that is to edit or disable the ruleset itself, which requires repository
administration rights and is by definition a deliberate configuration change —
the break-glass path, not a bypass.

## 13. Break-glass policy

Break-glass means temporarily relaxing protection to land a change the gate cannot
process. Permitted **only** for:

- emergency repository recovery;
- critical infrastructure repair;
- a CI outage in which no green run can be produced at all.

**Never** permitted for saving time, "CI is too slow", "the test failed but it is
probably fine", or an agent wanting to merge faster.

Every use must record:

```text
reason · the exact commit · the responsible human · a full CI run afterwards
```

If the follow-up CI is red: fix immediately, or revert immediately.

**An AI agent may never decide to use break-glass.** If an agent believes the gate
itself is broken, it raises a separate maintenance task — it does not disable the
gate inside a product task.

## 14. Agent restrictions

Agents — and future sub-agents — are ordinary contributors to this policy, with
extra prohibitions. An agent must never:

```text
disable branch protection / remove the ruleset
remove or rename the required status check to make its PR mergeable
turn CI off, or edit ci.yml to weaken it
force push main
bypass a red or missing gate
```

If an agent finds a bug in the gate, it files a maintenance task. It never
silently edits the protection mechanism as part of a product task.

## 15. Independent Acceptance is not the gate

These four things are **not** interchangeable:

```text
Local Verification   ≠   GitHub CI   ≠   Protected Merge   ≠   Independent Acceptance
```

```text
Local Verification      developer/agent runs the suites locally
        ↓
GitHub CI               the required Quality Gate (automatic)
        ↓
Protected Integration   this policy: the only normal path into main
        ↓
Integration Review      human / reviewer judgement
        ↓
Independent Acceptance  project owner / independent reviewer verdict
```

Merging a green PR does **not** grant a phase `Final Acceptance: PASS`, and no
agent may write that verdict for its own work. A green gate means "safe to
integrate" — never "accepted". Acceptance stays external
(`docs/PROJECT_STATE.md` §11).

## 16. Future-facing notes (recorded, not implemented here)

- **Safety.** Later work introduces high-risk code regions —
  `runtime/canx/safety/`, `runtime/canx/devices/`, `runtime/canx/protocols/`,
  `runtime/canx/agent/`, and eventually TX, replay, injection, diagnostics, UDS,
  automation and ECU-mutating operations. A later **SAFETY-01** task will add
  Safety Architecture & Risk Control. This policy deliberately introduces nothing
  that would let an agent bypass safety verification; no path-specific safety gate
  is added here.
- **Multi-agent.** CAN-X is moving to one Main Agent plus up to four Sub-Agents.
  This policy already guarantees that any number of parallel agent PRs cannot
  bypass `main`'s required gate. The orchestration protocol itself is AGENT-01,
  and it is now in force — see §17 and
  `docs/engineering/MULTI_AGENT_PROTOCOL.md`.
- **Delivery.** A later CD phase will build and publish artifacts from a trusted
  `main`. This policy makes `main` trustworthy enough to be that source; it adds
  no build, release, signing or updater capability.

---

## 17. Multi-agent integration (AGENT-01)

AGENT-01 adds the orchestration protocol: one Main Agent coordinating up to four
Sub-Agents. The protocol is stated in
`docs/engineering/MULTI_AGENT_PROTOCOL.md`, enforced by `tools/agent/`, and the
integration decision is recorded in
`docs/ADR/0002-parallel-development-serial-integration.md`.

**This policy is unchanged as the mechanism.** The ruleset, the required
`Quality Gate`, the PR-only path, the no-bypass configuration and the
`Local Verification ≠ GitHub CI ≠ Protected Merge ≠ Independent Acceptance`
boundary all still hold exactly as §2–§15 state them. AGENT-01 adds obligations
*on top*, for the parallel case only.

### 17.1 Parallel development, serial integration

```text
development  may be parallel      up to 4 Sub-Agents, isolated by branch + worktree
integration  is not parallel      the Main Agent merges one PR at a time
```

Two PRs that are each green on their own base are not evidence that their
combination is green. The integration precondition is therefore stricter than
§9's single-contributor rule:

> **A PR is merged only when it is based on the current integration head and its
> `Quality Gate` is green on that head.**

If the base moves between the green run and the merge, the PR is stale: update
it onto the new head, let CI re-run, and merge only then. This is
`tools/agent/validation.py:check_base`, which refuses a stale handoff with
`agent.base_stale` — a returned error code, not an instruction in a prompt.

### 17.2 Ownership before merge

- Every task declares a machine-readable `allowed_paths` surface; anything not
  listed is not editable.
- Ownership is decided by the **Git change set**, never by the handoff's own
  `changed_files` list or its `ownership_compliance` flag. Since FIX-2 the
  authoritative set is the task's **history** — the union of every path touched
  by every commit in `base..head` — not merely the final net tree diff, so a
  protected file edited and then restored before the head is still a violation.
  A violation is `agent.ownership_violation` and the handoff is rejected — "the
  change was fine anyway" is not a resolution.
- Protected paths are compared by **pattern overlap**, not string matching, so a
  broad ownership glob (`**`, `**/*.md`, `docs/**`) cannot reach `SPEC.md`,
  `AGENTS.md`, `docs/PROJECT_STATE.md` or `.github/workflows/**` from a Sub-Agent
  task. They are not forbidden; they are **not parallel**.
- A conflict above C1 between two tasks stops automatic integration
  (`docs/engineering/MULTI_AGENT_PROTOCOL.md` §8). Since FIX-2 the wait is a
  serialisation **lease** held while the earlier task is non-terminal
  (`READY` / `IN_PROGRESS` / `HANDOFF_READY` / `INTEGRATING` / `BLOCKED`), not a
  snapshot of "both happen to be READY"; only `DONE` releases it, and a
  `FAILED` / `CANCELLED` owner requires an explicit re-plan rather than silently
  handing the surface to the later task.
- Required tests must all be reported as `passed`; `skipped` and `not_run` are
  honest reports but not integration-passing results.
- `plan()` enforces `max_sub_agents` against the Sub-Agents **already running**:
  `available_slots = max(0, max_sub_agents - active)`, and the planner adds at
  most `available_slots`, so it never increases an over-subscription. An
  already over-dispatched set is reported as `capacity.active_over_capacity`
  rather than rounded away. Tasks held back only by the cap are reported as
  capacity-deferred, distinctly from blocked, conflicted and re-plan-required.
- The local `check-integration` verdict requires the orchestration plan as well
  as the repository: a context without it fails closed with
  `agent.integration_context_incomplete` rather than skipping the conflict gate.
- A task's branch **and** worktree path are both contract-frozen and both must
  match the registered worktree table (FIX-3). If the branch is registered at a
  different path, or the declared path holds another branch, or the metadata is
  ambiguous, the verdict is `agent.worktree_conflict`. Falling back to the branch
  ref is allowed only when neither is registered anywhere — never when a live
  worktree merely failed to match, because that would ignore its dirty state.
- Final integration evidence must come from the **configured** repository
  (`config.repository`), not merely from some valid Git repository (FIX-3). A
  wrong owner/repo, a lookalike name, an unsupported host or a missing origin is
  `agent.git_state_error` and the verdict is not ready.
- The final verdict has **one** authority path (FIX-4):
  `evaluate_integration` takes a repository path, collects the Git evidence
  itself and binds it to `config.repository`. It has no `evidence=` parameter, so
  a caller-created `RepositoryEvidence` - however internally consistent, and
  whatever `repository_identity` it claims - cannot produce `ready: true`.
  `repository=None` is `agent.integration_context_incomplete`, not ready.
  `RepositoryEvidence.repository_identity` is diagnostic; the proof is read from
  the real origin.
- Rejecting an unsafe remote never discloses it (FIX-4). An origin shape the
  canonicaliser does not recognise - a credential-bearing URL among them - fails
  closed with `origin_supported: false` and no URL in the structured error, so
  nothing leaks into CLI output, CI logs or captured error artifacts. A
  recognised-but-wrong repository still reports its safe canonical `owner/repo`.

### 17.3 Who checks what

```text
local `check-integration`   handoff schema · Git-backed evidence · ownership
                            base/current-head · dependency completion
                            conflict/deferral state · task readiness
GitHub Ruleset              Quality Gate green · protected PR workflow
```

The local gate reports `github_gate.checked_here = false` and does not pretend
otherwise; it embeds no GitHub client. **Merge eligibility requires both**, plus
a re-check that the base is still current at merge time.

### 17.4 What AGENT-01 did not touch

```text
the ruleset            unchanged, still active, still bypass_actors: []
the required check     unchanged — still exactly "Quality Gate"
ci.yml                 one line: mypy now also covers tools/agent (no weakening)
tests                  none skipped, deleted or loosened
runtime/canx/safety/   untouched; S1–S25 unchanged
```

### 17.5 Recorded gap

`strict_required_status_checks_policy` on ruleset `main-protected-integration`
(id `23600372`) is still `false`. The AGENT-01 rule above is enforced by the
protocol and the tooling, not by the platform. The ADR records turning the
platform flag on as a deliberate follow-up on its own change, together with the
negative case that must be verified. Until that happens, integration safety in
the parallel case rests on the Main Agent honouring §17.1 — which is exactly the
kind of reliance on discipline that `check_base` exists to reduce.

## 18. Change-aware validation (CI-03)

CI-01 ran every job on every event. CI-03 keeps the same gate but only runs the
validation jobs a change actually requires. The policy this section adds is the
*meaning* of a skip — because "a job did not run" now has two very different
causes.

### 18.1 Authorised skip ≠ missing validation

```text
authorised skip     the classifier decided this domain cannot be affected, the
                    classifier job succeeded, and `Quality Gate` recorded it
missing validation  the required check never reported at all — blocked exactly
                    like a red one (§7)
```

The distinction is enforced, not asserted. `Quality Gate` runs on every event
(`if: always()`), it reads the classifier's decision, and it **fails closed**:

```text
classifier did not succeed        → FAIL
a required domain job != success  → FAIL  (skipped counts as != success)
a not-required job is red         → FAIL  (a failure is evidence either way)
unreadable classification         → FAIL
```

So a skip can only pass if the classifier asked for it and the classifier itself
succeeded. There is no path where "nothing ran" produces a green gate.

### 18.2 When the classifier must escalate

```text
unknown path                → FULL CI
diff cannot be established  → FULL CI
workflow_dispatch           → FULL CI
classifier internal error   → FULL CI
Safety implementation       → FULL CI
CI control plane            → FULL CI
dependency / build authority→ FULL CI
project authority documents → FULL CI
Agent shared truth          → FULL CI
```

FULL CI means every domain job runs. It is never "no validation". The routing
table, the derivation of the compared commits, and the reasoning behind the
shared-contract and `scripts/**` escalations are in
`docs/engineering/CI_TIERED_QUALITY_GATE.md`.

### 18.3 Forcing complete validation

`workflow_dispatch` always classifies as FULL. That is the supported escape hatch;
no label, bot or comment-command system exists, and none is planned for this
stage.

### 18.4 What did not change

```text
ruleset                 main-protected-integration — unchanged, active
required status check   still exactly "Quality Gate"
triggers                pull_request → main · push → main · workflow_dispatch
job display names       Runtime / Python · Frontend / TypeScript · Desktop System / Rust
domain job commands     unchanged
```

`Local Verification ≠ GitHub CI ≠ Protected Merge ≠ Independent Acceptance` is
unchanged. CI-03 changes which validation jobs run *before* the gate.

### 18.5 Measured effect

A docs-only change now costs ~40 s of wall time instead of ~405 s, with the
required `Quality Gate` check still produced. The measurement, its run IDs and the
limits of what was observed are recorded in
`docs/engineering/CI_TIERED_QUALITY_GATE.md` §8 — including the routings that are
proven by unit test rather than by a measured run.
