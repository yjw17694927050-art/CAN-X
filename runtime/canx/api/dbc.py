"""DBC runtime HTTP surface — a thin adapter over the DBC and project domains.

Seven endpoints, and nothing else:

```text
GET  /dbc/assets?project_path=…                     list registered assets
POST /dbc/assets                                    import one asset from content
GET  /dbc/assets/{id}?project_path=…                one asset's metadata
GET  /dbc/assets/{id}/database?project_path=…       the canonical DBC definition
POST /dbc/assets/{id}/decode                        decode one frame
POST /dbc/assets/{id}/decode-batch                  decode a bounded contiguous batch
POST /dbc/assets/{id}/decode-frames                 decode an ordered work set (gaps allowed)
```

Everything this module does is a translation: HTTP in, canonical model through,
HTTP out. It does not extract bits, does not scale a value, does not resolve a
multiplexer, does not walk a project directory and does not open SQLite.
:class:`~canx.dbc.project_service.ProjectDbcService` stays the one source of
truth for "what DBCs does this project own", :class:`~canx.dbc.decode.DbcDecoder`
stays the one implementation of "what does this frame mean", and this file is
only the wire between them.

One endpoint is a write, and it is the one that carries a security boundary:

* **Content, never a location.** ``POST /dbc/assets`` accepts the DBC bytes a
  trusted caller already holds — Base64-encoded — together with a provenance file
  name. There is no field for a path, no field that is quietly interpreted as one,
  and no code path that would open one: a caller cannot make the Runtime browse a
  filesystem. Turning a user's file choice into bytes belongs to the desktop
  filesystem bridge, which does not exist yet; this endpoint is the Runtime half
  that will receive its output. The name a caller claims is validated by the
  *domain* (a plain ``.dbc`` file name), so a caller that reaches the domain
  directly inherits the same rule instead of restating it.

Four properties are load-bearing:

* **The decoder is not duplicated.** Each request loads the asset through
  ``ProjectDbcService.load_decoder``, which reads and verifies the project-owned
  bytes every time and compiles a ``DbcDecoder`` only when that verified content
  has not been seen before. There is no second decode path and no global "current
  DBC": a compiled decoder is reached only by presenting one asset's verified
  content identity, is scoped to its project, and a replaced or edited file fails
  integrity before the cache could ever be consulted.
* **The request body is bounded.** A batch is capped at
  :data:`MAX_BATCH_FRAMES` and a decode work set at
  :data:`~canx.dbc.decode_set.DecodeFrameSet.MAX_FRAMES`; each cap belongs to the
  container it guards. Neither is a property of the ``FrameBatch`` domain, whose own
  limits are untouched, and neither is restated in the Desktop client as a second,
  drifting number.
* **Two decode containers, one decode path.** ``decode-batch`` carries a
  ``FrameBatch`` — contiguous by definition, because a batch is a slice of one
  stream's numbering — and ``decode-frames`` carries a ``DecodeFrameSet``, which
  requires only that its frames are *ordered and non-repeating*. Both reach the same
  :class:`~canx.dbc.decode.DbcDecoder` through the same
  :meth:`~canx.dbc.project_service.ProjectDbcService.load_decoder`, so the second
  surface adds a request shape and not a second implementation. The strict surface
  keeps its strictness: ``1, 3`` is still refused as a batch.
* **Nothing blocking runs on the event loop.** Loading an asset reads a file and
  verifies a hash, and — the first time a given verified content is seen — decodes
  text, parses it and compiles a decoder; decoding then runs that decoder's plan.
  All of it is offloaded with ``asyncio.to_thread`` so a DBC request can never
  stall the realtime WebSocket served by the same loop.
* **A frame's failure is data, not the request's failure.** ``decode_batch``
  answers 200 with one outcome per frame even when some frames could not be
  decoded; only a failure of the *request* or of the *asset* becomes an error
  response. Aborting a batch because one frame was unknown would throw away the
  frames that were fine.

Failures are not translated here. ``ProjectError`` and every typed ``DbcError``
propagate to the application boundary, which maps them to the shared envelope and
an honest status code (see :mod:`canx.api.errors`).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from datetime import UTC
from pathlib import Path
from typing import Self

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from canx.api.errors import ApiRequestError, ErrorResponse
from canx.api.frame import FrameWire, FrameWireError, frame_to_wire, wire_to_frame
from canx.dbc.asset import DbcAsset
from canx.dbc.decode_model import (
    DecodedFrame,
    DecodedFrameBatch,
    DecodedFrameOutcome,
    DecodedSignal,
)
from canx.dbc.decode_set import DecodedFrameSet, DecodeFrameSet
from canx.dbc.model import DbcDatabase, DbcDocument, DbcMessage, DbcNode, DbcSignal
from canx.dbc.project_service import ProjectDbcService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Frame

#: The HTTP request guard on one ``decode-batch`` call. Chosen so a single request
#: stays a bounded amount of CPU, and kept here rather than in the domain: a
#: ``FrameBatch`` is a realtime unit and its size is a pipeline decision, not an
#: HTTP one.
#:
#: ``decode-frames`` uses :data:`~canx.dbc.decode_set.DecodeFrameSet.MAX_FRAMES`
#: instead of this constant: that bound belongs to the work set, and duplicating the
#: number here would let the two drift apart.
MAX_BATCH_FRAMES = 1000

#: The HTTP request guard on one content import. A bound on *this endpoint*, not a
#: permanent limit of the DBC domain or of the importer: the same import service
#: will parse a larger document handed to it by another trusted caller, and the
#: project layer has no opinion about size at all.
MAX_DBC_IMPORT_BYTES = 16 * 1024 * 1024

#: The longest Base64 text that can still decode to at most
#: :data:`MAX_DBC_IMPORT_BYTES` bytes. Checked before decoding, so an oversized
#: request is refused without first materialising its content in memory.
MAX_IMPORT_BASE64_CHARS = 4 * ((MAX_DBC_IMPORT_BYTES + 2) // 3)

#: How many Base64 characters are handed to one strict decode step.
#:
#: The decode is the largest piece of caller-controlled work on this surface, and
#: ``binascii.a2b_base64`` holds the GIL for the whole of one call — so offloading it
#: to a thread moves *where* it runs without changing *how long* the event loop
#: waits. Bounding the wait means bounding the call: one step is one C call, and the
#: loop is yielded between steps. 256 KiB of text is well under a millisecond of
#: decode on the reference machine, comfortably inside the realtime WebSocket's own
#: batch cadence.
#:
#: The value must stay a multiple of four. A step boundary that split a Base64
#: quantum would present each half as malformed input to a decoder that validates
#: strictly, so the bound would start refusing payloads the endpoint accepts.
DECODE_CHUNK_CHARS = 256 * 1024


class DbcAssetResponse(BaseModel):
    """One registered project-owned DBC asset.

    The project-relative copy path is deliberately absent. It is internal project
    layout, it is not needed to do anything with an asset, and this projection is
    what a caller is allowed to see. ``source_name`` is provenance — the name the
    file had when it was imported — never a path the caller can act on.
    """

    model_config = ConfigDict(frozen=True)

    asset_id: str
    source_name: str
    sha256: str
    size_bytes: int
    encoding: str
    imported_at: str


class DbcAssetListResponse(BaseModel):
    """This project's assets, in the domain's own deterministic order."""

    model_config = ConfigDict(frozen=True)

    assets: list[DbcAssetResponse]


class DbcAssetImportRequest(BaseModel):
    """One DBC document submitted for import as a project-owned asset.

    Three things are contract here, and each is deliberate:

    * ``content_base64`` carries the **exact original bytes**, encoded. A DBC file
      is not reliably text: it may be ``utf-8-sig``, it may be a legacy codec, and
      its comments and identifiers may be non-ASCII. Accepting "the decoded text"
      would pick an encoding on the caller's behalf and make neither the
      byte-for-byte copy nor its digest verifiable afterwards.
    * ``source_name`` is provenance, not a location — the name the file had *for
      the caller*. It is checked here as a string and validated as a plain ``.dbc``
      file name by the domain, which is the layer that keeps the rule even when a
      caller never passes through this adapter.
    * ``extra="forbid"``. A caller that sends ``source_path`` — or any other field
      this contract does not declare — gets a validation failure instead of being
      quietly ignored. Being quietly ignored is exactly how a path-shaped field
      would start to look accepted.

    ``strict=True`` for the same reason the frame contract is strict: a JSON
    payload has primitive categories, and a coerced ``"42"`` is not the ``42`` the
    caller sent.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    project_path: str
    source_name: str
    content_base64: str
    encoding: str | None = None

    @model_validator(mode="after")
    def _reject_transport_that_cannot_be_imported(self) -> Self:
        """Refuse the payloads that are decidable from the encoded text alone.

        This validator runs synchronously, on the event loop, so what it may do is
        bounded: an emptiness test and a length comparison, both O(1). It
        deliberately does **not** decode. A Base64 decode of up to
        :data:`MAX_DBC_IMPORT_BYTES` of content is the largest piece of
        caller-controlled work on this surface, and the same event loop serves the
        realtime WebSocket — so the decode belongs to
        :func:`decode_import_content`, which runs it in bounded steps with the loop
        yielded between them, together with the decoded size that only the decode can
        reveal.

        What it refuses, it refuses as the request contract: the same envelope, code
        and status as a shape failure the framework itself caught.
        """
        _require_importable_transport(self.content_base64)
        return self


