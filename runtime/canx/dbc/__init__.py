"""CAN-X DBC domain: canonical models, typed errors and the cantools adapter.

Importing this package must never import the third-party DBC engine. Only
:mod:`canx.dbc.parser` — the one module that is allowed to know about
``cantools`` — pulls it in, so the canonical model stays usable (and type
checkable) without the dependency being present.
"""

from canx.dbc.errors import (
    DbcDecodeError,
    DbcError,
    DbcFileNotFoundError,
    DbcModelError,
    DbcParseError,
    DbcReadError,
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
    "MAX_CLASSIC_PAYLOAD_LENGTH",
    "MAX_EXTENDED_FRAME_ID",
    "MAX_FD_PAYLOAD_LENGTH",
    "MAX_STANDARD_FRAME_ID",
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
    "DbcUnsupportedFormatError",
]
