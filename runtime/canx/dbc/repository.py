"""SQLite persistence for the project DBC asset registry (internal).

Raw SQLite stays behind this module: every function returns typed models, and no
``sqlite3.Connection``, ``Cursor`` or ``Row`` is ever handed to a caller. Each
public function opens its own short-lived connection through
:func:`asset_connection`, so a registry operation can never leak a Windows file
handle into the project directory.

The table belongs to :mod:`canx.project.storage`: the project layer owns table
existence and the schema version, and this module owns only the rows — the same
division the data domain uses for ``data_sessions`` and ``data_segments``.

Nothing here knows about ``cantools`` or parses a DBC. The registry stores asset
metadata; the DBC content lives in the project-owned ``.dbc`` file.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from canx.dbc.asset import DbcAsset
from canx.dbc.errors import (
    DbcAssetIntegrityError,
    DbcAssetRegistryError,
    DbcAssetValidationError,
)
from canx.project.errors import ProjectError
from canx.project.storage import (
    DATABASE_FILENAME,
    close_quietly,
    open_database,
    read_metadata,
)

ASSETS_TABLE = "dbc_assets"

_REQUIRED_TABLES = (ASSETS_TABLE,)

_ASSET_COLUMNS = (
    "asset_id, project_id, source_name, relative_path, sha256, size_bytes, encoding, imported_at"
)

_INSERT_ASSET = (
    f"INSERT INTO {ASSETS_TABLE} ({_ASSET_COLUMNS})"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


@contextmanager
def asset_connection(project_root: Path) -> Iterator[sqlite3.Connection]:
    """Yield a short-lived connection to the project database.

    The connection is validated against the project schema, has foreign keys
    enabled, and is always closed before the context exits.

    Raises:
        DbcAssetRegistryError: If the project database is missing, unreadable, of
            an unsupported schema, or missing the DBC asset registry.
    """
    database_path = project_root / DATABASE_FILENAME
    if not database_path.is_file():
        raise DbcAssetRegistryError(
            "The project database is missing.",
            code="dbc.project_database_missing",
            details={"path": str(database_path)},
        )
    try:
        connection = open_database(database_path)
    except ProjectError as error:
        raise DbcAssetRegistryError(
            "The project database is not available for the DBC registry.",
            code="dbc.project_database_unavailable",
            details={"path": str(database_path), "project_error_code": error.code},
        ) from error
    try:
        _require_tables(connection, database_path)
        yield connection
    finally:
        close_quietly(connection)


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a unit of work in one explicit SQLite transaction.

    The data domain's repository keeps its own copy of this helper: the two
    domains must not depend on each other, and an explicit transaction is nine
    lines of SQLite control rather than a shared domain concept.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        with suppress(sqlite3.Error):
            connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def read_project_id(connection: sqlite3.Connection) -> str:
    """Return the identity stored in the project metadata row.

    Raises:
        DbcAssetRegistryError: If the identity cannot be read.
    """
    try:
        return read_metadata(connection).project_id
    except ProjectError as error:
        raise DbcAssetRegistryError(
            "The project identity could not be read for the DBC registry.",
            details={"project_error_code": error.code},
        ) from error


def insert_asset(connection: sqlite3.Connection, asset: DbcAsset) -> None:
    """Register one project-owned DBC asset.

    Raises:
        DbcAssetRegistryError: If the row could not be committed.
    """
    with _registry_errors(asset_id=asset.asset_id), transaction(connection):
        connection.execute(_INSERT_ASSET, _asset_values(asset))


def read_asset(connection: sqlite3.Connection, asset_id: str) -> DbcAsset | None:
    """Return one registered asset, or ``None`` when it is not registered."""
    row = _fetch_one(
        connection,
        f"SELECT {_ASSET_COLUMNS} FROM {ASSETS_TABLE} WHERE asset_id = ?",
        (asset_id,),
    )
    return None if row is None else _asset_from_row(row)


def list_assets(connection: sqlite3.Connection, project_id: str) -> tuple[DbcAsset, ...]:
    """Return one project's registered assets in deterministic order.

    Ordered by import instant and then by asset id, so two assets imported within
    the same clock tick still come back in a stable, reproducible order rather
    than whatever order SQLite happens to return. Assets belonging to another
    project are not listed: the registry answers for the project it is asked
    about.
    """
    rows = _fetch_all(
        connection,
        f"SELECT {_ASSET_COLUMNS} FROM {ASSETS_TABLE} WHERE project_id = ?"
        " ORDER BY imported_at ASC, asset_id ASC",
        (project_id,),
    )
    return tuple(_asset_from_row(row) for row in rows)


def _require_tables(connection: sqlite3.Connection, path: Path) -> None:
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        names = {str(row[0]) for row in rows}
    except sqlite3.Error as error:
        raise DbcAssetRegistryError(
            "The project database schema could not be inspected.",
            code="dbc.project_database_unavailable",
            details={"path": str(path)},
        ) from error
    missing = [name for name in _REQUIRED_TABLES if name not in names]
    if missing:
        raise DbcAssetRegistryError(
            "The project database is missing the DBC asset registry table.",
            code="dbc.project_database_unavailable",
            details={"path": str(path), "missing_tables": missing},
        )


@contextmanager
def _registry_errors(*, asset_id: str) -> Iterator[None]:
    try:
        yield
    except sqlite3.Error as error:
        raise DbcAssetRegistryError(
            "The DBC asset registry row could not be committed.",
            details={"asset_id": asset_id, "reason": type(error).__name__},
        ) from error


def _fetch_one(
    connection: sqlite3.Connection, statement: str, parameters: tuple[object, ...]
) -> Sequence[Any] | None:
    try:
        row: Sequence[Any] | None = connection.execute(statement, parameters).fetchone()
    except sqlite3.Error as error:
        raise DbcAssetRegistryError(
            "The DBC asset registry could not be read.",
            details={"reason": type(error).__name__},
        ) from error
    return row


def _fetch_all(
    connection: sqlite3.Connection, statement: str, parameters: tuple[object, ...]
) -> list[Sequence[Any]]:
    try:
        rows = list(connection.execute(statement, parameters).fetchall())
    except sqlite3.Error as error:
        raise DbcAssetRegistryError(
            "The DBC asset registry could not be read.",
            details={"reason": type(error).__name__},
        ) from error
    return rows


def _asset_values(asset: DbcAsset) -> tuple[object, ...]:
    return (
        asset.asset_id,
        asset.project_id,
        asset.source_name,
        asset.relative_path,
        asset.sha256,
        asset.size_bytes,
        asset.encoding,
        asset.imported_at.astimezone(UTC).isoformat(),
    )


def _asset_from_row(row: Sequence[Any]) -> DbcAsset:
    """Rebuild one asset from a stored row, refusing a row that is not an asset."""
    try:
        return DbcAsset(
            asset_id=str(row[0]),
            project_id=str(row[1]),
            source_name=str(row[2]),
            relative_path=str(row[3]),
            sha256=str(row[4]),
            size_bytes=int(row[5]),
            encoding=str(row[6]),
            imported_at=_parse_timestamp(row[7]),
        )
    except DbcAssetValidationError as error:
        raise DbcAssetIntegrityError(
            "A stored DBC asset row does not describe a valid asset.",
            details={"asset_id": str(row[0]), "cause": error.code},
        ) from error
    except (TypeError, ValueError, IndexError) as error:
        raise DbcAssetIntegrityError(
            "A stored DBC asset row could not be decoded.",
            details={"asset_id": str(row[0])},
        ) from error


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise DbcAssetIntegrityError(
            "The stored imported_at is not a timestamp string.",
            details={"field": "imported_at"},
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise DbcAssetIntegrityError(
            "The stored imported_at is not an ISO-8601 timestamp.",
            details={"field": "imported_at", "value": value},
        ) from error
    if parsed.tzinfo is None:
        raise DbcAssetIntegrityError(
            "The stored imported_at has no UTC offset.",
            details={"field": "imported_at", "value": value},
        )
    return parsed.astimezone(UTC)