#: The one message for "this payload is larger than this endpoint accepts", shared
#: so the cheap guard and the post-decode check cannot describe the same refusal
#: two different ways.
_CONTENT_TOO_LARGE_MESSAGE = f"The DBC content must be at most {MAX_DBC_IMPORT_BYTES} bytes."

#: The one message for "no content was submitted at all".
_CONTENT_EMPTY_MESSAGE = "The DBC content must not be empty."

#: The one message for "this text is not the Base64 the contract describes", shared
#: by every step of the decode so that a payload refused at step seven reads exactly
#: like one refused at step one — a caller cannot learn where in the text the
#: trouble was, and has no reason to.
_CONTENT_NOT_BASE64_MESSAGE = "The DBC content is not valid Base64."

#: Where a content failure is attributed. The location the framework itself would
#: report for this field, so a caller sees one diagnostic shape whether the refusal
#: came from the cheap guard or from the decode.
_CONTENT_LOCATION = ("body", "content_base64")


def _require_importable_transport(content_base64: str) -> None:
    """Apply the transport rules that need no decoding.

    Both are O(1) and both are decidable from the text: an empty payload can never
    be a DBC document, and text longer than the encoded form of
    :data:`MAX_DBC_IMPORT_BYTES` cannot decode into the bound. Everything else —
    whether the text is canonical Base64 at all, and how many bytes it actually
    produces — is decided by :func:`decode_import_content`.

    Raises:
        ValueError: Naming the rule that failed, never the payload. A plain
            ``ValueError`` is what a Pydantic validator turns into the shared
            request-validation envelope, which is why this cannot raise the typed
            :class:`~canx.api.errors.ApiRequestError` used after validation.
    """
    if not content_base64:
        raise ValueError(_CONTENT_EMPTY_MESSAGE)
    if len(content_base64) > MAX_IMPORT_BASE64_CHARS:
        raise ValueError(_CONTENT_TOO_LARGE_MESSAGE)


