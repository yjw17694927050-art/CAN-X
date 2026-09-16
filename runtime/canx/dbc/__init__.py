"""CAN-X DBC domain: canonical models, owned-asset identity, typed errors, engine adapter.

Importing this package must never import the third-party DBC engine. Only
:mod:`canx.dbc.parser` — the one module that is allowed to know about
``cantools`` — pulls it in, so the canonical model, the owned-asset record and
the decode engine stay usable (and type checkable) without the dependency being
present. That is why :class:`ProjectDbcService` is not re-exported here: it
imports the import service, which imports the engine.

:mod:`canx.dbc.decode` is re-exported because it is on the other side of that
boundary: it decodes canonical frames against a canonical database and never
mentions the engine, so it belongs to the same engine-free graph as the model.
"""

from canx.dbc.asset import (
    ASSET_DIRECTORY,
    ASSET_SUFFIX,
    DbcAsset,
    asset_relative_path,
    resolve_asset_path,
)
from canx.dbc.decode import DbcDecoder
from canx.dbc.decode_model import (
    DbcDecodeFailure,
    DecodedFrame,
    DecodedFrameBatch,
    DecodedFrameOutcome,
    DecodedSignal,
)
from canx.dbc.errors import (
    DbcAssetIntegrityError,
    DbcAssetNotFoundError,
    DbcAssetRegistryError,
    DbcAssetStorageError,
    DbcAssetValidationError,
    DbcDecodeError,
    DbcDecodeUnsupportedError,
    DbcError,
    DbcFileNotFoundError,
    DbcFrameTypeMismatchError,
    DbcMessageNotFoundError,
    DbcModelError,
    DbcParseError,
    DbcPayloadTooShortError,
    DbcReadError,
    DbcSignalDecodeError,
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
    "DbcDecodeFailure",
    "DbcDecodeUnsupportedError",
    "DbcDecoder",
    "DbcDocument",
    "DbcError",
    "DbcFileNotFoundError",
    "DbcFrameTypeMismatchError",
    "DbcMessage",
    "DbcMessageNotFoundError",
    "DbcModelError",
    "DbcNode",
    "DbcParseError",
    "DbcPayloadTooShortError",
    "DbcReadError",
    "DbcSignal",
    "DbcSignalDecodeError",
    "DbcSource",
    "DbcSourceChangedError",
    "DbcUnsupportedFormatError",
    "DecodedFrame",
    "DecodedFrameBatch",
    "DecodedFrameOutcome",
    "DecodedSignal",
    "asset_relative_path",
    "resolve_asset_path",
]
