"""HTTP contract for the Project runtime read-model surface.

One endpoint — ``GET /project/inspect?project_path=…`` — driven over the real ASGI
app against real CAN-X projects that
:class:`~canx.project.service.ProjectService` created on a real filesystem. Nothing
about the project domain is mocked, so a passing case proves the chain:

``ASGITransport → /project/inspect → ProjectService.open → project.json +
project.db → typed response``.

Four properties are pinned deliberately:

* **the projection is the domain's identity.** ``project_id`` / ``display_name`` /
  ``created_at`` / ``updated_at`` are read through the validated handle, and
  ``schema_version`` is the canonical manifest version — never a guess from a file
  name or a directory name;
* **the answer is not a filesystem authority.** No path — of the project or of
  anything inside it — is part of the response;
* **every domain failure keeps its own code.** A missing path, a file where a
  directory belongs, an absent or malformed manifest, an unsupported schema, a
  missing database, a broken schema and an identity mismatch are seven different
  facts, each answered with the shared five-field envelope and a 400;
* **a malformed request is the request boundary's failure, not the domain's.** A
  request the framework rejects never reaches a project at all.

The module also pins the two lifecycle facts a read model is easiest to get wrong:
the blocking project open runs in a worker thread, and the temporary
:class:`~canx.project.service.ProjectHandle` is closed on the success path, on a
failure after it was opened, and even when the response itself cannot be built.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC
from pathlib import Path

import canx.api.project as project_module
import pytest
from canx.api.app import create_app
from canx.project.errors import ProjectError
from canx.project.manifest import MANIFEST_FILENAME
from canx.project.model import MANIFEST_FORMAT
from canx.project.service import ProjectHandle, ProjectService
from canx.project.storage import DATABASE_FILENAME
from httpx import ASGITransport, AsyncClient

#: A syntactically valid project UUID that is not the one a fixture project owns.
OTHER_PROJECT_ID = "11111111-2222-4333-8444-555555555555"

#: The five fields SPEC §38 requires, and nothing else.
ENVELOPE_FIELDS = {"code", "message", "details", "recoverable", "source"}

#: The exact response projection this endpoint is allowed to publish.
INSPECT_FIELDS = {"project_id", "display_name", "schema_version", "created_at", "updated_at"}

REQUEST_VALIDATION_CODE = "api.request_validation_failed"

#: Substrings that must never appear in a *success* answer: framework internals,
#: storage internals, and any absolute location.
LEAK_MARKERS = ("traceback", "pydantic", "sqlite", "project.db", "project.json")

#: Substrings that must never appear in a *failure* answer. A diagnosis may name
#: the target the caller supplied — that is the whole point of a diagnosis — but it
#: may not expose the layers that produced it.
INTERNAL_MARKERS = ("traceback", "pydantic", "sqlite", "pyarrow", "duckdb", "cantools")


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://testserver")


def _make_project(tmp_path: Path, *, display_name: str = "Vehicle A") -> Path:
    """Create a real CAN-X project and return it closed.

    Every request below has to open and validate the project itself, which is the
    behaviour under test — no request is handed an already-open handle.
    """
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name=display_name):
        pass
    return root


def _assert_project_error(status: int, body: dict[str, object], code: str) -> None:
    """Assert the whole shared-envelope contract for one project failure."""
    assert status == 400
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == code
    assert body["source"] == "project"
    assert isinstance(body["message"], str) and body["message"]
    assert isinstance(body["details"], dict)
    rendered = str(body).lower()
    for marker in INTERNAL_MARKERS:
        assert marker not in rendered, marker


def _assert_validation_envelope(status: int, body: dict[str, object]) -> None:
    """Assert the request-validation contract for a framework-level refusal."""
    assert status == 422
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == REQUEST_VALIDATION_CODE
    assert body["source"] == "api"
    assert body["recoverable"] is False


async def _inspect(client: AsyncClient, project_path: object) -> object:
    """Send one inspect request for ``project_path``."""
    return await client.get("/project/inspect", params={"project_path": project_path})


# --- the canonical read model ------------------------------------------------


async def test_a_valid_project_answers_with_its_canonical_read_model(
    tmp_path: Path,
) -> None:
    """The endpoint is a projection of the domain's own identity, not a re-derivation."""
    root = _make_project(tmp_path, display_name="Vehicle A")

    with ProjectService().open(root) as handle:
        expected_id = handle.project_id
        expected_schema_version = handle.schema_version
        expected = handle.read_metadata()

    async with _client() as client:
        response = await _inspect(client, str(root))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == INSPECT_FIELDS
    assert body["project_id"] == expected_id
    assert body["display_name"] == "Vehicle A"
    assert body["schema_version"] == expected_schema_version
    assert body["created_at"] == expected.created_at.astimezone(UTC).isoformat()
    assert body["updated_at"] == expected.updated_at.astimezone(UTC).isoformat()


