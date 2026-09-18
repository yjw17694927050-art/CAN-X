"""Repository-relative path contract and ownership matching.

Ownership is a machine-readable predicate, not a request: *anything not
explicitly owned is not editable* (AGENT-01 §7). Every path and pattern that
crosses the contract boundary is normalised here first, so a ``..``, an absolute
path or a Windows separator can never be smuggled in as an "owned" file
(AGENT-01 §8, §48).

The glob dialect is deliberately small and cannot be turned into a shell
expansion: ``*`` matches within one path segment, ``**`` matches across
segments, ``?`` matches one character. Character classes are **not** supported
and are treated literally, so no pattern can smuggle in a regex.
"""

from __future__ import annotations

import posixpath
import re
from functools import lru_cache
from typing import Final

from tools.agent.errors import PathInvalidError

_DRIVE_PREFIX: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]:")
_GLOB_CHARS: Final[frozenset[str]] = frozenset("*?")


def is_glob(pattern: str) -> bool:
    """True when ``pattern`` contains a wildcard."""
    return any(char in pattern for char in _GLOB_CHARS)


def _reject_common(raw: object, *, field: str) -> str:
    if not isinstance(raw, str):
        raise PathInvalidError(
            f"{field} must be a string",
            details={"field": field, "value": repr(raw)},
        )
    candidate = raw.strip()
    if not candidate:
        raise PathInvalidError(f"{field} must not be empty", details={"field": field})
    if "\x00" in candidate:
        raise PathInvalidError(
            f"{field} must not contain a NUL byte",
            details={"field": field, "value": raw},
        )
    if "\\" in candidate:
        raise PathInvalidError(
            f"{field} must use '/' separators, not a backslash",
            details={"field": field, "value": raw},
        )
    if candidate.startswith("/") or _DRIVE_PREFIX.match(candidate):
        raise PathInvalidError(
            f"{field} must be repository-relative, not absolute",
            details={"field": field, "value": raw},
        )
    return candidate


def normalize_repo_path(raw: object, *, field: str = "path") -> str:
    """Return the canonical repository-relative POSIX form of ``raw``.

    Rejects every shape that could escape the repository or alias another path:
    absolute paths, Windows drive prefixes, backslash separators, ``..``
    segments, the bare ``.`` and empty strings.
    """
    candidate = _reject_common(raw, field=field)
    normalized = posixpath.normpath(candidate)
    if normalized in {".", ".."} or normalized.startswith("../"):
        raise PathInvalidError(
            f"{field} must not escape the repository root",
            details={"field": field, "value": raw},
        )
    return normalized


def normalize_repo_pattern(raw: object, *, field: str = "pattern") -> str:
    """Return the canonical form of an ownership pattern (globs preserved).

    Patterns carry wildcards, so they are not passed through ``normpath``; the
    segment-level guards (``..``, ``.``, empty segments, no trailing slash) are
    applied instead.
    """
    candidate = _reject_common(raw, field=field)
    if candidate.endswith("/"):
        raise PathInvalidError(
            f"{field} must not end with '/'; use '/**' to own a subtree",
            details={"field": field, "value": raw},
        )
    segments = candidate.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise PathInvalidError(
            f"{field} must not contain empty, '.' or '..' segments",
            details={"field": field, "value": raw},
        )
    return candidate


@lru_cache(maxsize=512)
def compile_glob(pattern: str) -> re.Pattern[str]:
    """Compile an ownership glob into an anchored regular expression."""
    parts: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*" and index + 1 < length and pattern[index + 1] == "*":
            if index + 2 < length and pattern[index + 2] == "/":
                parts.append("(?:[^/]+/)*")
                index += 3
            else:
                parts.append(".*")
                index += 2
        elif char == "*":
            parts.append("[^/]*")
            index += 1
        elif char == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(char))
            index += 1
    return re.compile("^" + "".join(parts) + "$")


def matches_pattern(path: str, pattern: str) -> bool:
    """True when the repository-relative ``path`` is covered by ``pattern``."""
    return compile_glob(pattern).match(path) is not None


def matching_pattern(path: str, patterns: tuple[str, ...]) -> str | None:
    """The first pattern covering ``path``, or ``None``."""
    for pattern in patterns:
        if matches_pattern(path, pattern):
            return pattern
    return None


def _literal_prefix(pattern: str) -> str:
    index = min(
        (position for position in (pattern.find("*"), pattern.find("?")) if position != -1),
        default=-1,
    )
    return pattern if index == -1 else pattern[:index]


def patterns_overlap(left: str, right: str) -> bool:
    """A conservative, deterministic overlap test between two ownership patterns.

    Two literal paths overlap only when equal. A literal and a glob overlap when
    the glob covers the literal. Two globs overlap when one literal prefix is a
    prefix of the other - deliberately conservative, because a false "no
    overlap" would let two tasks race on the same file while a false "overlap"
    only forces a review that the protocol asks for anyway (fail closed).
    """
    left_glob = is_glob(left)
    right_glob = is_glob(right)
    if not left_glob and not right_glob:
        return left == right
    if not left_glob:
        return matches_pattern(left, right)
    if not right_glob:
        return matches_pattern(right, left)
    left_prefix = _literal_prefix(left)
    right_prefix = _literal_prefix(right)
    return left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)


def is_within(path: str, directory: str) -> bool:
    """True when ``path`` is ``directory`` itself or lives underneath it."""
    parent = directory.rstrip("/")
    return path == parent or path.startswith(parent + "/")
