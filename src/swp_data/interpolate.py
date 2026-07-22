"""Silver stage: interpolate native IONEX 71x73 TEC maps onto the Gauss-Legendre
23x45 grid the SFNO transform expects.

Grid contract:
  - Lmax=22
  - Gauss-Legendre latitudes, nlat=23 (cell-centered; GL nodes never sit at a pole)
  - Equiangular longitudes, nlon=45, 0-360 convention, endpoint=False
  - Poles handled by collapsing the native +-87.5 deg ring to a single averaged
    value (IONEX has no data beyond +-87.5; this is extrapolation, not observation)
"""
from __future__ import annotations

import csv
import logging
from collections import defaultdict
from datetime import datetime

import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.interpolate import RegularGridInterpolator

from .config import INTERPOLATE_CONTRACT, NLAT, NLON
from .lineage import fingerprint, should_rebuild
from .parse import parse_ionex, read_decompress
from .settings import Settings

logger = logging.getLogger(__name__)

# Duplicate maps of the same instant come from two different daily solutions, so
# they never agree exactly. Measured across the full CODE record the mean
# cell-wise difference is a near-constant ~1.4% of signal (1.42% in 2002, 1.42%
# in 2003, 1.45% in 2004, 1.50% in 2005, 1.0% in a quiet 2019 week).
#
# The threshold is therefore RELATIVE. An absolute one is meaningless here: TEC
# swings roughly 10x over a solar cycle, so a fixed TECU limit fires hardest at
# solar maximum -- precisely where relative agreement is best. 5% leaves >3x
# headroom over the observed baseline while still catching a genuinely
# misaligned map, which would differ by tens of percent.
_DUPLICATE_TOLERANCE_FRACTION = 0.05


def target_grid() -> tuple[np.ndarray, np.ndarray]:
    """Gauss-Legendre latitudes (deg) and equiangular 0-360 longitudes (deg)."""
    roots, _ = leggauss(NLAT)                       # roots = cos(colatitude), interior to (-1,1)
    lats = 90.0 - np.degrees(np.arccos(roots))       # -> latitude, ASCENDING S to N (-84.14 .. +84.14)
    lons = np.linspace(0.0, 360.0, NLON, endpoint=False)
    return lats, lons


