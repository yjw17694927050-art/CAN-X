# CAN-X — AGENT-02 Readiness

> **Document**: `docs/engineering/AGENT_02_READINESS.md`
> **Scope**: Can a first real AGENT-02 pilot start on the AGENT-01 foundation as it stands?
> **Status**: PREPARATION COMPLETE · awaiting independent acceptance.
> **Not claimed**: `Final Acceptance: PASS`, `Status: CLOSED`, "multi-agent verified".
> **Audited head**: `maintenance/agent-02-preparation` @ `63a1b26` (from `origin/main` `bf88873`).
> **Related**: `docs/engineering/MULTI_AGENT_PROTOCOL.md` (the protocol),
> `docs/engineering/INTEGRATION_POLICY.md` (the merge side),
> `.agent/config.json` (the knobs), `AGENT_01_*` close-outs in `docs/project-state/`.

This document answers exactly one question and nothing else. It does not restate the
protocol and it does not reproduce logs.

```text
Question:  does the AGENT-01 foundation actually support a first real pilot?
Verdict:   READY FOR PILOT
           — 48 READY, 2 PARTIAL, 0 BLOCKED, 0 NOT IMPLEMENTED
           — no P0-like blocker, no P1-like blocker
           — 2 P2-like notes (R29, R49), both with a documented workaround
```

## 1. How the matrix was produced

Three kinds of evidence, kept separate so a green test is never mistaken for a
demonstration:

| Code | Meaning |
| --- | --- |
| **T** | automated test in `tests/unit/agent_tools/**` or `tests/integration/test_agent_*.py` |
| **L** | live invocation of the real CLI during this audit (exit code + JSON envelope observed) |
| **R** | code read: the executable path was read, not inferred from the protocol |

Statuses: `READY` (executable path exists *and* is evidenced) · `PARTIAL` (path works, but
part of it is untested or needs an operator workaround) · `BLOCKED` · `NOT IMPLEMENTED`.

```text
Baseline suite run for this audit (this head):
  pytest tests/unit/agent_tools -q                    -> 292 passed
  pytest tests/integration/test_agent_*.py \
       test_multi_agent_orchestration_simulation.py    -> 102 passed in 106.9s
  ruff check runtime tests tools                      -> All checks passed
  mypy runtime tools/agent tools/ci                   -> Success, 98 files
```

## 2. The matrix

### 2.1 Identity, config and contracts

| ID | Capability | Authority / executable path | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| R01 | Repository identity binding | `tools/agent/worktree.py:validate_repository`, `gitcmd.py:canonical_repository` | T `test_repository_identity.py`, `test_agent_repository_identity.py`; L `check-integration --repo .` → `agent.git_state_error` | READY | Four documented GitHub origin shapes only; unsupported origin never echoes the URL |
| R02 | Config parsing | `tools/agent/config.py:AgentConfig.from_dict`, `load_config` | T `test_examples_and_schemas.py:117`; every L command loads it | READY | — |
| R03 | TaskContract parsing | `contracts.py:TaskContract.from_dict`, `load_task` | T `test_contracts.py`, `test_examples_and_schemas.py`; L `validate-task` → exit 0 | READY | Parsing never executes |
| R04 | TaskContract validation | `validation.py:validate_task` | T `test_validation.py`, `test_protected_ownership.py`, `test_paths.py`; L exit 2 on a malformed contract | READY | — |
| R05 | Frozen task fields | `contracts.FROZEN_FIELDS`, `validation.assert_frozen_unchanged` | T `test_validation.py:354,362,368`, `test_contracts.py:111` | READY | — |
| R06 | Revision rules | `validation.py:validate_task` (revision ≥ 1), `assert_frozen_unchanged` | T `test_validation.py:354-371` | READY | A bump is the only way to change a frozen field |
| R07 | Branch naming | `validation.py:_validate_branch_name` | T `test_validation.py:136,141,212` | READY | `<prefix><task-id>-<slug>`; `main` may use a maintenance branch |

### 2.2 Worktrees

| ID | Capability | Authority / executable path | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| R08 | worktree create | `worktree.py:create_worktree` | T `test_agent_worktree_lifecycle.py:72,88,108,128,144,158,169`; L Phase D preflight | READY | Refuses a dirty repo, an existing path/branch, a short or unresolvable base |
| R09 | worktree list | `worktree.py:list_worktrees` | T `test_agent_worktree_lifecycle.py:200`; L `worktree list` → 5 records | READY | See observation O-2 (leftover worktrees) |
| R10 | worktree validate | `worktree.py:validate_worktree` | T `:180,195`; L Phase D | READY | Refuses branch mismatch and unregistered path |
| R11 | worktree remove safety | `worktree.py:remove_worktree` | T `:215,233,253,271,289,294,309` | READY | No `rm -rf` path exists in the module; `git worktree remove` only |
| R12 | dirty worktree protection | `create_worktree` `require_clean`; `evidence.collect_repository_evidence` | T `test_agent_worktree_lifecycle.py:144`, `test_agent_worktree_evidence.py` (dirty from either entry point) | READY | A de-registered leftover directory is also treated as dirty |

