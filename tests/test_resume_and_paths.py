"""Resumability and data-root resolution.

Two failures that only show up on somebody else's machine: re-running `extract`
against a manifest full of real data gaps, and importing the Dataset after
building into a non-default data root.
"""
from __future__ import annotations

import csv
from datetime import date

import pytest

from swp_data import extract
from swp_data.extract import _COLS, _is_permanent_gap
from swp_data.settings import Settings


def write_manifest(settings: Settings, rows: list[dict]) -> None:
    path = settings.layout.manifest_file("ionex")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_COLS)
        writer.writeheader()
        writer.writerows(rows)


def gap_row(key: str) -> dict:
    return {
        "source": "ionex", "key": key, "expected_filename": f"codg{key[-3:]}0.15i.Z",
        "status": "failed", "reason": "404", "n_bytes": 0,
        "checked_at": "2015-01-01T00:00:00+00:00",
    }


class TestPermanentGap:
    def test_404_is_permanent(self):
        assert _is_permanent_gap(gap_row("2015-001"))

    @pytest.mark.parametrize("reason", ["timeout", "http_503", "connection:boom"])
    def test_transient_failures_are_retried(self, reason):
        row = gap_row("2015-001") | {"reason": reason}
        assert not _is_permanent_gap(row)

    def test_unseen_key_is_not_a_gap(self):
        assert not _is_permanent_gap(None)


class TestIonexResume:
    """`pull_ionex` had its own inline skip logic that omitted the 404 rule, so
    every known gap was re-requested (twice -- both naming schemes) on every run.
    """

    def test_known_gap_is_not_re_requested(self, tmp_path, monkeypatch):
        settings = Settings(data_root=tmp_path)
        write_manifest(settings, [gap_row("2015-001")])

        calls = []
        monkeypatch.setattr(extract, "download", lambda *a, **k: calls.append(a))

        extract.pull_ionex(settings, object(), date(2015, 1, 1), date(2015, 1, 1))
        assert calls == []

    def test_unseen_day_is_still_fetched(self, tmp_path, monkeypatch):
        """The control: skipping must not swallow days we have never tried."""
        settings = Settings(data_root=tmp_path)
        write_manifest(settings, [gap_row("2015-001")])

        calls = []
        monkeypatch.setattr(
            extract, "download",
            lambda *a, **k: (calls.append(a),
                             {"status": "downloaded", "reason": "", "n_bytes": 4096})[1],
        )

        extract.pull_ionex(settings, object(), date(2015, 1, 2), date(2015, 1, 2))
        assert len(calls) == 1


class TestDataRootResolution:
    """The CLI honoured config.yaml and SWP_DATA_ROOT; the Dataset hardcoded
    "data". Building into a non-default root produced a working build and a
    Dataset pointing somewhere nothing was ever written.
    """

    def test_default_root_follows_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWP_DATA_ROOT", str(tmp_path))
        from swp_data.dataset import default_root

        assert default_root() == tmp_path / "gold" / "training_windows"

    def test_default_root_is_read_at_call_time(self, tmp_path, monkeypatch):
        """Not captured at import, or the env var would have to precede the import."""
        from swp_data.dataset import default_root

        monkeypatch.setenv("SWP_DATA_ROOT", str(tmp_path / "one"))
        first = default_root()
        monkeypatch.setenv("SWP_DATA_ROOT", str(tmp_path / "two"))
        assert default_root() != first

    def test_missing_gold_names_the_build_command(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWP_DATA_ROOT", str(tmp_path))
        from swp_data.dataset import DTECWindowDataset

        with pytest.raises(FileNotFoundError, match="assemble windows"):
            DTECWindowDataset(split="train")