def _to_0_360(lons: np.ndarray, grid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Convert source longitudes from -180..180 to 0..360 and re-sort columns to match."""
    lons_360 = lons % 360.0
    uniq, idx = np.unique(lons_360, return_index=True)
    return uniq, grid[:, idx]


def interpolate_map(grid: np.ndarray, src_lats: np.ndarray, src_lons: np.ndarray,
                    tgt_lats: np.ndarray, tgt_lons: np.ndarray) -> np.ndarray:
    """Interpolate one native [n_lat, n_lon] IONEX map onto the GL target grid."""
    lons_360, grid_360 = _to_0_360(src_lons, grid)

    # Wrap-pad across the 0/360 seam so the interpolator sees continuity there.
    pad_lons = np.concatenate([lons_360[-1:] - 360.0, lons_360, lons_360[:1] + 360.0])
    pad_grid = np.concatenate([grid_360[:, -1:], grid_360, grid_360[:, :1]], axis=1)

    # src_lats runs 87.5 -> -87.5 (descending); RegularGridInterpolator needs ascending.
    lat_order = np.argsort(src_lats)
    interp = RegularGridInterpolator(
        (src_lats[lat_order], pad_lons), pad_grid[lat_order],
        bounds_error=False, fill_value=None,
    )

    in_range = (tgt_lats >= src_lats.min()) & (tgt_lats <= src_lats.max())
    pts = np.array([[la, lo] for la in tgt_lats[in_range] for lo in tgt_lons])
    vals = interp(pts).reshape(in_range.sum(), NLON)

    out = np.empty((NLAT, NLON), dtype=np.float32)
    out[in_range] = vals

    # Collapse-to-point: rows beyond native coverage get the mean of the
    # nearest native edge ring (top edge for northern gap, bottom for southern).
    if not in_range.all():
        top_val = np.nanmean(grid_360[np.argmax(src_lats)])       # +87.5 ring
        bot_val = np.nanmean(grid_360[np.argmin(src_lats)])       # -87.5 ring
        for i, la in enumerate(tgt_lats):
            if in_range[i]:
                continue
            out[i] = top_val if la > 0 else bot_val

    return out

def _to_epoch_utc(t: datetime) -> int:
    """Epoch seconds for a UT map timestamp.

    This is the single place the pipeline mints epoch seconds, and every
    downstream stage reads them back as UTC. `datetime.timestamp()` on a *naive*
    datetime interprets it in the machine's local zone, which would shift every
    frame by the local UTC offset (and by a different amount either side of a DST
    boundary) while leaving all downstream equality checks passing. So require
    tz-awareness rather than trusting the caller.
    """
    if t.tzinfo is None:
        raise ValueError(
            f"refusing to convert naive datetime {t!r} to an epoch: IONEX epochs "
            "are UT and must be tz-aware (see parse_ionex)"
        )
    return int(t.timestamp())


# Need to understand this function better
def interpolate_to_gl(maps, src_lats, src_lons):
    """maps: list of (timestamp, [71,73] ndarray) from parse_ionex;
    src_lats, src_lons: the native grid vectors parse_ionex returned for these maps.

    Returns (tec [N,23,45] float32, timestamps [N] UTC epoch seconds, and the
    target lats/lons).
    """
    tgt_lats, tgt_lons = target_grid()
    stack = np.stack([
        interpolate_map(grid, src_lats, src_lons, tgt_lats, tgt_lons)
        for _, grid in maps
    ])
    timestamps = np.array([_to_epoch_utc(t) for t, _ in maps], dtype=np.int64)
    return stack, timestamps, (tgt_lats, tgt_lons)


# ---------------------------------------------------------------------------
# Batch builder: interpolate all IONEX -> per-year .npz in data/interpolated_gl23x45/
# ---------------------------------------------------------------------------

def dedupe_epochs(tec: np.ndarray, timestamps: np.ndarray,
                  ) -> tuple[np.ndarray, np.ndarray, int, dict[str, float]]:
    """Collapse the duplicate epochs IONEX creates at day boundaries.

    Each daily file spans 00:00 to 24:00 inclusive, so day N's last map and day
    N+1's first map are the same instant -- roughly 364 duplicates per year.

    They used to survive all the way to windowing, where `np.unique` dropped
    whichever copy sorted first. That cost a redundant IRI evaluation per
    duplicate (the slowest stage in the pipeline) and never checked that the two
    copies agreed. Collapsing them here, where they are created, does both.

    Returns (tec, timestamps, n_dropped, disagreement) where `disagreement`
    holds "max", "mean" and "relative" over the cells of the duplicated maps.

    The two copies come from different daily solutions, so they disagree slightly
    everywhere. "relative" -- mean difference over mean magnitude -- is the one
    to judge on, because the absolute difference tracks the signal: measured
    across the record it stays near 1.4% while the raw TECU value ranges from
    0.08 at solar minimum to 0.48 at maximum. "max" is reported alongside but is
    a single outlier cell and is not a quality signal on its own.
    """
    order = np.argsort(timestamps, kind="stable")
    tec, timestamps = tec[order], timestamps[order]

    keep = np.concatenate([[True], np.diff(timestamps) != 0])
    n_dropped = int((~keep).sum())

    disagreement = {"max": 0.0, "mean": 0.0, "relative": 0.0}
    if n_dropped:
        dup = np.flatnonzero(~keep)
        kept = tec[dup - 1].astype(np.float64)
        diff = np.abs(tec[dup].astype(np.float64) - kept)
        if diff.size and not np.all(np.isnan(diff)):
            mean = float(np.nanmean(diff))
            scale = float(np.nanmean(np.abs(kept)))
            disagreement = {
                "max": float(np.nanmax(diff)),
                "mean": mean,
                "relative": mean / scale if scale > 0 else 0.0,
            }

    return tec[keep], timestamps[keep], n_dropped, disagreement


# Need to understand this function better
def build_interpolated(settings: Settings, year: int | None = None, overwrite: bool = False):
    """Interpolate every present IONEX day to the GL grid, one .npz per year.

    Reads the ionex manifest for present days and skips 404 gaps. Resumable: a
    year is skipped only when its output exists AND was built from exactly the
    set of source files the manifest now lists. Downloading more days for a year
    that was already interpolated therefore rebuilds it -- previously the extra
    days were silently ignored, which is how 218 days of 2023 stayed frozen out
    of a completed build.
    """
    layout = settings.layout
    out_dir = layout.tec_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Group present IONEX days by year from the manifest.
    by_year = defaultdict(list)
    with open(layout.manifest_file("ionex"), newline="") as f:
        for r in csv.DictReader(f):
            if r["status"] in ("present", "downloaded"):
                by_year[int(r["key"].split("-")[0])].append(r)

    grid_written = layout.grid_file.exists()

    for current_year in sorted(by_year):
        if year is not None and current_year != year:
            continue

        dest = out_dir / f"{current_year}.npz"
        rows = sorted(by_year[current_year], key=lambda x: x["key"])
        source_fp = fingerprint(INTERPOLATE_CONTRACT,
                                [(r["key"], r["expected_filename"]) for r in rows])

        if not should_rebuild(dest, source_fp, overwrite, str(current_year)):
            continue

        tec_parts, ts_parts = [], []
        for r in rows:
            doy = int(r["key"].split("-")[1])
            path = layout.ionex_day_dir(current_year, doy) / r["expected_filename"]
            maps, src_lats, src_lons = parse_ionex(read_decompress(str(path)))
            tec, ts, (tgt_lats, tgt_lons) = interpolate_to_gl(maps, src_lats, src_lons)
            tec_parts.append(tec)
            ts_parts.append(ts)

        if not tec_parts:
            logger.warning("%d: manifest lists no readable days, skip", current_year)
            continue

        tec_all = np.concatenate(tec_parts)
        ts_all = np.concatenate(ts_parts)

        tec_all, ts_all, n_dup, disagreement = dedupe_epochs(tec_all, ts_all)
        if n_dup:
            logger.info("%d: collapsed %d duplicate day-boundary epochs "
                        "(disagreement %.2f%% of signal; mean %.3g / max %.3g TECU)",
                        current_year, n_dup, 100 * disagreement["relative"],
                        disagreement["mean"], disagreement["max"])
            if disagreement["relative"] > _DUPLICATE_TOLERANCE_FRACTION:
                logger.warning(
                    "%d: duplicate epochs disagree by %.1f%% of signal, above the %.0f%% "
                    "expected from separate daily solutions. The 24:00 map of one day "
                    "and the 00:00 map of the next are the same instant; the earlier "
                    "copy was kept, but check for a misaligned map.",
                    current_year, 100 * disagreement["relative"],
                    100 * _DUPLICATE_TOLERANCE_FRACTION,
                )

        np.savez(dest, tec=tec_all, timestamps=ts_all, input_fingerprint=source_fp)
        logger.info("%d: %d maps -> %s", current_year, tec_all.shape[0], dest.name)

        if not grid_written:
            np.savez(layout.grid_file, lats=tgt_lats, lons=tgt_lons)
            grid_written = True
