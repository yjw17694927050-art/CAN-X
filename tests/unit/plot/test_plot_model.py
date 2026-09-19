"""Behaviour tests for the historical Plot domain models.

The models are the frozen contract, so these tests assert the two facts that keep
two physically different signals apart and the two bounds that keep a series
trustworthy: a signal is identified by channel + asset + message + signal (never
by a name, and never by its unit), and a series can never exceed its budget or
carry unordered timestamps.
"""

from __future__ import annotations

import pytest
from canx.plot.errors import PlotValidationError
from canx.plot.model import (
    MAX_PLOT_SAMPLE_BUDGET,
    PlotQuery,
    PlotSample,
    PlotSeries,
    SignalIdentity,
)

ASSET_A = "11111111-1111-4111-8111-111111111111"
ASSET_B = "22222222-2222-4222-8222-222222222222"
SESSION = "33333333-3333-4333-8333-333333333333"


def identity(**changes: object) -> SignalIdentity:
    values: dict[str, object] = {
        "channel_id": "can0",
        "asset_id": ASSET_A,
        "message_name": "EngineData",
        "signal_name": "EngineSpeed",
        "unit": "rpm",
    }
    values.update(changes)
    return SignalIdentity(**values)  # type: ignore[arg-type]


def test_the_channel_is_part_of_the_identity() -> None:
    assert identity(channel_id="can0") != identity(channel_id="can1")


def test_the_asset_is_part_of_the_identity() -> None:
    assert identity(asset_id=ASSET_A) != identity(asset_id=ASSET_B)


def test_the_message_is_part_of_the_identity() -> None:
    assert identity(message_name="EngineData") != identity(message_name="OtherData")


def test_the_signal_name_is_part_of_the_identity() -> None:
    assert identity(signal_name="EngineSpeed") != identity(signal_name="CoolantTemp")


def test_the_unit_is_not_part_of_the_identity() -> None:
    """Unit is presentation: the same signal in two units is the same signal."""
    assert identity(unit="rpm") == identity(unit=None)
    assert identity(unit="rpm") == identity(unit="rad/s")
    assert hash(identity(unit="rpm")) == hash(identity(unit=None))


def test_an_asset_id_is_normalized_to_its_canonical_uuid_form() -> None:
    assert identity(asset_id=ASSET_A.upper()) == identity(asset_id=ASSET_A)


@pytest.mark.parametrize("field", ["channel_id", "message_name", "signal_name"])
def test_a_blank_identity_fact_is_refused(field: str) -> None:
    with pytest.raises(PlotValidationError):
        identity(**{field: "   "})


def test_a_non_uuid_asset_id_is_refused() -> None:
    with pytest.raises(PlotValidationError):
        identity(asset_id="not-a-uuid")


def test_a_non_string_unit_is_refused() -> None:
    with pytest.raises(PlotValidationError):
        identity(unit=7)


def test_a_query_defaults_to_the_bounded_budget() -> None:
    query = PlotQuery(session_id=SESSION, identity=identity())
    assert query.sample_budget > 1
    assert query.time_start is None
    assert query.time_end is None


@pytest.mark.parametrize(
    "budget", [0, 1, -1, True, 1.5], ids=["zero", "one", "neg", "bool", "float"]
)
def test_an_unusable_sample_budget_is_refused(budget: object) -> None:
    with pytest.raises(PlotValidationError):
        PlotQuery(session_id=SESSION, identity=identity(), sample_budget=budget)  # type: ignore[arg-type]


def test_a_budget_above_the_hard_maximum_is_refused() -> None:
    with pytest.raises(PlotValidationError):
        PlotQuery(
            session_id=SESSION,
            identity=identity(),
            sample_budget=MAX_PLOT_SAMPLE_BUDGET + 1,
        )


def test_an_inverted_time_window_is_refused() -> None:
    with pytest.raises(PlotValidationError) as info:
        PlotQuery(session_id=SESSION, identity=identity(), time_start=10.0, time_end=1.0)
    assert info.value.code == "plot.invalid_time_window"


def test_a_series_cannot_exceed_its_own_budget() -> None:
    samples = tuple(PlotSample(time=float(i), value=float(i), sequence=i) for i in range(5))
    with pytest.raises(PlotValidationError) as info:
        PlotSeries(
            session_id=SESSION,
            identity=identity(),
            samples=samples,
            matched_frame_count=5,
            sample_budget=4,
        )
    assert info.value.code == "plot.budget_exceeded"


def test_a_series_cannot_carry_unordered_timestamps() -> None:
    samples = (
        PlotSample(time=2.0, value=0.0, sequence=0),
        PlotSample(time=1.0, value=0.0, sequence=1),
    )
    with pytest.raises(PlotValidationError) as info:
        PlotSeries(
            session_id=SESSION,
            identity=identity(),
            samples=samples,
            matched_frame_count=2,
            sample_budget=10,
        )
    assert info.value.code == "plot.unordered_series"


def test_downsampled_and_empty_are_derived_from_the_samples() -> None:
    samples = tuple(PlotSample(time=float(i), value=float(i), sequence=i) for i in range(2))
    series = PlotSeries(
        session_id=SESSION,
        identity=identity(),
        samples=samples,
        matched_frame_count=100,
        sample_budget=10,
    )
    assert series.downsampled is True
    assert series.empty is False

    empty = PlotSeries(
        session_id=SESSION,
        identity=identity(),
        samples=(),
        matched_frame_count=0,
        sample_budget=10,
    )
    assert empty.downsampled is False
    assert empty.empty is True
