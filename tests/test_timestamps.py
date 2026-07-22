"""Frame timestamps must be true UT on every machine.

`interpolate_to_gl` is the single place the pipeline mints epoch seconds, and
every downstream stage reads them back as UTC. Calling `.timestamp()` on a naive
datetime silently reinterprets it in the machine's local zone -- which shifts
every frame by the local UTC offset, by a *different* amount either side of a DST
boundary, while leaving all downstream equality checks passing.

That bug misaligns the IRI baseline against the observations it is subtracted
from, and pulls driver samples from after the frame they are attached to. These
tests pin the convention down.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import numpy as np
import pytest

from swp_data.interpolate import _to_epoch_utc, interpolate_to_gl
from swp_data.parse import parse_ionex

from conftest import EPOCHS

# Spread across the sign of the UTC offset and both sides of northern DST.
TIMEZONES = ["America/Denver", "UTC", "Asia/Kolkata", "Pacific/Auckland"]


@pytest.fixture
def in_timezone():
    """Run a block under a given TZ, restoring the original afterwards."""
    original = os.environ.get("TZ")

    def _set(tz: str) -> None:
        os.environ["TZ"] = tz
        time.tzset()

    yield _set

    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


def _epochs_under(tz, ionex_stream, in_timezone):
    in_timezone(tz)
    maps, src_lats, src_lons = parse_ionex(ionex_stream())
    _, timestamps, _ = interpolate_to_gl(maps, src_lats, src_lons)
    return timestamps


@pytest.mark.parametrize("tz", TIMEZONES)
def test_epochs_are_true_ut(tz, ionex_stream, in_timezone):
    expected = [int(e.replace(tzinfo=timezone.utc).timestamp()) for e in EPOCHS]
    assert list(_epochs_under(tz, ionex_stream, in_timezone)) == expected


def test_epochs_are_reproducible_across_machines(ionex_stream, in_timezone):
    """A build on a UTC cloud box must equal a build on a laptop in local time."""
    results = [_epochs_under(tz, ionex_stream, in_timezone) for tz in TIMEZONES]
    for other in results[1:]:
        assert np.array_equal(results[0], other)


def test_parse_ionex_emits_tz_aware_datetimes(ionex_stream):
    maps, _, _ = parse_ionex(ionex_stream())
    assert maps
    assert all(t.tzinfo is not None for t, _ in maps)


def test_naive_datetime_is_rejected():
    """Regression guard: the conversion must refuse to guess a timezone."""
    with pytest.raises(ValueError, match="naive"):
        _to_epoch_utc(datetime(2015, 1, 1))


def test_dst_boundary_does_not_shift_spacing(ionex_stream, in_timezone):
    """Hourly UT frames stay hourly across a northern DST transition.

    Under the old local-time conversion the offset changed mid-file, producing a
    one-hour spacing irregularity that `valid_window_starts` silently dropped.
    """
    in_timezone("America/Denver")
    epochs = [datetime(2015, 3, 8, h, 0, 0) for h in range(24)]  # US DST spring-forward
    maps, src_lats, src_lons = parse_ionex(ionex_stream(epochs))
    _, timestamps, _ = interpolate_to_gl(maps, src_lats, src_lons)
    assert np.all(np.diff(timestamps) == 3600)
