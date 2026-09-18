# Main-Agent Task Prompt Template

Use this when you are the agent coordinating parallel work. Fill every
`<placeholder>`; delete nothing. The protocol it implements is
`docs/engineering/MULTI_AGENT_PROTOCOL.md`.

---

## 0. Read first

```text
AGENTS.md
PRD.md                                  (relevant sections)
SPEC.md                                 (relevant sections)
docs/PROJECT_STATE.md
docs/engineering/INTEGRATION_POLICY.md
docs/engineering/MULTI_AGENT_PROTOCOL.md
.github/workflows/ci.yml
.agent/config.json
```

Do not start from your memory of the repository. Read the canonical documents and
confirm the current `main` head, the latest `main` CI run and the ruleset state
from GitHub.

## 1. Freeze the goal

State, in one paragraph, what must be true when the whole effort is done, and what
is explicitly out of scope. Decompose only after that is written down.

## 2. Freeze the shared contracts first

```text
Which public-truth artefacts does this work touch?
  runtime/canx/domain/**  · runtime/canx/api/**  · runtime/canx/safety/**
  runtime/canx/agent/tools.py  · .agent/schemas/**
Who owns each one?  (exactly one task, or the Main Agent itself)
```

A contract that no task owns is a contract no one is allowed to invent. Decide it
before delegating; consumers implement against it.

## 3. Build the DAG

```text
task_id · objective · depends_on[] · allowed_paths · forbidden_paths
required_tests · acceptance_criteria · risk_class
```

Then run:

```bash
python -m tools.agent.cli plan --file <plan.json>
```

Confirm the verdict matches your intent before dispatching anything: the runnable
set, the blocked set, the deferred set and the blocking conflicts.

## 4. Assign ownership

Per task, write a `TaskContract` and validate it:

```bash
python -m tools.agent.cli validate-task --file .agent/tasks/<task-id>.json
```

`allowed_paths` is the whole permission surface. Anything not listed is not
editable. No task may own a protected path unless its owner is `main`.

## 5. Allocate branch and worktree

```text
branch     agent/<task-id>-<lower-kebab-slug>
worktree   .worktrees/<task-id-lower>
```

```bash
python -m tools.agent.cli worktree create --task-id <id> --branch <branch> \
  --path .worktrees/<slug> --base <current-main-40-hex>
```

One branch, one agent, one task, one worktree. Never two agents on one branch.

Release the next wave only when the independent set is genuinely independent; the
parallelism cap is four, and it is a ceiling, not a target.

## 6. Dispatch a Sub-Agent

Use `prompts/sub-agent.prompt.md`. Give it: its contract file, its worktree, its
branch, its tests, its handoff requirements — and nothing else it does not need.

## 7. Validate the handoff

```bash
python -m tools.agent.cli validate-handoff --task <task.json> --handoff <handoff.json>
python -m tools.agent.cli check-integration --task <task.json> \
  --handoff <handoff.json> --integration-head <current-main-40-hex>
```

`check-integration` must report `ready: true`. Re-derive ownership; never accept
the `ownership_compliance` flag on its own. A `C2+` conflict with another
in-flight task stops the integration.

## 8. Integrate, serially

```text
one PR at a time
PR updated onto the current integration head
Quality Gate green on that head
merge through the protected workflow — never a direct push, never a bypass
```

If the base moves between the green run and the merge, the handoff is stale:
update, re-run CI, then merge.

## 9. Report

```text
tasks planned / dispatched / integrated / blocked / cancelled
per-task handoff verdict and blockers
conflicts found and how they were resolved
stale bases encountered and how they were cleared
CI results per head
what is deferred, and why
```

## 10. Never

```text
self-accept the phase          write Final Acceptance: PASS / Status: CLOSED
merge a red or missing gate    disable or weaken the ruleset or ci.yml
let two tasks share a branch   widen a task's ownership after the fact
claim a real multi-agent pilot
```

The phase ends at `awaiting independent acceptance`. That verdict is external.