async def decode_import_content(content_base64: str) -> bytes:
    """Decode one import payload strictly, in bounded steps. Yields the event loop.

    This is the **only** place the submitted Base64 is decoded, and it happens
    exactly **once** for one request. The cheap transport rules are re-applied here
    as well, so a caller that reaches this function without the request model in
    front of it gets the same refusals.

    Being a coroutine is part of the contract rather than a detail of the code.
    ``binascii.a2b_base64`` holds the GIL for the whole of one call, so a single
    decode of up to :data:`MAX_DBC_IMPORT_BYTES` would keep the event loop from being
    scheduled for as long as the decode takes — *no matter which thread the call was
    made on*, because the loop and the worker cannot both run while the GIL is held.
    Offloading answers "which thread" and not "how long the loop waits"; only
    bounding each call and yielding between them answers the second. See
    :func:`_decode_strict_base64` for what keeps the bounded decoder accepting
    exactly what the whole-payload decoder accepted.

    Three refusals, all of them about the *request* rather than about DBC:

    * an empty payload, and text longer than the encoded form of
      :data:`MAX_DBC_IMPORT_BYTES` — refused before any decode;
    * text that is not canonical Base64 — ``validate=True``, so a lenient decoder's
      tolerance for stray characters, missing padding or the URL-safe alphabet does
      not quietly become part of the contract;
    * a payload that decodes to nothing, or to more than the bound. The length
      guard cannot decide the second on its own: it bounds what a text of that
      length *can* decode to, not what this particular text does.

    Args:
        content_base64: The Base64 text as it arrived.

    Returns:
        The exact bytes the caller submitted.

    Raises:
        ApiRequestError: Naming the field and the rule that failed, never quoting
            the payload. It is a *request* failure, so the application boundary
            answers it with the shared request-validation envelope — the same 422
            the framework's own validation produces, never a DBC diagnosis and
            never a server error.
    """
    _require_importable_transport(content_base64)
    raw = await _decode_strict_base64(content_base64)
    if not raw:
        raise _unimportable_content(_CONTENT_EMPTY_MESSAGE)
    if len(raw) > MAX_DBC_IMPORT_BYTES:
        raise _unimportable_content(_CONTENT_TOO_LARGE_MESSAGE)
    return raw


