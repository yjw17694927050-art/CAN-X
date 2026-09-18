"""``.agent/config.json`` - the repository-side knobs of the AGENT-01 protocol.

The config is data, never executable: it names a parallelism cap, the protected
path classes and the branch convention. It holds **no** credential of any kind -
authentication stays with the external GitHub environment (AGENT-01 §103, §104).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from tools.agent.errors import PathInvalidError, TaskInvalidError
from tools.agent.paths import matching_pattern, normalize_repo_pattern, overlapping_pattern

DEFAULT_CONFIG_RELPATH: Final[str] = ".agent/config.json"
SUPPORTED_SCHEMA_VERSION: Final[int] = 1
MAX_SUB_AGENT_CEILING: Final[int] = 4


def _require_str(data: dict[str, Any], key: str, *, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TaskInvalidError(
            f"{where}.{key} must be a non-empty string",
            details={"field": f"{where}.{key}"},
        )
    return value


def _require_str_tuple(data: dict[str, Any], key: str, *, where: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TaskInvalidError(
            f"{where}.{key} must be a list of strings",
            details={"field": f"{where}.{key}"},
        )
    return tuple(value)


def _require_pattern_tuple(data: dict[str, Any], key: str, *, where: str) -> tuple[str, ...]:
    raw = _require_str_tuple(data, key, where=where)
    normalized: list[str] = []
    for entry in raw:
        try:
            normalized.append(normalize_repo_pattern(entry, field=f"{where}.{key}"))
        except PathInvalidError as exc:
            raise TaskInvalidError(exc.message, details=exc.details) from exc
    return tuple(normalized)


@dataclass(frozen=True, kw_only=True)
class AgentConfig:
    """Resolved, validated tooling configuration."""

    max_sub_agents: int
    default_branch: str
    repository: str
    branch_prefix: str
    worktrees_dir: str
    main_agent_ids: frozenset[str]
    protected_paths: tuple[str, ...]
    public_truth_paths: tuple[str, ...]
    safety_paths: tuple[str, ...]
    schema_version: int = SUPPORTED_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: Any) -> AgentConfig:
        """Parse and validate a config document."""
        if not isinstance(data, dict):
            raise TaskInvalidError("config must be a JSON object", details={"where": "config"})
        schema_version = data.get("schema_version")
        if schema_version != SUPPORTED_SCHEMA_VERSION:
            raise TaskInvalidError(
                f"config.schema_version must be {SUPPORTED_SCHEMA_VERSION}",
                details={"field": "config.schema_version", "value": repr(schema_version)},
            )
        max_sub_agents = data.get("max_sub_agents")
        if (
            not isinstance(max_sub_agents, int)
            or isinstance(max_sub_agents, bool)
            or not 1 <= max_sub_agents <= MAX_SUB_AGENT_CEILING
        ):
            raise TaskInvalidError(
                f"config.max_sub_agents must be an integer in 1..{MAX_SUB_AGENT_CEILING}",
                details={"field": "config.max_sub_agents", "value": repr(max_sub_agents)},
            )
        main_agent_ids = _require_str_tuple(data, "main_agent_ids", where="config")
        if not main_agent_ids:
            raise TaskInvalidError(
                "config.main_agent_ids must name at least one agent",
                details={"field": "config.main_agent_ids"},
            )
        return cls(
            max_sub_agents=max_sub_agents,
            default_branch=_require_str(data, "default_branch", where="config"),
            repository=_require_str(data, "repository", where="config"),
            branch_prefix=_require_str(data, "branch_prefix", where="config"),
            worktrees_dir=normalize_repo_pattern(
                _require_str(data, "worktrees_dir", where="config"),
                field="config.worktrees_dir",
            ),
            main_agent_ids=frozenset(main_agent_ids),
            protected_paths=_require_pattern_tuple(data, "protected_paths", where="config"),
            public_truth_paths=_require_pattern_tuple(data, "public_truth_paths", where="config"),
            safety_paths=_require_pattern_tuple(data, "safety_paths", where="config"),
        )

    def is_main_agent(self, owner: str) -> bool:
        """True when ``owner`` is allowed to own a protected path."""
        return owner in self.main_agent_ids

    def protected_match(self, pattern: str) -> str | None:
        """The protected pattern ``pattern`` collides with, if any."""
        return matching_pattern(pattern, self.protected_paths)

    def public_truth_match(self, pattern: str) -> str | None:
        """The public-truth pattern ``pattern`` collides with, if any."""
        return matching_pattern(pattern, self.public_truth_paths)

    def safety_match(self, pattern: str) -> str | None:
        """The safety pattern ``pattern`` collides with, if any."""
        return matching_pattern(pattern, self.safety_paths)

    # -- pattern versus pattern ------------------------------------------------
    # An ownership surface may be a glob, so "does it reach a protected path?"
    # is an overlap question, not a match question. `**` matches no literal
    # protected file name, yet it can reach every one of them.

    def protected_overlap(self, pattern: str) -> str | None:
        """The protected pattern whose surface ``pattern`` can reach, if any."""
        return overlapping_pattern(pattern, self.protected_paths)

    def public_truth_overlap(self, pattern: str) -> str | None:
        """The public-truth pattern whose surface ``pattern`` can reach."""
        return overlapping_pattern(pattern, self.public_truth_paths)

    def safety_overlap(self, pattern: str) -> str | None:
        """The safety pattern whose surface ``pattern`` can reach."""
        return overlapping_pattern(pattern, self.safety_paths)


def load_config(path: Path) -> AgentConfig:
    """Read and validate the config at ``path``."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise TaskInvalidError(
            f"config file not found: {path}",
            details={"field": "config", "path": str(path)},
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TaskInvalidError(
            f"config file is not valid JSON: {path}",
            details={"field": "config", "path": str(path), "error": str(exc)},
        ) from exc
    return AgentConfig.from_dict(data)
