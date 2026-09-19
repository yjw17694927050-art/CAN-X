"""CAN-X historical Plot domain (V0.4-04).

This package is the **frozen domain and service contract** for historical signal
queries and Runtime-side downsampling. It deliberately builds no UI and exposes no
HTTP surface — V0.4-05 owns the Plot view — and it adds no second data engine: it
reads frames through the existing :mod:`canx.query` bounded query, decodes them
through the existing :mod:`canx.dbc` decoder, and reduces them here, in the
Runtime.

Public surface:

* :class:`~canx.plot.model.SignalIdentity` — a signal named by four facts
  (channel, asset, message, signal), never by a bare name.
* :class:`~canx.plot.model.PlotQuery` — a session, an identity, a time window and a
  bounded sample budget.
* :class:`~canx.plot.model.PlotSeries` — a bounded, ordered, deterministic result.
* :class:`~canx.plot.downsample.StreamingDownsampler` — the online, bounded
  min/max reducer that turns a large dataset into a bounded series.
* :class:`~canx.plot.service.HistoricalSignalQueryService` — the single entry
  point that composes session, segments, frames and DBC decode into one series.
"""

from canx.plot.downsample import StreamingDownsampler, downsample_samples
from canx.plot.errors import (
    PlotAssetError,
    PlotError,
    PlotQueryError,
    PlotSessionError,
    PlotSignalNotFoundError,
    PlotValidationError,
)
from canx.plot.model import (
    DEFAULT_PLOT_SAMPLE_BUDGET,
    MAX_PLOT_SAMPLE_BUDGET,
    MIN_PLOT_SAMPLE_BUDGET,
    PlotQuery,
    PlotSample,
    PlotSeries,
    SignalIdentity,
)
from canx.plot.service import FRAME_PAGE_SIZE, HistoricalSignalQueryService

__all__ = [
    "DEFAULT_PLOT_SAMPLE_BUDGET",
    "FRAME_PAGE_SIZE",
    "MAX_PLOT_SAMPLE_BUDGET",
    "MIN_PLOT_SAMPLE_BUDGET",
    "HistoricalSignalQueryService",
    "PlotAssetError",
    "PlotError",
    "PlotQuery",
    "PlotQueryError",
    "PlotSample",
    "PlotSeries",
    "PlotSessionError",
    "PlotSignalNotFoundError",
    "PlotValidationError",
    "SignalIdentity",
    "StreamingDownsampler",
    "downsample_samples",
]
