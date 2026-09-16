"""A genuine older project must open, migrate, and keep working under V0.3-03.

Two compatibility gates, both real. The V0.2-01 runtime wrote a metadata-only
database stamped ``user_version = 1``; the V0.2-02 runtime wrote one stamped
``user_version = 2`` holding data sessions and Parquet segments. Both are upgraded
in place to the current schema, keeping their identity and every row they had.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.errors import ProjectIdentityMismatchError
from canx.project.manifest import MANIFEST_FILENAME
from canx.project.model import MANIFEST_FORMAT, MANIFEST_SCHEMA_VERSION
from canx.project.service import PROJECT_DIRECTORY_NAMES, ProjectService
from canx.project.storage import DATABASE_FILENAME, DATABASE_SCHEMA_VERSION

V1_SCHEMA_VERSION = 1
V2_SCHEMA_VERSION = 2
STREAM_ID = "upgraded-stream"

_V1_METADATA_TABLE = (
    "CREATE TABLE project_metadata ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), "
    "project_id TEXT NOT NULL, "
    "display_name TEXT NOT NULL, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)"
)
_V1_CREATED_AT = datetime(2026, 9, 10, 7, 45, tzinfo=UTC)
_V1_UPDATED_AT = datetime(2026, 9, 11, 18, 5, tzinfo=UTC)


def create_v1_project(root: Path, *, display_name: str = "Legacy Vehicle") -> tuple[str, str]:
    """Write exactly what the V0.2-01 runtime produced, and nothing more."""
    project_id = str(uuid4())
    root.mkdir(parents=True)
    for name in PROJECT_DIRECTORY_NAMES:
        (root / name).mkdir()
    (root / MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "format": MANIFEST_FORMAT,
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "project_id": project_id,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        connection.execute(_V1_METADATA_TABLE)
        connection.execute(
            "INSERT INTO project_metadata"
            " (id, project_id, display_name, created_at, updated_at)"
            " VALUES (1, ?, ?, ?, ?)",
            (
                project_id,
                display_name,
                _V1_CREATED_AT.isoformat(),
                _V1_UPDATED_AT.isoformat(),
            ),
        )
        connection.execute(f"PRAGMA user_version = {V1_SCHEMA_VERSION}")
        connection.commit()
    finally:
        connection.close()
    return project_id, display_name


def stored_metadata(root: Path) -> tuple[str, str, str, str]:
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        row = connection.execute(
            "SELECT project_id, display_name, created_at, updated_at"
            " FROM project_metadata WHERE id = 1"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return (str(row[0]), str(row[1]), str(row[2]), str(row[3]))


def stored_tables(root: Path) -> set[str]:
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        return {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        connection.close()


def stored_schema_version(root: Path) -> int:
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        row = connection.execute("PRAGMA user_version").fetchone()
    finally:
        connection.close()
    assert row is not None
    return int(row[0])


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x300,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=1,
        data=bytes([sequence % 256]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=3_000.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def batch_of(count: int) -> FrameBatch:
    """Build one contiguous batch of ``count`` frames starting at sequence 0."""
    return FrameBatch.create(
        stream_id=STREAM_ID, frames=[frame(sequence) for sequence in range(count)]
    )


def test_a_v1_project_is_prepared_as_a_real_v1_database(tmp_path: Path) -> None:
    """Guard the fixture itself: it must look like a V0.2-01 project."""
    root = tmp_path / "legacy.canx"
    create_v1_project(root)

    assert stored_schema_version(root) == V1_SCHEMA_VERSION == 1
    assert stored_tables(root) == {"project_metadata"}
    assert (root / MANIFEST_FILENAME).is_file()


def test_opening_a_v1_project_migrates_it_in_place(tmp_path: Path) -> None:
    root = tmp_path / "legacy.canx"
    project_id, display_name = create_v1_project(root)
    manifest_before = (root / MANIFEST_FILENAME).read_text(encoding="utf-8")

    with ProjectService().open(root) as handle:
        assert handle.project_id == project_id
        assert handle.display_name == display_name
        assert handle.metadata.created_at == _V1_CREATED_AT
        assert handle.metadata.updated_at == _V1_UPDATED_AT

    assert stored_schema_version(root) == DATABASE_SCHEMA_VERSION == 3
    assert {
        "project_metadata",
        "data_sessions",
        "data_segments",
        "dbc_assets",
    } <= stored_tables(root)
    assert stored_metadata(root) == (
        project_id,
        display_name,
        _V1_CREATED_AT.isoformat(),
        _V1_UPDATED_AT.isoformat(),
    )
    assert (root / MANIFEST_FILENAME).read_text(encoding="utf-8") == manifest_before


def test_the_upgraded_project_can_hold_a_data_session(tmp_path: Path) -> None:
    root = tmp_path / "legacy.canx"
    project_id, _ = create_v1_project(root)

    with ProjectService().open(root) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=4)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch_of(6))
        completed = writer.finalize()

        assert completed.project_id == project_id
        assert completed.state is DataSessionState.COMPLETED
        assert completed.frame_count == 6
        assert completed.segment_count == 2
        assert service.inspect_integrity().clean is True
        assert service.read_segment(writer.session_id, 1) == (frame(4), frame(5))


def test_a_session_written_after_upgrade_survives_a_reopen(tmp_path: Path) -> None:
    root = tmp_path / "legacy.canx"
    project_id, _ = create_v1_project(root)
    with ProjectService().open(root) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=3)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch_of(3))
        completed = writer.finalize()
        session_id = writer.session_id

    with ProjectService().open(root) as reopened:
        reloaded = DataSessionService(reopened.root)
        assert reopened.project_id == project_id
        assert reloaded.get_session(session_id) == completed
        assert reloaded.read_segment(session_id, 0) == (frame(0), frame(1), frame(2))
        assert reloaded.inspect_integrity().clean is True


def test_the_migration_is_idempotent_across_repeated_opens(tmp_path: Path) -> None:
    root = tmp_path / "legacy.canx"
    project_id, display_name = create_v1_project(root)
    first_metadata = None

    for _ in range(4):
        with ProjectService().open(root) as handle:
            assert handle.project_id == project_id
            assert handle.display_name == display_name
        if first_metadata is None:
            first_metadata = stored_metadata(root)

    assert stored_schema_version(root) == DATABASE_SCHEMA_VERSION
    assert stored_metadata(root) == first_metadata


def test_the_manifest_schema_version_is_not_rewritten_by_the_database_upgrade(
    tmp_path: Path,
) -> None:
    root = tmp_path / "legacy.canx"
    project_id, _ = create_v1_project(root)

    with ProjectService().open(root):
        pass

    payload = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert payload == {
        "format": MANIFEST_FORMAT,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "project_id": project_id,
    }


def test_a_v1_project_with_the_wrong_identity_is_still_rejected(tmp_path: Path) -> None:
    """Migration must not paper over an identity mismatch."""
    root = tmp_path / "legacy.canx"
    create_v1_project(root)
    manifest_path = root / MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["project_id"] = str(uuid4())
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProjectIdentityMismatchError):
        ProjectService().open(root)

# ---------------------------------------------------------------------------
# V2 -> V3: a V0.2-02 project with real persisted data keeps every byte of it
# ---------------------------------------------------------------------------


def _downgrade_to_v2(root: Path) -> None:
    """Turn a freshly written project database into the V2 shape it had before.

    V3 adds exactly one table, so dropping it and restamping ``user_version = 2``
    reproduces what the V0.2-02 runtime left on disk — while the session rows and
    the Parquet segment files stay exactly as the real writers produced them.
    """
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        connection.execute("DROP TABLE dbc_assets")
        connection.execute(f"PRAGMA user_version = {V2_SCHEMA_VERSION}")
        connection.commit()
    finally:
        connection.close()


def test_a_v2_project_with_real_persisted_data_survives_the_upgrade(tmp_path: Path) -> None:
    """The upgrade must keep the rows *and* the files a V0.2-02 project already had."""
    root = tmp_path / "v2-vehicle.canx"
    with ProjectService().create(root, display_name="V2 Vehicle") as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=3)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch_of(5))
        completed = writer.finalize()
        session_id = writer.session_id
        segments = service.list_segments(session_id)
        manifest_before = (root / MANIFEST_FILENAME).read_text(encoding="utf-8")

    assert completed.frame_count == 5
    assert completed.segment_count == 2

    _downgrade_to_v2(root)
    assert stored_schema_version(root) == V2_SCHEMA_VERSION
    assert "dbc_assets" not in stored_tables(root)

    with ProjectService().open(root) as upgraded:
        assert upgraded.project_id == completed.project_id
        assert upgraded.display_name == "V2 Vehicle"
        restored = DataSessionService(upgraded.root)
        assert restored.get_session(session_id) == completed
        assert restored.list_segments(session_id) == segments
        assert restored.inspect_integrity().clean is True

        frames: list[Frame] = []
        for segment in segments:
            frames.extend(restored.read_segment(session_id, segment.segment_index))
        assert frames == [frame(sequence) for sequence in range(5)]

    assert stored_schema_version(root) == DATABASE_SCHEMA_VERSION
    assert "dbc_assets" in stored_tables(root)
    assert (root / MANIFEST_FILENAME).read_text(encoding="utf-8") == manifest_before