async def _decode_strict_base64(content_base64: str) -> bytes:
    """Decode the payload strictly, one bounded step at a time.

    The accepted language is exactly the whole-payload decoder's. Each step is the
    same ``binascii`` call under the same ``validate=True``, so the alphabet rule is
    untouched; the two rules that had to be stated outright are the ones position
    used to imply:

    * **padding may occur only in the final step.** A decoder that validated each
      step in isolation would accept ``AA==AAAA``, which the whole-payload decoder
      refuses because Base64 allows padding only at the end. Refusing ``=`` anywhere
      but the last step is what keeps the two decoders describing one language
      instead of two — the bounded decoder is not allowed to be the more permissive
      one.
    * **every step but the last is a whole number of Base64 quanta**, because
      :data:`DECODE_CHUNK_CHARS` is a multiple of four. A boundary that split a
      quantum would make each half look like a broken tail.

    Steps are decoded on workers and awaited between them; that await is what gives
    the event loop its turns back.
    """
    parts: list[bytes] = []
    total = len(content_base64)
    for start in range(0, total, DECODE_CHUNK_CHARS):
        step = content_base64[start : start + DECODE_CHUNK_CHARS]
        if "=" in step and start + DECODE_CHUNK_CHARS < total:
            raise _unimportable_content(_CONTENT_NOT_BASE64_MESSAGE)
        try:
            parts.append(await asyncio.to_thread(_decode_base64_step, step))
        except (binascii.Error, ValueError) as error:
            raise _unimportable_content(_CONTENT_NOT_BASE64_MESSAGE) from error
    return b"".join(parts)


def _decode_base64_step(step: str) -> bytes:
    """Strictly decode one bounded step. Blocking by design.

    A named function rather than an inline ``base64.b64decode`` call so that the step
    itself is observable: the responsiveness tests record which thread ran it, which
    is how "the decode does not run on the event loop" is asserted about the work
    rather than about the call that scheduled it.
    """
    return base64.b64decode(step, validate=True)


def _unimportable_content(message: str) -> ApiRequestError:
    """Build the typed request-contract refusal for the content field."""
    return ApiRequestError(message, location=_CONTENT_LOCATION)


class DbcChoiceResponse(BaseModel):
    """One declared ``VAL_`` entry: a raw value and the label naming it."""

    model_config = ConfigDict(frozen=True)

    value: int
    label: str


class DbcSignalResponse(BaseModel):
    """One canonical signal definition, as declared by the DBC document.

    This is the *definition*, not a decoded value: ``factor``/``offset`` describe
    the mapping without applying it, and ``start_bit`` is carried exactly as the
    document expresses it.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    start_bit: int
    length: int
    byte_order: str
    is_signed: bool
    is_float: bool
    factor: float
    offset: float
    minimum: float | None
    maximum: float | None
    unit: str | None
    receivers: list[str]
    choices: list[DbcChoiceResponse]
    is_multiplexer: bool
    multiplexer_signal: str | None
    multiplexer_ids: list[int] | None
    comment: str | None


class DbcMessageResponse(BaseModel):
    """One canonical message definition.

    ``frame_id`` travels with ``is_extended``: the pair, never the id alone,
    decides which identifier space the message addresses.
    """

    model_config = ConfigDict(frozen=True)

    frame_id: int
    name: str
    length: int
    is_extended: bool
    is_fd: bool
    senders: list[str]
    comment: str | None
    cycle_time: int | None
    signals: list[DbcSignalResponse]


class DbcNodeResponse(BaseModel):
    """One canonical network node."""

    model_config = ConfigDict(frozen=True)

    name: str
    comment: str | None


class DbcDatabaseResponse(BaseModel):
    """The canonical content of one imported DBC document.

    No path, no traceback and no parser internals: a caller receives the document
    as CAN-X understands it, in the order the source declared it.
    """

    model_config = ConfigDict(frozen=True)

    version: str | None
    messages: list[DbcMessageResponse]
    nodes: list[DbcNodeResponse]


class DecodedSignalResponse(BaseModel):
    """One decoded signal value, with its raw value kept alongside.

    ``choice_label`` is additional semantics, never a replacement: ``raw_value``
    stays visible so a caller can still plot and compare raw against physical.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    raw_value: int
    physical_value: float
    choice_label: str | None
    unit: str | None


