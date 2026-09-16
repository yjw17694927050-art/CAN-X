"""CAN-X DBC domain: canonical models, owned-asset identity, typed errors, engine adapter.

Importing this package must never import the third-party DBC engine. Only
:mod:`canx.dbc.parser` — the one module that is allowed to know about
``cantools`` — pulls it in, so the canonical model and the asset record stay
usable (and type checkable) without the dependency being present. That is why
:class:`ProjectDbcService` is not re-exported here: it imports the import service,
which imports the engine.
"""

from canx.dbc.asset import (
    ASSET_DIRECTORY,
    ASSET_SUFFIX,
    DbcAsset,
    asset_relative_path,
    resolve_asset_path,
)
from canx.dbc.errors import (
    DbcAssetIntegrityError,
    DbcAssetNotFoundError,
    DbcAssetRegistryError,
    DbcAssetStorageError,
    DbcAssetValidationError,
    DbcDecodeError,
    DbcError,
    DbcFileNotFoundError,
    DbcModelError,
    DbcParseError,
    DbcReadError,
    DbcSourceChangedError,
    DbcUnsupportedFormatError,
)
from canx.dbc.model import (
    MAX_CLASSIC_PAYLOAD_LENGTH,
    MAX_EXTENDED_FRAME_ID,
    MAX_FD_PAYLOAD_LENGTH,
    MAX_STANDARD_FRAME_ID,
    DbcByteOrder,
    DbcChoice,
    DbcDatabase,
    DbcDocument,
    DbcMessage,
    DbcNode,
    DbcSignal,
    DbcSource,
)

__all__ = [
    "ASSET_DIRECTORY",
    "ASSET_SUFFIX",
    "MAX_CLASSIC_PAYLOAD_LENGTH",
    "MAX_EXTENDED_FRAME_ID",
    "MAX_FD_PAYLOAD_LENGTH",
    "MAX_STANDARD_FRAME_ID",
    "DbcAsset",
    "DbcAssetIntegrityError",
    "DbcAssetNotFoundError",
    "DbcAssetRegistryError",
    "DbcAssetStorageError",
    "DbcAssetValidationError",
    "DbcByteOrder",
    "DbcChoice",
    "DbcDatabase",
    "DbcDecodeError",
    "DbcDocument",
    "DbcError",
    "DbcFileNotFoundError",
    "DbcMessage",
    "DbcModelError",
    "DbcNode",
    "DbcParseError",
    "DbcReadError",
    "DbcSignal",
    "DbcSource",
    "DbcSourceChangedError",
    "DbcUnsupportedFormatError",
    "asset_relative_path",
    "resolve_asset_path",
]
