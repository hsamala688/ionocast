"""Driver fills must be bounded, masked, and must invalidate the windows they touch.

An unbounded `interpolate(method="time")` draws a straight line across an
arbitrarily long outage. OMNI has genuine multi-day plasma gaps, so that
fabricates driver history -- and because the fill leaves no NaN behind, both of
the pipeline's "no NaN survives" guards report clean.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from swp_data.assemble import (align_kp_to_timestamps, align_omni_to_timestamps,
                               valid_window_starts)
from swp_data.config import (DRIVER_FEATURES, INPUT_STEPS, NLAT, NLON,
                             OMNI_HRO_FEATURES, TARGET_STEPS)

FRAMES = pd.date_range("2015-01-01", "2015-01-11", freq="1h")
OUTAGE_START, OUTAGE_END = "2015-01-04", "2015-01-07"


def epochs(index: pd.DatetimeIndex) -> np.ndarray:
    return index.to_numpy(dtype="datetime64[s]").astype(np.int64)


def frame_at(stamp: str) -> int:
    return int(np.where(FRAMES == pd.Timestamp(stamp))[0][0])


@pytest.fixture
def omni_with_outage():
    """5-minute OMNI where only proton_density has a 3-day outage."""
    index = pd.date_range("2015-01-01", "2015-01-11", freq="5min")
    frame = pd.DataFrame(
        {col: np.linspace(0.0, 100.0, len(index)) for col in OMNI_HRO_FEATURES},
        index=index,
    )
    gap = (index >= OUTAGE_START) & (index < OUTAGE_END)
    frame.loc[gap, "proton_density"] = np.nan
    return frame


@pytest.fixture
def kp_with_missing_window():
    """3-hourly Kp with one whole day of windows dropped by the producer."""
    index = pd.date_range("2015-01-01", "2015-01-11", freq="3h")
    kp = pd.DataFrame({"kp": np.arange(len(index), dtype=float) % 9.0}, index=index)
    lost = (kp.index >= "2015-01-05") & (kp.index < "2015-01-06")
    return kp.drop(kp.index[lost])


class TestOmniFill:
    def test_long_outage_is_not_filled(self, omni_with_outage):
        values, _ = align_omni_to_timestamps(omni_with_outage, epochs(FRAMES))
        col = OMNI_HRO_FEATURES.index("proton_density")
        assert np.isnan(values[frame_at("2015-01-05 12:00"), col])

    def test_short_extrapolation_is_still_filled(self, omni_with_outage):
        """One hour past the last observation is inside the tolerance."""
        values, _ = align_omni_to_timestamps(omni_with_outage, epochs(FRAMES))
        col = OMNI_HRO_FEATURES.index("proton_density")
        assert not np.isnan(values[frame_at("2015-01-04 01:00"), col])

    def test_gaps_are_measured_per_channel(self, omni_with_outage):
        """An IMF outage does not imply a plasma outage, and vice versa."""
        values, _ = align_omni_to_timestamps(omni_with_outage, epochs(FRAMES))
        col = OMNI_HRO_FEATURES.index("b_magnitude")
        assert not np.isnan(values[:, col]).any()

    def test_imputed_mask_marks_filled_values(self, omni_with_outage):
        _, imputed = align_omni_to_timestamps(omni_with_outage, epochs(FRAMES))
        col = OMNI_HRO_FEATURES.index("proton_density")
        assert imputed[frame_at("2015-01-05 12:00"), col]

    def test_imputed_mask_is_false_on_exact_observations(self, omni_with_outage):
        """Frames on the hour coincide with a 5-minute OMNI sample."""
        _, imputed = align_omni_to_timestamps(omni_with_outage, epochs(FRAMES))
        col = OMNI_HRO_FEATURES.index("b_magnitude")
        assert not imputed[frame_at("2015-01-02 00:00"), col]

    def test_tolerance_is_configurable(self, omni_with_outage):
        col = OMNI_HRO_FEATURES.index("proton_density")
        generous, _ = align_omni_to_timestamps(
            omni_with_outage, epochs(FRAMES), max_gap_minutes=10_000.0
        )
        assert not np.isnan(generous[:, col]).any()


class TestKpFill:
    def test_stale_value_is_not_carried_across_a_missing_window(
        self, kp_with_missing_window
    ):
        values, _ = align_kp_to_timestamps(kp_with_missing_window, epochs(FRAMES))
        assert np.isnan(values[frame_at("2015-01-05 12:00"), 0])

    def test_value_on_its_own_stamp_is_kept_and_not_imputed(
        self, kp_with_missing_window
    ):
        values, imputed = align_kp_to_timestamps(kp_with_missing_window, epochs(FRAMES))
        i = frame_at("2015-01-02 03:00")
        assert not np.isnan(values[i, 0])
        assert not imputed[i, 0]

    def test_mid_window_value_is_retained_but_marked(self, kp_with_missing_window):
        """Kp is a step function, so carrying it across its own window is exact."""
        values, imputed = align_kp_to_timestamps(kp_with_missing_window, epochs(FRAMES))
        i = frame_at("2015-01-02 04:00")
        assert not np.isnan(values[i, 0])
        assert imputed[i, 0]


class TestWindowRejection:
    def test_no_surviving_window_contains_a_rejected_driver(
        self, omni_with_outage, kp_with_missing_window
    ):
        """The whole point of leaving NaN rather than filling."""
        timestamps = epochs(FRAMES)
        omni_values, _ = align_omni_to_timestamps(omni_with_outage, timestamps)
        kp_values, _ = align_kp_to_timestamps(kp_with_missing_window, timestamps)
        drivers = np.concatenate([omni_values, kp_values], axis=1)
        assert drivers.shape[1] == len(DRIVER_FEATURES)

        dtec = np.zeros((len(timestamps), NLAT, NLON), dtype=np.float32)
        # FRAMES is hourly; state the cadence rather than lean on the default,
        # which is 7200 and would reject every window here for the wrong reason.
        starts = valid_window_starts(dtec, drivers, timestamps, cadence_seconds=3600)

        total_steps = INPUT_STEPS + TARGET_STEPS
        bad = np.isnan(drivers).any(axis=1)
        assert not any(bad[s:s + total_steps].any() for s in starts)
        assert len(starts) > 0, "windows clear of the outage should survive"
