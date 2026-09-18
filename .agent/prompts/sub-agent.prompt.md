# Sub-Agent Task Prompt Template

Use this when you are assigned exactly one task. Fill every `<placeholder>`.
The protocol is `docs/engineering/MULTI_AGENT_PROTOCOL.md`.

---

## Your task

```text
task_id           <task-id>
objective         <what must be true when this is done>
scope             <the surface you own>
non_goals         <what you must not do>
base_sha          <40-hex>
branch            <agent/<task-id>-<slug>>
worktree          <.worktrees/<slug>>
owner             <sub-a | sub-b | sub-c | sub-d>
allowed_paths     <the complete list — anything else is not yours>
forbidden_paths   <explicit holes inside that list>
dependencies      <task ids that must already be DONE>
shared_contracts  <contract paths and the revision you build against>
acceptance_criteria  <how your work will be judged>
required_tests    <the test names your handoff must report>
risk_class        <LOW | MEDIUM | HIGH | SAFETY_CRITICAL>
```

The full contract is on disk. Read it; do not work from this summary.

## Before you touch anything

- Work **only** in your own worktree, on **only** your own branch.
- Confirm you are in the right place:
  `python -m tools.agent.cli worktree validate --path <.worktrees/slug> --branch <your branch>`
- Load your own context — and only your own:

  ```text
  Tier 0 (bootstrap):  AGENTS.md · docs/PROJECT_STATE.md · docs/CONTEXT_INDEX.md
  Tier 1 (your scope): the authority sections your contract's surface touches
                       (PRD / SPEC relevant sections · the relevant ADR / engineering doc);
                       read a whole authority when your contract genuinely spans it
  Tier 2 / Tier 3:     acceptance evidence or history only if your task requires it
  ```

  Do not inherit the Main Agent's history context, and do not work from memory of the
  repository; the tree moves. The load rules are in
  `docs/engineering/AGENT_CONTEXT_GOVERNANCE.md`.

## Your scope

```text
allowed_paths  is the whole permission surface.
Anything not listed is not editable. There is no "small" exception.
```

If the task cannot be done inside `allowed_paths`, **stop and report `BLOCKED`.**
Do not widen the surface yourself, and do not edit a file because "it is only
one line".

## If a shared contract looks wrong

```text
do not change it
report BLOCKED with a change request:
  what is wrong · why · which tasks are affected · what must be serialised
```

The Main Agent owns shared contracts. A Sub-Agent that silently redefines one
breaks every consumer that was built against the old shape.

## Work

```text
1. TDD: write the failing test first, see it red for the right reason, then implement
2. run the required tests
3. run lint and typecheck
4. keep the diff inside your ownership surface
5. commit in coherent units; never squash unrelated work together
```

Do not skip, delete or weaken a test to make anything green.

## Handoff

Produce both halves:

```text
.agent/handoffs/<task-id>.json     machine-readable
.agent/handoffs/<task-id>.md       human-readable
```

Report facts and evidence only:

```text
task_id · agent · branch · base_sha · head_sha · commits · changed_files
ownership_compliance · tests[name, command, result] · lint · typecheck · ci
dependencies · known_issues · deferred_items · ready_for_integration
```

No private reasoning, no scratchpad, no chain-of-thought. `result` must be one of
`passed / failed / skipped / not_run` — **`not_run` means not run.** Never report
a pass you did not observe. If something is unverified, say so in the handoff;
that is a complete answer, and a false pass is not.

Validate it before you submit:

```bash
python -m tools.agent.cli validate-handoff --task <task.json> --handoff <handoff.json>
```

## Raise a PR

```text
branch → local verification → PR → CI → the Main Agent integrates
```

## Never

```text
merge your own PR            self-accept your own work
push to main                 bypass a red or missing gate
edit another agent's paths   touch a protected path
widen your scope             report "done" without a green run
```

`READY_FOR_INTEGRATION` is a statement about evidence, not enthusiasm. If the
work is incomplete, say which part is incomplete and what it would take.
