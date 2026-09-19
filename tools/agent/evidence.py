"""Git-backed evidence: what the repository says actually happened.

AGENT-01's trust boundary, restated (FIX-1 §54):

```text
Sub-Agent report        untrusted evidence
Git repository state    authoritative evidence
Task contract           what is permitted
Main Agent verifier     independently compares the two
```

A handoff is a *report format*. Editing its JSON must never be able to turn a
dishonest or stale delivery into ``READY_FOR_INTEGRATION``. Everything the
verifier needs is therefore re-derived here from the real worktree, the real
branch and the real commit range - never from a field the Sub-Agent typed.

This module performs the Git I/O. Comparing the collected evidence against a
handoff is a pure function in :mod:`tools.agent.validation`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.agent.contracts import ExecutionMode, TaskContract
from tools.agent.errors import GitStateError, WorktreeConflictError
from tools.agent.gitcmd import (
    commit_shas,
    contains,
    diff_name_status,
    git_lines,
    history_touched_paths,
    remote_identity,
    resolve_revision,
    touched_paths,
)
from tools.agent.worktree import (
    WorktreeRecord,
    list_worktrees,
    primary_worktree,
    validate_repository,
)

#: Where the evidence was read from. Surfaced so a fallback is never silent.
SOURCE_WORKTREE = "worktree"
SOURCE_BRANCH_REF = "branch-ref"
#: A native shared-worktree delivery: the Main Agent's own worktree (ADR-0003).
SOURCE_NATIVE_SHARED = "native-shared"


@dataclass(frozen=True, kw_only=True)
class RepositoryEvidence:
    """The facts the repository can prove about a task's delivery."""

    repository_root: Path
    source: str
    worktree_path: Path | None
    branch: str
    base_sha: str
    head_sha: str
    base_is_ancestor: bool
    clean: bool
    #: Net tree delta: ``base`` tree versus ``head`` tree. Informational.
    net_changed_paths: tuple[str, ...]
    #: Union of every path touched by every commit in ``base..head``. This is the
    #: authoritative ownership surface: a transient edit that a later commit
    #: reverts is invisible in the net diff but must still be refused.
    history_touched_paths: tuple[str, ...]
    commits: tuple[str, ...]
    diff_records: tuple[tuple[str, tuple[str, ...]], ...]
    #: The canonical ``owner/repo`` identity of the origin the evidence came
    #: from, when one could be read. Diagnostics only - the binding itself is
    #: enforced by ``validate_repository(expected_remote=...)``.
    repository_identity: str | None = None
    #: The HEAD of the worktree the evidence was read from. In native-shared mode
    #: that worktree *is* the integration worktree, so this is the integration
    #: head, and the two flags below are the proof that the delivery is on it
    #: (V0.3-12-FIX-1). ``None`` for the branch-ref fallback of an isolated task.
    integration_head_sha: str | None = None
    base_on_integration_head: bool = False
    head_on_integration_head: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "repository_root": str(self.repository_root),
            "repository_identity": self.repository_identity,
            "source": self.source,
            "worktree_path": str(self.worktree_path) if self.worktree_path else None,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "base_is_ancestor": self.base_is_ancestor,
            "clean": self.clean,
            "integration_head_sha": self.integration_head_sha,
            "base_on_integration_head": self.base_on_integration_head,
            "head_on_integration_head": self.head_on_integration_head,
            "net_changed_paths": list(self.net_changed_paths),
            "history_touched_paths": list(self.history_touched_paths),
            "commits": list(self.commits),
            "diff_records": [[status, list(paths)] for status, paths in self.diff_records],
        }


def declared_coordinates(task: TaskContract) -> tuple[str, str]:
    """The ``(branch, worktree)`` an **isolated** task declares (V0.3-12-FIX-1).

    Only an ``isolated-worktree`` task has them: the parser refuses a
    native-shared task that declares either, and a native-shared delivery is
    collected by :func:`_collect_native_shared` before any of this runs. Raising
    rather than defaulting is deliberate — a missing coordinate must never be
    able to look like a valid one, and the type checker narrowing falls out of
    the same guard instead of a cast.
    """
    if task.branch is None or task.worktree is None:
        raise WorktreeConflictError(
            "an isolated task must declare both a branch and a worktree",
            details={
                "task_id": task.task_id,
                "execution_mode": str(task.execution_mode),
                "branch": task.branch,
                "worktree": task.worktree,
            },
        )
    return task.branch, task.worktree


