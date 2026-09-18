"""Frozen task and handoff contracts (AGENT-01 §15, §16, §36).

These records are the interchange format between the Main Agent and a Sub-Agent.
They are plain data: parsing never executes, never imports and never shells out,
so a handoff or task file is inert even when it is attacker-controlled
(AGENT-01 §99, §100).

``RiskClass`` is a *development* risk label. It is deliberately a different type
from the Runtime's ``canx.safety.risk.RiskLevel``: a high-risk development task
is not a vehicle operation, and the two must never be conflated (AGENT-01 §27).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from tools.agent.errors import AgentToolingError, HandoffInvalidError, TaskInvalidError
from tools.agent.lifecycle import TaskStatus

SCHEMA_VERSION: Final[int] = 1

#: Task ids are deterministic, bounded and human readable (AGENT-01 §32):
#: ``AGENT-01-A``, ``AGENT-02-B``, ``agent-task-001``, ``V0.3-12``.
TASK_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$"
)

#: A full 40-hex commit id. A short sha is refused so a stale base can never be
#: resolved ambiguously (AGENT-01 §34).
SHA_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")

#: The subset of a git refname the protocol needs, plus the ``agent/`` family.
BRANCH_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")
BRANCH_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Results a handoff may report for a test. ``not_run`` is a first-class answer:
#: "not executed" must be reportable as itself, never as a pass (AGENT-01 §75).
TEST_RESULTS: Final[frozenset[str]] = frozenset({"passed", "failed", "skipped", "not_run"})

#: A task may not silently mutate these once it is under way (AGENT-01 §36).
FROZEN_FIELDS: Final[tuple[str, ...]] = ("objective", "allowed_paths", "shared_contracts")

#: Fields a task document must carry. ``task.schema.json`` mirrors this list
#: exactly; a test asserts the two never drift (AGENT-01 §19, §53).
TASK_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "schema_version",
    "task_id",
    "title",
    "objective",
    "scope",
    "non_goals",
    "base_sha",
    "branch",
    "worktree",
    "owner",
    "allowed_paths",
    "forbidden_paths",
    "dependencies",
    "shared_contracts",
    "acceptance_criteria",
    "required_tests",
    "handoff_requirements",
    "risk_class",
    "status",
    "revision",
)

#: Fields a handoff document must carry. Optional context fields
#: (``lint`` / ``typecheck`` / ``ci`` / ``known_issues`` / ``deferred_items``)
#: may be absent or null - absence is reported as absence, never as a pass.
HANDOFF_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "schema_version",
    "task_id",
    "agent",
    "branch",
    "base_sha",
    "head_sha",
    "commits",
    "changed_files",
    "ownership_compliance",
    "tests",
    "dependencies",
    "ready_for_integration",
)

ErrorType = type[AgentToolingError]


class RiskClass(StrEnum):
    """Development-task risk label (not a vehicle-operation risk level)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    SAFETY_CRITICAL = "SAFETY_CRITICAL"


def _expect_mapping(value: object, *, where: str, error: ErrorType) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise error(f"{where} must be a JSON object", details={"field": where})
    return value


def _expect_str(data: dict[str, Any], key: str, *, where: str, error: ErrorType) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise error(
            f"{where}.{key} must be a non-empty string",
            details={"field": f"{where}.{key}"},
        )
    return value


def _expect_optional_str(
    data: dict[str, Any], key: str, *, where: str, error: ErrorType
) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise error(f"{where}.{key} must be a string or null", details={"field": f"{where}.{key}"})
    return value


def _expect_str_tuple(
    data: dict[str, Any], key: str, *, where: str, error: ErrorType, required: bool = True
) -> tuple[str, ...]:
    if required and key not in data:
        raise error(f"{where}.{key} is required", details={"field": f"{where}.{key}"})
    value = data.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise error(
            f"{where}.{key} must be a list of strings",
            details={"field": f"{where}.{key}"},
        )
    return tuple(value)