async def test_the_identity_is_never_derived_from_the_directory_name(tmp_path: Path) -> None:
    """A folder called ``vehicle.canx`` is not evidence of anything.

    ``display_name`` is the metadata the project database owns and ``project_id``
    is the UUID the manifest and the database agree on — neither is the path, and
    nothing here may start guessing one from the other.
    """
    root = tmp_path / "misleading-name.canx"
    with ProjectService().create(root, display_name="Real Name") as handle:
        expected_id = handle.project_id

    async with _client() as client:
        response = await _inspect(client, str(root))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == "Real Name"
    assert body["display_name"] != root.name
    assert body["project_id"] == expected_id


async def test_the_read_model_publishes_no_filesystem_location(tmp_path: Path) -> None:
    """The caller already knows what it asked for; this answer is identity only."""
    root = _make_project(tmp_path)

    async with _client() as client:
        response = await _inspect(client, str(root))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == INSPECT_FIELDS
    rendered = json.dumps(body)
    assert str(root) not in rendered
    assert root.name not in rendered
    assert "path" not in " ".join(body).lower()
    lowered = rendered.lower()
    for marker in LEAK_MARKERS:
        assert marker not in lowered, marker
    for marker in ("dbc", "data", "cache", "logs", "exports"):
        assert marker not in lowered, marker


async def test_two_projects_answer_for_themselves_and_never_for_each_other(
    tmp_path: Path,
) -> None:
    """No global current project: each request names its own target."""
    first = tmp_path / "first.canx"
    second = tmp_path / "second.canx"
    with ProjectService().create(first, display_name="First") as handle:
        first_id = handle.project_id
    with ProjectService().create(second, display_name="Second") as handle:
        second_id = handle.project_id

    async with _client() as client:
        first_body = (await _inspect(client, str(first))).json()
        second_body = (await _inspect(client, str(second))).json()
        # Asking again must not have changed either answer.
        first_again = (await _inspect(client, str(first))).json()

    assert first_body == first_again
    assert first_body["project_id"] == first_id
    assert second_body["project_id"] == second_id
    assert first_body["display_name"] == "First"
    assert second_body["display_name"] == "Second"


async def test_an_inspection_leaves_the_project_exactly_as_it_was(tmp_path: Path) -> None:
    """A read model reads. It must not rewrite, repair or touch the target."""
    root = _make_project(tmp_path)
    manifest = (root / MANIFEST_FILENAME).read_bytes()
    database = (root / DATABASE_FILENAME).read_bytes()
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    async with _client() as client:
        response = await _inspect(client, str(root))

    assert response.status_code == 200, response.text
    assert (root / MANIFEST_FILENAME).read_bytes() == manifest
    assert (root / DATABASE_FILENAME).read_bytes() == database
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*")) == before


# --- one code per domain fact ------------------------------------------------


