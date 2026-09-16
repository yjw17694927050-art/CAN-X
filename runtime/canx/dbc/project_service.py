"""Project-owned DBC assets: import, register, list, get and load.

This is the layer that turns "a DBC file on this machine" into "a DBC this
project owns". It is the only DBC module that touches both the filesystem and the
registry, and it keeps neither open between calls: every operation opens the
project, does one thing, and closes.

The commit order is deliberate:

```text
validate the project                            (project domain)
↓  validate + hash the source                   (V0.3-02 import service)
↓  re-read the source and prove it is unchanged (this module)
↓  write the exact bytes through a staging file (this module)
↓  atomically promote staging → dbc/<asset_id>.dbc
↓  insert the registry row                      (dbc repository)
```

A registry row can therefore never point at a file that was never written. The
reverse — a written file whose row was not committed, after a crash between the
last two steps — is possible, and is recorded as a known limitation rather than
papered over. Files under ``dbc/`` that are not in the registry are not trusted
and are never adopted automatically: registration is what makes a file an asset.

Nothing here knows about ``cantools``: parsing goes through
:class:`~canx.dbc.service.DbcImportService`, the one boundary that does.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from canx.dbc import repository
from canx.dbc.asset import DbcAsset, asset_relative_path, resolve_asset_path
from canx.dbc.errors import (
    DbcAssetIntegrityError,
    DbcAssetNotFoundError,
    DbcAssetStorageError,
    DbcError,
    DbcSourceChangedError,
)
from canx.dbc.model import DbcDocument, DbcSource
from canx.dbc.service import DbcImportService
from canx.project.service import ProjectService

#: Prefix of the staging file a new asset is written through.
STAGING_PREFIX = ".tmp-"


class ProjectDbcService:
    """Stateless entry point for the DBC assets one project owns.

    The service holds no connection between calls, mirroring
    :class:`~canx.data.session.DataSessionService`: the project identity is
    validated through the project domain, and the registry is read or written
    through its own short-lived connection.
    """

    def __init__(self, project_root: Path, *, importer: DbcImportService | None = None) -> None:
        self._root = Path(project_root)
        self._importer = DbcImportService() if importer is None else importer

    @property
    def project_root(self) -> Path:
        """Return the project root this service reads and writes."""
        return self._root

    def import_asset(self, source: str | Path, *, encoding: str | None = None) -> DbcAsset:
        """Import one external DBC file as a project-owned, immutable asset.

        The source is validated by the V0.3-02 import service, re-read afterwards
        to prove it did not change in between, and then persisted **byte for
        byte**: the project-owned copy is the exact file that was validated, not a
        re-formatted rendering of the parsed model.

        Args:
            source: Path to the external DBC file. It is only ever read.
            encoding: Codec to decode the source with. ``None`` selects the
                declared default; the encoding actually used is recorded.

        Returns:
            The registered asset: its stable id, the project that owns it, the
            project-relative copy path, and the provenance of the source.

        Raises:
            ProjectError: If the project root is not a readable CAN-X project.
            DbcFileNotFoundError: If ``source`` is not a regular readable file.
            DbcUnsupportedFormatError: If ``source`` is not a ``.dbc`` file.
            DbcReadError: If the source exists but cannot be read.
            DbcDecodeError: If the bytes are not text under the declared encoding.
            DbcParseError: If the text is not a well-formed DBC document.
            DbcModelError: If the document cannot satisfy a CAN-X invariant.
            DbcSourceChangedError: If the source changed between validation and
                persistence; nothing is written or registered.
            DbcAssetStorageError: If the project-owned copy could not be written.
            DbcAssetRegistryError: If the registry row could not be committed; the
                newly written file is removed on a best-effort basis.
        """
        project_id = self._require_project_id()
        document = self._importer.import_file(source, encoding=encoding)
        raw = _reread_verified(source, document.source)
        asset_id = str(uuid4())
        relative_path = asset_relative_path(asset_id)
        target = self._root / PurePosixPath(relative_path)
        _stage_asset(target, raw)
        asset = DbcAsset(
            asset_id=asset_id,
            project_id=project_id,
            source_name=document.source.name,
            relative_path=relative_path,
            sha256=document.source.sha256,
            size_bytes=document.source.size_bytes,
            encoding=document.source.encoding,
            imported_at=datetime.now(UTC),
        )
        try:
            with repository.asset_connection(self._root) as connection:
                repository.insert_asset(connection, asset)
        except DbcError:
            # The file was written but the registry refused it. An unregistered
            # file is not an asset, so it is removed rather than left behind for
            # something to adopt later. If the removal fails the asset simply
            # stays unregistered, which is the documented crash state.
            _remove_quietly(target)
            raise
        return asset

    def list_assets(self) -> tuple[DbcAsset, ...]:
        """Return this project's registered assets in deterministic order.

        Only registry rows are returned. A ``.dbc`` file that was copied into
        ``dbc/`` by hand is not listed, and is never adopted implicitly.

        Raises:
            ProjectError: If the project root is not a readable CAN-X project.
            DbcAssetRegistryError: If the registry could not be read.
        """
        project_id = self._require_project_id()
        with repository.asset_connection(self._root) as connection:
            return repository.list_assets(connection, project_id)

    def get_asset(self, asset_id: str) -> DbcAsset:
        """Return one registered asset of this project.

        Raises:
            ProjectError: If the project root is not a readable CAN-X project.
            DbcAssetNotFoundError: If ``asset_id`` is not registered here. An
                asset registered in a different project reports the same failure:
                from this project's point of view it does not exist.
            DbcAssetIntegrityError: If the row claims a different project.
            DbcAssetRegistryError: If the registry could not be read.
        """
        normalized = _normalized_asset_id(asset_id)
        project_id = self._require_project_id()
        with repository.asset_connection(self._root) as connection:
            asset = repository.read_asset(connection, normalized)
        if asset is None:
            raise DbcAssetNotFoundError(
                "The DBC asset is not registered in this project.",
                details={"asset_id": normalized},
            )
        if asset.project_id != project_id:
            raise DbcAssetIntegrityError(
                "The registered DBC asset belongs to a different project.",
                details={"asset_id": normalized, "asset_project_id": asset.project_id},
            )
        return asset

    def load_asset(self, asset_id: str) -> DbcDocument:
        """Load one registered asset into a canonical DBC document.

        Integrity is established *before* the document is parsed, so a tampered or
        missing file is reported as an asset-integrity failure rather than as a
        confusing parser error:

        ```text
        resolve the stored path inside dbc/  →  prove the file exists
        →  prove the size  →  prove the SHA-256
        →  decode with the registered encoding  →  parse
        ```

        The returned provenance names the original import (its base name, size,
        digest and encoding) and carries **no path**: the durable source of this
        document is the project-owned copy, not the machine it was imported from.

        Raises:
            ProjectError: If the project root is not a readable CAN-X project.
            DbcAssetNotFoundError: If the asset is not registered in this project.
            DbcAssetIntegrityError: If the registered file is missing, cannot be
                read, or no longer matches the registered size or digest.
            DbcAssetRegistryError: If the registry could not be read.
        """
        asset = self.get_asset(asset_id)
        raw = _read_asset_bytes(resolve_asset_path(self._root, asset.relative_path), asset)
        try:
            return self._importer.load_bytes(
                raw, source_name=asset.source_name, encoding=asset.encoding
            )
        except DbcError as error:
            # These bytes hash to what was registered, so they are the very bytes
            # that parsed when the asset was imported. A failure now means the row
            # and the file no longer tell the same story, never that the document
            # is malformed.
            raise DbcAssetIntegrityError(
                "The registered DBC asset does not parse as its registry row describes.",
                details={"asset_id": asset.asset_id, "cause": error.code},
            ) from error

    def _require_project_id(self) -> str:
        """Validate the project through the project domain and return its identity.

        The project domain decides what a CAN-X project is, so a rejected root
        keeps its ``ProjectError`` with ``source = "project"``: the fact is that
        the project itself is unusable, not that a DBC operation misbehaved.

        Raises:
            ProjectError: If the project root is not a readable CAN-X project.
        """
        with ProjectService().open(self._root) as handle:
            return handle.project_id


def _reread_verified(source: str | Path, validated: DbcSource) -> bytes:
    """Re-read the source and prove it still hashes to what was validated.

    The import service reads and validates one snapshot of the file. Persisting a
    *second* read without comparing them is the time-of-check/time-of-use hole
    this function closes: the bytes returned here are the bytes that hash to
    ``validated.sha256``, and they are the bytes that get written.

    Raises:
        DbcSourceChangedError: If the file can no longer be read, or no longer
            matches the size and digest that were validated.
    """
    path = Path(source)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise DbcSourceChangedError(
            "The DBC source could not be read again for persistence.",
            details={"source_name": validated.name, "reason": type(error).__name__},
        ) from error
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) != validated.size_bytes or digest != validated.sha256:
        raise DbcSourceChangedError(
            "The DBC source changed between validation and persistence.",
            details={
                "source_name": validated.name,
                "expected_size_bytes": validated.size_bytes,
                "actual_size_bytes": len(raw),
                "expected_sha256": validated.sha256,
                "actual_sha256": digest,
            },
        )
    return raw


def _stage_asset(target: Path, raw: bytes) -> None:
    """Write ``raw`` to ``target`` without ever exposing a partial asset file.

    The bytes go to a staging name, are flushed and fsynced, and only then replace
    the final name atomically. A failure removes the staging file, so neither a
    registry row nor a plausibly named half-written ``.dbc`` can be left behind.
    A directory fsync is not available on Windows, so the file's own fsync plus
    the atomic rename is the strongest guarantee the platform offers here.

    Raises:
        DbcAssetStorageError: If the copy could not be written or promoted.
    """
    staging = target.with_name(f"{STAGING_PREFIX}{target.name}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with staging.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        _remove_quietly(staging)
        raise DbcAssetStorageError(
            "The project-owned DBC copy could not be written.",
            details={"asset_name": target.name, "reason": type(error).__name__},
        ) from error
    try:
        os.replace(staging, target)
    except OSError as error:
        _remove_quietly(staging)
        raise DbcAssetStorageError(
            "The staged DBC copy could not be promoted to its asset name.",
            details={"asset_name": target.name, "reason": type(error).__name__},
        ) from error


def _read_asset_bytes(path: Path, asset: DbcAsset) -> bytes:
    """Read a project-owned asset and prove it is still the registered bytes.

    Raises:
        DbcAssetIntegrityError: If the file is missing, unreadable, or no longer
            matches the registered size or digest.
    """
    if not path.is_file():
        raise DbcAssetIntegrityError(
            "The registered DBC asset file is missing.",
            details={"asset_id": asset.asset_id, "relative_path": asset.relative_path},
        )
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise DbcAssetIntegrityError(
            "The registered DBC asset file could not be read.",
            details={"asset_id": asset.asset_id, "reason": type(error).__name__},
        ) from error
    if len(raw) != asset.size_bytes:
        raise DbcAssetIntegrityError(
            "The registered DBC asset file no longer has its registered size.",
            details={
                "asset_id": asset.asset_id,
                "expected_size_bytes": asset.size_bytes,
                "actual_size_bytes": len(raw),
            },
        )
    digest = hashlib.sha256(raw).hexdigest()
    if digest != asset.sha256:
        raise DbcAssetIntegrityError(
            "The registered DBC asset file no longer has its registered digest.",
            details={
                "asset_id": asset.asset_id,
                "expected_sha256": asset.sha256,
                "actual_sha256": digest,
            },
        )
    return raw


def _normalized_asset_id(asset_id: str) -> str:
    """Return the canonical UUID form of ``asset_id``.

    Raises:
        DbcAssetNotFoundError: If ``asset_id`` is not a UUID. A registered asset
            always has one, so "not registered" is the honest answer for a lookup
            key that could never identify an asset — not a validation complaint
            about the caller's string.
    """
    try:
        return str(UUID(asset_id))
    except (ValueError, AttributeError, TypeError) as error:
        raise DbcAssetNotFoundError(
            "The DBC asset is not registered in this project.",
            details={"asset_id": repr(asset_id)},
        ) from error


def _remove_quietly(path: Path) -> None:
    """Best-effort removal for a failure path where the original error must win."""
    with suppress(OSError):
        path.unlink()