def _expect_bool(data: dict[str, Any], key: str, *, where: str, error: ErrorType) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise error(f"{where}.{key} must be a boolean", details={"field": f"{where}.{key}"})
    return value


def _expect_int(data: dict[str, Any], key: str, *, where: str, error: ErrorType) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise error(f"{where}.{key} must be an integer", details={"field": f"{where}.{key}"})
    return value


def _expect_schema_version(data: dict[str, Any], *, where: str, error: ErrorType) -> int:
    if "schema_version" not in data:
        raise error(
            f"{where}.schema_version is required",
            details={"field": f"{where}.schema_version"},
        )
    version = data["schema_version"]
    if version != SCHEMA_VERSION:
        raise error(
            f"{where}.schema_version must be {SCHEMA_VERSION}",
            details={"field": f"{where}.schema_version", "value": repr(version)},
        )
    return SCHEMA_VERSION


@dataclass(frozen=True, kw_only=True)
class ContractRef:
    """A shared contract a task is built against (AGENT-01 §35)."""

    path: str
    sha: str | None = None

    @classmethod
    def from_dict(cls, data: object, *, error: ErrorType = TaskInvalidError) -> ContractRef:
        payload = _expect_mapping(data, where="shared_contracts[]", error=error)
        path = _expect_str(payload, "path", where="shared_contracts[]", error=error)
        sha = _expect_optional_str(payload, "sha", where="shared_contracts[]", error=error)
        return cls(path=path, sha=sha)

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "sha": self.sha}


@dataclass(frozen=True, kw_only=True)
class TestResult:
    """One test command a handoff reports (AGENT-01 §16, §39)."""

    #: This is a data record, not a test case. Without this, pytest's collector
    #: tries to collect any module that imports it.
    __test__ = False

    name: str
    command: str
    result: str

    @classmethod
    def from_dict(cls, data: object, *, error: ErrorType = HandoffInvalidError) -> TestResult:
        payload = _expect_mapping(data, where="tests[]", error=error)
        name = _expect_str(payload, "name", where="tests[]", error=error)
        command = _expect_str(payload, "command", where="tests[]", error=error)
        result = _expect_str(payload, "result", where="tests[]", error=error)
        if result not in TEST_RESULTS:
            raise error(
                f"tests[].result must be one of {sorted(TEST_RESULTS)}",
                details={"field": "tests[].result", "value": result},
            )
        return cls(name=name, command=command, result=result)

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "command": self.command, "result": self.result}


