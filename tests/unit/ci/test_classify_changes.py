"""Unit tests for the CI-03 change classifier.

These tests pin the routing decisions the tiered quality gate depends on. They
are deliberately written against the *public* classifier surface
(:func:`tools.ci.classify_changes.classify_paths`,
:func:`tools.ci.classify_changes.classify_event`) so that the workflow's job
conditions are consequences of a tested function rather than of YAML path
expressions.
"""

from __future__ import annotations

from tools.ci.classify_changes import (
    DOCS_ONLY,
    FULL,
    NO_CHANGES,
    Classification,
    classify_event,
    classify_paths,
)

# --------------------------------------------------------------------------
# Tier A — ordinary documentation
# --------------------------------------------------------------------------


def test_docs_acceptance_report_is_docs_only() -> None:
    result = classify_paths(["docs/acceptance/example.md"])

    assert result.runtime_required is False
    assert result.frontend_required is False
    assert result.rust_required is False
    assert result.full_required is False
    assert result.classification == DOCS_ONLY


def test_project_state_compaction_pages_are_docs_only() -> None:
    result = classify_paths(
        [
            "docs/PROJECT_STATE.md",
            "docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_V0.3-09.md",
            "README.md",
        ]
    )

    assert result.classification == DOCS_ONLY
    assert result.runtime_required is False
    assert result.frontend_required is False
    assert result.rust_required is False


def test_empty_diff_is_reported_as_no_changes() -> None:
    result = classify_paths([])

    assert result.classification == NO_CHANGES
    assert result.runtime_required is False
    assert result.frontend_required is False
    assert result.rust_required is False


# --------------------------------------------------------------------------
# Tier C — Runtime only, and its shared-contract escalation
# --------------------------------------------------------------------------


def test_runtime_implementation_requires_only_the_python_job() -> None:
    result = classify_paths(["runtime/canx/recorder/project_recorder.py"])

    assert result.runtime_required is True
    assert result.frontend_required is False
    assert result.rust_required is False
    assert result.full_required is False


def test_python_tests_require_the_python_job() -> None:
    result = classify_paths(["tests/unit/recorder/test_project_recorder.py"])

    assert result.runtime_required is True
    assert result.frontend_required is False
    assert result.rust_required is False


def test_runtime_http_api_is_a_shared_contract() -> None:
    """A Runtime API change can break a frontend contract that Python tests cannot see."""

    result = classify_paths(["runtime/canx/api/project.py"])

    assert result.runtime_required is True
    assert result.frontend_required is True
    assert result.classification == "runtime+frontend"


def test_realtime_transport_is_a_shared_contract() -> None:
    result = classify_paths(["runtime/canx/transport/messagepack.py"])

    assert result.runtime_required is True
    assert result.frontend_required is True


def test_runtime_control_api_requires_rust() -> None:
    """`runtime/canx/api/app.py` defines the Rust-consumed control contract.

    The desktop sidecar (`apps/desktop/src-tauri/src/runtime_sidecar.rs`) calls
    `GET /health` — asserting `service == "canx-runtime"` and `schema_version == 1`
    — and `POST /runtime/shutdown`, asserting HTTP 202. Both routes and the models
    they answer with are defined by the FastAPI application factory in this file,
    so it has three real consumers: Python, TypeScript and Rust.
    """

    result = classify_paths(["runtime/canx/api/app.py"])

    assert result.runtime_required is True
    assert result.frontend_required is True
    assert result.rust_required is True
    assert result.classification == "runtime+frontend+rust"


def test_other_runtime_api_routers_stay_runtime_plus_frontend() -> None:
    """Only the control-plane factory carries the Rust-consumed contract; the
    other API routers remain a Python + frontend contract."""

    result = classify_paths(["runtime/canx/api/project.py"])

    assert result.runtime_required is True
    assert result.frontend_required is True
    assert result.rust_required is False


# --------------------------------------------------------------------------
# Tier B / Tier D — Frontend and Rust
# --------------------------------------------------------------------------


def test_frontend_component_requires_only_the_frontend_job() -> None:
    result = classify_paths(["apps/desktop/src/components/dbc/DbcWorkspace.tsx"])

    assert result.frontend_required is True
    assert result.runtime_required is False
    assert result.rust_required is False