### 2.3 Paths, protection and conflict classes

| ID | Capability | Authority / executable path | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| R13 | protected ownership, exact match | `validation.py:181-190`, `config.json:protected_paths` | T `test_context_protection.py` (A1/A2), `test_protected_ownership.py`; L exit 3 `agent.protected_path_conflict` | READY | Closed for the context truth surface on this branch (AGENT-CONTEXT-PROTECTION) |
| R14 | protected ownership, glob overlap | `paths.py:patterns_overlap`, `overlapping_pattern` | T `test_protected_ownership.py` (parametrized), `test_context_protection.py` (A3/A4) | READY | Pattern-vs-pattern, so `**`, `docs/**`, `**/*.md` cannot slip through |
| R15 | public-truth classification | `conflicts.py:classify_pattern` | T `test_conflicts.py`, `test_context_protection.py` (A7/A8/A9) | READY | ≥ C3, never auto-resolved |
| R16 | safety classification | `classify_pattern` (safety first), `requires_serial_review` | T `test_conflicts.py` | READY | C4 always serial; note `docs/**` is already C4 via `SAFETY_ARCHITECTURE.md` |
| R17 | C0 conflict | `conflicts.py:classify_pair` | T `test_conflicts.py`; L plan fixture `(A,B)=C0` | READY | auto-integratable |
| R18 | C1 conflict | `classify_pair` | T `test_conflicts.py`; T `test_context_protection.py` (control) | READY | auto-integratable |
| R19 | C2 conflict | `classify_pattern` shared contracts | T `test_conflicts.py` (`shared_contracts`) | READY | serial review |
| R20 | C3 conflict | `classify_pattern` public truth | T `test_conflicts.py`; L plan fixture `(B,D)=C3 auto=False` | READY | serial review |
| R21 | C4 conflict | `classify_pattern` safety | T `test_conflicts.py`, `test_orchestration_lifecycle.py:242` | READY | fail closed |

### 2.4 Dependency graph and capacity

| ID | Capability | Authority / executable path | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| R22 | dependency DAG | `graph.py:TaskGraph`, `orchestration.py:plan` | T `test_lifecycle_and_graph.py`; L `plan` → `blocked=[AGENT-02-C]` | READY | Readiness is derived, never trusted from the label |
| R23 | cycle rejection | `graph.py:_ordered_ids` → `DagCycleError` | T `test_lifecycle_and_graph.py`; L `agent.dag_cycle`, exit 2 | READY | Names the cycle members |
| R24 | `max_sub_agents` enforcement | `orchestration.py:plan` | T `test_capacity_and_gates.py:48,55,67,73`; L 6 ready tasks → 4 runnable, 2 capacity-deferred | READY | Cap is a ceiling: `available_slots = max(0, max − active)` |
| R25 | active execution slot counting | `lifecycle.EXECUTION_SLOT_STATUSES` | T `test_orchestration_lifecycle.py:106,294,310,322,338,345,352,365` | READY | Only `IN_PROGRESS` consumes a slot; over-dispatch is reported, not rounded away |
| R26 | conflict lease behaviour | `lifecycle.holds_conflict_lease` | T `test_orchestration_lifecycle.py:114,152,173,183,221,229,242` | READY | Lease survives status transitions; released only on `DONE` |
| R27 | FAILED replan behaviour | `lifecycle.requires_conflict_replan`, `plan` | T `test_orchestration_lifecycle.py:131,229,254` | READY | `replan` is a distinct deferral kind, outranking `conflict` |
| R28 | CANCELLED replan behaviour | same, parametrized over terminal statuses | T `test_orchestration_lifecycle.py:229` | READY | A vanished owner is not a silent release |

### 2.5 Handoff and evidence

