"""The decoder cache: bounded, content-keyed, project-scoped and fail-closed.

The cache is the one piece of state this increment adds to the DBC stack, so its
own contract is asserted directly rather than inferred from timing: that a hit
returns exactly what a miss built, that the bound really bounds, that every field
of the key is part of the identity, and that the integrity proof still runs on
every call — a hit skips parsing, never verifying.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from canx.dbc.decode import DbcDecoder
from canx.dbc.decoder_cache import DecoderCache, DecoderKey
from canx.dbc.errors import DbcAssetIntegrityError
from canx.dbc.model import DbcDatabase
from canx.dbc.project_service import ProjectDbcService
from canx.project.service import ProjectService

BASIC_DBC = (
    'VERSION "1.0"\n'
    "\n"
    "BU_: N1 N2\n"
    "\n"
    "BO_ 256 Demo: 8 N1\n"
    ' SG_ Speed : 0|16@1+ (0.5,-10) [-10|1000] "km/h" N2\n'
)


def decoder() -> DbcDecoder:
    """A distinct, minimal compiled decoder, for identity comparisons."""
    return DbcDecoder(DbcDatabase(messages=(), nodes=(), version=None))


def key(**overrides: object) -> DecoderKey:
    """A key that is valid by default, with the named fields replaced."""
    fields: dict[str, object] = {
        "project_id": "project-1",
        "sha256": "a" * 64,
        "size_bytes": 128,
        "encoding": "utf-8",
    }
    fields.update(overrides)
    return DecoderKey(
        project_id=str(fields["project_id"]),
        sha256=str(fields["sha256"]),
        size_bytes=int(str(fields["size_bytes"])),
        encoding=str(fields["encoding"]),
    )


def build_project(tmp_path: Path, text: str, name: str = "vehicle") -> tuple[Path, str]:
    """Create a real project, import ``text`` as one asset, and close it."""
    root = tmp_path / f"{name}.canx"
    with ProjectService().create(root, display_name=name) as handle:
        inbox = tmp_path / f"inbox-{name}"
        inbox.mkdir(parents=True, exist_ok=True)
        source = inbox / "asset.dbc"
        source.write_text(text, encoding="utf-8")
        asset_id = ProjectDbcService(handle.root).import_asset(source).asset_id
    return root, asset_id


def test_a_hit_returns_the_decoder_the_miss_put_in() -> None:
    cache = DecoderCache(max_entries=2)
    subject = decoder()

    assert cache.get(key()) is None
    cache.put(key(), subject)

    assert cache.get(key()) is subject
    assert len(cache) == 1


def test_the_cache_evicts_the_least_recently_used_entry() -> None:
    cache = DecoderCache(max_entries=2)
    first, second, third = key(sha256="a" * 64), key(sha256="b" * 64), key(sha256="c" * 64)
    cache.put(first, decoder())
    cache.put(second, decoder())
    # Reading the first makes the second the least recently used.
    assert cache.get(first) is not None

    cache.put(third, decoder())

    assert len(cache) == 2
    assert cache.get(second) is None
    assert cache.get(first) is not None
    assert cache.get(third) is not None


def test_a_bound_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        DecoderCache(max_entries=0)


@pytest.mark.parametrize(
    "override",
    [
        {"project_id": "project-2"},
        {"sha256": "b" * 64},
        {"size_bytes": 129},
        {"encoding": "latin-1"},
    ],
)
def test_every_field_of_the_key_is_part_of_the_identity(override: dict[str, object]) -> None:
    assert key(**override) != key()


def test_unchanged_content_reuses_one_compiled_decoder(tmp_path: Path) -> None:
    root, asset_id = build_project(tmp_path, BASIC_DBC)
    service = ProjectDbcService(root)

    assert service.load_decoder(asset_id) is service.load_decoder(asset_id)


def test_two_projects_get_their_own_entry(tmp_path: Path) -> None:
    root_a, asset_a = build_project(tmp_path, BASIC_DBC, name="project_a")
    root_b, asset_b = build_project(tmp_path, BASIC_DBC, name="project_b")

    first = ProjectDbcService(root_a).load_decoder(asset_a)
    second = ProjectDbcService(root_b).load_decoder(asset_b)

    assert first is not second


def test_two_assets_of_one_project_with_the_same_content_share_an_entry(
    tmp_path: Path,
) -> None:
    root, asset_id = build_project(tmp_path, BASIC_DBC)
    with ProjectService().open(root) as handle:
        again = tmp_path / "again.dbc"
        again.write_text(BASIC_DBC, encoding="utf-8")
        second_id = ProjectDbcService(handle.root).import_asset(again).asset_id

    service = ProjectDbcService(root)
    assert service.load_decoder(asset_id) is service.load_decoder(second_id)


def test_a_tampered_asset_is_refused_before_the_cache_is_consulted(tmp_path: Path) -> None:
    root, asset_id = build_project(tmp_path, BASIC_DBC)
    service = ProjectDbcService(root)
    assert service.load_decoder(asset_id) is not None

    target = root / "dbc" / f"{asset_id}.dbc"
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(DbcAssetIntegrityError) as caught:
        service.load_decoder(asset_id)
    assert caught.value.code == "dbc.asset_integrity_failed"
    assert caught.value.recoverable is False
