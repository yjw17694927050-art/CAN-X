# `.agent/` — Multi-Agent Orchestration Contracts

This directory holds the **data** half of AGENT-01. The enforcement lives in
`tools/agent/`, and the rules are stated in
`docs/engineering/MULTI_AGENT_PROTOCOL.md`. Read the protocol before using
anything here.

```text
config.json                    the knobs: parallelism cap, protected/public-truth/
                               safety path classes, branch prefix, worktrees dir
schemas/task.schema.json       the task interchange contract (JSON Schema 2020-12)
schemas/handoff.schema.json    the handoff interchange contract
examples/task.example.json     one valid task
examples/handoff.example.json  one valid handoff for that task
examples/handoff.ownership-violation.example.json
                               the same handoff, changing a file it does not own
examples/tasks.dependency.example.json
                               the four-task plan the simulation runs
prompts/                       Main-Agent and Sub-Agent prompt templates
handoffs/                      where produced handoffs land
```

## Using it

```bash
# validate a task contract
python -m tools.agent.cli validate-task --file .agent/examples/task.example.json

# validate a handoff against its task
python -m tools.agent.cli validate-handoff \
  --task .agent/examples/task.example.json \
  --handoff .agent/examples/handoff.example.json

# the Main Agent's integration verdict for one handoff
python -m tools.agent.cli check-integration \
  --task .agent/examples/task.example.json \
  --handoff .agent/examples/handoff.example.json \
  --integration-head <40-hex-current-main>

# the whole-plan verdict for a task set
python -m tools.agent.cli plan --file .agent/examples/tasks.dependency.example.json

# isolated worktrees
python -m tools.agent.cli worktree create --task-id AGENT-02-B \
  --branch agent/AGENT-02-B-handoff-validator \
  --path .worktrees/agent-02-b --base <40-hex>
python -m tools.agent.cli worktree list
python -m tools.agent.cli worktree remove --path .worktrees/agent-02-b
```

On Windows, `scripts\agent.cmd` is the same entry point.

Exit codes are the contract: `0` success, `2` validation error, `3` ownership /
conflict, `4` git state error, `5` internal failure. Failures print a structured
envelope:

```json
{ "code": "agent.ownership_violation", "message": "...", "details": { } }
```

## Two things this directory is not

- **Not executable.** A task or handoff is data. Parsing never imports, never
  evaluates and never runs a command; the `command` strings inside a handoff are
  evidence for a human, and nothing in this repository executes them.
- **Not a place for credentials.** `config.json` names paths and caps. No token,
  password or secret belongs in it — authentication stays with the external
  GitHub environment.

## Keeping the two halves in sync

The schemas and the Python parser are checked against each other by
`tests/unit/agent_tools/test_examples_and_schemas.py`:

- the schema `required` list must equal the parser's required-field constant;
- the example keys must match the schema's property set exactly;
- the shipped examples must pass the same validators a real task and handoff do.

Changing a contract means changing both halves and their tests in one commit.