async def test_a_project_path_that_does_not_exist_is_not_found(tmp_path: Path) -> None:
    absent = tmp_path / "absent.canx"

    async with _client() as client:
        response = await _inspect(client, str(absent))

    _assert_project_error(response.status_code, response.json(), "project.not_found")
    assert response.json()["details"]["path"] == str(absent)
    assert not absent.exists()


async def test_a_file_where_a_directory_belongs_is_not_a_directory(tmp_path: Path) -> None:
    """A regular file is a different fact from a missing path, and keeps its code."""
    target = tmp_path / "not-a-directory.canx"
    target.write_bytes(b"just a file")

    async with _client() as client:
        response = await _inspect(client, str(target))

    _assert_project_error(response.status_code, response.json(), "project.not_a_directory")
    assert target.read_bytes() == b"just a file"


async def test_a_directory_without_a_manifest_reports_the_missing_manifest(
    tmp_path: Path,
) -> None:
    plain = tmp_path / "empty-directory"
    plain.mkdir()

    async with _client() as client:
        response = await _inspect(client, str(plain))

    _assert_project_error(response.status_code, response.json(), "project.manifest_missing")
    assert sorted(path.name for path in plain.iterdir()) == []


async def test_a_malformed_manifest_is_reported_as_malformed(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    (root / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")

    async with _client() as client:
        response = await _inspect(client, str(root))

    _assert_project_error(response.status_code, response.json(), "project.manifest_malformed")


async def test_a_manifest_of_another_format_is_refused(tmp_path: Path) -> None:
    """A JSON object that is not a CAN-X manifest is not a CAN-X project."""
    root = _make_project(tmp_path)
    original = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    original["format"] = "some-other-tool"
    (root / MANIFEST_FILENAME).write_text(json.dumps(original), encoding="utf-8")

    async with _client() as client:
        response = await _inspect(client, str(root))

    _assert_project_error(response.status_code, response.json(), "project.unsupported_format")


async def test_an_unsupported_manifest_schema_version_is_refused(tmp_path: Path) -> None:
    """A newer project is refused, never partially read."""
    root = _make_project(tmp_path)
    (root / MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "format": MANIFEST_FORMAT,
                "schema_version": 99,
                "project_id": OTHER_PROJECT_ID,
            }
        ),
        encoding="utf-8",
    )

    async with _client() as client:
        response = await _inspect(client, str(root))

    body = response.json()
    _assert_project_error(response.status_code, body, "project.unsupported_schema_version")
    assert body["details"]["schema_version"] == 99