def test_tauri_rust_source_requires_the_frontend_job_too() -> None:
    """Tauri IPC is a Rust + TypeScript boundary, not a Rust-internal one.

    The renderer invokes these commands by exact command name, and the frontend's
    own drift test reads these Rust sources — so a change here must run the
    frontend job, or the cross-language contract test would be skipped.
    """

    for path in (
        "apps/desktop/src-tauri/src/main.rs",
        "apps/desktop/src-tauri/src/lib.rs",
        "apps/desktop/src-tauri/src/dbc_file_bridge.rs",
        "apps/desktop/src-tauri/src/runtime_sidecar.rs",
    ):
        result = classify_paths([path])
        assert result.rust_required is True, path
        assert result.frontend_required is True, path
        assert result.runtime_required is False, path


def test_tauri_rust_tests_icons_and_packaging_stay_rust_only() -> None:
    """Rust's own integration tests, icons and packaging resources have no
    TypeScript consumer, so they do not drag the frontend job in."""

    for path in (
        "apps/desktop/src-tauri/tests/runtime_sidecar.rs",
        "apps/desktop/src-tauri/icons/icon.ico",
        "apps/desktop/src-tauri/tauri.conf.json",
    ):
        result = classify_paths([path])
        assert result.rust_required is True, path
        assert result.frontend_required is False, path
        assert result.runtime_required is False, path


def test_tauri_ipc_frontend_bridges_require_rust() -> None:
    """The TypeScript IPC bridges invoke Rust commands, so changing one can break
    the Rust side of the same contract and must run the Rust job."""

    for path in (
        "apps/desktop/src/desktop/dbc-file-bridge.ts",
        "apps/desktop/src/desktop/dbc-file-bridge.test.ts",
        "apps/desktop/src/runtime/runtime-client.ts",
        "apps/desktop/src/smoke/dbc-dialog-smoke.ts",
    ):
        result = classify_paths([path])
        assert result.frontend_required is True, path
        assert result.rust_required is True, path
        assert result.runtime_required is False, path


def test_runtime_http_clients_are_not_escalated_to_rust() -> None:
    """Only the Tauri IPC bridges require Rust. The HTTP Runtime clients do not:
    they speak HTTP to the Python runtime and have no Rust consumer."""

    for path in (
        "apps/desktop/src/runtime/capture-client.ts",
        "apps/desktop/src/runtime/dbc-client.ts",
        "apps/desktop/src/runtime/realtime-stream.ts",
    ):
        result = classify_paths([path])
        assert result.frontend_required is True, path
        assert result.rust_required is False, path


def test_frontend_source_prefix_does_not_swallow_src_tauri() -> None:
    """`apps/desktop/src/` and `apps/desktop/src-tauri/` are different surfaces."""

    frontend = classify_paths(["apps/desktop/src/App.tsx"])
    rust = classify_paths(["apps/desktop/src-tauri/tauri.conf.json"])

    assert frontend.frontend_required is True and frontend.rust_required is False
    assert rust.rust_required is True and rust.frontend_required is False


# --------------------------------------------------------------------------
# Tier E — Agent tooling
# --------------------------------------------------------------------------


def test_agent_tooling_requires_the_python_job() -> None:
    result = classify_paths(["tools/agent/validation.py", ".agent/prompts/sub-agent.prompt.md"])

    assert result.runtime_required is True
    assert result.frontend_required is False
    assert result.rust_required is False
    assert result.full_required is False


def test_agent_schemas_force_full_ci() -> None:
    result = classify_paths([".agent/schemas/task.schema.json"])

    assert result.full_required is True
    assert result.classification == FULL


def test_agent_config_forces_full_ci() -> None:
    result = classify_paths([".agent/config.json"])

    assert result.full_required is True


# --------------------------------------------------------------------------
# Tier G — critical authorities force FULL CI
# --------------------------------------------------------------------------


def test_safety_implementation_forces_full_ci() -> None:
    result = classify_paths(["runtime/canx/safety/kernel.py"])

    assert result.full_required is True
    assert result.classification == FULL
    assert result.runtime_required is True
    assert result.all_required is True


def test_safety_architecture_authority_forces_full_ci() -> None:
    result = classify_paths(["docs/architecture/SAFETY_ARCHITECTURE.md"])

    assert result.full_required is True


def test_ci_workflow_itself_forces_full_ci() -> None:
    result = classify_paths([".github/workflows/ci.yml"])

    assert result.full_required is True
    assert result.classification == FULL


def test_ci_tooling_forces_full_ci() -> None:
    result = classify_paths(["tools/ci/classify_changes.py"])

    assert result.full_required is True


