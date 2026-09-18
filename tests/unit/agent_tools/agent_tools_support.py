"""Shared helpers for the AGENT-01 tooling tests.

Not a test module: pytest collects ``test_*.py`` only, and this file is imported
by the tests in this directory through pytest's rootdir insertion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.agent.config import AgentConfig, load_config
from tools.agent.contracts import (
    ContractRef,
    HandoffContract,
    RiskClass,
    TaskContract,
    TestResult,
)
from tools.agent.lifecycle import TaskStatus

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO_ROOT / ".agent"
EXAMPLE_DIR = AGENT_DIR / "examples"
SCHEMA_DIR = AGENT_DIR / "schemas"

#: The real integration head the examples were authored against.
BASE_SHA = "81f9c4131513c19d859fceac704d9b74449041f2"
HEAD_SHA = "5f3a1c9e4b2d7a6f8e0c1b2a3d4e5f60718293a4"
OTHER_SHA = "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567"


def config() -> AgentConfig:
    """The repository's own ``.agent/config.json``, parsed."""
    return load_config(AGENT_DIR / "config.json")


def task_kwargs(**overrides: Any) -> dict[str, Any]:
    """A minimal valid task, with per-test overrides."""
    base: dict[str, Any] = {
        "task_id": "AGENT-02-B",
        "title": "Re-derive ownership compliance",
        "objective": "validate_handoff refuses an out-of-ownership change.",
        "scope": "tools/agent/validation.py",
        "non_goals": (),
        "base_sha": BASE_SHA,
        "branch": "agent/AGENT-02-B-handoff-validator",
        "worktree": ".worktrees/agent-02-b",
        "owner": "sub-b",
        "allowed_paths": ("tools/agent/validation.py",),
        "forbidden_paths": (),
        "dependencies": (),
        "shared_contracts": (),
        "acceptance_criteria": ("The ownership gate refuses the violation.",),
        "required_tests": ("unit",),
        "handoff_requirements": ("Report the focused pytest run.",),
        "risk_class": RiskClass.MEDIUM,
        "status": TaskStatus.READY,
        "revision": 1,
    }
    base.update(overrides)
    return base


def make_task(**overrides: Any) -> TaskContract:
    """A minimal valid ``TaskContract``, with per-test overrides."""
    return TaskContract(**task_kwargs(**overrides))


def handoff_kwargs(**overrides: Any) -> dict[str, Any]:
    """A minimal valid handoff, with per-test overrides."""
    base: dict[str, Any] = {
        "task_id": "AGENT-02-B",
        "agent": "sub-b",
        "branch": "agent/AGENT-02-B-handoff-validator",
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "commits": ("5f3a1c9 fix(agent): re-derive ownership compliance",),
        "changed_files": ("tools/agent/validation.py",),
        "ownership_compliance": True,
        "tests": (TestResult(name="unit", command="python -m pytest -q", result="passed"),),
        "dependencies": (),
        "ready_for_integration": True,
    }
    base.update(overrides)
    return base


def make_handoff(**overrides: Any) -> HandoffContract:
    """A minimal valid ``HandoffContract``, with per-test overrides."""
    return HandoffContract(**handoff_kwargs(**overrides))


def contract_ref(path: str, sha: str | None = None) -> ContractRef:
    return ContractRef(path=path, sha=sha)