| ID | Capability | Authority / executable path | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| R29 | handoff generation | `handoff.py:build_handoff` (read path) · `write_handoff` / `render_markdown` (write path) | T `build_handoff` via `test_agent_git_backed_handoff.py`, `test_agent_worktree_evidence.py`. **`write_handoff` / `render_markdown` / `render_commit_list` / `head_of` / `branch_of` have no test and no caller** (only `MULTI_AGENT_PROTOCOL.md:607` refers to them). | PARTIAL | **P2.** `build_handoff` derives `changed_files`/`ownership_compliance` from Git and is tested. The *write* half — the `.json` + `.md` pair the Sub-Agent prompt requires — has no CLI subcommand and is only exercised parse→validate, never write→parse. A pilot Sub-Agent must produce the JSON itself. See O-1. |
| R30 | handoff schema validation | `contracts.py:load_handoff`, `validation.py:validate_handoff` | T `test_validation.py`, `test_examples_and_schemas.py`, `test_agent_git_backed_handoff.py`; L `validate-handoff` exit 0 | READY | Schema and parser required-field lists are asserted never to drift |
| R31 | required-test validation | `validation.py:_validate_tests` | T `test_validation.py:162,235`, `test_required_tests.py` | READY | `not_run` is a legitimate report, never a pass |
| R32 | ownership self-report distrust | `validate_handoff` re-derives from `changed_files` | T `test_validation.py:189-190` (claims `ownership_compliance=True` while changing `SPEC.md` → refused) | READY | The flag is not evidence |
| R33 | Git-derived ownership | `validation.py:verify_repository_evidence` | T `test_agent_git_backed_handoff.py`, `test_multi_agent_orchestration_simulation.py:359` | READY | Verified against `history_touched_paths`, not the net diff |
| R34 | `history_touched_paths` | `gitcmd.py:history_touched_paths` | T `test_agent_context_protection.py` (new, this branch), `test_multi_agent_orchestration_simulation.py:380` | READY | Modify-then-restore is invisible in the net diff, visible here |
| R35 | branch identity | `evidence.py:resolve_task_worktree` (cases B/C) | T `test_agent_worktree_evidence.py` | READY | Never follows the branch to another path |
| R36 | worktree identity | `resolve_task_worktree`, `primary_worktree` | T `test_agent_worktree_evidence.py`, `test_agent_worktree_lifecycle.py` | READY | Task path resolves against the primary worktree, not the caller's |
| R37 | repository identity at the gate | `validate_repository(expected_remote=config.repository)` | T `test_agent_worktree_evidence.py` (trust chain), `test_agent_repository_identity.py` | READY | Binding is enforced, not advisory |
| R38 | base SHA validation | `contracts.SHA_RE` (40 lowercase hex), `validation._validate_sha` | T `test_validation.py`, `test_agent_worktree_lifecycle.py:169` | READY | A short sha is refused so a base can never resolve ambiguously |
| R39 | stale-base rejection | `validation.py:check_base` → `BaseStaleError` | L `agent.base_stale` (integration-head ≠ base), exit 3 | READY | A green run on a stale base is not green |
| R40 | ancestry verification | `gitcmd.contains` → `BaseNotAncestorError` | T `test_agent_git_backed_handoff.py:338` | READY | Proved with `merge-base --is-ancestor` |
| R41 | commit evidence verification | `validation.py:_commit_evidence_blocker` | T `test_agent_git_backed_handoff.py:248`, `test_evidence_hardening.py` | READY | One-to-one bijection; a duplicate or ambiguous prefix token fails |
| R42 | changed_files evidence verification | `verify_repository_evidence` reported-vs-actual | T `test_agent_git_backed_handoff.py:109,267,515` | READY | Omission and invention are separated in `details` |

### 2.6 Integration gate, GitHub boundary, cleanup and errors

| ID | Capability | Authority / executable path | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| R43 | missing repository evidence → NOT READY | `integration_context_incomplete` | T `test_capacity_and_gates.py:146`; L `--repo` omitted → `ready=false`, exit 3 | READY | Fail closed, never "assume clean" |
| R44 | missing orchestration plan → NOT READY | `IntegrationContext.build(include_plan=False)` | T `test_capacity_and_gates.py:154`, `test_evidence_hardening.py`; L `--plan` omitted → 3 blockers | READY | A conflict gate cannot be proved without the plan |
| R45 | dependency blocker | `validation.py:_dependency_blocker` | T `test_capacity_and_gates.py:163,178,197` | READY | `unsatisfiable_dependencies` tells the Main Agent to re-plan |
| R46 | conflict blocker | `validation.py:_conflict_blocker` | T `test_capacity_and_gates.py:217,238` | READY | Deferral is enforced at integration time too, not only dispatch |
| R47 | local integration readiness | `IntegrationContext`, `evaluate_integration` | T `test_agent_final_authority.py`, `test_capacity_and_gates.py`, `test_agent_worktree_evidence.py` | READY | Collects every blocker instead of the first |
| R48 | local / GitHub gate separation | `IntegrationReadiness.to_dict()["github_gate"]` | T `test_multi_agent_orchestration_simulation.py` (the verdict never claims to have checked it) | READY | By design: the local gate does not contact GitHub and says so |
| R49 | cleanup refusal on unsafe state | `worktree.py:remove_worktree` | T `test_agent_worktree_lifecycle.py:215,233,253,271,289,294` | PARTIAL | **P2.** Refusal is correct, but the default integration ref is the **local** `main`, which is stale here (`ae22b82`, behind `origin/main` `bf88873`; verified with `merge-base --is-ancestor`). A branch already contained in `origin/main` is therefore refused with `agent.branch_unmerged` unless `--integration-branch origin/main` is passed. See O-3. |
| R50 | structured error codes / no bare crash | `errors.py:EXIT_CODES`, `cli.py:main` envelope | R + L: `agent.dag_cycle` → 2, `agent.protected_path_conflict` → 3, `agent.task_invalid` → 2, success → 0, all with a JSON envelope and no traceback | READY | No automated test asserts the CLI exit-code contract; verified live in this audit only |

