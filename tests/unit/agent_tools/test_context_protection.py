"""AGENT-CONTEXT-PROTECTION: the context governance truth surface is protected.

Two independent invariants over one surface:

```text
protected_paths     Main-Agent-only. A Sub-Agent whose allowed_paths can *reach*
                    the router or the layer definitions is refused with
                    agent.protected_path_conflict.
public_truth_paths  shared governance truth. Ownership overlapping it classifies
                    at least C3 and may not be auto-integrated.
```

Both are needed and neither implies the other: `docs/engineering/MULTI_AGENT_PROTOCOL.md`
is public truth but not protected, while `pyproject.toml` is protected but not
public truth. Protection stops a Sub-Agent *declaring* the surface; the C3 rule
stops two concurrent owners from racing it through integration.

Every test reads the repository's real `.agent/config.json` through
``agent_tools_support.config`` - there is no fixture copy to drift from it.
"""

from __future__ import annotations

import pytest
from agent_tools_support import config, make_task

from tools.agent.conflicts import ConflictLevel, classify_pair, classify_pattern
from tools.agent.errors import ProtectedPathConflictError
from tools.agent.validation import validate_task

CONTEXT_ROUTER = "docs/CONTEXT_INDEX.md"
CONTEXT_GOVERNANCE = "docs/engineering/AGENT_CONTEXT_GOVERNANCE.md"
PROTECTED = (CONTEXT_ROUTER, CONTEXT_GOVERNANCE)


# --------------------------------------------------------------- the config itself


@pytest.mark.parametrize("path", PROTECTED)
def test_the_shipped_config_names_the_context_truth_surface_in_both_classes(
    path: str,
) -> None:
    """The gap this closes: both files were in neither protected nor public truth."""
    loaded = config()
    assert path in loaded.protected_paths
    assert path in loaded.public_truth_paths


def test_the_protection_surface_is_exact_not_the_whole_docs_tree() -> None:
    """Two files were the gap; the fix is two files, not `docs/**`."""
    loaded = config()
    for surface in (loaded.protected_paths, loaded.public_truth_paths):
        assert "docs/**" not in surface
        assert "docs/engineering/**" not in surface
        assert "docs/*.md" not in surface


# ---------------------------------------------- A1/A2: an exact Sub-Agent claim


@pytest.mark.parametrize("path", PROTECTED)
def test_a_sub_agent_cannot_own_a_context_truth_file(path: str) -> None:
    with pytest.raises(ProtectedPathConflictError) as raised:
        validate_task(make_task(owner="sub-a", allowed_paths=(path,)), config())
    assert raised.value.code == "agent.protected_path_conflict"
    assert raised.value.details["protected"] == path
    assert raised.value.details["owner"] == "sub-a"


# ------------------------------------------- A3/A4: a broader glob that reaches it


@pytest.mark.parametrize(
    ("allowed", "expected_guard"),
    [
        (("docs/CONTEXT_*.md",), CONTEXT_ROUTER),
        (("docs/engineering/*.md",), CONTEXT_GOVERNANCE),
    ],
)
def test_a_broader_glob_that_can_reach_a_context_truth_file_is_refused(
    allowed: tuple[str, ...], expected_guard: str
) -> None:
    """Pattern-versus-pattern: the surface must not be able to *reach* the file."""
    with pytest.raises(ProtectedPathConflictError) as raised:
        validate_task(make_task(owner="sub-b", allowed_paths=allowed), config())
    assert raised.value.details["protected"] == expected_guard


# ------------------------------------------------- A5/A6: the Main Agent may own it


@pytest.mark.parametrize("path", PROTECTED)
def test_the_main_agent_may_own_a_context_truth_file(path: str) -> None:
    validate_task(
        make_task(
            owner="main",
            branch="maintenance/agent-02-preparation",
            allowed_paths=(path,),
        ),
        config(),
    )


# ------------------------------------------------------- A7/A8: C3 classification


@pytest.mark.parametrize("path", PROTECTED)
def test_a_context_truth_pattern_classifies_at_least_c3(path: str) -> None:
    assert classify_pattern(path, config=config()) is ConflictLevel.C3


def test_the_pre_existing_public_truth_surface_still_classifies_c3() -> None:
    """Control: the C3 answer above is a real classification, not a constant."""
    level = classify_pattern("docs/engineering/MULTI_AGENT_PROTOCOL.md", config=config())
    assert level is ConflictLevel.C3


# -------------------------------------------- A9: unrelated docs are not promoted


@pytest.mark.parametrize(
    "path",
    [
        "docs/engineering/RELIABILITY_CAPTURE_FINALIZATION.md",
        "docs/engineering/CI_TIERED_QUALITY_GATE.md",
        "docs/project-state/README.md",
        "docs/CONTEXT_ROUTING_NOTES.md",
    ],
)
def test_an_unrelated_docs_file_is_not_promoted_to_c3(path: str) -> None:
    """Exact paths, not prefixes: a lookalike name is ordinary shared surface."""
    assert classify_pattern(path, config=config()) is ConflictLevel.C1


# --------------------------------------------- pair level: never auto-resolvable


def test_two_owners_of_the_context_truth_surface_are_not_auto_resolvable() -> None:
    """A concurrent overlap on the governance truth must stop for serial review."""
    router = make_task(
        task_id="AGENT-02-M1",
        owner="main",
        branch="maintenance/agent-02-router",
        allowed_paths=(CONTEXT_ROUTER,),
    )
    governance = make_task(
        task_id="AGENT-02-M2",
        owner="main",
        branch="maintenance/agent-02-governance",
        # Deliberately *not* `docs/**`: that surface also reaches
        # `docs/architecture/SAFETY_ARCHITECTURE.md` and is already C4, which
        # would mask whether the context truth itself reaches C3.
        allowed_paths=("docs/CONTEXT_*.md",),
    )
    conflict = classify_pair(router, governance, config())
    assert conflict.level is ConflictLevel.C3
    assert not conflict.auto_resolvable