class DecodedMessageResponse(BaseModel):
    """The decoded content of one frame: its message and its active signals."""

    model_config = ConfigDict(frozen=True)

    message_name: str
    signals: list[DecodedSignalResponse]


class DecodeResponse(BaseModel):
    """One decoded frame.

    The frame is the full canonical projection and not a summary, so
    ``sequence``, ``channel_id``, ``direction``, every timestamp field and
    ``flags`` survive the decode. Returning ``dict[str, float]`` would throw that
    provenance away and force every later view to reinvent it.
    """

    model_config = ConfigDict(frozen=True)

    frame: FrameWire
    message_name: str
    signals: list[DecodedSignalResponse]


class DecodeOutcomeResponse(BaseModel):
    """What happened to exactly one submitted frame.

    ``decoded`` and ``failure`` are mutually exclusive: exactly one is present, so
    a caller branches on one fact instead of guessing which field to trust. The
    frame is always present, whether or not it decoded.

    Shared by the two decode surfaces — a frame's outcome does not depend on which
    container carried the frame, so rendering it two ways would be two shapes for
    one fact.
    """

    model_config = ConfigDict(frozen=True)

    frame: FrameWire
    decoded: DecodedMessageResponse | None
    failure: ErrorResponse | None


class DecodeBatchResponse(BaseModel):
    """One outcome per submitted frame, in the submitted order."""

    model_config = ConfigDict(frozen=True)

    schema_version: int
    stream_id: str
    first_sequence: int
    last_sequence: int
    frame_count: int
    outcomes: list[DecodeOutcomeResponse]