## 3. Findings

### P0-like: none. P1-like: none.

Both `PARTIAL` rows are P2: each has a working path and a documented workaround, and
neither prevents a pilot from starting, committing, handing off or integrating.

**O-1 — the handoff write half is untested and unwired (R29, P2).**
`build_handoff` is tested and Git-derived. `write_handoff` / `render_markdown` are not
covered anywhere and are referenced only from prose (`MULTI_AGENT_PROTOCOL.md:607`).
The CLI exposes `validate-task`, `validate-handoff`, `check-integration`, `plan` and
`worktree *` — but no command that *produces* a handoff. Consequence for the pilot: the
Sub-Agent hand-writes `.agent/handoffs/<task-id>.json`, and the only guarantee is that
`validate-handoff` will refuse a malformed one. That is fail-closed and acceptable for a
first pilot; it is not the same as a tested round trip. *Pilot mitigation*: the Main Agent
runs `validate-handoff` before `check-integration`, exactly as the prompt already says.

**O-2 — three leftover task worktrees are registered (R09, hygiene).**
`worktree list` returns five records, three of them from earlier sessions
(`reliability-01`, `reliability-01-fix-1`, `v0.1.1-acceptance-hardening`) plus one under
`~/.codex/worktrees/`. All three under `.worktrees/` are clean (0 dirty entries). They do
not collide with the pilot's `.worktrees/agent-02-*` names. They were **left untouched**:
they are not this run's work, and no lifecycle tool may remove another session's worktree
on its own initiative.

**O-3 — the local `main` ref is stale (R49, P2).**
`config.default_branch` is `main`, and `remove_worktree` defaults its containment check to
that name. Locally `main` is `ae22b82` (PR #8), several merges behind `origin/main`
`bf88873` (PR #17). A dry-run/pilot worktree branched from the current integration head is
therefore refused by the cleanup gate even though it is contained in `origin/main`.
*Workaround, verified in Phase D*: pass `--integration-branch origin/main`. *Structural
note for the pilot plan*: treat "refresh local `main`" as a pre-flight step.

**O-4 — the shipped example task/handoff pair cannot demonstrate `ready: true` (P2).**
`.agent/examples/task.example.json` carries `"status": "READY"`, not `HANDOFF_READY`, so
`evaluate_integration` records `agent.task_invalid` for it. This is correct behaviour, but
an operator following `main-agent.prompt.md` §7 with the shipped example files will see
blockers. Recorded, deliberately not "fixed" here: changing a shipped example outside the
pilot's scope would be scope drift, and the example is a *dispatch-stage* contract by
design.

**O-5 — protection surface closed on this branch (context).**
`docs/CONTEXT_INDEX.md` and `docs/engineering/AGENT_CONTEXT_GOVERNANCE.md` entered both
`protected_paths` and `public_truth_paths` (commit `bc6f5e6`), closing the prerequisite
recorded in `docs/PROJECT_STATE.md` §AGENT-CONTEXT-PROTECTION. Verified end-to-end: a
Sub-Agent contract owning the router is refused with `agent.protected_path_conflict`
(exit 3) through the real CLI.

## 4. What this audit does not prove

```text
no Sub-Agent was launched                     -> "multi-agent verified" is NOT claimed
the local gate never contacts GitHub          -> R48 is a boundary, not a verified check
no real CAN hardware, no macOS                -> unchanged; this audit adds no platform claim
CI for this branch                            -> belongs to the PR, not to this document
```

The readiness verdict is about the *tooling contract*. A pilot adds the one thing no
audit can substitute for: a real Sub-Agent producing a real handoff from real work.

## 5. Verdict

```text
READY FOR PILOT
  READY             48
  PARTIAL            2   (R29 handoff write half · R49 stale local main on cleanup)
  BLOCKED            0
  NOT IMPLEMENTED    0
  P0-like blockers   0
  P1-like blockers   0
  P2-like notes      4   (O-1, O-2, O-3, O-4)

REAL PILOT NOT STARTED.
```

Next: `docs/engineering/AGENT_02_PILOT_PLAN.md`.
