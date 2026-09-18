"""Pairwise conflict classification and serialisation rules (AGENT-01 §21, §26, §57, §95).

Conflict levels:

```text
C0  no conflict                          auto-integratable
C1  textual, low-risk overlap            auto-integratable
C2  semantic / shared-contract overlap   stop - serial review required
C3  public-truth overlap                 stop - serial review required
C4  safety / migration / schema overlap  stop - serial review required
```

The classification is derived from the *declared ownership surface*, not from
whatever the two agents happened to touch, so it can be computed before either
branch exists. When in doubt it errs upward: a false C2 costs a review, a false
C0 costs a broken `main`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import Final

from tools.agent.config import AgentConfig
from tools.agent.contracts import RiskClass, TaskContract
from tools.agent.paths import patterns_overlap

#: Highest level the Main Agent may resolve without escalating.
AUTO_RESOLVABLE_CEILING: Final[int] = 1


class ConflictLevel(IntEnum):
    """Ordered conflict severity; higher always wins."""

    C0 = 0
    C1 = 1
    C2 = 2
    C3 = 3
    C4 = 4

    def __str__(self) -> str:
        return self.name


_LEVEL_MEANING: Final[dict[ConflictLevel, str]] = {
    ConflictLevel.C0: "no overlapping ownership",
    ConflictLevel.C1: "textual, low-risk overlap",
    ConflictLevel.C2: "semantic / shared-contract overlap",
    ConflictLevel.C3: "public-truth overlap",
    ConflictLevel.C4: "safety / migration / schema overlap",
}


@dataclass(frozen=True, kw_only=True)
class PairConflict:
    """The classified relationship between two tasks' ownership surfaces."""

    left: str
    right: str
    level: ConflictLevel
    overlapping_paths: tuple[str, ...]
    reason: str

    @property
    def auto_resolvable(self) -> bool:
        """True when the Main Agent may resolve this without serial review."""
        return int(self.level) <= AUTO_RESOLVABLE_CEILING

    def to_dict(self) -> dict[str, object]:
        return {
            "left": self.left,
            "right": self.right,
            "level": str(self.level),
            "auto_resolvable": self.auto_resolvable,
            "overlapping_paths": list(self.overlapping_paths),
            "reason": self.reason,
        }


def _shared_contract_paths(*tasks: TaskContract) -> tuple[str, ...]:
    return tuple(ref.path for task in tasks for ref in task.shared_contracts)


def classify_pattern(
    pattern: str,
    *,
    config: AgentConfig,
    shared_contracts: Iterable[str] = (),
) -> ConflictLevel:
    """Classify a single ownership pattern against the protected path classes."""
    if any(patterns_overlap(pattern, guard) for guard in config.safety_paths):
        return ConflictLevel.C4
    if any(patterns_overlap(pattern, guard) for guard in config.public_truth_paths):
        return ConflictLevel.C3
    if any(patterns_overlap(pattern, contract) for contract in shared_contracts):
        return ConflictLevel.C2
    return ConflictLevel.C1


def classify_pair(left: TaskContract, right: TaskContract, config: AgentConfig) -> PairConflict:
    """Classify the overlap between two tasks."""
    shared = _shared_contract_paths(left, right)
    overlaps: list[str] = []
    level = ConflictLevel.C0
    for pattern in left.allowed_paths:
        for other in right.allowed_paths:
            if not patterns_overlap(pattern, other):
                continue
            overlaps.append(f"{pattern} ~ {other}")
            level = max(
                level,
                classify_pattern(pattern, config=config, shared_contracts=shared),
                classify_pattern(other, config=config, shared_contracts=shared),
            )
    if level is ConflictLevel.C0:
        return PairConflict(
            left=left.task_id,
            right=right.task_id,
            level=ConflictLevel.C0,
            overlapping_paths=(),
            reason=_LEVEL_MEANING[ConflictLevel.C0],
        )
    return PairConflict(
        left=left.task_id,
        right=right.task_id,
        level=level,
        overlapping_paths=tuple(sorted(set(overlaps))),
        reason=f"{_LEVEL_MEANING[level]}: {', '.join(sorted(set(overlaps)))}",
    )


def classify_all(
    tasks: Sequence[TaskContract], config: AgentConfig
) -> tuple[PairConflict, ...]:
    """Every unordered task pair, classified."""
    conflicts: list[PairConflict] = []
    for index, left in enumerate(tasks):
        for right in tasks[index + 1 :]:
            conflicts.append(classify_pair(left, right, config))
    return tuple(conflicts)


def blocking_conflicts(
    tasks: Sequence[TaskContract], config: AgentConfig
) -> tuple[PairConflict, ...]:
    """Pairs that may not be auto-integrated."""
    return tuple(
        conflict for conflict in classify_all(tasks, config) if not conflict.auto_resolvable
    )


def requires_serial_review(task: TaskContract, config: AgentConfig) -> bool:
    """True when the task may never be integrated in parallel with another (§26).

    A ``SAFETY_CRITICAL`` development task, or one whose ownership surface
    touches a safety path, is serialised no matter how small its overlap.
    """
    if task.risk_class is RiskClass.SAFETY_CRITICAL:
        return True
    return any(
        patterns_overlap(pattern, guard)
        for pattern in task.allowed_paths
        for guard in config.safety_paths
    )


def parallel_safe_pairs(
    tasks: Sequence[TaskContract], config: AgentConfig
) -> tuple[tuple[str, str], ...]:
    """Task pairs that may be developed in parallel without a serial review."""
    safe: list[tuple[str, str]] = []
    for conflict in classify_all(tasks, config):
        if not conflict.auto_resolvable:
            continue
        left = next(task for task in tasks if task.task_id == conflict.left)
        right = next(task for task in tasks if task.task_id == conflict.right)
        if requires_serial_review(left, config) or requires_serial_review(right, config):
            continue
        safe.append((conflict.left, conflict.right))
    return tuple(safe)
