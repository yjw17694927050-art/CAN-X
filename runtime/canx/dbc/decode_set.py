"""Decode work sets — the frames one decode request carries, and what it answered.

```text
capture stream        FrameBatch          contiguous slice of ONE stream's numbering
decode request        DecodeFrameSet      ordered, non-contiguous, still one stream
decode answer         DecodedFrameSet     one outcome per submitted frame, in order
```

These two containers exist because the two jobs are not the same job. A
:class:`~canx.domain.batch.FrameBatch` is a slice of a *stream*: its sequences are
consecutive because a stream numbers everything it carries, and its
``first_sequence``/``last_sequence`` describe a range. A DBC decode is a
*frame-local, stateless transformation*: a decoder asked about sequence 1, 3 and 5
does not need sequence 2 to exist, and never looks at it. Forcing the decode path
through the batch container therefore forced every caller to invent frames that
were never captured — ``1`` and ``3`` cannot be one batch — which is exactly how a
viewport that alternates two channels degenerated into one request per frame.

Four properties are deliberate:

* **Order is the caller's, and it is preserved exactly.** Frames are decoded in
  the order they were submitted and outcomes come back in the same order, so a
  caller aligns an outcome to a frame by position without trusting a sort.
* **The sequences must be strictly increasing — and nothing more.** Increasing is
  the weakest property that keeps "the outcome at index ``i`` is about the frame
  at index ``i``" checkable while still refusing a payload that names the same
  frame twice or names frames in an order the caller did not send them in. A gap
  is *not* refused: gaps are what this container is for.
* **The set is bounded.** A request that carries an unbounded number of frames is
  an unbounded amount of work on one HTTP request; :data:`DecodeFrameSet.MAX_FRAMES`
  is that bound, and the HTTP layer does not define a second one.
* **Nothing here knows about capture.** No stream epoch, no recorder, no
  persistence: this container never touches the numbering a stream is entitled to
  rely on, so widening what a decode request may carry cannot weaken what a
  captured batch must be.

The frames themselves stay canonical :class:`~canx.domain.frame.Frame` values, so
a decode is still "this frame, interpreted", never "a new frame that replaced it".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import ClassVar

from canx.dbc.decode_model import DecodedFrameOutcome
from canx.domain.frame import Frame


@dataclass(frozen=True, slots=True)
class DecodeFrameSet:
    """One decode request's frames: ordered, bounded, non-contiguous.

    Args:
        stream_id: The stream the frames belong to. Sequences are only comparable
            inside one stream, so this travels with them and is echoed back.
        frames: The canonical frames to decode, in submission order. Between one
            and :data:`MAX_FRAMES` of them, with strictly increasing sequences.

    Raises:
        ValueError: If ``stream_id`` is blank, ``frames`` is not a tuple of
            canonical frames, is empty, is longer than :data:`MAX_FRAMES`, or its
            sequences are not strictly increasing. Every refusal is about the
            *request*: a caller that built this by hand cannot submit a payload
            this container does not describe.
    """

    #: The shape version of this container, independent of every other schema.
    CURRENT_SCHEMA_VERSION: ClassVar[int] = 1

    #: How many frames one decode request may carry.
    #:
    #: The bound is this container's, not the HTTP layer's and not the domain's:
    #: ``FrameBatch`` has no size limit at all (its size is a pipeline decision),
    #: while a decode request is a single bounded piece of CPU work with a
    #: serialized request and response on either side of it.
    MAX_FRAMES: ClassVar[int] = 1000

    schema_version: int
    stream_id: str
    frame_count: int
    frames: tuple[Frame, ...]

    def __post_init__(self) -> None:
        """Reject a work set that could not describe a decode request."""
        if self.schema_version != self.CURRENT_SCHEMA_VERSION:
            raise ValueError("unsupported DecodeFrameSet schema_version")
        if not isinstance(self.stream_id, str) or not self.stream_id.strip():
            raise ValueError("stream_id must be a non-blank string")
        if not isinstance(self.frames, tuple) or not all(
            isinstance(frame, Frame) for frame in self.frames
        ):
            raise ValueError("frames must be a tuple of canonical Frame values")
        if not self.frames:
            raise ValueError("DecodeFrameSet frames must be non-empty")
        if len(self.frames) > self.MAX_FRAMES:
            raise ValueError(f"DecodeFrameSet frames must be at most {self.MAX_FRAMES}")
        if any(later.sequence <= earlier.sequence for earlier, later in pairwise(self.frames)):
            raise ValueError("DecodeFrameSet sequences must be strictly increasing")
        if self.frame_count != len(self.frames):
            raise ValueError("DecodeFrameSet frame_count does not match frames")

    @classmethod
    def create(cls, *, stream_id: str, frames: Sequence[Frame]) -> DecodeFrameSet:
        """Create a validated work set and derive its count.

        A convenience for callers that hold any sequence, mirroring
        :meth:`~canx.domain.batch.FrameBatch.create`: the model itself stores a
        tuple, so a list cannot reach the invariant above.
        """
        frozen = tuple(frames)
        if not frozen:
            raise ValueError("DecodeFrameSet frames must be non-empty")
        return cls(
            schema_version=cls.CURRENT_SCHEMA_VERSION,
            stream_id=stream_id,
            frame_count=len(frozen),
            frames=frozen,
        )

    @property
    def sequences(self) -> tuple[int, ...]:
        """The submitted sequences, in submission order. The caller's own account."""
        return tuple(frame.sequence for frame in self.frames)


