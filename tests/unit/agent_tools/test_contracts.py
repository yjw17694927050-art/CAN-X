"""Task and handoff contracts: parsing, required fields and round-tripping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agent_tools_support import EXAMPLE_DIR, make_handoff, make_task

from tools.agent.contracts import (
    HANDOFF_REQUIRED_FIELDS,
    TASK_REQUIRED_FIELDS,
    HandoffContract,
    TaskContract,
    load_handoff,
    load_task,
    load_task_plan,
    parse_task_plan,
)
from tools.agent.errors import HandoffInvalidError, TaskInvalidError


def _raw(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLE_DIR / name).read_text(encoding="utf-8"))


def test_the_example_task_round_trips_through_to_dict() -> None:
    task = load_task(EXAMPLE_DIR / "task.example.json")
    assert TaskContract.from_dict(task.to_dict()) == task


def test_the_example_handoff_round_trips_through_to_dict() -> None:
    handoff = load_handoff(EXAMPLE_DIR / "handoff.example.json")
    assert HandoffContract.from_dict(handoff.to_dict()) == handoff


@pytest.mark.parametrize("field", TASK_REQUIRED_FIELDS)
def test_a_task_missing_any_required_field_is_refused(field: str) -> None:
    payload = _raw("task.example.json")
    del payload[field]
    with pytest.raises(TaskInvalidError) as raised:
        TaskContract.from_dict(payload)
    assert raised.value.code == "agent.task_invalid"
    assert field in str(raised.value.details.get("field", ""))


@pytest.mark.parametrize("field", HANDOFF_REQUIRED_FIELDS)
def test_a_handoff_missing_any_required_field_is_refused(field: str) -> None:
    payload = _raw("handoff.example.json")
    del payload[field]
    with pytest.raises(HandoffInvalidError) as raised:
        HandoffContract.from_dict(payload)
    assert raised.value.code == "agent.handoff_invalid"
    assert field in str(raised.value.details.get("field", ""))


def test_a_handoff_reporting_an_unknown_test_result_is_refused() -> None:
    payload = _raw("handoff.example.json")
    payload["tests"][0]["result"] = "probably"
    with pytest.raises(HandoffInvalidError):
        HandoffContract.from_dict(payload)


def test_a_handoff_may_omit_its_optional_context_fields() -> None:
    payload = _raw("handoff.example.json")
    for optional in ("lint", "typecheck", "ci", "known_issues", "deferred_items"):
        payload.pop(optional, None)
    handoff = HandoffContract.from_dict(payload)
    assert handoff.lint is None
    assert handoff.ci is None
    assert handoff.known_issues == ()


def test_an_unknown_field_is_data_not_a_side_effect() -> None:
    payload = _raw("task.example.json")
    payload["shell"] = "rm -rf /"
    task = TaskContract.from_dict(payload)
    assert not hasattr(task, "shell")
    assert "shell" not in task.to_dict()


def test_the_plan_loader_reads_every_task_in_order() -> None:
    tasks = load_task_plan(EXAMPLE_DIR / "tasks.dependency.example.json")
    assert [task.task_id for task in tasks] == [
        "AGENT-02-A",
        "AGENT-02-B",
        "AGENT-02-C",
        "AGENT-02-D",
    ]


def test_a_plan_without_tasks_is_refused() -> None:
    with pytest.raises(TaskInvalidError):
        parse_task_plan({"schema_version": 1, "tasks": []})


def test_a_plan_with_the_wrong_schema_version_is_refused() -> None:
    with pytest.raises(TaskInvalidError):
        parse_task_plan({"schema_version": 2, "tasks": [{}]})


def test_a_missing_file_reports_its_path_not_a_traceback() -> None:
    with pytest.raises(TaskInvalidError) as raised:
        load_task(Path("does/not/exist.json"))
    assert raised.value.code == "agent.task_invalid"
    assert str(raised.value.details["path"]).endswith("exist.json")


def test_frozen_snapshot_names_only_the_immutable_fields() -> None:
    task = make_task()
    assert set(task.frozen_snapshot()) == {"objective", "allowed_paths", "shared_contracts"}


def test_the_required_field_constants_are_not_empty() -> None:
    assert len(TASK_REQUIRED_FIELDS) == len(set(TASK_REQUIRED_FIELDS))
    assert len(HANDOFF_REQUIRED_FIELDS) == len(set(HANDOFF_REQUIRED_FIELDS))
    assert TASK_REQUIRED_FIELDS != HANDOFF_REQUIRED_FIELDS


def test_a_handoff_test_is_looked_up_by_name() -> None:
    handoff = make_handoff()
    assert handoff.test_named("unit") is not None
    assert handoff.test_named("integration") is None