class DecodeFrameSetResponse(BaseModel):
    """One outcome per submitted frame of a decode work set, in the submitted order.

    The sibling of :class:`DecodeBatchResponse`, and deliberately not a superset of
    it. There is no ``first_sequence``/``last_sequence``: a work set has no range,
    and publishing one would invite a caller to reason about the frames *between*
    two submitted sequences — frames this answer says nothing about. ``sequences``
    is the caller's own account of what was submitted, echoed so an outcome can be
    aligned to a frame without trusting position alone.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int
    stream_id: str
    frame_count: int
    sequences: list[int]
    outcomes: list[DecodeOutcomeResponse]


class DbcFramePayload(FrameWire):
    """One frame submitted for decoding, validated by building the canonical frame.

    The frame's rules are not restated here. The payload is turned into a
    canonical :class:`~canx.domain.frame.Frame` while the request is being
    validated, so the domain object is what rejects an illegal DLC, an
    out-of-range arbitration id, a payload whose length contradicts its DLC, or a
    non-finite timestamp. The failure surfaces as the shared request-validation
    envelope, exactly like a shape error, because a caller cannot act differently
    on the two — and it happens before any project is opened.
    """

    @model_validator(mode="after")
    def _reject_non_canonical(self) -> Self:
        try:
            wire_to_frame(self)
        except FrameWireError as error:
            raise ValueError(str(error)) from error
        return self


class DbcDecodeRequest(BaseModel):
    """One frame decode against one project-owned asset."""

    model_config = ConfigDict(frozen=True)

    project_path: str
    frame: DbcFramePayload


class DbcDecodeBatchRequest(BaseModel):
    """A bounded batch decode against one project-owned asset."""

    model_config = ConfigDict(frozen=True)

    project_path: str
    stream_id: str = Field(min_length=1)
    frames: list[DbcFramePayload] = Field(min_length=1, max_length=MAX_BATCH_FRAMES)

    @model_validator(mode="after")
    def _reject_non_canonical_batch(self) -> Self:
        """Prove the submitted frames form a canonical batch, before any work.

        A ``FrameBatch`` is contiguous by definition, so a frame list that is not
        one does not describe a batch at all: it is a request that does not match
        the contract, reported the same way as any other payload-shape failure.
        Checking it here rather than in the handler also means a rejected request
        never opens a project or reads an asset.
        """
        try:
            FrameBatch.create(
                stream_id=self.stream_id,
                frames=[wire_to_frame(frame) for frame in self.frames],
            )
        except ValueError as error:
            raise ValueError(str(error)) from error
        return self


class DbcDecodeFramesRequest(BaseModel):
    """A bounded decode work set against one project-owned asset.

    The same body as :class:`DbcDecodeBatchRequest` with one difference that is the
    whole point: the frames must be **ordered and non-repeating**, and need not be
    consecutive. A viewport that alternates two channels leaves each channel's
    sequences full of the other channel's gaps, and a decode is frame-local, so
    those frames are a perfectly well-formed request — they were only ever refused
    because they were being measured against a capture batch's contract.
    """

    model_config = ConfigDict(frozen=True)

    project_path: str
    stream_id: str = Field(min_length=1)
    frames: list[DbcFramePayload] = Field(
        min_length=1, max_length=DecodeFrameSet.MAX_FRAMES
    )

    @model_validator(mode="after")
    def _reject_non_canonical_work_set(self) -> Self:
        """Prove the submitted frames form a decode work set, before any work.

        The bound comes from the container rather than a second constant here, so
        the number a caller sees refused is the number the domain enforces. Checking
        it at validation time rather than in the handler also means a rejected
        request never opens a project or reads an asset.
        """
        try:
            DecodeFrameSet.create(
                stream_id=self.stream_id,
                frames=[wire_to_frame(frame) for frame in self.frames],
            )
        except ValueError as error:
            raise ValueError(str(error)) from error
        return self


def create_dbc_router() -> APIRouter:
    """Build the DBC router.

    A separate router keeps the DBC surface independent of the capture lifecycle,
    which it genuinely is: reading a project's DBC assets needs no running
    capture, and a running capture does not make a DBC decodable.
    """
    router = APIRouter(tags=["dbc"], prefix="/dbc")

    @router.post("/assets", response_model=DbcAssetResponse, status_code=201)
    async def import_asset(request: DbcAssetImportRequest) -> DbcAssetResponse:
        """Import DBC content a trusted caller already holds as a project asset.

        The create half of the collection ``GET /dbc/assets`` lists, and the only
        write on this surface. The response is the same :class:`DbcAssetResponse`
        projection every other asset endpoint returns, so an imported asset is
        immediately usable through the read and decode endpoints without a second
        lookup format.

        Nothing about the request is interpreted as a location: the content travels
        as Base64 and the name travels as provenance. The content is decoded here, in
        bounded steps, so that the decode cannot keep the event loop from being
        scheduled — and only then handed, as bytes, to the worker that parses and
        persists it. Validation itself applies only the rules that need no decode.
        """
        raw = await decode_import_content(request.content_base64)
        asset = await asyncio.to_thread(_import_asset, request, raw)
        return _asset_payload(asset)

    @router.get("/assets", response_model=DbcAssetListResponse)
    async def list_assets(project_path: str) -> DbcAssetListResponse:
        """List the assets this project owns, in the domain's own order."""
        assets = await asyncio.to_thread(_list_assets, project_path)
        return DbcAssetListResponse(assets=[_asset_payload(asset) for asset in assets])

    @router.get("/assets/{asset_id}", response_model=DbcAssetResponse)
    async def get_asset(asset_id: str, project_path: str) -> DbcAssetResponse:
        """Return one registered asset's metadata, or a typed 404."""
        asset = await asyncio.to_thread(_get_asset, project_path, asset_id)
        return _asset_payload(asset)

    @router.get("/assets/{asset_id}/database", response_model=DbcDatabaseResponse)
    async def get_database(asset_id: str, project_path: str) -> DbcDatabaseResponse:
        """Return the canonical definition of one registered asset."""
        document = await asyncio.to_thread(_load_document, project_path, asset_id)
        return _database_payload(document.database)

    @router.post("/assets/{asset_id}/decode", response_model=DecodeResponse)
    async def decode(asset_id: str, request: DbcDecodeRequest) -> DecodeResponse:
        """Decode one frame against one asset; a frame it does not define is 422."""
        decoded = await asyncio.to_thread(
            _decode_one, request.project_path, asset_id, request.frame
        )
        return _decoded_payload(decoded)

    @router.post("/assets/{asset_id}/decode-batch", response_model=DecodeBatchResponse)
    async def decode_batch(
        asset_id: str, request: DbcDecodeBatchRequest
    ) -> DecodeBatchResponse:
        """Decode a bounded batch; one outcome per frame, always in input order."""
        decoded = await asyncio.to_thread(
            _decode_batch, request.project_path, asset_id, request.stream_id, request.frames
        )
        return _batch_payload(decoded)

    @router.post("/assets/{asset_id}/decode-frames", response_model=DecodeFrameSetResponse)
    async def decode_frames(
        asset_id: str, request: DbcDecodeFramesRequest
    ) -> DecodeFrameSetResponse:
        """Decode a work set; one outcome per frame, always in input order.

        The surface the Desktop's realtime decode path uses. It exists so a request
        can carry the frames one asset actually has in the viewport — ``1, 3, 5``
        when another channel owns ``2`` and ``4`` — instead of one request per frame.
        """
        decoded = await asyncio.to_thread(
            _decode_frames, request.project_path, asset_id, request.stream_id, request.frames
        )
        return _frame_set_payload(decoded)

    return router


