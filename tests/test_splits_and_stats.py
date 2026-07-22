"""Split boundaries and normalization statistics.

Both were silently wrong in the shipped artifact: train's last window had its
targets inside val's period, and the recorded normalization was weighted by how
many overlapping windows each frame happened to fall into.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from swp_data.assemble import split_window_starts, window_stats
from swp_data.config import (DRIVER_FEATURES, INPUT_STEPS, NLAT, NLON,
                             SPLIT_EMBARGO_STEPS, TARGET_STEPS)

TOTAL_STEPS = INPUT_STEPS + TARGET_STEPS
CADENCE = 7200
TRAIN_END, VAL_END = 2019, 2022


def frames_spanning(start="2018-01-01", end="2024-12-31"):
    idx = pd.date_range(start, end, freq=f"{CADENCE}s", tz="UTC")
    return idx.to_numpy(dtype="datetime64[s]").astype(np.int64)


def all_starts(timestamps):
    return np.arange(len(timestamps) - TOTAL_STEPS + 1, dtype=np.int64)


def year_of(epoch):
    return pd.to_datetime(epoch, unit="s", utc=True).year


class TestSplitPurge:
    @pytest.fixture
    def split(self):
        ts = frames_spanning()
        return ts, split_window_starts(all_starts(ts), ts, TRAIN_END, VAL_END, CADENCE)

    def test_no_window_crosses_a_boundary(self, split):
        """The original bug: a train window whose targets land in val's period."""
        ts, sp = split
        for name, starts in sp.items():
            for s in starts:
                first, last = year_of(ts[s]), year_of(ts[s + TOTAL_STEPS - 1])
                assert first == last or (first <= TRAIN_END) == (last <= TRAIN_END)

    def test_train_windows_end_before_val_begins(self, split):
        ts, sp = split
        train_last = ts[sp["train"] + TOTAL_STEPS - 1].max()
        val_first = ts[sp["val"]].min()
        assert train_last < val_first

    def test_splits_share_no_frames(self, split):
        ts, sp = split
        def occupied(starts):
            return set((starts[:, None] + np.arange(TOTAL_STEPS)[None, :]).ravel().tolist())
        tr, va, te = (occupied(sp[k]) for k in ("train", "val", "test"))
        assert not (tr & va) and not (va & te) and not (tr & te)

    def test_embargo_gap_is_enforced(self, split):
        ts, sp = split
        train_last = ts[sp["train"] + TOTAL_STEPS - 1].max()
        val_first = ts[sp["val"]].min()
        assert val_first - train_last >= SPLIT_EMBARGO_STEPS * CADENCE

    def test_splits_no_longer_partition_the_windows(self, split):
        """Purge + embargo drop windows on purpose; nothing should silently
        reassign them to keep the totals tidy."""
        ts, sp = split
        assert sum(len(s) for s in sp.values()) < len(all_starts(ts))

    def test_every_split_still_populated(self, split):
        _, sp = split
        assert all(len(s) > 0 for s in sp.values())

    def test_zero_embargo_still_purges(self):
        """Purge and embargo are independent; disabling one keeps the other."""
        ts = frames_spanning()
        sp = split_window_starts(all_starts(ts), ts, TRAIN_END, VAL_END, CADENCE,
                                 embargo_steps=0)
        train_last = ts[sp["train"] + TOTAL_STEPS - 1].max()
        assert train_last < ts[sp["val"]].min()


class TestNormalizationOverUniqueFrames:
    @pytest.fixture
    def series(self):
        n = 400
        rng = np.random.default_rng(0)
        dtec = rng.normal(3.0, 13.0, (n, NLAT, NLON)).astype(np.float32)
        omni = rng.normal(0.0, 1.0, (n, len(DRIVER_FEATURES))).astype(np.float32)
        return dtec, omni

    def test_matches_direct_mean_over_the_frames_used(self, series):
        """The definition: mean over unique train input frames, each counted once."""
        dtec, omni = series
        starts = np.arange(len(dtec) - TOTAL_STEPS + 1, dtype=np.int64)
        mean, std, omean, ostd = window_stats(dtec, omni, starts, chunk_size=64)

        frames = np.unique((starts[:, None] + np.arange(INPUT_STEPS)[None, :]).ravel())
        assert mean == pytest.approx(dtec[frames].astype(np.float64).mean(), rel=1e-9)
        assert std == pytest.approx(dtec[frames].astype(np.float64).std(), rel=1e-9)
        assert omean == pytest.approx(omni[frames].astype(np.float64).mean(axis=0), rel=1e-9)
        assert ostd == pytest.approx(omni[frames].astype(np.float64).std(axis=0), rel=1e-9)

    def test_differs_from_overlap_weighted(self, series):
        """Guard against regressing to per-window accumulation.

        A frame-varying signal makes multiplicity weighting visible: interior
        frames appear in INPUT_STEPS windows, edge frames in fewer.
        """
        dtec, omni = series
        n = len(dtec)
        dtec = dtec + np.arange(n, dtype=np.float32)[:, None, None]   # ramp
        starts = np.arange(n - TOTAL_STEPS + 1, dtype=np.int64)

        unique_mean, *_ = window_stats(dtec, omni, starts, chunk_size=64)
        idx = starts[:, None] + np.arange(INPUT_STEPS)[None, :]
        weighted_mean = dtec[idx].astype(np.float64).mean()
        assert unique_mean != pytest.approx(weighted_mean, rel=1e-12)

    def test_chunk_size_does_not_change_the_result(self, series):
        dtec, omni = series
        starts = np.arange(len(dtec) - TOTAL_STEPS + 1, dtype=np.int64)
        a = window_stats(dtec, omni, starts, chunk_size=7)
        b = window_stats(dtec, omni, starts, chunk_size=4096)
        assert a[0] == pytest.approx(b[0], rel=1e-9)
        assert a[1] == pytest.approx(b[1], rel=1e-9)

    def test_empty_train_split_is_rejected(self, series):
        dtec, omni = series
        with pytest.raises(ValueError, match="zero windows"):
            window_stats(dtec, omni, np.array([], dtype=np.int64), chunk_size=64)