async def test_a_missing_database_is_reported_as_a_missing_database(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    (root / DATABASE_FILENAME).unlink()

    async with _client() as client:
        response = await _inspect(client, str(root))

    _assert_project_error(response.status_code, response.json(), "project.database_missing")


async def test_a_database_missing_its_schema_tables_is_refused(tmp_path: Path) -> None:
    """A ``user_version`` stamp is a claim, not evidence."""
    root = _make_project(tmp_path)
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        connection.execute("DROP TABLE data_sessions")
        connection.commit()
    finally:
        connection.close()

    async with _client() as client:
        response = await _inspect(client, str(root))

    _assert_project_error(response.status_code, response.json(), "project.database_schema_invalid")


async def test_a_manifest_that_disagrees_with_the_database_is_an_identity_mismatch(
    tmp_path: Path,
) -> None:
    """The two identity documents must agree, and a disagreement is its own code."""
    root = _make_project(tmp_path)
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        connection.execute(
            "UPDATE project_metadata SET project_id = ?", (OTHER_PROJECT_ID,)
        )
        connection.commit()
    finally:
        connection.close()

    async with _client() as client:
        response = await _inspect(client, str(root))

    body = response.json()
    _assert_project_error(response.status_code, body, "project.identity_mismatch")
    assert body["details"]["database_project_id"] == OTHER_PROJECT_ID


async def test_a_domain_failure_is_never_downgraded_to_a_server_error(tmp_path: Path) -> None:
    """Four different bad targets, four domain codes, no 500 among them."""
    plain = tmp_path / "plain"
    plain.mkdir()
    malformed = _make_project(tmp_path)
    (malformed / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")

    async with _client() as client:
        responses = [
            await _inspect(client, str(tmp_path / "absent.canx")),
            await _inspect(client, str(plain)),
            await _inspect(client, str(malformed)),
        ]

    for response in responses:
        assert response.status_code == 400, response.text
        body = response.json()
        assert set(body) == ENVELOPE_FIELDS
        assert body["source"] == "project"
        assert body["code"].startswith("project.")
        assert body["code"] != REQUEST_VALIDATION_CODE
        assert body["recoverable"] is False


# --- the request boundary ----------------------------------------------------


async def test_a_missing_project_path_is_a_request_validation_failure() -> None:
    """``project_path`` is required; a request without it never reaches the domain.

    This is the request boundary, and it stays visibly separate from the domain's:
    the envelope, the code and the ``source`` are the shared ones the framework
    rejection path publishes for every endpoint in this application, and the
    framework's own ``{"detail": …}`` protocol is nowhere in the answer.
    """
    async with _client() as client:
        response = await client.get("/project/inspect")

    body = response.json()
    _assert_validation_envelope(response.status_code, body)
    assert "detail" not in body
    issues = body["details"]["errors"]
    assert issues and all(set(issue) == {"location", "type", "message"} for issue in issues)
    assert any("project_path" in issue["location"] for issue in issues)
    assert all(issue["location"][0] == "query" for issue in issues)
    assert body["code"] != "project.not_found"


async def test_the_runtime_normalizes_nothing_about_the_supplied_path(tmp_path: Path) -> None:
    """The domain decides what a path means; this layer must not rewrite it.

    An empty value is handed through untouched rather than silently expanded into
    the runtime's working directory, and the answer it gets is the domain's answer
    for *that* string — a rejection, not an inspection of somewhere else.
    """
    async with _client() as client:
        response = await client.get("/project/inspect", params={"project_path": ""})

    # Whatever the domain decided, this must not have become "inspect the cwd".
    assert response.status_code == 400, response.text
    assert response.json()["source"] == "project"
    assert response.json()["code"].startswith("project.")


async def test_a_rejected_request_never_reaches_the_project_domain(tmp_path: Path) -> None:
    """The control case is what makes this a proof rather than an assertion.

    With a well-shaped request and an absent target the handler runs and reports
    ``project.not_found``; with the same target and no path at all the request is
    refused before any project is opened.
    """
    absent = tmp_path / "absent.canx"

    async with _client() as client:
        reached = await _inspect(client, str(absent))
        rejected = await client.get("/project/inspect")

    assert reached.status_code == 400
    assert reached.json()["code"] == "project.not_found"
    _assert_validation_envelope(rejected.status_code, rejected.json())
    assert not absent.exists()


# --- event-loop boundary -----------------------------------------------------


class _OpenSpy:
    """Records the thread and the handle of every project open."""

    def __init__(self) -> None:
        self.threads: list[int] = []
        self.handles: list[ProjectHandle] = []

    @property
    def leaked(self) -> list[ProjectHandle]:
        """Every handle this request path opened but did not close."""
        return [handle for handle in self.handles if not handle.closed]


def _spy_on_open(monkeypatch: pytest.MonkeyPatch) -> _OpenSpy:
    """Record every project open without changing anything else about it."""
    spy = _OpenSpy()
    original = project_module._open_project

    def counted(project_path: str) -> ProjectHandle:
        # The thread is recorded *before* the open, because a refused target never
        # returns a handle and its validation is exactly what has to be off-loop.
        spy.threads.append(threading.get_ident())
        handle = original(project_path)
        spy.handles.append(handle)
        return handle

    monkeypatch.setattr(project_module, "_open_project", counted)
    return spy


async def test_a_complete_project_open_runs_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening a project is a filesystem walk, a JSON read and a SQLite open.

    ``ProjectService.open`` performs all of it, so none of it may run on the loop
    the realtime WebSocket is served from. Being an ``async`` route says nothing
    about where the work happened; the thread the work ran on is what is asserted.
    """
    root = _make_project(tmp_path)
    spy = _spy_on_open(monkeypatch)
    event_loop_thread = threading.get_ident()

    async with _client() as client:
        response = await _inspect(client, str(root))

    assert response.status_code == 200, response.text
    assert spy.threads, "the project must actually have been opened"
    assert all(thread != event_loop_thread for thread in spy.threads), (
        "ProjectService.open must not run on the event loop"
    )


async def test_a_failing_project_open_also_runs_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation is the expensive half, and the failing path does all of it too."""
    plain = tmp_path / "plain"
    plain.mkdir()
    spy = _spy_on_open(monkeypatch)
    event_loop_thread = threading.get_ident()

    async with _client() as client:
        response = await _inspect(client, str(plain))

    assert response.status_code == 400, response.text
    assert spy.threads, "the target must actually have been examined"
    assert all(thread != event_loop_thread for thread in spy.threads)


# --- handle ownership --------------------------------------------------------


async def test_the_success_path_closes_its_project_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ProjectHandle`` owns the SQLite connection; a request owns the handle."""
    root = _make_project(tmp_path)
    spy = _spy_on_open(monkeypatch)

    async with _client() as client:
        response = await _inspect(client, str(root))

    assert response.status_code == 200, response.text
    assert len(spy.handles) == 1
    assert spy.handles[0].closed is True, "the request must not return with an open handle"
    assert spy.leaked == []


async def test_a_failure_after_the_open_still_closes_the_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure happens *inside* the owned scope, so it must not leak a connection."""
    root = _make_project(tmp_path)
    spy = _spy_on_open(monkeypatch)

    def explode(_handle: ProjectHandle) -> object:
        raise ProjectError(
            "The project metadata could not be read.", code="project.metadata_unreadable"
        )

    monkeypatch.setattr(ProjectHandle, "read_metadata", explode)

    async with _client() as client:
        response = await _inspect(client, str(root))

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "project.metadata_unreadable"
    assert len(spy.handles) == 1
    assert spy.handles[0].closed is True
    assert spy.leaked == []


async def test_a_response_that_cannot_be_built_leaks_no_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup cannot depend on the response succeeding: the handle is gone first.

    The projection is built *after* the owned scope has exited, so a rendering
    failure has nothing left to leak. Without that ordering this test fails with an
    open connection, not merely with a different status code.
    """
    root = _make_project(tmp_path)
    spy = _spy_on_open(monkeypatch)

    def explode(_metadata: object, _schema_version: int) -> object:
        raise RuntimeError("the projection could not be built")

    monkeypatch.setattr(project_module, "_inspect_payload", explode)

    async with _client() as client:
        with pytest.raises(RuntimeError):
            await _inspect(client, str(root))

    assert len(spy.handles) == 1
    assert spy.handles[0].closed is True
    assert spy.leaked == []


async def test_repeated_inspection_retains_no_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is no cached handle, no session and no current project behind this.

    The strongest available evidence is that the request count and the open count
    agree and that every one of those handles is closed: a retained handle would
    show up as an open one, and a cached one would show up as fewer opens.
    """
    root = _make_project(tmp_path)
    spy = _spy_on_open(monkeypatch)
    requests = 25

    async with _client() as client:
        for _ in range(requests):
            response = await _inspect(client, str(root))
            assert response.status_code == 200, response.text

    assert len(spy.handles) == requests, "every request must open its own project"
    assert spy.leaked == [], "no handle may survive its request"
    assert (root / DATABASE_FILENAME).is_file()
