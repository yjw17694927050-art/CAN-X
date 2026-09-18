"""The shipped examples and JSON schemas must agree with the code.

An example that no longer parses, or a schema whose ``required`` list has drifted
from the parser, is a documentation defect that turns into a protocol defect the
first time another agent copies it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agent_tools_support import BASE_SHA, EXAMPLE_DIR, SCHEMA_DIR, config

from tools.agent.contracts import (
    HANDOFF_REQUIRED_FIELDS,
    TASK_REQUIRED_FIELDS,
    load_handoff,
    load_task,
    load_task_plan,
)
from tools.agent.errors import OwnershipViolationError
from tools.agent.orchestration import plan
from tools.agent.validation import validate_handoff, validate_task

TASK_SCHEMA = SCHEMA_DIR / "task.schema.json"
HANDOFF_SCHEMA = SCHEMA_DIR / "handoff.schema.json"


def _schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_shipped_example_parses() -> None:
    assert load_task(EXAMPLE_DIR / "task.example.json").task_id == "AGENT-02-B"
    assert load_handoff(EXAMPLE_DIR / "handoff.example.json").task_id == "AGENT-02-B"
    assert len(load_task_plan(EXAMPLE_DIR / "tasks.dependency.example.json")) == 4


def test_the_valid_examples_pass_validation() -> None:
    task = load_task(EXAMPLE_DIR / "task.example.json")
    validate_task(task, config())
    validate_handoff(load_handoff(EXAMPLE_DIR / "handoff.example.json"), task, config())


def test_the_ownership_violation_example_is_a_real_violation() -> None:
    task = load_task(EXAMPLE_DIR / "task.example.json")
    handoff = load_handoff(EXAMPLE_DIR / "handoff.ownership-violation.example.json")
    assert handoff.ownership_compliance is True, "the example must claim compliance"
    with pytest.raises(OwnershipViolationError):
        validate_handoff(handoff, task, config())


@pytest.mark.parametrize(
    ("schema_path", "required"),
    [(TASK_SCHEMA, TASK_REQUIRED_FIELDS), (HANDOFF_SCHEMA, HANDOFF_REQUIRED_FIELDS)],
)
def test_the_schema_required_list_matches_the_parser(
    schema_path: Path, required: tuple[str, ...]
) -> None:
    schema = _schema(schema_path)
    assert sorted(schema["required"]) == sorted(required)


def test_the_schemas_declare_their_identity() -> None:
    for path in (TASK_SCHEMA, HANDOFF_SCHEMA):
        schema = _schema(path)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["type"] == "object"
        assert schema["title"].startswith("CAN-X")
        assert "yjw17694927050-art/CAN-X" in schema["$id"]


def test_the_task_schema_properties_match_the_example_exactly() -> None:
    schema = _schema(TASK_SCHEMA)
    example = json.loads((EXAMPLE_DIR / "task.example.json").read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert sorted(schema["properties"]) == sorted(example)


def test_the_handoff_schema_properties_cover_the_example_without_extras() -> None:
    schema = _schema(HANDOFF_SCHEMA)
    example = json.loads((EXAMPLE_DIR / "handoff.example.json").read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert set(example) <= set(schema["properties"])
    optional = set(schema["properties"]) - set(schema["required"])
    assert optional == {"lint", "typecheck", "ci", "known_issues", "deferred_items"}


def test_the_task_schema_pins_the_shipped_schema_version() -> None:
    schema = _schema(TASK_SCHEMA)
    assert schema["properties"]["schema_version"]["const"] == 1
    assert _schema(HANDOFF_SCHEMA)["properties"]["schema_version"]["const"] == 1


def test_the_dependency_example_has_the_simulated_shape() -> None:
    tasks = load_task_plan(EXAMPLE_DIR / "tasks.dependency.example.json")
    by_id = {task.task_id: task for task in tasks}
    assert by_id["AGENT-02-A"].dependencies == ()
    assert by_id["AGENT-02-B"].dependencies == ()
    assert by_id["AGENT-02-C"].dependencies == ("AGENT-02-A",)
    assert by_id["AGENT-02-D"].dependencies == ()
    for task in tasks:
        assert task.base_sha == BASE_SHA


def test_the_dependency_example_plans_into_the_advertised_shape() -> None:
    tasks = load_task_plan(EXAMPLE_DIR / "tasks.dependency.example.json")
    result = plan(tasks, config())
    assert set(result.runnable_ids()) == {"AGENT-02-A", "AGENT-02-B"}
    assert result.blocked_ids() == ("AGENT-02-C",)
    assert result.deferred_ids() == ("AGENT-02-D",)


def test_the_config_caps_parallelism_at_four() -> None:
    assert config().max_sub_agents == 4