@dataclass(frozen=True, kw_only=True)
class TaskContract:
    """One unit of delegated work with a bounded ownership surface."""

    task_id: str
    title: str
    objective: str
    scope: str
    non_goals: tuple[str, ...]
    base_sha: str
    branch: str
    worktree: str
    owner: str
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    dependencies: tuple[str, ...]
    shared_contracts: tuple[ContractRef, ...]
    acceptance_criteria: tuple[str, ...]
    required_tests: tuple[str, ...]
    handoff_requirements: tuple[str, ...]
    risk_class: RiskClass = RiskClass.LOW
    status: TaskStatus = TaskStatus.PLANNED
    revision: int = 1
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: object) -> TaskContract:
        """Parse a task contract. Never executes anything."""
        payload = _expect_mapping(data, where="task", error=TaskInvalidError)
        error = TaskInvalidError
        if "risk_class" not in payload:
            raise error("task.risk_class is required", details={"field": "task.risk_class"})
        risk_raw = payload["risk_class"]
        if not isinstance(risk_raw, str) or risk_raw not in {item.value for item in RiskClass}:
            raise error(
                f"task.risk_class must be one of {[item.value for item in RiskClass]}",
                details={"field": "task.risk_class", "value": repr(risk_raw)},
            )
        if "status" not in payload:
            raise error("task.status is required", details={"field": "task.status"})
        status_raw = payload["status"]
        if not isinstance(status_raw, str) or status_raw not in {item.value for item in TaskStatus}:
            raise error(
                f"task.status must be one of {[item.value for item in TaskStatus]}",
                details={"field": "task.status", "value": repr(status_raw)},
            )
        contracts_raw = payload.get("shared_contracts")
        if not isinstance(contracts_raw, list):
            raise error(
                "task.shared_contracts must be a list",
                details={"field": "task.shared_contracts"},
            )
        return cls(
            schema_version=_expect_schema_version(payload, where="task", error=error),
            task_id=_expect_str(payload, "task_id", where="task", error=error),
            title=_expect_str(payload, "title", where="task", error=error),
            objective=_expect_str(payload, "objective", where="task", error=error),
            scope=_expect_str(payload, "scope", where="task", error=error),
            non_goals=_expect_str_tuple(payload, "non_goals", where="task", error=error),
            base_sha=_expect_str(payload, "base_sha", where="task", error=error),
            branch=_expect_str(payload, "branch", where="task", error=error),
            worktree=_expect_str(payload, "worktree", where="task", error=error),
            owner=_expect_str(payload, "owner", where="task", error=error),
            allowed_paths=_expect_str_tuple(payload, "allowed_paths", where="task", error=error),
            forbidden_paths=_expect_str_tuple(
                payload, "forbidden_paths", where="task", error=error
            ),
            dependencies=_expect_str_tuple(payload, "dependencies", where="task", error=error),
            shared_contracts=tuple(ContractRef.from_dict(item) for item in contracts_raw),
            acceptance_criteria=_expect_str_tuple(
                payload, "acceptance_criteria", where="task", error=error
            ),
            required_tests=_expect_str_tuple(payload, "required_tests", where="task", error=error),
            handoff_requirements=_expect_str_tuple(
                payload, "handoff_requirements", where="task", error=error
            ),
            risk_class=RiskClass(risk_raw),
            status=TaskStatus(status_raw),
            revision=_expect_int(payload, "revision", where="task", error=error),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "title": self.title,
            "objective": self.objective,
            "scope": self.scope,
            "non_goals": list(self.non_goals),
            "base_sha": self.base_sha,
            "branch": self.branch,
            "worktree": self.worktree,
            "owner": self.owner,
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "dependencies": list(self.dependencies),
            "shared_contracts": [item.to_dict() for item in self.shared_contracts],
            "acceptance_criteria": list(self.acceptance_criteria),
            "required_tests": list(self.required_tests),
            "handoff_requirements": list(self.handoff_requirements),
            "risk_class": str(self.risk_class),
            "status": str(self.status),
            "revision": self.revision,
        }

    def frozen_snapshot(self) -> dict[str, object]:
        """The fields that may not change without a revision bump (§36)."""
        full = self.to_dict()
        return {name: full[name] for name in FROZEN_FIELDS}


