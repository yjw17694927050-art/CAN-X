"""The HTTP request-validation error boundary.

A request whose *shape* is wrong never reaches a route handler: FastAPI rejects
it first, and by default answers with its own ``{"detail": [...]}`` payload. That
payload is a second, undocumented error protocol — it has no ``code``, no
``source``, and it echoes the caller's raw input back.

SPEC §38 requires one structured shape for every error that crosses an API
boundary, so framework-level rejections are brought into the same envelope while
keeping their own status (422) and their own diagnosis
(``api.request_validation_failed`` / ``source = api``). Keeping them separate from
the domain errors is the point: "your payload does not match the contract" and
"the contract rejected your values" are different facts, and a caller has to be
able to tell them apart.

These tests pin three things: the envelope itself, the absence of leaked
internals or echoed input, and the fact that a rejected request has no side
effects at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from canx.api.app import create_app
from canx.data.session import DataSessionService
from canx.project.service import ProjectService
from canx.runtime.service import RuntimeService
from httpx import ASGITransport, AsyncClient

SESSION_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"

#: The five fields SPEC §38 requires, and nothing else.
ENVELOPE_FIELDS = {"code", "message", "details", "recoverable", "source"}

REQUEST_VALIDATION_CODE = "api.request_validation_failed"
REQUEST_VALIDATION_MESSAGE = "The request payload does not match the API contract."

#: Substrings that must never appear in a rejection: framework internals, Python
#: exception text, storage/engine internals, and the caller's own payload.
LEAK_MARKERS = (
    "traceback",
    "pydantic",
    "validationerror",
    "duckdb",
    "sqlite",
    "pyarrow",
    '"input"',
    "pydantic_core",
)


def _client(**kwargs: object) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app(**kwargs)),  # type: ignore[arg-type]
        base_url="http://testserver",
    )


def assert_request_validation_envelope(response_status: int, body: dict[str, object]) -> None:
    """Assert the whole request-validation contract for one response."""
    assert response_status == 422
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == REQUEST_VALIDATION_CODE
    assert body["message"] == REQUEST_VALIDATION_MESSAGE
    assert body["source"] == "api"
    assert body["recoverable"] is False
    details = body["details"]
    assert isinstance(details, dict)
    issues = details["errors"]
    assert isinstance(issues, list) and issues
    for issue in issues:
        assert isinstance(issue, dict)
        assert set(issue) == {"location", "type", "message"}
        assert isinstance(issue["location"], list) and issue["location"]
        assert isinstance(issue["type"], str) and issue["type"]
        assert isinstance(issue["message"], str) and issue["message"]
    rendered = str(body).lower()
    for marker in LEAK_MARKERS:
        assert marker not in rendered, marker


@pytest.mark.parametrize(
    "payload",
    [
        {"project_path": "x", "session_id": SESSION_ID, "limit": "abc"},
        {"project_path": "x", "session_id": SESSION_ID, "after_sequence": "later"},
        {"project_path": "x", "session_id": SESSION_ID, "after_sequence": 1.5},
        {"project_path": "x", "session_id": SESSION_ID, "limit": [1]},
        {"project_path": "x"},
        {"session_id": SESSION_ID},
        {"project_path": "x", "session_id": SESSION_ID, "filters": {"is_fd": {"nested": 1}}},
        {"project_path": "x", "session_id": SESSION_ID, "filters": {"is_fd": [True, False]}},
        {"project_path": "x", "session_id": SESSION_ID, "filters": "not-an-object"},
        {"project_path": "x", "session_id": SESSION_ID, "filters": {"sequence_start": {}}},
        {"project_path": "x", "session_id": SESSION_ID, "filters": {"arbitration_id_mask": {}}},
        {"project_path": "x", "session_id": SESSION_ID, "filters": {"channel_ids": 3}},
        {"project_path": 5, "session_id": SESSION_ID},
        {"project_path": "x", "session_id": ["a"]},
        [],
    ],
    ids=[
        "limit-not-an-integer",
        "cursor-not-an-integer",
        "cursor-is-a-float",
        "limit-is-a-list",
        "missing-project-path",
        "missing-session-id",
        "is-fd-is-an-object",
        "is-fd-is-a-list",
        "filters-is-a-string",
        "sequence-start-is-an-object",
        "mask-is-an-object",
        "channel-ids-is-an-integer",
        "project-path-is-an-integer",
        "session-id-is-a-list",
        "body-is-an-array",
    ],
)
async def test_a_trace_query_request_that_fails_framework_validation(
    payload: object,
) -> None:
    async with _client() as client:
        response = await client.post("/trace/query", json=payload)

    assert_request_validation_envelope(response.status_code, response.json())


@pytest.mark.parametrize(
    "payload",
    [
        {"project_path": "x", "session_id": SESSION_ID, "filters": {"is_fd": {"nested": 1}}},
        {"project_path": "x", "session_id": SESSION_ID, "filters": {"directions": 7}},
        {"project_path": "x"},
        {"session_id": SESSION_ID},
        {},
        [],
    ],
    ids=[
        "filter-flag-wrong-type",
        "filter-directions-wrong-type",
        "missing-project-path",
        "missing-session-id",
        "empty-body",
        "body-is-an-array",
    ],
)
async def test_a_trace_summary_request_that_fails_framework_validation(
    payload: object,
) -> None:
    async with _client() as client:
        response = await client.post("/trace/summary", json=payload)

    assert_request_validation_envelope(response.status_code, response.json())


@pytest.mark.parametrize(
    "content",
    [b"{not json", b"", b'{"project_path": }', b"'single-quoted'"],
    ids=["truncated-object", "empty-body", "missing-value", "single-quoted"],
)
async def test_a_malformed_json_body(content: bytes) -> None:
    async with _client() as client:
        response = await client.post(
            "/trace/query", content=content, headers={"content-type": "application/json"}
        )

    assert_request_validation_envelope(response.status_code, response.json())


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        ({"project_path": "x", "session_id": SESSION_ID, "limit": "abc"}, "int_parsing"),
        ({"project_path": "x"}, "missing"),
    ],
    ids=["limit-not-an-integer", "missing-session-id"],
)
async def test_a_locator_names_the_offending_field(
    payload: dict[str, object], expected_type: str
) -> None:
    """The location has to be actionable, or the envelope is useless to a caller."""
    async with _client() as client:
        response = await client.post("/trace/query", json=payload)

    issues = response.json()["details"]["errors"]
    assert issues[0]["type"] == expected_type
    assert issues[0]["location"][0] == "body"
    assert issues[0]["location"][-1] in {"limit", "session_id"}


async def test_a_malformed_body_is_located_without_echoing_it() -> None:
    """A JSON parse failure points at a position, never at the received bytes."""
    async with _client() as client:
        response = await client.post(
            "/trace/query",
            content=b'{"project_path": "secret-value"}',
            headers={"content-type": "application/json"},
        )

    body = response.json()
    assert_request_validation_envelope(response.status_code, body)
    assert "secret-value" not in str(body)


@pytest.mark.parametrize(
    "payload",
    [
        {"channel_count": "two"},
        {"channel_count": 2},
        {"rate_hz": "fast"},
        {"batch_size": "many"},
        {"is_fd": "maybe"},
        {"project_path": 5},
        {"recording_path": []},
    ],
    ids=[
        "channel-count-not-a-literal",
        "channel-count-outside-the-literal",
        "rate-not-a-number",
        "batch-size-not-an-integer",
        "is-fd-not-a-boolean",
        "project-path-not-a-string",
        "recording-path-not-a-string",
    ],
)
async def test_a_non_trace_endpoint_shares_the_same_boundary(
    payload: dict[str, object],
) -> None:
    """The boundary is app-level, so no endpoint can drift into its own protocol."""
    async with _client() as client:
        response = await client.post("/capture/start", json=payload)

    assert_request_validation_envelope(response.status_code, response.json())


async def test_a_rejected_capture_request_never_starts_a_capture(tmp_path: Path) -> None:
    service = RuntimeService()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(runtime_service=service)),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/capture/start",
            json={"channel_count": "two", "project_path": str(tmp_path / "absent.canx")},
        )

    assert_request_validation_envelope(response.status_code, response.json())
    assert service.has_session is False
    assert service.capture_active is False
    assert service.data_session_id is None
    assert not (tmp_path / "absent.canx").exists()


async def test_a_rejected_trace_request_never_reaches_the_route_handler(
    tmp_path: Path,
) -> None:
    """A well-shaped request reaches the handler; a malformed one never does.

    The control case is what makes this a proof rather than an assertion: with a
    valid payload and the same absent project the handler runs and reports
    ``project.not_found``, so the 422 on the malformed payload shows the request
    was rejected before any project was opened or queried.
    """
    absent = tmp_path / "absent.canx"
    async with _client() as client:
        reached = await client.post(
            "/trace/query",
            json={"project_path": str(absent), "session_id": SESSION_ID, "limit": 10},
        )
        rejected = await client.post(
            "/trace/query",
            json={"project_path": str(absent), "session_id": SESSION_ID, "limit": "ten"},
        )

    assert reached.status_code == 400
    assert reached.json()["code"] == "project.not_found"
    assert reached.json()["source"] == "project"

    assert_request_validation_envelope(rejected.status_code, rejected.json())
    assert not absent.exists()


async def test_a_rejected_request_leaves_a_valid_project_untouched(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Untouched"):
        pass
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    async with _client() as client:
        rejected = await client.post(
            "/trace/query",
            json={"project_path": str(root), "session_id": SESSION_ID, "limit": "ten"},
        )
        summary = await client.post(
            "/trace/summary",
            json={"project_path": str(root), "session_id": SESSION_ID, "filters": 7},
        )

    assert_request_validation_envelope(rejected.status_code, rejected.json())
    assert_request_validation_envelope(summary.status_code, summary.json())

    after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    assert after == before
    assert DataSessionService(root).list_sessions() == ()


async def test_the_boundary_does_not_swallow_domain_validation(
    tmp_path: Path,
) -> None:
    """Semantic rejection stays a domain 400; the two layers must not merge.

    An id outside the 29-bit space is a *value* problem, not a payload-shape
    problem, so it must keep its domain code and its 400 — different status,
    different source, different fix.
    """
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Domain") as handle:
        writer = DataSessionService(handle.root).start(stream_id="boundary")
        session_id = writer.session_id
        writer.finalize()

    async with _client() as client:
        response = await client.post(
            "/trace/query",
            json={
                "project_path": str(root),
                "session_id": session_id,
                "filters": {"arbitration_id_start": -1},
            },
        )

    assert response.status_code == 400
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == "query.invalid_arbitration_id"
    assert body["source"] == "query"
    assert body["code"] != REQUEST_VALIDATION_CODE