def test_build_scripts_force_full_ci() -> None:
    result = classify_paths(["scripts/build-runtime.cmd"])

    assert result.full_required is True


def test_dependency_manifests_force_full_ci() -> None:
    for path in (
        "pyproject.toml",
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "apps/desktop/src-tauri/Cargo.toml",
        "apps/desktop/src-tauri/Cargo.lock",
    ):
        result = classify_paths([path])
        assert result.full_required is True, path


def test_project_authorities_force_full_ci() -> None:
    for path in ("AGENTS.md", "PRD.md", "SPEC.md"):
        result = classify_paths([path])
        assert result.full_required is True, path


def test_agent_shared_truth_documents_force_full_ci() -> None:
    for path in (
        "docs/engineering/INTEGRATION_POLICY.md",
        "docs/engineering/MULTI_AGENT_PROTOCOL.md",
    ):
        result = classify_paths([path])
        assert result.full_required is True, path


def test_ordinary_engineering_doc_does_not_force_full_ci() -> None:
    """Only the named shared-truth engineering documents are critical."""

    result = classify_paths(["docs/engineering/CI_TIERED_QUALITY_GATE.md"])

    assert result.full_required is False
    assert result.classification == DOCS_ONLY


# --------------------------------------------------------------------------
# Fail closed
# --------------------------------------------------------------------------


def test_unknown_top_level_path_forces_full_ci() -> None:
    result = classify_paths(["newthing/config.yaml"])

    assert result.full_required is True
    assert "newthing/config.yaml" in result.unmatched


def test_unknown_root_file_forces_full_ci() -> None:
    result = classify_paths(["build.rs"])

    assert result.full_required is True


def test_one_unknown_path_escalates_the_whole_change_set() -> None:
    result = classify_paths(["docs/readme.md", "mystery.bin"])

    assert result.full_required is True


# --------------------------------------------------------------------------
# Unions
# --------------------------------------------------------------------------


def test_frontend_plus_runtime_is_the_union() -> None:
    result = classify_paths(
        ["apps/desktop/src/App.tsx", "runtime/canx/recorder/project_recorder.py"]
    )

    assert result.runtime_required is True
    assert result.frontend_required is True
    assert result.rust_required is False
    assert result.classification == "runtime+frontend"


def test_safety_plus_docs_escalates_to_full() -> None:
    result = classify_paths(["runtime/canx/safety/kernel.py", "docs/notes.md"])

    assert result.full_required is True
    assert result.all_required is True


def test_rust_plus_frontend_is_the_union() -> None:
    result = classify_paths(
        ["apps/desktop/src-tauri/src/lib.rs", "apps/desktop/src/App.tsx"]
    )

    assert result.rust_required is True
    assert result.frontend_required is True
    assert result.runtime_required is False


# --------------------------------------------------------------------------
# Event resolution — workflow_dispatch and undeterminable diffs
# --------------------------------------------------------------------------


def test_workflow_dispatch_forces_full_ci_without_needing_a_diff(repo_root: str) -> None:
    result = classify_event("workflow_dispatch", repo=repo_root, base="", head="")

    assert result.full_required is True
    assert result.classification == FULL
    assert result.all_required is True


def test_push_with_an_all_zero_before_sha_forces_full_ci(repo_root: str) -> None:
    result = classify_event(
        "push",
        repo=repo_root,
        base="0" * 40,
        head="0" * 40,
    )

    assert result.full_required is True


def test_undeterminable_diff_forces_full_ci(repo_root: str) -> None:
    result = classify_event(
        "pull_request",
        repo=repo_root,
        base="deadbeef" * 5,
        head="deadbeef" * 5,
    )

    assert result.full_required is True
    assert "reason" in result.as_dict()


def test_push_with_a_missing_before_sha_forces_full_ci(repo_root: str) -> None:
    result = classify_event("push", repo=repo_root, base="", head="")

    assert result.full_required is True


def test_classification_is_json_serialisable_and_stable() -> None:
    payload = classify_paths(["runtime/canx/recorder/project_recorder.py"]).as_dict()

    assert payload["runtime_required"] is True
    assert payload["frontend_required"] is False
    assert payload["rust_required"] is False
    assert payload["full_required"] is False
    assert isinstance(payload["classification"], str)
    assert isinstance(payload["reason"], str)


def test_classification_is_immutable() -> None:
    result = classify_paths(["docs/a.md"])
    assert isinstance(result, Classification)

    try:
        result.runtime_required = True  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("Classification must be immutable")