def resolve_task_worktree(
    root: Path, task: TaskContract
) -> tuple[Path, WorktreeRecord | None]:
    """Locate ``task``'s registered worktree, or refuse a contract mismatch.

    ``root`` may be the main worktree or a linked task worktree - both are
    supported inputs. ``task.worktree`` is repository-relative, so it is resolved
    against the **primary** worktree root, never against whichever worktree
    happened to invoke the tool (AGENT-01-FIX-2 §15-§18).

    The contract freezes *both* identities - the branch and the worktree path -
    and Git must agree with both. Every registered worktree is examined and the
    state is classified (AGENT-01-FIX-3 §5-§11):

    ```text
    A  declared path + branch both match          -> use that worktree
    B  declared path exists, branch differs       -> agent.worktree_conflict
    C  branch is registered at another path       -> agent.worktree_conflict
    D  ambiguous metadata (same path/branch x2)   -> agent.worktree_conflict
    E  neither the path nor the branch registered -> branch-ref fallback
    ```

    A mismatch is never silently repaired: following the branch to a different
    worktree would make ``task.worktree`` non-authoritative, and falling back to
    the branch ref would ignore a live worktree that may hold dirty work.
    """
    declared_branch, declared_worktree = declared_coordinates(task)
    primary_root = primary_worktree(root)
    declared = (primary_root / declared_worktree).resolve()
    records = list_worktrees(primary_root)
    path_matches = [record for record in records if record.path.resolve() == declared]
    branch_matches = [
        record for record in records if (record.branch or "") == declared_branch
    ]
    exact = [record for record in path_matches if (record.branch or "") == declared_branch]

    def conflict(reason: str, actual: WorktreeRecord | None) -> WorktreeConflictError:
        return WorktreeConflictError(
            reason,
            details={
                "task_id": task.task_id,
                "expected_branch": declared_branch,
                "expected_worktree": declared_worktree,
                "actual_worktree": str(actual.path) if actual is not None else None,
                "actual_branch": actual.branch if actual is not None else None,
                "declared_path": str(declared),
            },
        )

    if len(exact) > 1 or len(path_matches) > 1 or len(branch_matches) > 1:
        ambiguous = (exact or path_matches or branch_matches)[0]
        raise conflict(
            "worktree metadata is ambiguous for this task's branch or path", ambiguous
        )
    if exact:
        return primary_root, exact[0]
    if path_matches:
        raise conflict(
            "the declared task worktree path is checked out on a different branch",
            path_matches[0],
        )
    if branch_matches:
        raise conflict(
            "the task branch is registered at a different worktree path than the"
            " contract declares",
            branch_matches[0],
        )
    return primary_root, None


def collect_repository_evidence(
    repo: Path,
    task: TaskContract,
    *,
    expected_repository: str | None = None,
    delivery_head_sha: str | None = None,
    integration_head_sha: str | None = None,
) -> RepositoryEvidence:
    """Read the authoritative facts for ``task`` out of the repository.

    ``expected_repository`` is a canonical ``owner/repo`` identity. When it is
    given, the repository must be *that* repository - a wrong owner, a lookalike
    name, an unsupported host or a missing origin raises ``agent.git_state_error``
    instead of yielding evidence. The final integration gate always passes
    ``config.repository``, so "some valid Git repository" is never enough
    (AGENT-01-FIX-3 §16-§20).

    The task worktree is the strongest local source of truth, and handoff
    validation is meant to happen before any cleanup, so it is preferred. When
    no task worktree is registered the task *branch ref* is used instead - still
    Git, and still required to resolve to the head the handoff claims - and the
    fallback is recorded in ``source`` rather than hidden (FIX-1 §9). In that
    case ``clean`` means "no live task worktree exists **and** nothing is left at
    the declared path to contain uncommitted changes" - **not** "a worktree was
    inspected and found clean" (FIX-2 §20).
    """
    root = validate_repository(repo, expected_remote=expected_repository)
    if task.execution_mode is ExecutionMode.NATIVE_SHARED:
        return _collect_native_shared(
            root,
            task,
            delivery_head_sha=delivery_head_sha,
            integration_head_sha=integration_head_sha,
        )
    primary_root, record = resolve_task_worktree(root, task)
    if record is not None:
        source = SOURCE_WORKTREE
        worktree_path: Path | None = record.path
        branch = record.branch or ""
        head_sha = record.head or resolve_revision("HEAD", cwd=record.path)
        clean = not git_lines(["status", "--porcelain"], cwd=record.path)
        probe = record.path
    else:
        source = SOURCE_BRANCH_REF
        worktree_path = None
        branch, declared_worktree = declared_coordinates(task)
        head_sha = resolve_revision(branch, cwd=primary_root)
        # No task worktree is registered. ``clean`` then means "nothing is left
        # at the declared path that could hold uncommitted work": a leftover,
        # de-registered directory is treated as dirty rather than assumed clean,
        # so removing a worktree's registration cannot launder its dirty state
        # (FIX-2 §20). The declared path is never the primary root here - if it
        # were, the main worktree would have matched above.
        declared = (primary_root / declared_worktree).resolve()
        clean = declared == primary_root.resolve() or not declared.exists()
        probe = primary_root
    base_sha = task.base_sha
    records = diff_name_status(probe, base_sha, head_sha)
    return RepositoryEvidence(
        repository_root=primary_root,
        source=source,
        worktree_path=worktree_path,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        base_is_ancestor=contains(probe, base_sha, head_sha),
        clean=clean,
        net_changed_paths=touched_paths(probe, base_sha, head_sha),
        history_touched_paths=history_touched_paths(probe, base_sha, head_sha),
        commits=commit_shas(probe, base_sha, head_sha),
        diff_records=records,
        repository_identity=remote_identity(primary_root),
    )


