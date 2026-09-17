"""The Project read model over a real project, a real filesystem and the real app.

Nothing about the project stack is mocked here. The project is created by
:class:`~canx.project.service.ProjectService`, closed, then reached over HTTP
through the real FastAPI ASGI application, which opens it again and validates
``project.json``, ``project.db``, the schema and the manifest/database agreement
before it answers. A passing run therefore proves the whole chain —

``HTTP adapter → ProjectService.open → project.json + project.db → ProjectMetadata
→ typed response``

— and not merely that a handler returns a shape.

The assertions compare the HTTP answer against the *domain's own* answer for the
same project, read back from the same disk, so the endpoint cannot be a second
opinion about what the project is.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC
from pathlib import Path

from canx.api.app import create_app
from canx.project.manifest import MANIFEST_FILENAME
from canx.project.model import MANIFEST_FORMAT
from canx.project.service import ProjectService
from canx.project.storage import DATABASE_FILENAME
from httpx import ASGITransport, AsyncClient

#: A syntactically valid project UUID that is not the one a fixture project owns.
OTHER_PROJECT_ID = "11111111-2222-4333-8444-555555555555"

ENVELOPE_FIELDS = {"code", "message", "details", "recoverable", "source"}
INSPECT_FIELDS = {"project_id", "display_name", "schema_version", "created_at", "updated_at"}


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://testserver")


def _created_project(tmp_path: Path, *, name: str, display_name: str) -> Path:
    """Create a real CAN-X project on the real filesystem and close it again.

    The project is deliberately left closed: every request below opens and
    validates it itself, which is the behaviour under test.
    """
    root = tmp_path / name
    with ProjectService().create(root, display_name=display_name):
        pass
    return root


async def test_a_real_project_is_inspected_end_to_end(tmp_path: Path) -> None:
    """create → close → HTTP inspect → the project domain's own identity."""
    root = _created_project(tmp_path, name="vehicle.canx", display_name="Powertrain Rig")

    with ProjectService().open(root) as handle:
        expected_id = handle.project_id
        expected_schema_version = handle.schema_version
        expected = handle.read_metadata()

    async with _client() as client:
        response = await client.get("/project/inspect", params={"project_path": str(root)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == INSPECT_FIELDS
    assert body["project_id"] == expected_id
    assert body["display_name"] == "Powertrain Rig"
    assert body["schema_version"] == expected_schema_version
    assert body["created_at"] == expected.created_at.astimezone(UTC).isoformat()
    assert body["updated_at"] == expected.updated_at.astimezone(UTC).isoformat()

    # The identity the endpoint published is the one the manifest and the database
    # agree on, read straight from disk rather than from the domain's snapshot.
    manifest = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["project_id"] == body["project_id"]
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        row = connection.execute(
            "SELECT project_id, display_name FROM project_metadata WHERE id = 1"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert row[0] == body["project_id"]
    assert row[1] == body["display_name"]


async def test_a_project_missing_its_database_is_rejected_over_http(tmp_path: Path) -> None:
    """The real filesystem produces the real domain failure, not a mocked one."""
    root = _created_project(tmp_path, name="broken.canx", display_name="Broken")
    (root / DATABASE_FILENAME).unlink()

    async with _client() as client:
        response = await client.get("/project/inspect", params={"project_path": str(root)})

    assert response.status_code == 400, response.text
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == "project.database_missing"
    assert body["source"] == "project"
    assert body["recoverable"] is False


async def test_a_manifest_that_disagrees_with_the_database_is_rejected_over_http(
    tmp_path: Path,
) -> None:
    """The agreement check is performed on every request, not cached at create time."""
    root = _created_project(tmp_path, name="mismatched.canx", display_name="Mismatched")
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        connection.execute("UPDATE project_metadata SET project_id = ?", (OTHER_PROJECT_ID,))
        connection.commit()
    finally:
        connection.close()

    async with _client() as client:
        response = await client.get("/project/inspect", params={"project_path": str(root)})

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == "project.identity_mismatch"
    assert body["source"] == "project"


async def test_a_plain_directory_is_not_a_project(tmp_path: Path) -> None:
    """A directory that merely looks like one is refused, and left untouched."""
    plain = tmp_path / "not-a-project"
    plain.mkdir()
    (plain / "notes.txt").write_text("user data", encoding="utf-8")
    before = sorted(path.name for path in plain.iterdir())

    async with _client() as client:
        response = await client.get("/project/inspect", params={"project_path": str(plain)})

    assert response.status_code == 400, response.text
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"].startswith("project.")
    assert body["source"] == "project"
    assert sorted(path.name for path in plain.iterdir()) == before


async def test_a_foreign_json_manifest_is_not_a_project(tmp_path: Path) -> None:
    """Another tool's JSON object is not a CAN-X manifest."""
    root = tmp_path / "foreign.canx"
    root.mkdir()
    (root / MANIFEST_FILENAME).write_text(
        json.dumps(
            {"format": "some-other-tool", "schema_version": 1, "project_id": OTHER_PROJECT_ID}
        ),
        encoding="utf-8",
    )

    async with _client() as client:
        response = await client.get("/project/inspect", params={"project_path": str(root)})

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "project.unsupported_format"
    assert response.json()["source"] == "project"


async def test_two_real_projects_are_never_confused_with_each_other(tmp_path: Path) -> None:
    """No global current project: consecutive requests answer for their own target."""
    first = _created_project(tmp_path, name="first.canx", display_name="First Rig")
    second = _created_project(tmp_path, name="second.canx", display_name="Second Rig")

    with ProjectService().open(first) as handle:
        first_id = handle.project_id
    with ProjectService().open(second) as handle:
        second_id = handle.project_id

    async with _client() as client:
        first_body = (
            await client.get("/project/inspect", params={"project_path": str(first)})
        ).json()
        second_body = (
            await client.get("/project/inspect", params={"project_path": str(second)})
        ).json()
        first_again = (
            await client.get("/project/inspect", params={"project_path": str(first)})
        ).json()

    assert first_body == first_again
    assert first_body["project_id"] == first_id
    assert second_body["project_id"] == second_id
    assert first_body["display_name"] == "First Rig"
    assert second_body["display_name"] == "Second Rig"


async def test_inspecting_a_project_changes_nothing_about_it(tmp_path: Path) -> None:
    """The read model reads: no migration, no repair, no rewrite."""
    root = _created_project(tmp_path, name="untouched.canx", display_name="Untouched")
    manifest = (root / MANIFEST_FILENAME).read_bytes()
    database = (root / DATABASE_FILENAME).read_bytes()
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    async with _client() as client:
        for _ in range(5):
            response = await client.get("/project/inspect", params={"project_path": str(root)})
            assert response.status_code == 200, response.text

    assert (root / MANIFEST_FILENAME).read_bytes() == manifest
    assert (root / DATABASE_FILENAME).read_bytes() == database
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*")) == before


async def test_the_answer_is_the_same_whether_the_creator_handle_stayed_open(
    tmp_path: Path,
) -> None:
    """A project is a fact on disk, not a runtime session.

    The endpoint must not depend on some other code path having left a handle open
    — it opens what it needs, closes it, and answers the same either way.
    """
    root = tmp_path / "shared.canx"
    with ProjectService().create(root, display_name="Shared") as creator:
        async with _client() as client:
            while_open = await client.get(
                "/project/inspect", params={"project_path": str(root)}
            )
        creator_id = creator.project_id

    assert while_open.status_code == 200, while_open.text

    async with _client() as client:
        after_close = await client.get("/project/inspect", params={"project_path": str(root)})

    assert after_close.status_code == 200, after_close.text
    assert after_close.json() == while_open.json()
    assert after_close.json()["project_id"] == creator_id


async def test_an_unsupported_manifest_schema_is_reported_over_http(tmp_path: Path) -> None:
    """A project written by a newer runtime is refused, never partially read."""
    root = _created_project(tmp_path, name="future.canx", display_name="Future")

    async with _client() as client:
        current = await client.get("/project/inspect", params={"project_path": str(root)})
    assert current.status_code == 200, current.text
    project_id = current.json()["project_id"]

    (root / MANIFEST_FILENAME).write_text(
        json.dumps(
            {"format": MANIFEST_FORMAT, "schema_version": 99, "project_id": project_id}
        ),
        encoding="utf-8",
    )

    async with _client() as client:
        response = await client.get("/project/inspect", params={"project_path": str(root)})

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == "project.unsupported_schema_version"
    assert body["details"]["schema_version"] == 99
    assert body["details"]["supported_schema_version"] == 1