def _import_asset(request: DbcAssetImportRequest, raw: bytes) -> DbcAsset:
    """Import one already-decoded document as a project-owned asset. Blocking.

    Everything expensive about the request happens here, off the event loop: the DBC
    parse, the filesystem write and fsync, and the registry insert. The decode is
    deliberately *not* here. It is the one piece of caller-controlled work on this
    surface large enough to matter, and bounding it means yielding the event loop
    between steps — which a synchronous function cannot do, whichever thread it runs
    on. It has therefore already happened, exactly once, and the payload arrives **as
    bytes**: never turned back into text on the way, because the bytes are what the
    project owns and the digest of what the caller sent has to be the digest of what
    is stored.
    """
    return ProjectDbcService(Path(request.project_path)).import_asset_bytes(
        raw,
        source_name=request.source_name,
        encoding=request.encoding,
    )


def _list_assets(project_path: str) -> tuple[DbcAsset, ...]:
    """Open the project and list its registered assets. Blocking by design.

    ``ProjectDbcService`` validates the project through the project domain, so a
    directory that merely looks like a project is rejected with ``source =
    "project"`` rather than silently producing an empty list.
    """
    return ProjectDbcService(Path(project_path)).list_assets()


def _get_asset(project_path: str, asset_id: str) -> DbcAsset:
    """Open the project and read one asset's metadata. Blocking by design."""
    return ProjectDbcService(Path(project_path)).get_asset(asset_id)


def _load_document(project_path: str, asset_id: str) -> DbcDocument:
    """Load one project-owned asset into a canonical document. Blocking by design."""
    return ProjectDbcService(Path(project_path)).load_asset(asset_id)


def _decode_one(project_path: str, asset_id: str, payload: DbcFramePayload) -> DecodedFrame:
    """Load the asset's decoder and decode one frame. Blocking by design.

    The project-owned bytes are read and verified on every request; the compile that
    turns them into a decoder is reused while the verified content is unchanged (see
    :meth:`~canx.dbc.project_service.ProjectDbcService.load_decoder`). The decoder is
    a compiled view of one asset's content, never a "current DBC": it is reached only
    by presenting that asset's verified identity, and a tampered file is refused
    before any cache is consulted.
    """
    frame = _canonical_frame(payload)
    decoder = ProjectDbcService(Path(project_path)).load_decoder(asset_id)
    return decoder.decode_frame(frame)


def _decode_batch(
    project_path: str,
    asset_id: str,
    stream_id: str,
    payloads: list[DbcFramePayload],
) -> DecodedFrameBatch:
    """Build the canonical batch, load the asset's decoder and decode. Blocking.

    The batch is decoded by ``DbcDecoder.decode_batch`` and not by a loop here:
    the domain already defines what a batch outcome is — one outcome per frame,
    ordered, with a failed frame captured as data — and a loop at this layer would
    be a second, drifting definition of the same thing.
    """
    batch = FrameBatch.create(
        stream_id=stream_id, frames=[_canonical_frame(payload) for payload in payloads]
    )
    decoder = ProjectDbcService(Path(project_path)).load_decoder(asset_id)
    return decoder.decode_batch(batch)


def _decode_frames(
    project_path: str,
    asset_id: str,
    stream_id: str,
    payloads: list[DbcFramePayload],
) -> DecodedFrameSet:
    """Build the work set, load the asset's decoder and decode. Blocking.

    The sibling of :func:`_decode_batch`, over the decode-specific container, and
    deliberately not a second decode path: both reach the decoder through
    ``ProjectDbcService.load_decoder``, so the file is read, its size and SHA-256 are
    proven, and the compiled decoder is reused from the same bounded, project-scoped
    cache. This function adds a request *shape* — ordered frames with gaps — and
    nothing else.
    """
    work = DecodeFrameSet.create(
        stream_id=stream_id, frames=[_canonical_frame(payload) for payload in payloads]
    )
    decoder = ProjectDbcService(Path(project_path)).load_decoder(asset_id)
    return decoder.decode_frames(work)


def _canonical_frame(payload: DbcFramePayload) -> Frame:
    """Rebuild the canonical frame a validated payload describes.

    The payload was already proven legal while the request was validated, so this
    cannot fail; it exists because validation and execution are two different
    passes and the second needs the value, not the proof.
    """
    return wire_to_frame(payload)