@dataclass(frozen=True, kw_only=True)
class HandoffContract:
    """What a Sub-Agent reports back once its task is implementation-complete."""

    task_id: str
    agent: str
    branch: str
    base_sha: str
    head_sha: str
    commits: tuple[str, ...]
    changed_files: tuple[str, ...]
    ownership_compliance: bool
    tests: tuple[TestResult, ...]
    lint: str | None = None
    typecheck: str | None = None
    ci: str | None = None
    dependencies: tuple[str, ...] = ()
    known_issues: tuple[str, ...] = ()
    deferred_items: tuple[str, ...] = ()
    ready_for_integration: bool = False
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: object) -> HandoffContract:
        """Parse a handoff. Never executes anything."""
        payload = _expect_mapping(data, where="handoff", error=HandoffInvalidError)
        error = HandoffInvalidError
        tests_raw = payload.get("tests")
        if not isinstance(tests_raw, list):
            raise error("handoff.tests must be a list", details={"field": "handoff.tests"})
        return cls(
            schema_version=_expect_schema_version(payload, where="handoff", error=error),
            task_id=_expect_str(payload, "task_id", where="handoff", error=error),
            agent=_expect_str(payload, "agent", where="handoff", error=error),
            branch=_expect_str(payload, "branch", where="handoff", error=error),
            base_sha=_expect_str(payload, "base_sha", where="handoff", error=error),
            head_sha=_expect_str(payload, "head_sha", where="handoff", error=error),
            commits=_expect_str_tuple(payload, "commits", where="handoff", error=error),
            changed_files=_expect_str_tuple(payload, "changed_files", where="handoff", error=error),
            ownership_compliance=_expect_bool(
                payload, "ownership_compliance", where="handoff", error=error
            ),
            tests=tuple(TestResult.from_dict(item) for item in tests_raw),
            lint=_expect_optional_str(payload, "lint", where="handoff", error=error),
            typecheck=_expect_optional_str(payload, "typecheck", where="handoff", error=error),
            ci=_expect_optional_str(payload, "ci", where="handoff", error=error),
            dependencies=_expect_str_tuple(payload, "dependencies", where="handoff", error=error),
            known_issues=_expect_str_tuple(
                payload, "known_issues", where="handoff", error=error, required=False
            ),
            deferred_items=_expect_str_tuple(
                payload, "deferred_items", where="handoff", error=error, required=False
            ),
            ready_for_integration=_expect_bool(
                payload, "ready_for_integration", where="handoff", error=error
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "agent": self.agent,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "commits": list(self.commits),
            "changed_files": list(self.changed_files),
            "ownership_compliance": self.ownership_compliance,
            "tests": [item.to_dict() for item in self.tests],
            "lint": self.lint,
            "typecheck": self.typecheck,
            "ci": self.ci,
            "dependencies": list(self.dependencies),
            "known_issues": list(self.known_issues),
            "deferred_items": list(self.deferred_items),
            "ready_for_integration": self.ready_for_integration,
        }

    def test_named(self, name: str) -> TestResult | None:
        """The reported test whose ``name`` matches, or ``None``."""
        for result in self.tests:
            if result.name == name:
                return result
        return None


def parse_task_plan(data: object) -> tuple[TaskContract, ...]:
    """Parse a plan document (``{"schema_version": 1, "tasks": [...]}``)."""
    payload = _expect_mapping(data, where="plan", error=TaskInvalidError)
    _expect_schema_version(payload, where="plan", error=TaskInvalidError)
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise TaskInvalidError(
            "plan.tasks must be a non-empty list of task contracts",
            details={"field": "plan.tasks"},
        )
    return tuple(TaskContract.from_dict(item) for item in tasks)


def load_task_plan(path: Path) -> tuple[TaskContract, ...]:
    """Read a plan document from disk. Parsing executes nothing."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise TaskInvalidError(
            f"plan file not found: {path}",
            details={"field": "plan", "path": str(path)},
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TaskInvalidError(
            f"plan file is not valid JSON: {path}",
            details={"field": "plan", "path": str(path), "error": str(exc)},
        ) from exc
    return parse_task_plan(data)


def load_task(path: Path) -> TaskContract:
    """Read a single task contract from disk."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise TaskInvalidError(
            f"task file not found: {path}",
            details={"field": "task", "path": str(path)},
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TaskInvalidError(
            f"task file is not valid JSON: {path}",
            details={"field": "task", "path": str(path), "error": str(exc)},
        ) from exc
    return TaskContract.from_dict(data)


def load_handoff(path: Path) -> HandoffContract:
    """Read a handoff from disk."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HandoffInvalidError(
            f"handoff file not found: {path}",
            details={"field": "handoff", "path": str(path)},
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HandoffInvalidError(
            f"handoff file is not valid JSON: {path}",
            details={"field": "handoff", "path": str(path), "error": str(exc)},
        ) from exc
    return HandoffContract.from_dict(data)
