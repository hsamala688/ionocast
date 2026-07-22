"""Cadence resampling and the window spacing contract.

The IONEX record switches from 2-hourly to hourly on 2014-10-19. Because window
length is a frame *count*, that switch silently changes the physical forecast
horizon: 6 input / 3 target frames means "+2/+4/+6 h" before the switch and
"+1/+2/+3 h" after. A build that mixes them trains one model on two problems
with no channel distinguishing them.

These tests pin down the two halves of the fix -- resample to a cadence, then
require every window to match it exactly.
"""
from __future__ import annotations

import numpy as np
import pytest

from swp_data.assemble import (_horizon_labels, decimate_to_cadence,
                               valid_window_starts)
from swp_data.config import INPUT_STEPS, NLAT, NLON, TARGET_STEPS

TOTAL_STEPS = INPUT_STEPS + TARGET_STEPS


def series(timestamps: np.ndarray):
    """Frame series carrying its own index, so we can prove values are untouched."""
    n = len(timestamps)
    dtec = np.arange(n, dtype=np.float32)[:, None, None] * np.ones((1, NLAT, NLON), np.float32)
    omni = np.arange(n, dtype=np.float32)[:, None] * np.ones((1, 6), np.float32)
    return dtec, omni, timestamps.astype(np.int64)


def hourly(n: int, start: int = 0) -> np.ndarray:
    return start + np.arange(n, dtype=np.int64) * 3600


def two_hourly(n: int, start: int = 0) -> np.ndarray:
    return start + np.arange(n, dtype=np.int64) * 7200


class TestDecimation:
    def test_hourly_halves_to_two_hourly(self):
        d, o, t = series(hourly(100))
        _, _, t2 = decimate_to_cadence(d, o, t, 7200)
        assert np.all(np.diff(t2) == 7200)
        assert len(t2) == 50

    def test_two_hourly_passes_through_untouched(self):
        d, o, t = series(two_hourly(50))
        _, _, t2 = decimate_to_cadence(d, o, t, 7200)
        assert np.array_equal(t2, t)

    def test_values_are_never_recomputed(self):
        """Pure index selection: retained rows must be bit-identical."""
        d, o, t = series(hourly(100))
        d2, o2, t2 = decimate_to_cadence(d, o, t, 7200)
        kept = np.searchsorted(t, t2)
        assert np.array_equal(d2, d[kept])
        assert np.array_equal(o2, o[kept])

    def test_odd_hour_phase_survives(self):
        """CODE's 2000-2002 maps sit on ODD UT hours.

        A modular rule (`epoch % 7200 == 0`) would delete all of them -- the three
        highest-activity years in the archive. The greedy rule must not care about
        phase.
        """
        d, o, t = series(two_hourly(50, start=3600))   # 01:00, 03:00, ...
        _, _, t2 = decimate_to_cadence(d, o, t, 7200)
        assert np.array_equal(t2, t)

    def test_resynchronizes_after_a_gap(self):
        """A modular rule would drop every post-gap frame off the grid."""
        t = np.concatenate([hourly(20), hourly(20, start=20 * 3600 + 5 * 86400)])
        d, o, t = series(t)
        _, _, t2 = decimate_to_cadence(d, o, t, 7200)
        gaps = np.diff(t2)
        assert (gaps == 7200).sum() >= 16   # both runs decimated
        assert len(t2) >= 20

    def test_cadence_3600_drops_nothing(self):
        """Era selection happens in the window check, not here."""
        t = np.concatenate([two_hourly(30), hourly(30, start=30 * 7200)])
        d, o, t = series(t)
        _, _, t2 = decimate_to_cadence(d, o, t, 3600)
        assert np.array_equal(t2, t)

    def test_empty_series_is_safe(self):
        d, o, t = series(np.array([], dtype=np.int64))
        assert len(decimate_to_cadence(d, o, t, 7200)[2]) == 0


class TestWindowCadenceContract:
    def test_uniform_but_wrong_cadence_is_rejected(self):
        """The old rule accepted any *self-consistent* spacing. That is what let a
        60%-2-hourly train split and a 100%-hourly val split coexist."""
        d, o, t = series(hourly(40))
        assert len(valid_window_starts(d, o, t, cadence_seconds=7200)) == 0
        assert len(valid_window_starts(d, o, t, cadence_seconds=3600)) > 0

    def test_windows_straddling_the_transition_are_rejected(self):
        two, hr = two_hourly(20), hourly(20, start=20 * 7200)
        d, o, t = series(np.concatenate([two, hr]))
        starts = valid_window_starts(d, o, t, cadence_seconds=7200)
        assert all(np.all(np.diff(t[s:s + TOTAL_STEPS]) == 7200) for s in starts)

    def test_cadence_3600_selects_the_hourly_era(self):
        """One parameter picks the era -- no boundary date hardcoded anywhere."""
        two, hr = two_hourly(20), hourly(30, start=20 * 7200)
        d, o, t = series(np.concatenate([two, hr]))
        starts = valid_window_starts(d, o, t, cadence_seconds=3600)
        assert len(starts) > 0
        assert all(t[s] >= hr[0] for s in starts)

    def test_non_finite_windows_still_rejected(self):
        d, o, t = series(two_hourly(40))
        d[10] = np.nan
        starts = valid_window_starts(d, o, t, cadence_seconds=7200)
        assert all(not np.isnan(d[s:s + TOTAL_STEPS]).any() for s in starts)

    def test_reversed_or_duplicate_timestamps_rejected(self):
        """Subsumes the old strictly-increasing check."""
        t = two_hourly(40).copy()
        t[15] = t[14]
        d, o, t = series(t)
        starts = valid_window_starts(d, o, t, cadence_seconds=7200)
        assert all(np.all(np.diff(t[s:s + TOTAL_STEPS]) == 7200) for s in starts)


class TestHorizonLabels:
    @pytest.mark.parametrize("cadence,history,leads", [
        (7200, "12h", "+2/+4/+6h"),
        (3600, "6h", "+1/+2/+3h"),
    ])
    def test_labels(self, cadence, history, leads):
        assert _horizon_labels(cadence) == (history, leads)
