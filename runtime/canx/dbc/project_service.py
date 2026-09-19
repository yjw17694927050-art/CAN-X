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

Two import entry points exist, and they differ only in where the bytes and their
provenance come from:

```text
import_asset(source)          an external file path: validated, re-read and
                              compared, so the bytes written are the bytes that
                              were validated (the TOCTOU gate)

import_asset_bytes(raw, …)    content a trusted caller already holds: the bytes
                              it hands over are the bytes written, and there is
                              nothing to re-read because no second read of
                              anything ever happened
```

Below those two lines the persistence body is **one** function. Duplicating the
staging file, the atomic promotion, the registry insert and the cleanup-on-failure
path would mean two chances for that order to drift — and the invariant the order
exists to produce, that a registry row can never point at a file that was never
written, is the whole point of this module.

Neither entry point takes a location from a caller that is not trusted with the
filesystem: ``import_asset`` reads a path the host process was given, and
``import_asset_bytes`` never consults the filesystem for its content at all.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from canx.dbc import repository
from canx.dbc.asset import DbcAsset, asset_relative_path, resolve_asset_path
from canx.dbc.decode import DbcDecoder
from canx.dbc.decoder_cache import DECODER_CACHE, DecoderKey
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

        This is the path-based entry point, for a caller the host already trusts
        with a filesystem location — a desktop filesystem bridge, or the test
        suite. A caller that holds the bytes instead submits them to
        :meth:`import_asset_bytes`, which never opens a path at all.


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
            DbcAssetIntegrityError: If the project's ``dbc`` directory no longer
                resolves inside the project, or is missing: there is then no safe
                place to write the copy, and nothing is written or registered.
            DbcAssetStorageError: If the project-owned copy could not be written.
            DbcAssetRegistryError: If the registry row could not be committed; the
                newly written file is removed on a best-effort basis.
        """
        project_id = self._require_project_id()
        document = self._importer.import_file(source, encoding=encoding)
        raw = _reread_verified(source, document.source)
        return self._persist_imported_document(project_id, document, raw)

    def import_asset_bytes(
        self, raw: bytes, *, source_name: str, encoding: str | None = None
    ) -> DbcAsset:
        """Import DBC bytes a trusted caller already holds as a project-owned asset.

        The content-based sibling of :meth:`import_asset`, and the entry point the
        Runtime control plane uses: a caller that has the bytes submits them, and
        the Runtime never learns a location it would otherwise have to open. The
        bytes are persisted **exactly as submitted**, so the digest, the size and
        the byte-for-byte content of the project-owned copy are the ones the caller
        can verify against what it sent.

        There is no second filesystem read here, and none is invented: the caller's
        bytes are already an immutable snapshot, so the TOCTOU gate that belongs to
        a *path* — validate, re-read, compare — has nothing to protect against.

        Args:
            raw: The exact DBC bytes to import.
            source_name: The file name being claimed for this content. Provenance
                only: a plain ``.dbc`` file name, never a path.
            encoding: Codec to decode the bytes with. ``None`` selects the declared
                default; the encoding actually used is recorded.

        Returns:
            The registered asset: its stable id, the project that owns it, the
            project-relative copy path, and the provenance of the content.

        Raises:
            ProjectError: If the project root is not a readable CAN-X project.
            DbcUnsupportedFormatError: If ``source_name`` is not a plain, non-blank
                ``.dbc`` file name; nothing is written or registered.
            DbcDecodeError: If the bytes are not text under the declared encoding.
            DbcParseError: If the text is not a well-formed DBC document.
            DbcModelError: If the document cannot satisfy a CAN-X invariant.
            DbcAssetIntegrityError: If the project's ``dbc`` directory no longer
                resolves inside the project, or is missing: there is then no safe
                place to write the copy, and nothing is written or registered.
            DbcAssetStorageError: If the project-owned copy could not be written.
            DbcAssetRegistryError: If the registry row could not be committed; the
                newly written file is removed on a best-effort basis.
        """
        project_id = self._require_project_id()
        document = self._importer.import_bytes(
            raw, source_name=source_name, encoding=encoding
        )
        return self._persist_imported_document(project_id, document, raw)

    def _persist_imported_document(
        self, project_id: str, document: DbcDocument, raw: bytes
    ) -> DbcAsset:
        """Write one validated document's exact bytes and register it.

        The single persistence body both import entry points end in. ``raw`` must
        be the very bytes ``document`` describes — the path-based caller proves it
        with :func:`_reread_verified`, and the content-based caller supplies it
        directly — because this function writes ``raw`` and records
        ``document.source``'s digest for it.

        The commit order is the module's contract:

        ```text
        derive the asset identity and its canonical relative path
        ↓  resolve the target through the same gate a load uses
        ↓  write the exact bytes through a staging file (flush + fsync)
        ↓  atomically promote staging → dbc/<asset_id>.dbc
        ↓  insert the registry row
        ```

        Raises:
            DbcAssetIntegrityError: If the path escapes the project or its ``dbc``
                directory, or that directory is missing.
            DbcAssetStorageError: If the copy could not be written or promoted.
            DbcAssetRegistryError: If the registry row could not be committed.
        """
        asset_id = str(uuid4())
        asset = DbcAsset(
            asset_id=asset_id,
            project_id=project_id,
            source_name=document.source.name,
            relative_path=asset_relative_path(asset_id),
            sha256=document.source.sha256,
            size_bytes=document.source.size_bytes,
            encoding=document.source.encoding,
            imported_at=datetime.now(UTC),
        )
        # The write target comes out of the same gate a load uses. Reaching for
        # ``project_root / relative_path`` here would bypass containment entirely,
        # and ``<project>/dbc`` may already be a link pointing somewhere else —
        # the escape would happen on write, before any registry row could exist.
        target = resolve_asset_path(self._root, asset)
        _stage_asset(target, raw)
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
        asset, raw = self.read_verified_asset(asset_id)
        return self._parse_verified_asset(asset, raw)

    def read_verified_asset(self, asset_id: str) -> tuple[DbcAsset, bytes]:
        """Open the project, resolve one asset and prove its bytes are unchanged.

        The integrity half of :meth:`load_asset`, with the parse left out. It exists
        for callers that want a decoder rather than a document, where the document's
        *content* is what :mod:`canx.dbc.decoder_cache` may reuse — so the parse is
        allowed to happen once per verified content identity instead of once per
        request.

        The order is the reason this is a separate step rather than a cached whole:

        ```text
        registry row  →  resolve the stored path inside dbc/  →  read the bytes
        →  prove the size  →  prove the SHA-256
        ```

        Nothing here is cached, and nothing here may be: a file that no longer
        matches its row fails *here*, before any cache is consulted, so a tampered
        asset can neither populate nor hit an entry.

        Args:
            asset_id: The asset to read.

        Returns:
            The registry row and the exact bytes it was proven to describe.

        Raises:
            ProjectError: If the project root is not a readable CAN-X project.
            DbcAssetNotFoundError: If the asset is not registered in this project.
            DbcAssetIntegrityError: If the registered file is missing, cannot be
                read, or no longer matches the registered size or digest.
            DbcAssetRegistryError: If the registry could not be read.
        """
        asset = self.get_asset(asset_id)
        raw = _read_asset_bytes(resolve_asset_path(self._root, asset), asset)
        return asset, raw

    def load_decoder(self, asset_id: str) -> DbcDecoder:
        """Return a compiled decoder for one asset, reusing a verified-content cache.

        The decode path's entry point. Every call performs the same integrity proof
        as :meth:`load_asset` — the project is opened, the registry row is read, the
        file is hashed — and the decoder is compiled only when the *verified* content
        identity has not been seen before. A parse and a compile are pure functions
        of the bytes and their encoding, and the bytes are proven to be the row's, so
        a hit returns exactly what a miss would have built.

        The key carries the project identity as well as the content identity, so the
        same bytes owned by two projects are two entries and no decoder is reached
        across a project boundary. See :mod:`canx.dbc.decoder_cache` for the cache's
        own contract.

        Args:
            asset_id: The asset to decode against.

        Returns:
            A compiled, read-only decoder for the asset's canonical database.

        Raises:
            ProjectError: If the project root is not a readable CAN-X project.
            DbcAssetNotFoundError: If the asset is not registered in this project.
            DbcAssetIntegrityError: If the registered file is missing, cannot be
                read, no longer matches the registered size or digest, or does not
                parse as its registry row describes.
            DbcAssetRegistryError: If the registry could not be read.
        """
        asset, raw = self.read_verified_asset(asset_id)
        key = DecoderKey(
            project_id=asset.project_id,
            sha256=asset.sha256,
            size_bytes=asset.size_bytes,
            encoding=asset.encoding,
        )
        cached = DECODER_CACHE.get(key)
        if cached is not None:
            return cached
        decoder = DbcDecoder(self._parse_verified_asset(asset, raw).database)
        DECODER_CACHE.put(key, decoder)
        return decoder

    def _parse_verified_asset(self, asset: DbcAsset, raw: bytes) -> DbcDocument:
        """Parse bytes already proven to match ``asset``'s registry row.

        A failure here is never "the document is malformed": these bytes hash to what
        was registered, so they are the very bytes that parsed when the asset was
        imported, and the failure means the row and the file no longer tell the same
        story.

        Raises:
            DbcAssetIntegrityError: If the verified bytes do not parse.
        """
        try:
            return self._importer.load_bytes(
                raw, source_name=asset.source_name, encoding=asset.encoding
            )
        except DbcError as error:
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

    ``target`` has already been resolved by :func:`resolve_asset_path`, which
    proves both its identity and that its directory is an existing, in-project
    ``dbc`` directory. This function therefore never creates a directory: a
    missing or redirected ``dbc`` is a tampered project layout, and recreating it
    here would hide exactly the condition the caller just checked.

    The staging file is derived from the verified ``target``, so it lands in the
    same proven directory as the file it becomes.

    Raises:
        DbcAssetStorageError: If the copy could not be written or promoted.
    """
    staging = target.with_name(f"{STAGING_PREFIX}{target.name}")
    try:
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