def _asset_payload(asset: DbcAsset) -> DbcAssetResponse:
    """Render one asset's metadata for the wire."""
    return DbcAssetResponse(
        asset_id=asset.asset_id,
        source_name=asset.source_name,
        sha256=asset.sha256,
        size_bytes=asset.size_bytes,
        encoding=asset.encoding,
        imported_at=asset.imported_at.astimezone(UTC).isoformat(),
    )


def _database_payload(database: DbcDatabase) -> DbcDatabaseResponse:
    """Render one canonical database as the projection a caller receives."""
    return DbcDatabaseResponse(
        version=database.version,
        messages=[_message_payload(message) for message in database.messages],
        nodes=[_node_payload(node) for node in database.nodes],
    )


def _message_payload(message: DbcMessage) -> DbcMessageResponse:
    """Render one canonical message definition."""
    return DbcMessageResponse(
        frame_id=message.frame_id,
        name=message.name,
        length=message.length,
        is_extended=message.is_extended,
        is_fd=message.is_fd,
        senders=list(message.senders),
        comment=message.comment,
        cycle_time=message.cycle_time,
        signals=[_signal_payload(signal) for signal in message.signals],
    )


def _signal_payload(signal: DbcSignal) -> DbcSignalResponse:
    """Render one canonical signal definition."""
    return DbcSignalResponse(
        name=signal.name,
        start_bit=signal.start_bit,
        length=signal.length,
        byte_order=signal.byte_order.value,
        is_signed=signal.is_signed,
        is_float=signal.is_float,
        factor=signal.factor,
        offset=signal.offset,
        minimum=signal.minimum,
        maximum=signal.maximum,
        unit=signal.unit,
        receivers=list(signal.receivers),
        choices=[
            DbcChoiceResponse(value=choice.value, label=choice.label)
            for choice in signal.choices
        ],
        is_multiplexer=signal.is_multiplexer,
        multiplexer_signal=signal.multiplexer_signal,
        multiplexer_ids=(
            None if signal.multiplexer_ids is None else list(signal.multiplexer_ids)
        ),
        comment=signal.comment,
    )


def _node_payload(node: DbcNode) -> DbcNodeResponse:
    """Render one canonical node."""
    return DbcNodeResponse(name=node.name, comment=node.comment)


def _decoded_payload(decoded: DecodedFrame) -> DecodeResponse:
    """Render one decoded frame, provenance included."""
    return DecodeResponse(
        frame=frame_to_wire(decoded.frame),
        message_name=decoded.message_name,
        signals=[_signal_value_payload(signal) for signal in decoded.signals],
    )


def _signal_value_payload(signal: DecodedSignal) -> DecodedSignalResponse:
    """Render one decoded signal value."""
    return DecodedSignalResponse(
        name=signal.name,
        raw_value=signal.raw_value,
        physical_value=signal.physical_value,
        choice_label=signal.choice_label,
        unit=signal.unit,
    )


def _batch_payload(batch: DecodedFrameBatch) -> DecodeBatchResponse:
    """Render one decoded batch, keeping its bookkeeping and its order."""
    return DecodeBatchResponse(
        schema_version=batch.schema_version,
        stream_id=batch.stream_id,
        first_sequence=batch.first_sequence,
        last_sequence=batch.last_sequence,
        frame_count=batch.frame_count,
        outcomes=[_outcome_payload(outcome) for outcome in batch.outcomes],
    )


def _frame_set_payload(decoded: DecodedFrameSet) -> DecodeFrameSetResponse:
    """Render one decoded work set, keeping its order and its submitted sequences."""
    return DecodeFrameSetResponse(
        schema_version=decoded.schema_version,
        stream_id=decoded.stream_id,
        frame_count=decoded.frame_count,
        sequences=list(decoded.sequences),
        outcomes=[_outcome_payload(outcome) for outcome in decoded.outcomes],
    )


def _outcome_payload(outcome: DecodedFrameOutcome) -> DecodeOutcomeResponse:
    """Render one frame's outcome, keeping ``decoded`` and ``failure`` exclusive."""
    decoded = outcome.decoded
    failure = outcome.failure
    return DecodeOutcomeResponse(
        frame=frame_to_wire(outcome.frame),
        decoded=(
            None
            if decoded is None
            else DecodedMessageResponse(
                message_name=decoded.message_name,
                signals=[_signal_value_payload(signal) for signal in decoded.signals],
            )
        ),
        failure=(
            None
            if failure is None
            else ErrorResponse(
                code=failure.code,
                message=failure.message,
                details=dict(failure.details),
                recoverable=failure.recoverable,
                source=failure.source,
            )
        ),
    )
