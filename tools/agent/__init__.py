"""AGENT-01 multi-agent orchestration tooling.

The modules here implement the machine-verifiable half of the protocol in
``docs/engineering/MULTI_AGENT_PROTOCOL.md``:

```text
errors      structured error codes and their CLI exit codes
config      .agent/config.json — parallelism cap, protected paths, branch prefix
paths       repository-relative path contract and ownership glob matching
contracts   frozen TaskContract / HandoffContract records
lifecycle   task status set and the allowed status transitions
graph       dependency DAG, readiness and integration order
conflicts   pairwise conflict classification (C0-C4) and serialisation rules
validation  task / handoff / base / revision validators
gitcmd      a thin, argument-list-only wrapper around the git CLI
worktree    isolated worktree lifecycle (create / validate / list / remove)
handoff     build and render a handoff from real repository state
cli         the ``python -m tools.agent.cli`` entry point
```

Every module is standard-library only and platform-neutral: no shell string is
ever assembled from task data, and no path is ever passed to a shell.
"""
