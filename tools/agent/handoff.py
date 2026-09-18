"""Build and render a handoff from real repository state (AGENT-01 §16-§18, §44, §92).

The handoff records *facts and evidence*: what changed, which commands ran and
what they returned. It deliberately has no field for private reasoning, and the
commands it carries are strings for a human to read - nothing in this repository
executes a command taken out of a handoff.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from tools.agent.contracts import HandoffContract, TaskContract, TestResult
from tools.agent.gitcmd import (
    current_branch,
    git_lines,
    history_touched_paths,
    repository_root,
    resolve_revision,
)
from tools.agent.validation import ownership_violations

HANDOFF_JSON_SUFFIX = ".json"
HANDOFF_MARKDOWN_SUFFIX = ".md"


def build_handoff(
    repo: Path,
    task: TaskContract,
    *,
    agent: str,
    tests: Sequence[TestResult],
    lint: str | None = None,
    typecheck: str | None = None,
    ci: str | None = None,
    known_issues: Sequence[str] = (),
    deferred_items: Sequence[str] = (),
    ready_for_integration: bool = False,
    revision: str = "HEAD",
) -> HandoffContract:
    """Collect a handoff from the live state of the task branch.

    ``ownership_compliance`` is computed, never accepted as input: the field is a
    derived fact, not a claim the agent gets to make about itself (§20).

    ``changed_files`` means **every path the task history touched** - the union
    across every commit in ``base..head`` - not merely the final PR net diff. It
    is derived by the same helper the verifier uses, so an honest handoff and the
    gate agree by construction (AGENT-01-FIX-2 §22, §26).
    """
    root = repository_root(repo)
    branch = current_branch(root)
    head_sha = resolve_revision(revision, cwd=root)
    base_sha = task.base_sha
    changed_files = history_touched_paths(root, base_sha, head_sha)
    commits = git_lines(["log", "--oneline", f"{base_sha}..{head_sha}"], cwd=root)
    return HandoffContract(
        task_id=task.task_id,
        agent=agent,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        commits=commits,
        changed_files=changed_files,
        ownership_compliance=not ownership_violations(task, changed_files),
        tests=tuple(tests),
        lint=lint,
        typecheck=typecheck,
        ci=ci,
        dependencies=task.dependencies,
        known_issues=tuple(known_issues),
        deferred_items=tuple(deferred_items),
        ready_for_integration=ready_for_integration,
    )


def render_markdown(handoff: HandoffContract) -> str:
    """Render a handoff as the human-readable half of the pair (AGENT-01 §92)."""
    lines: list[str] = [
        f"# Handoff - {handoff.task_id}",
        "",
        f"- Agent: `{handoff.agent}`",
        f"- Branch: `{handoff.branch}`",
        f"- Base SHA: `{handoff.base_sha}`",
        f"- Head SHA: `{handoff.head_sha}`",
        f"- Ready for integration: {'YES' if handoff.ready_for_integration else 'NO'}",
        f"- Ownership compliance: {'YES' if handoff.ownership_compliance else 'NO'}",
        "",
        "## Commits",
        "",
    ]
    lines.extend(f"- `{commit}`" for commit in handoff.commits or ["(none)"])
    lines += ["", "## Changed files", ""]
    lines.extend(f"- `{path}`" for path in handoff.changed_files or ["(none)"])
    lines += ["", "## Tests", "", "| name | result | command |", "| --- | --- | --- |"]
    for result in handoff.tests:
        lines.append(f"| {result.name} | {result.result} | `{result.command}` |")
    lines += [
        "",
        "## Static checks",
        "",
        f"- lint: {handoff.lint or 'not reported'}",
        f"- typecheck: {handoff.typecheck or 'not reported'}",
        f"- CI: {handoff.ci or 'not reported'}",
        "",
        "## Dependencies",
        "",
    ]
    lines.extend(f"- `{dependency}`" for dependency in handoff.dependencies or ["(none)"])
    lines += ["", "## Known issues", ""]
    lines.extend(f"- {issue}" for issue in handoff.known_issues or ["(none)"])
    lines += ["", "## Deferred items", ""]
    lines.extend(f"- {item}" for item in handoff.deferred_items or ["(none)"])
    lines.append("")
    return "\n".join(lines)


def write_handoff(handoff: HandoffContract, directory: Path) -> tuple[Path, Path]:
    """Write ``<task-id>.json`` and ``<task-id>.md``; return both paths."""
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{handoff.task_id}{HANDOFF_JSON_SUFFIX}"
    markdown_path = directory / f"{handoff.task_id}{HANDOFF_MARKDOWN_SUFFIX}"
    json_path.write_text(
        json.dumps(handoff.to_dict(), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(handoff), encoding="utf-8")
    return json_path, markdown_path


def render_commit_list(repo: Path, base_sha: str, head_sha: str) -> tuple[str, ...]:
    """Short sha plus subject for every commit between two revisions."""
    return git_lines(["log", "--oneline", f"{base_sha}..{head_sha}"], cwd=repository_root(repo))


def head_of(repo: Path, revision: str = "HEAD") -> str:
    """The full commit id of ``revision`` in ``repo``."""
    return resolve_revision(revision, cwd=repository_root(repo))


def branch_of(repo: Path) -> str:
    """The branch checked out in ``repo``."""
    return current_branch(repository_root(repo))
