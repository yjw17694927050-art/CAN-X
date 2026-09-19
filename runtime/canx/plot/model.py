"""Typed, engine-independent historical Plot domain models.

Nothing in this module imports ``duckdb``, ``sqlite3``, ``pyarrow``, ``cantools``
or FastAPI. A ``DbcDecoder``, a ``DuckDBPyRelation`` or a raw row tuple must never
leak into the plot contract: the service translates both ways and the public
surface speaks only in CAN-X models.

Three decisions are deliberate and load-bearing:

* **A signal is identified by four facts, never by a name.** ``asset_id`` names
  the DBC document, ``message_name`` and ``signal_name`` name the definition
  inside it, and ``channel_id`` names the live/persisted stream that definition
  was applied to. This mirrors the V0.3 realtime
  ``SignalSelection`` (``apps/desktop/src/workspace/session.ts``) so a historical
  series and a live series describe the same thing. Two same-named signals on two
  channels are two different identities and can never be merged into one series.
* **``unit`` is presentation, never identity.** It travels with the identity so an
  axis can label itself, but it is excluded from equality and hashing
  (``compare=False``), exactly as the realtime model documents.
* **The sample budget is a hard, checked bound.** A :class:`PlotSeries` refuses to
  exist with more samples than its own ``sample_budget`` field, so "the output is
  bounded" is a property of the type rather than a promise the service makes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from uuid import UUID

from canx.plot.errors import PlotValidationError

#: The default upper bound on the number of samples a plot series may return.
DEFAULT_PLOT_SAMPLE_BUDGET = 1_000

#: The hard upper bound a caller may request, independent of any product default.
MAX_PLOT_SAMPLE_BUDGET = 10_000

#: The smallest budget that can still preserve a distinct first and last point.
MIN_PLOT_SAMPLE_BUDGET = 2


@dataclass(frozen=True, slots=True)
class SignalIdentity:
    """The four facts that name one decodable signal, plus its unit.

    A bare signal name is not an identity: the same name can be declared by two
    DBC assets, by two messages of one asset, or arrive on two channels. Every
    fact that decides "which physical curve is this" is required, and ``unit`` is
    the only optional field because it decides nothing.

    ``asset_id`` is normalized to its canonical UUID string, so an identity built
    from a differently-formatted spelling of the same asset compares equal.
    """

    channel_id: str
    asset_id: str
    message_name: str
    signal_name: str
    #: Presentation metadata, never identity: excluded from ``==`` and ``hash``.
    unit: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        _require_non_blank(self.channel_id, field="channel_id")
        object.__setattr__(
            self, "asset_id", _require_uuid(self.asset_id, field="asset_id")
        )
        _require_non_blank(self.message_name, field="message_name")
        _require_non_blank(self.signal_name, field="signal_name")
        if self.unit is not None and not isinstance(self.unit, str):
            raise PlotValidationError(
                "unit must be a string or None.",
                code="plot.invalid_identity",
                details={"unit": repr(self.unit)},
            )


@dataclass(frozen=True, slots=True)
class PlotSample:
    """One point of a decoded signal series.

    ``time`` is the frame's *normalized* timestamp — the same instant the realtime
    pipeline places a frame at, so live and historical panels agree on the x-axis.
    ``value`` is the physical value the DBC declared (``raw * factor + offset``).
    ``sequence`` is carried as provenance so two samples are never ambiguous and
    the first/last point can be proven by identity rather than by position.
    """

    time: float
    value: float
    sequence: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.time, bool)
            or not isinstance(self.time, (int, float))
            or not isfinite(self.time)
            or self.time < 0
        ):
            raise PlotValidationError(
                "time must be a finite non-negative number of seconds.",
                code="plot.invalid_sample",
                details={"time": repr(self.time)},
            )
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not isfinite(self.value)
        ):
            raise PlotValidationError(
                "value must be a finite number.",
                code="plot.invalid_sample",
                details={"value": repr(self.value)},
            )
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise PlotValidationError(
                "sequence must be a non-negative integer.",
                code="plot.invalid_sample",
                details={"sequence": repr(self.sequence)},
            )
        object.__setattr__(self, "time", float(self.time))
        object.__setattr__(self, "value", float(self.value))


@dataclass(frozen=True, slots=True)
class PlotQuery:
    """One bounded historical signal-series request.

    Args:
        session_id: The registered data session to read.
        identity: The four-fact identity of the signal to plot.
        time_start: Inclusive lower bound on the frame's normalized timestamp, or
            ``None`` for "from the start of the session".
        time_end: Inclusive upper bound on the frame's normalized timestamp, or
            ``None`` for "to the end of the session".
        sample_budget: The hard upper bound on the returned sample count. Must be
            at least :data:`MIN_PLOT_SAMPLE_BUDGET` and at most
            :data:`MAX_PLOT_SAMPLE_BUDGET`.
    """

    session_id: str
    identity: SignalIdentity
    time_start: float | None = None
    time_end: float | None = None
    sample_budget: int = DEFAULT_PLOT_SAMPLE_BUDGET

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", _require_uuid(self.session_id, field="session_id")
        )
        if not isinstance(self.identity, SignalIdentity):
            raise PlotValidationError(
                "identity must be a SignalIdentity.",
                code="plot.invalid_query",
                details={"type": type(self.identity).__name__},
            )
        _require_optional_time(self.time_start, field="time_start")
        _require_optional_time(self.time_end, field="time_end")
        if (
            self.time_start is not None
            and self.time_end is not None
            and self.time_end < self.time_start
        ):
            raise PlotValidationError(
                "time_end must not be smaller than time_start.",
                code="plot.invalid_time_window",
                details={"time_start": self.time_start, "time_end": self.time_end},
            )
        _require_budget(self.sample_budget)


@dataclass(frozen=True, slots=True)
class PlotSeries:
    """One bounded, deterministic signal series and its provenance.

    ``samples`` is ordered by non-decreasing timestamp, so a plot never has to
    re-sort and two runs over the same data produce the same curve.
    ``matched_frame_count`` counts every frame that matched the identity *before*
    any reduction, so a caller can tell a genuinely sparse signal from a heavily
    downsampled one. ``downsampled`` is derived, never stored, so it can never
    disagree with the samples it describes.
    """

    session_id: str
    identity: SignalIdentity
    samples: tuple[PlotSample, ...]
    matched_frame_count: int
    sample_budget: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", _require_uuid(self.session_id, field="session_id")
        )
        if not isinstance(self.identity, SignalIdentity):
            raise PlotValidationError(
                "identity must be a SignalIdentity.",
                code="plot.invalid_series",
                details={"type": type(self.identity).__name__},
            )
        if not isinstance(self.samples, tuple) or not all(
            isinstance(sample, PlotSample) for sample in self.samples
        ):
            raise PlotValidationError(
                "samples must be a tuple of PlotSample values.",
                code="plot.invalid_series",
            )
        _require_count(self.matched_frame_count, field="matched_frame_count")
        _require_budget(self.sample_budget)
        if len(self.samples) > self.sample_budget:
            raise PlotValidationError(
                "A plot series must never carry more samples than its budget.",
                code="plot.budget_exceeded",
                details={
                    "sample_count": len(self.samples),
                    "sample_budget": self.sample_budget,
                },
            )
        if self.matched_frame_count < len(self.samples):
            raise PlotValidationError(
                "matched_frame_count must not be smaller than the returned samples.",
                code="plot.invalid_series",
                details={
                    "matched_frame_count": self.matched_frame_count,
                    "sample_count": len(self.samples),
                },
            )
        for previous, following in zip(self.samples, self.samples[1:], strict=False):
            if following.time < previous.time:
                raise PlotValidationError(
                    "plot samples must be ordered by non-decreasing timestamp.",
                    code="plot.unordered_series",
                    details={
                        "previous_time": previous.time,
                        "following_time": following.time,
                    },
                )

    @property
    def downsampled(self) -> bool:
        """Return whether reduction dropped any matched frame."""
        return len(self.samples) < self.matched_frame_count

    @property
    def empty(self) -> bool:
        """Return whether no frame matched the identity in the window."""
        return not self.samples


def _require_non_blank(value: object, *, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PlotValidationError(
            f"{field} must be a non-blank string.",
            code="plot.invalid_identity",
            details={field: repr(value)},
        )


def _require_uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PlotValidationError(
            f"{field} must be a non-empty UUID string.",
            code="plot.invalid_identifier",
            details={field: repr(value)},
        )
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError) as error:
        raise PlotValidationError(
            f"{field} is not a UUID.",
            code="plot.invalid_identifier",
            details={field: value},
        ) from error


def _require_optional_time(value: object, *, field: str) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise PlotValidationError(
            f"{field} must be a finite non-negative number of seconds or None.",
            code="plot.invalid_time_window",
            details={field: repr(value)},
        )


def _require_count(value: object, *, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PlotValidationError(
            f"{field} must be a non-negative integer.",
            code="plot.invalid_series",
            details={field: repr(value)},
        )


def _require_budget(value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PlotValidationError(
            "sample_budget must be an integer.",
            code="plot.invalid_budget",
            details={"sample_budget": repr(value)},
        )
    if value < MIN_PLOT_SAMPLE_BUDGET:
        raise PlotValidationError(
            "sample_budget must be at least 2: a series of one point is not a curve"
            " and cannot preserve both a first and a last point.",
            code="plot.invalid_budget",
            details={"sample_budget": value, "minimum": MIN_PLOT_SAMPLE_BUDGET},
        )
    if value > MAX_PLOT_SAMPLE_BUDGET:
        raise PlotValidationError(
            "sample_budget exceeds the maximum number of samples a series may carry.",
            code="plot.budget_exceeded",
            details={"sample_budget": value, "maximum": MAX_PLOT_SAMPLE_BUDGET},
        )
