"""A bounded cache of compiled DBC decoders, keyed on verified content identity.

One decode request repeats work that only the asset's *content* decides: reading the
``.dbc`` bytes, verifying their size and SHA-256, decoding text, parsing with
``cantools``, converting to the canonical model and compiling a
:class:`~canx.dbc.decode.DbcDecoder`. The live viewport sends one small request at
a time, so that static sequence would otherwise run again for every request. This
module removes the repetition for the part that is a pure function of the content
— the parse and the compile — and nothing else.

Four properties are deliberate, and each answers a way the cache could go wrong:

* **The key is content identity, not a name.** A :class:`DecoderKey` carries the
  project identity, the verified size and SHA-256 of the bytes, and the encoding
  that decoded them. A decoder is a pure function of exactly those inputs, so a
  hit can only ever return the decoder the bytes would have produced. Keying on
  ``asset_id`` or ``source_name`` would instead let a replaced file reuse an entry,
  and keying on nothing but the digest would let one project answer for another.
* **Integrity is checked before the cache is consulted, never by it.** The bytes
  are read and hashed on every request by
  :meth:`~canx.dbc.project_service.ProjectDbcService.read_verified_asset`; a file
  that no longer matches its registry row fails there, so a tampered asset can
  neither populate nor hit an entry. A cache hit skips *parsing*, never *verifying*.
* **One project cannot reach another's entry.** The project identity is part of
  the key, so the same bytes owned by two projects are two entries. There is no
  global "current DBC": an entry is created and read only when a caller presents
  the verified identity of the asset it is decoding.
* **It is bounded in entries, and it forgets least-recently-used first.** The
  number of compiled decoders alive at once is fixed, so a long session with many
  bindings cannot grow the process without limit.

The cache is not thread-hostile: requests run on worker threads, so single
``get``/``put`` operations are serialised by a lock. Two threads racing to build
the same entry would each parse and produce an equal decoder — a redundant parse,
never a wrong answer — which the one-request-in-flight viewport does not exercise.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

from canx.dbc.decode import DbcDecoder

#: How many compiled decoders one process keeps alive.
#:
#: Small on purpose: a decoder is the compiled form of one document, and the live
#: workspace binds a handful of DBCs at once. The bound exists so a long session
#: cannot accumulate decoders without limit, not because the number is tuned.
DEFAULT_MAX_ENTRIES = 8

#: The value below which the bound stops being a bound.
MIN_MAX_ENTRIES = 1


@dataclass(frozen=True, slots=True)
class DecoderKey:
    """The verified identity of the parse a decoder was compiled from.

    Every field is part of what makes a decode reproducible: the project it belongs
    to, the size and digest of the bytes that were parsed, and the encoding those
    bytes were decoded with. Naming the asset is deliberately *not* part of the
    key — two names can carry one document, and the document is what was compiled.
    """

    project_id: str
    sha256: str
    size_bytes: int
    encoding: str


class DecoderCache:
    """A bounded, least-recently-used map from verified content identity to decoder."""

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        if max_entries < MIN_MAX_ENTRIES:
            raise ValueError(f"max_entries must be at least {MIN_MAX_ENTRIES}")
        self._max_entries = max_entries
        self._entries: OrderedDict[DecoderKey, DbcDecoder] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: DecoderKey) -> DbcDecoder | None:
        """Return the decoder for ``key``, marking it most recently used, or ``None``."""
        with self._lock:
            decoder = self._entries.get(key)
            if decoder is None:
                return None
            self._entries.move_to_end(key)
            return decoder

    def put(self, key: DecoderKey, decoder: DbcDecoder) -> None:
        """Store ``decoder`` under ``key``, evicting the least recently used entry."""
        with self._lock:
            self._entries[key] = decoder
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        """Return how many entries are currently held."""
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        """Forget every entry."""
        with self._lock:
            self._entries.clear()


#: The process-wide decoder cache. Bounded in entries, keyed on verified content
#: identity, and scoped to one project by the project identity in each key. It is
#: not a "current DBC": no request can consult it without first presenting the
#: verified identity of the asset it is about to decode.
DECODER_CACHE = DecoderCache()