@dataclass(frozen=True, slots=True)
class DecodedFrameSet:
    """One decode answer: exactly one outcome per submitted frame, in order.

    The pairing with :class:`DecodeFrameSet` is structural rather than advisory —
    ``frame_count`` must equal ``len(outcomes)`` and the outcomes' sequences must be
    strictly increasing — so "no frame was silently dropped" stays checkable without
    the contiguity a :class:`~canx.dbc.decode_model.DecodedFrameBatch` requires.

    It carries no ``first_sequence``/``last_sequence``: a set has no range, and
    reporting one would invite a caller to reason about the frames *between* two
    submitted sequences — frames this answer says nothing about.
    """

    CURRENT_SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int
    stream_id: str
    frame_count: int
    outcomes: tuple[DecodedFrameOutcome, ...]

    def __post_init__(self) -> None:
        """Reject an answer whose bookkeeping does not describe its outcomes."""
        if self.schema_version != self.CURRENT_SCHEMA_VERSION:
            raise ValueError("unsupported DecodedFrameSet schema_version")
        if not isinstance(self.stream_id, str) or not self.stream_id.strip():
            raise ValueError("stream_id must be a non-blank string")
        if not isinstance(self.outcomes, tuple) or not all(
            isinstance(outcome, DecodedFrameOutcome) for outcome in self.outcomes
        ):
            raise ValueError("outcomes must be a tuple of DecodedFrameOutcome values")
        if not self.outcomes:
            raise ValueError("DecodedFrameSet outcomes must be non-empty")
        if any(
            later.frame.sequence <= earlier.frame.sequence
            for earlier, later in pairwise(self.outcomes)
        ):
            raise ValueError("DecodedFrameSet sequences must be strictly increasing")
        if self.frame_count != len(self.outcomes):
            raise ValueError("DecodedFrameSet frame_count does not match outcomes")

    @classmethod
    def create(
        cls, *, stream_id: str, outcomes: Sequence[DecodedFrameOutcome]
    ) -> DecodedFrameSet:
        """Create a validated answer and derive its count."""
        frozen = tuple(outcomes)
        if not frozen:
            raise ValueError("DecodedFrameSet outcomes must be non-empty")
        return cls(
            schema_version=cls.CURRENT_SCHEMA_VERSION,
            stream_id=stream_id,
            frame_count=len(frozen),
            outcomes=frozen,
        )

    @property
    def sequences(self) -> tuple[int, ...]:
        """The answered sequences, in the order they were answered."""
        return tuple(outcome.frame.sequence for outcome in self.outcomes)
