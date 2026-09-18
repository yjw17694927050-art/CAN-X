"""Shared fixtures for the CI-03 control-plane tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> str:
    """The repository root, used by the event-resolution tests.

    Those tests exercise the *fail-closed* path only: they hand the resolver an
    event whose diff cannot be established, and assert that the classifier
    escalates to FULL CI rather than silently skipping every domain job. No test
    in this package mutates the real repository.
    """

    return str(Path(__file__).resolve().parents[3])