def _collect_native_shared(
    root: Path,
    task: TaskContract,
    *,
    delivery_head_sha: str | None,
    integration_head_sha: str | None = None,
) -> RepositoryEvidence:
    """Evidence for a worker that executed inside the Main Agent's worktree.

    The AGENT-01 model resolves a *task* worktree through ``task.worktree`` and
    ``task.branch``. A native-shared worker has neither: the host Harness is not
    asked to create them, and the delivery is simply a commit range on the
    branch the Main Agent already has checked out (ADR-0003, V0.3-12-FIX-1).

    So there is nothing to resolve and nothing to trust. The integration
    worktree is the only surface the delivery can be on, and it is read here:

    * the **branch** comes from the repository, not from a field — a handoff
      cannot name the branch it wishes it were on;
    * the **head** is the commit the handoff claims, resolved to a real commit
      and then required to be an ancestor of the integration head, so an
      invented or foreign head is a refusal rather than an assumption;
    * the **base** is ``task.base_sha``, and it too must be an ancestor of the
      integration head: the delivery has to be *on* this branch;
    * ownership is decided exactly as before, on
      ``history_touched_paths(base, head)`` — the union of every path any commit
      in the range touched, not the net tree delta.

    ``clean`` means the integration worktree has no uncommitted work, so the
    committed range really is the whole delivery.

    Raises:
        GitStateError: If the worktree is not on a named branch, if no delivery
            head was supplied, or if the claimed head does not resolve.
    """
    primary_root = primary_worktree(root)
    records = [
        record
        for record in list_worktrees(primary_root)
        if record.path.resolve() == primary_root.resolve()
    ]
    if len(records) != 1:
        raise GitStateError(
            "the integration worktree could not be identified unambiguously",
            details={"repository_root": str(primary_root), "matches": len(records)},
        )
    record = records[0]
    branch = record.branch or ""
    worktree_head_sha = record.head or resolve_revision("HEAD", cwd=primary_root)
    if not branch:
        raise GitStateError(
            "the integration worktree is not on a named branch; a shared-worktree"
            " delivery must be committed on one",
            details={"repository_root": str(primary_root), "head": worktree_head_sha},
        )
    if delivery_head_sha is None:
        raise GitStateError(
            "a native-shared delivery must name the commit it claims",
            details={"field": "delivery_head_sha", "task_id": task.task_id},
        )
    head_sha = resolve_revision(delivery_head_sha, cwd=primary_root)
    base_sha = task.base_sha
    # The integration head is named by the *caller*, exactly as it is in the
    # isolated path: the tooling does not decide which branch is "the"
    # integration branch. Without a caller-supplied head the checked-out branch's
    # tip is used, which is the same answer for a single-worktree run — and the
    # ancestry below is still decided by Git, never by the argument.
    head_of_integration = resolve_revision(
        integration_head_sha or worktree_head_sha, cwd=primary_root
    )
    return RepositoryEvidence(
        repository_root=primary_root,
        source=SOURCE_NATIVE_SHARED,
        worktree_path=primary_root,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        base_is_ancestor=contains(primary_root, base_sha, head_sha),
        clean=not git_lines(["status", "--porcelain"], cwd=primary_root),
        net_changed_paths=touched_paths(primary_root, base_sha, head_sha),
        history_touched_paths=history_touched_paths(primary_root, base_sha, head_sha),
        commits=commit_shas(primary_root, base_sha, head_sha),
        diff_records=diff_name_status(primary_root, base_sha, head_sha),
        repository_identity=remote_identity(primary_root),
        integration_head_sha=head_of_integration,
        base_on_integration_head=contains(primary_root, base_sha, head_of_integration),
        head_on_integration_head=contains(primary_root, head_sha, head_of_integration),
    )
