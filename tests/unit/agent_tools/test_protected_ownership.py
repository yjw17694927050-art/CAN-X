"""P0-1: a protected path cannot be reached through a broader ownership surface.

The distinction this file freezes:

```text
path    vs pattern     matches_pattern / matching_pattern
pattern vs pattern     patterns_overlap / overlapping_pattern
```

Using the first for the second is how ``**`` came to look like it never touched
``SPEC.md``: ``SPEC.md`` does not match the literal text ``**``.
"""

from __future__ import annotations

import pytest
from agent_tools_support import config, make_task

from tools.agent.errors import AgentToolingError, ProtectedPathConflictError
from tools.agent.paths import matching_pattern, overlapping_pattern, patterns_overlap
from tools.agent.validation import validate_task


def test_matvhing_and_overlap_answer_different_questions() -> None:
    # Does the file SPEC.md fall under the ownership pattern "**"? Yes.
    assert matching_pattern("SPEC.md", ("**",)) == "**"
    # Does the *pattern* "**" cover the literal name "SPEC.md"? Not by matching -
    # only by overlap. This is the trap the old gate fell into.
    assert matching_pattern("**", ("SPEC.md",)) is None
    assert patterns_overlap("**", "SPEC.md")
    assert overlapping_pattern("**", ("SPEC.md",)) == "SPEC.md"


@pytest.mark.parametrize(
    "allowed",
    [
        ("**",),
        ("**/*.md",),
        ("docs/**",),
        ("**/AGENTS.md",),
        (".github/**",),
        ("runtime/canx/safety/**",),
        ("runtime/canx/safety/policy.py",),
    ],
)
def test_a_sub_agent_may_not_own_a_surface_that_can_reach_a_protected_path(
    allowed: tuple[str, ...],
) -> None:
    with pytest.raises(ProtectedPathConflictError) as raised:
        validate_task(make_task(owner="sub-a", allowed_paths=allowed), config())
    assert raised.value.code == "agent.protected_path_conflict"
    assert raised.value.details["protected"] in config().protected_paths


def test_the_whole_repository_is_not_ownable_by_a_sub_agent() -> None:
    with pytest.raises(ProtectedPathConflictError) as raised:
        validate_task(make_task(owner="sub-a", allowed_paths=("**",)), config())
    assert raised.value.details["path"] == "**"


@pytest.mark.parametrize(
    "allowed",
    [
        ("runtime/canx/alpha/**",),
        ("runtime/canx/alpha/**", "tests/unit/alpha/**"),
        ("docs/engineering/MULTI_AGENT_PROTOCOL.md",),
        ("pyproject.toml.notes",),
    ],
)
def test_a_genuinely_disjoint_surface_is_still_accepted(allowed: tuple[str, ...]) -> None:
    """The fix is correct overlap semantics - not banning globs."""
    validate_task(make_task(owner="sub-a", allowed_paths=allowed), config())


def test_the_main_agent_may_still_own_a_protected_path() -> None:
    validate_task(
        make_task(
            task_id="AGENT-02-M",
            owner="main",
            branch="maintenance/agent-02-protocol",
            allowed_paths=("**",),
        ),
        config(),
    )


def test_the_conflict_names_the_protected_pattern_it_hit() -> None:
    with pytest.raises(ProtectedPathConflictError) as raised:
        validate_task(make_task(owner="sub-b", allowed_paths=("docs/**",)), config())
    assert raised.value.details["protected"] == "docs/PROJECT_STATE.md"


def test_an_invalid_ownership_shape_is_still_a_path_error_not_a_weaker_rule() -> None:
    """Path validation was not relaxed to make the overlap rule pass."""
    with pytest.raises(AgentToolingError) as raised:
        validate_task(
            make_task(owner="sub-a", allowed_paths=("runtime/canx/foo/",)),
            config(),
        )
    assert raised.value.code == "agent.path_invalid"
