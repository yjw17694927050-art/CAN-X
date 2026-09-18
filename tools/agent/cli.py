"""The AGENT-01 command-line entry point.

```text
python -m tools.agent.cli validate-task      --file .agent/examples/task.example.json
python -m tools.agent.cli validate-handoff   --task ... --handoff ...
python -m tools.agent.cli check-integration  --task ... --handoff ... --integration-head <sha>
python -m tools.agent.cli plan               --file .agent/examples/tasks.dependency.example.json
python -m tools.agent.cli worktree create    --task-id ... --branch ... --path ... --base <sha>
python -m tools.agent.cli worktree list
python -m tools.agent.cli worktree validate  --path .worktrees/task-a
python -m tools.agent.cli worktree remove    --path .worktrees/task-a
```

Exit codes are the contract (AGENT-01 §55): 0 success, 2 validation error,
3 ownership / conflict, 4 git state, 5 internal. Every failure prints the
structured envelope ``{"code": ..., "message": ..., "details": {...}}``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tools.agent.config import DEFAULT_CONFIG_RELPATH, AgentConfig, load_config
from tools.agent.contracts import load_handoff, load_task, load_task_plan
from tools.agent.errors import DEFAULT_EXIT_CODE, AgentToolingError
from tools.agent.gitcmd import is_git_repository, repository_root
from tools.agent.orchestration import plan
from tools.agent.validation import (
    evaluate_integration,
    validate_handoff,
    validate_task,
)
from tools.agent.worktree import (
    create_worktree,
    list_worktrees,
    validate_repository,
    validate_worktree,
)
from tools.agent.worktree import remove_worktree as remove_task_worktree


def _discover_repository(start: Path) -> Path:
    try:
        if is_git_repository(start):
            return repository_root(start)
    except AgentToolingError:
        pass
    return start


def _resolve_config(explicit: Path | None, repo: Path) -> AgentConfig:
    path = explicit if explicit is not None else repo / DEFAULT_CONFIG_RELPATH
    return load_config(path)


def _emit(payload: object) -> None:
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.agent.cli",
        description="CAN-X AGENT-01 multi-agent orchestration tooling.",
    )
    parser.add_argument("--repo", type=Path, default=None, help="repository root")
    parser.add_argument("--config", type=Path, default=None, help="path to .agent/config.json")
    sub = parser.add_subparsers(dest="command", required=True)

    validate_task_parser = sub.add_parser("validate-task", help="validate one task contract")
    validate_task_parser.add_argument("--file", type=Path, required=True)

    validate_handoff_parser = sub.add_parser("validate-handoff", help="validate a handoff")
    validate_handoff_parser.add_argument("--task", type=Path, required=True)
    validate_handoff_parser.add_argument("--handoff", type=Path, required=True)

    check_parser = sub.add_parser(
        "check-integration", help="evaluate a handoff against the integration head"
    )
    check_parser.add_argument("--task", type=Path, required=True)
    check_parser.add_argument("--handoff", type=Path, required=True)
    check_parser.add_argument("--integration-head", type=str, required=True)

    plan_parser = sub.add_parser("plan", help="compute the orchestration plan for a task set")
    plan_parser.add_argument("--file", type=Path, required=True)

    worktree_parser = sub.add_parser("worktree", help="isolated worktree lifecycle")
    worktree_sub = worktree_parser.add_subparsers(dest="worktree_command", required=True)
    create_parser = worktree_sub.add_parser("create")
    create_parser.add_argument("--task-id", required=True)
    create_parser.add_argument("--branch", required=True)
    create_parser.add_argument("--path", type=Path, required=True)
    create_parser.add_argument("--base", required=True)
    worktree_sub.add_parser("list")
    validate_parser = worktree_sub.add_parser("validate")
    validate_parser.add_argument("--path", type=Path, required=True)
    validate_parser.add_argument("--branch", default=None)
    validate_parser.add_argument("--head", default=None)
    remove_parser = worktree_sub.add_parser("remove")
    remove_parser.add_argument("--path", type=Path, required=True)
    remove_parser.add_argument("--integration-branch", default=None)
    remove_parser.add_argument("--allow-unmerged", action="store_true")
    remove_parser.add_argument("--delete-branch", action="store_true")
    return parser


def _run(args: argparse.Namespace) -> int:
    repo = (args.repo or _discover_repository(Path.cwd())).resolve()
    config = _resolve_config(args.config, repo)

    if args.command == "validate-task":
        task = load_task(args.file)
        validate_task(task, config)
        _emit({"status": "ok", "code": None, "task_id": task.task_id})
        return 0

    if args.command == "validate-handoff":
        task = load_task(args.task)
        handoff = load_handoff(args.handoff)
        validate_handoff(handoff, task, config)
        _emit({"status": "ok", "code": None, "task_id": task.task_id})
        return 0

    if args.command == "check-integration":
        task = load_task(args.task)
        handoff = load_handoff(args.handoff)
        readiness = evaluate_integration(handoff, task, config, args.integration_head)
        _emit(readiness.to_dict())
        return 0 if readiness.ready else 3

    if args.command == "plan":
        tasks = load_task_plan(args.file)
        for task in tasks:
            validate_task(task, config)
        _emit(plan(tasks, config).to_dict())
        return 0

    if args.command == "worktree":
        validate_repository(repo, expected_remote=config.repository)
        if args.worktree_command == "create":
            record = create_worktree(
                repo,
                task_id=args.task_id,
                branch=args.branch,
                path=args.path,
                base_sha=args.base,
                expected_remote=config.repository,
            )
            _emit(record.to_dict())
            return 0
        if args.worktree_command == "list":
            _emit([record.to_dict() for record in list_worktrees(repo)])
            return 0
        if args.worktree_command == "validate":
            record = validate_worktree(
                repo, args.path, expected_branch=args.branch, expected_head=args.head
            )
            _emit(record.to_dict())
            return 0
        if args.worktree_command == "remove":
            remove_task_worktree(
                repo,
                args.path,
                integration_branch=args.integration_branch or config.default_branch,
                allow_unmerged=args.allow_unmerged,
                delete_branch=args.delete_branch,
            )
            _emit({"status": "removed", "path": str(args.path)})
            return 0
    raise AgentToolingError(f"unhandled command: {args.command}")  # pragma: no cover


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except AgentToolingError as exc:
        sys.stderr.write(json.dumps(exc.as_dict(), indent=2) + "\n")
        return exc.exit_code
    except OSError as exc:  # pragma: no cover - defensive
        sys.stderr.write(
            json.dumps(
                {"code": "agent.internal", "message": str(exc), "details": {}},
                indent=2,
            )
            + "\n"
        )
        return DEFAULT_EXIT_CODE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
