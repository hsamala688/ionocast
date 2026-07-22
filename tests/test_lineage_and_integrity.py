"""Staleness detection, duplicate-epoch collapsing, and the two guard fixes.

The common thread is silent wrongness. Each of these produced a build that
logged success while carrying data that did not follow from its inputs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from swp_data.extract import _is_credential_failure, _is_permanent_gap
from swp_data.interpolate import dedupe_epochs
from swp_data.lineage import fingerprint, should_rebuild, stored_fingerprint


def write_npz(path, fp=None, **arrays):
    if fp is not None:
        arrays["input_fingerprint"] = fp
    np.savez(path, **arrays)
    return path


class TestFingerprint:
    def test_same_inputs_same_digest(self):
        a = np.arange(10)
        assert fingerprint(a, "x") == fingerprint(np.arange(10), "x")

    def test_value_change_is_detected(self):
        a = np.arange(10.0)
        b = a.copy(); b[3] += 1e-9
        assert fingerprint(a) != fingerprint(b)

    def test_parts_are_separated(self):
        """('ab','c') and ('a','bc') must not collide."""
        assert fingerprint("ab", "c") != fingerprint("a", "bc")

    def test_list_order_matters(self):
        assert fingerprint([1, 2]) != fingerprint([2, 1])


class TestShouldRebuild:
    def test_missing_output_rebuilds(self, tmp_path):
        assert should_rebuild(tmp_path / "nope.npz", "fp", False, "x")

    def test_matching_fingerprint_skips(self, tmp_path):
        p = write_npz(tmp_path / "a.npz", fp="abc", data=np.arange(3))
        assert not should_rebuild(p, "abc", False, "x")

    def test_changed_inputs_rebuild(self, tmp_path):
        """The core failure: regenerating an upstream table left this in place."""
        p = write_npz(tmp_path / "a.npz", fp="abc", data=np.arange(3))
        assert should_rebuild(p, "different", False, "x")

    def test_output_without_a_fingerprint_rebuilds(self, tmp_path):
        """Unverifiable provenance is treated as stale, not as fine."""
        p = write_npz(tmp_path / "a.npz", data=np.arange(3))
        assert should_rebuild(p, "abc", False, "x")

    def test_overwrite_always_rebuilds(self, tmp_path):
        p = write_npz(tmp_path / "a.npz", fp="abc", data=np.arange(3))
        assert should_rebuild(p, "abc", True, "x")

    def test_truncated_file_rebuilds(self, tmp_path):
        p = tmp_path / "a.npz"
        p.write_bytes(b"PK\x03\x04 not a real npz")
        assert should_rebuild(p, "abc", False, "x")

    def test_fingerprints_chain(self, tmp_path):
        """A change upstream must propagate downstream without rehashing bulk data."""
        up = write_npz(tmp_path / "up.npz", fp="v1", data=np.arange(3))
        derived_v1 = fingerprint(stored_fingerprint(up))
        write_npz(tmp_path / "up.npz", fp="v2", data=np.arange(3))
        assert fingerprint(stored_fingerprint(up)) != derived_v1


class TestDedupeEpochs:
    def make(self, timestamps, values=None):
        ts = np.asarray(timestamps, dtype=np.int64)
        if values is None:
            values = np.arange(len(ts), dtype=np.float32)
        tec = np.asarray(values, dtype=np.float32)[:, None, None] * np.ones((1, 2, 2), np.float32)
        return tec, ts

    def test_day_boundary_duplicate_collapsed(self):
        """Each IONEX file spans 00:00..24:00, so consecutive days share an instant."""
        tec, ts = self.make([0, 3600, 7200, 7200, 10800])
        _, out_ts, n, _ = dedupe_epochs(tec, ts)
        assert n == 1
        assert np.array_equal(out_ts, [0, 3600, 7200, 10800])

    def test_first_copy_is_kept(self):
        tec, ts = self.make([0, 7200, 7200], values=[1.0, 2.0, 9.0])
        out_tec, _, _, _ = dedupe_epochs(tec, ts)
        assert out_tec[-1, 0, 0] == pytest.approx(2.0)

    def test_disagreement_is_measured(self):
        """Duplicates should be identical; report it when they are not."""
        tec, ts = self.make([0, 7200, 7200], values=[1.0, 2.0, 2.5])
        stats = dedupe_epochs(tec, ts)[3]
        assert stats["max"] == pytest.approx(0.5)

    def test_identical_duplicates_report_zero(self):
        tec, ts = self.make([0, 7200, 7200], values=[1.0, 2.0, 2.0])
        stats = dedupe_epochs(tec, ts)[3]
        assert stats["max"] == 0.0 and stats["mean"] == 0.0
        assert stats["relative"] == 0.0

    def test_disagreement_is_relative_to_signal(self):
        """The judged statistic must not scale with TEC magnitude.

        Absolute difference tracks the signal, which swings ~10x over a solar
        cycle -- an absolute threshold fires hardest at solar maximum, where
        relative agreement is actually best.
        """
        # Values chosen exactly representable in float32 so the comparison is
        # about the statistic, not rounding.
        quiet, _ = self.make([0, 7200, 7200], values=[1.0, 10.0, 10.5])
        loud, ts = self.make([0, 7200, 7200], values=[1.0, 100.0, 105.0])
        q = dedupe_epochs(quiet, ts)[3]
        l = dedupe_epochs(loud, ts)[3]

        assert l["mean"] > q["mean"] * 5          # absolute differs 10x
        assert l["relative"] == pytest.approx(q["relative"], rel=1e-6)

    def test_output_is_sorted_and_unique(self):
        tec, ts = self.make([7200, 0, 3600, 7200])
        _, out_ts, _, _ = dedupe_epochs(tec, ts)
        assert np.array_equal(out_ts, np.unique(out_ts))
        assert np.all(np.diff(out_ts) > 0)

    def test_no_duplicates_is_a_no_op(self):
        tec, ts = self.make([0, 3600, 7200])
        out_tec, out_ts, n, stats = dedupe_epochs(tec, ts)
        assert n == 0 and stats["max"] == 0.0
        assert np.array_equal(out_ts, ts) and np.array_equal(out_tec, tec)

    def test_nan_maps_do_not_break_the_comparison(self):
        tec, ts = self.make([0, 7200, 7200])
        tec[2, 0, 0] = np.nan
        _, _, n, stats = dedupe_epochs(tec, ts)
        assert n == 1 and np.isfinite(stats["max"])


class TestExtractGuards:
    @pytest.mark.parametrize("reason", ["auth", "bad_content:html_page"])
    def test_credential_failures_abort(self, reason):
        """An HTML body with HTTP 200 is the Earthdata login page, not data."""
        assert _is_credential_failure(reason)

    @pytest.mark.parametrize("reason", ["404", "timeout", "bad_content:too_small"])
    def test_other_failures_do_not_abort(self, reason):
        assert not _is_credential_failure(reason)

    def test_gap_and_credential_rules_are_distinct(self):
        row = {"status": "failed", "reason": "404"}
        assert _is_permanent_gap(row) and not _is_credential_failure(row["reason"])


class TestF107Finiteness:
    def test_nan_present_in_index_is_not_returned(self):
        """derive_f107_daily interpolates forward-only, so a leading NaN survives
        with its date still in the index. It must not reach IRI."""
        from swp_data.assemble import f107_for_day

        idx = pd.to_datetime(["2015-01-01", "2015-01-02"]).date
        s = pd.Series([np.nan, 120.0], index=idx)
        with pytest.raises(KeyError, match="finite"):
            f107_for_day(s, idx[0])

    def test_falls_back_to_previous_day(self):
        from swp_data.assemble import f107_for_day

        idx = pd.to_datetime(["2015-01-01", "2015-01-02"]).date
        s = pd.Series([110.0, np.nan], index=idx)
        assert f107_for_day(s, idx[1]) == pytest.approx(110.0)

    def test_finite_value_is_returned(self):
        from swp_data.assemble import f107_for_day

        idx = pd.to_datetime(["2015-01-01"]).date
        assert f107_for_day(pd.Series([133.0], index=idx), idx[0]) == pytest.approx(133.0)
