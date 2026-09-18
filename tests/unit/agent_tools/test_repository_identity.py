"""P2: repository identity is canonicalised, then compared exactly.

``expected_remote in origin_url`` accepted ``github.com/evil/owner-repo-copy.git``
as ``owner/repo``. This file covers the pure half - the canonicaliser; the
git-dependent half lives in
``tests/integration/test_agent_repository_identity.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_tools_support import config

from tools.agent.gitcmd import canonical_repository

CANONICAL = "yjw17694927050-art/CAN-X"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/yjw17694927050-art/CAN-X.git",
        "https://github.com/yjw17694927050-art/CAN-X",
        "https://github.com/yjw17694927050-art/CAN-X/",
        "git@github.com:yjw17694927050-art/CAN-X.git",
        "ssh://git@github.com/yjw17694927050-art/CAN-X.git",
        "http://github.com/yjw17694927050-art/CAN-X.git",
        "  https://github.com/yjw17694927050-art/CAN-X.git  ",
    ],
)
def test_the_supported_github_origin_forms_canonicalise(url: str) -> None:
    assert canonical_repository(url) == CANONICAL


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/evil/yjw17694927050-art/CAN-X-copy.git",
        "https://example.com/yjw17694927050-art/CAN-X.git",
        "https://github.com/yjw17694927050-art/CAN-X.git/extra",
        "https://gitlab.com/yjw17694927050-art/CAN-X.git",
        "https://github.com/yjw17694927050-art",
        "not-a-url",
        "",
        "https://github.com//CAN-X.git",
    ],
)
def test_an_unrecognised_or_malformed_origin_does_not_canonicalise(url: str) -> None:
    """Unknown host or malformed shape: no identity, and therefore fail closed."""
    assert canonical_repository(url) is None


@pytest.mark.parametrize(
    ("url", "identity"),
    [
        # A well-formed URL for a *different* repository is a real identity - it
        # must canonicalise and then fail the equality check, not be mistaken for
        # a malformed URL.
        ("git@github.com:yjw17694927050-art/CAN-X-evil.git", "yjw17694927050-art/CAN-X-evil"),
        ("https://github.com/owner/repo/", "owner/repo"),
        ("https://github.com/yjw17694927050-art/CAN-X-copy.git", "yjw17694927050-art/CAN-X-copy"),
    ],
)
def test_a_well_formed_lookalike_canonicalises_to_a_different_identity(
    url: str, identity: str
) -> None:
    result = canonical_repository(url)
    assert result == identity
    assert result != CANONICAL


def test_the_config_stores_an_already_canonical_identity() -> None:
    assert config().repository == CANONICAL
    # validate_repository falls back to the raw value when it is already in the
    # `owner/repo` shape, so the canonicaliser returning None here is expected.
    assert canonical_repository(CANONICAL) is None


def test_the_identity_check_reads_a_url_and_stores_nothing() -> None:
    """The origin comparison never persists or echoes a credential."""
    from tools.agent import gitcmd

    source = Path(gitcmd.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("token", "password", "secret"):
        assert forbidden not in source
