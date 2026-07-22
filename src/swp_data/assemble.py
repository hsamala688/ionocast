"""Silver + gold stage: IRI baseline, dTEC residual, driver alignment, windowing.

Baseline residual definition:
    dTEC = IONEX vTEC interpolated to GL23x45 - IRI vTEC on GL23x45

The plasmaspheric offset in IONEX-minus-IRI is intentionally retained for the
baseline dataset. It is expected to be mostly zonal and representable by the
SFNO m=0 modes; a learned zonal correction can be added later.

Four subcommands (swp-data assemble {iri,dtec,omni,windows}), run in order:
    silver/iri_gl23x45/{year}.npz          -> iri [N,23,45], timestamps [N]
    silver/dtec_gl23x45/{year}.npz         -> dtec [N,23,45], timestamps [N]
    silver/omni_aligned_gl23x45/{year}.npz -> drivers [N,6], timestamps [N]
    gold/training_windows/*.npy            -> normalized train/val/test windows
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import PyIRI
import PyIRI.main_library as iri_main

from .config import (AALT, DRIVER_FEATURES, DTEC_CONTRACT, INPUT_STEPS,
                     IRI_CONTRACT, KP_WINDOW_HOURS, LMAX, NLAT, NLON,
                     OMNI_CONTRACT, OMNI_HRO_FEATURES, OMNI_MAX_GAP_MINUTES,
                     SPLIT_EMBARGO_STEPS, TARGET_CADENCE_SECONDS, TARGET_STEPS)
from .lineage import (dependency_version, fingerprint, should_rebuild,
                      stored_fingerprint)
from .parse import parse_omni_hro, read_decompress
from .settings import DataLayout
from .sources.omni import hro_dest

logger = logging.getLogger(__name__)


def load_f107(data_root: Path) -> pd.Series:
    f107 = pd.read_parquet(DataLayout(data_root).f107_daily)
    if "f107_obs" not in f107.columns:
        raise ValueError(f"F10.7 table missing f107_obs column: {list(f107.columns)}")
    out = f107["f107_obs"].astype(float)
    out.index = pd.to_datetime(out.index).date
    return out


def load_gl_grid(data_root: Path) -> tuple[np.ndarray, np.ndarray]:
    grid_path = DataLayout(data_root).grid_file
    grid = np.load(grid_path)
    lats = grid["lats"].astype(float)
    lons = grid["lons"].astype(float)
    if lats.shape != (NLAT,) or lons.shape != (NLON,):
        raise ValueError(f"Unexpected GL grid shape: lats={lats.shape}, lons={lons.shape}")
    return lats, lons


def iter_years(data_root: Path, requested_year: int | None) -> list[int]:
    if requested_year is not None:
        return [requested_year]
    source_dir = DataLayout(data_root).tec_dir
    return sorted(int(path.stem) for path in source_dir.glob("*.npz") if path.stem != "grid")


def timestamps_to_frame(timestamps: np.ndarray) -> pd.DataFrame:
    dt = pd.to_datetime(timestamps, unit="s", utc=True)
    return pd.DataFrame({
        "timestamp": timestamps.astype(np.int64),
        "datetime": dt,
        "date": dt.date,
        "ut": dt.hour + dt.minute / 60.0 + dt.second / 3600.0,
    })


def iri_for_day(day, ut_values: np.ndarray, lats: np.ndarray, lons: np.ndarray,
                f107_value: float) -> np.ndarray:
    lat2d, lon2d = np.meshgrid(lats, lons, indexing="ij")
    alat = lat2d.ravel()
    alon = lon2d.ravel()

    *_, edp = iri_main.IRI_density_1day(
        day.year,
        day.month,
        day.day,
        ut_values.astype(float),
        alon,
        alat,
        AALT,
        f107_value,
        PyIRI.coeff_dir,
        0,
    )
    tec = iri_main.edp_to_vtec(edp, AALT)
    return tec.reshape(len(ut_values), NLAT, NLON).astype(np.float32)


def f107_for_day(f107: pd.Series, day) -> float:
    """Daily observed F10.7 for the IRI baseline, falling back one day.

    Membership in the index is not enough: `derive_f107_daily` interpolates with
    pandas' default forward-only direction, so a leading NaN survives into a row
    that still has a date. A NaN reaching IRI_density_1day would propagate
    silently into the baseline -- and therefore into every dTEC value, inputs and
    targets alike. Require a finite value, not merely a present one.
    """
    if day in f107.index:
        value = float(f107.loc[day])
        if np.isfinite(value):
            return value
        logger.warning("%s: F10.7 present but not finite; falling back a day", day)

    previous_day = day - pd.Timedelta(days=1)
    if previous_day in f107.index:
        value = float(f107.loc[previous_day])
        if np.isfinite(value):
            logger.info("%s: missing F10.7; using %s for boundary timestamp",
                        day, previous_day)
            return value

    raise KeyError(f"No finite F10.7 value for {day} or the preceding day")


def build_iri_cache(data_root: Path, year: int | None = None, overwrite: bool = False) -> None:
    layout = DataLayout(data_root)
    source_dir = layout.tec_dir
    out_dir = layout.iri_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    lats, lons = load_gl_grid(data_root)
    f107 = load_f107(data_root)

    for y in iter_years(data_root, year):
        source_path = source_dir / f"{y}.npz"
        dest_path = out_dir / f"{y}.npz"

        if not source_path.exists():
            logger.warning("%d: missing %s, skip", y, source_path)
            continue

        source = np.load(source_path)
        timestamps = source["timestamps"].astype(np.int64)
        frame = timestamps_to_frame(timestamps)

        # F10.7 is fingerprinted alongside the TEC frames because it is the
        # dependency that actually went stale: regenerating f107_daily.parquet
        # from GFZ left every iri_gl23x45 year in place, so the baseline -- and
        # therefore every dTEC value, inputs and targets -- stayed CelesTrak-derived.
        days = sorted(frame["date"].unique())
        f107_used = np.array([f107_for_day(f107, d) for d in days], dtype=np.float64)
        source_fp = fingerprint(IRI_CONTRACT,
                                dependency_version("PyIRI"),
                                timestamps,
                                stored_fingerprint(source_path) or "unknown",
                                f107_used)

        if not should_rebuild(dest_path, source_fp, overwrite, f"{y} IRI"):
            continue

        iri = np.empty((len(timestamps), NLAT, NLON), dtype=np.float32)

        for day, group in frame.groupby("date", sort=True):
            ut_values = group["ut"].to_numpy(dtype=float)
            day_iri = iri_for_day(day, ut_values, lats, lons, f107_for_day(f107, day))
            iri[group.index.to_numpy()] = day_iri

        np.savez_compressed(
            dest_path,
            iri=iri,
            timestamps=timestamps,
            lats=lats.astype(np.float32),
            lons=lons.astype(np.float32),
            lmax=np.array(LMAX, dtype=np.int16),
            altitude_km=AALT.astype(np.float32),
            tec_definition="IRI vTEC on GL23x45 integrated from 80 to 2000 km",
            input_fingerprint=source_fp,
        )
        logger.info("%d: %d maps -> %s", y, iri.shape[0], dest_path.name)


def build_dtec_cache(data_root: Path, year: int | None = None, overwrite: bool = False) -> None:
    layout = DataLayout(data_root)
    ionex_dir = layout.tec_dir
    iri_dir = layout.iri_dir
    out_dir = layout.dtec_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    for y in iter_years(data_root, year):
        ionex_path = ionex_dir / f"{y}.npz"
        iri_path = iri_dir / f"{y}.npz"
        dest_path = out_dir / f"{y}.npz"

        if not iri_path.exists():
            logger.warning("%d: missing %s, skip", y, iri_path)
            continue

        source_fp = fingerprint(DTEC_CONTRACT,
                                stored_fingerprint(ionex_path) or "unknown",
                                stored_fingerprint(iri_path) or "unknown")
        if not should_rebuild(dest_path, source_fp, overwrite, f"{y} dTEC"):
            continue

        ionex = np.load(ionex_path)
        iri = np.load(iri_path)

        ionex_timestamps = ionex["timestamps"].astype(np.int64)
        iri_timestamps = iri["timestamps"].astype(np.int64)
        if not np.array_equal(ionex_timestamps, iri_timestamps):
            raise ValueError(f"{y}: IONEX and IRI timestamps do not match")

        dtec = ionex["tec"].astype(np.float32) - iri["iri"].astype(np.float32)
        np.savez_compressed(
            dest_path,
            dtec=dtec,
            timestamps=ionex_timestamps,
            lats=iri["lats"].astype(np.float32),
            lons=iri["lons"].astype(np.float32),
            lmax=np.array(LMAX, dtype=np.int16),
            residual_definition=(
                "dTEC = IONEX vTEC on GL23x45 minus IRI vTEC on GL23x45. "
                "No plasmaspheric correction applied; remaining offset is a "
                "known mostly zonal systematic."
            ),
            input_fingerprint=source_fp,
        )
        logger.info("%d: %d maps -> %s", y, dtec.shape[0], dest_path.name)


def load_omni_year(data_root: Path, year: int) -> pd.DataFrame:
    omni_path = hro_dest(DataLayout(data_root), year)
    if not omni_path.exists():
        raise FileNotFoundError(f"Missing OMNI HRO file: {omni_path}")

    omni = parse_omni_hro(read_decompress(str(omni_path)))
    missing_features = [feature for feature in OMNI_HRO_FEATURES if feature not in omni.columns]
    if missing_features:
        raise ValueError(f"OMNI frame missing columns: {missing_features}")

    omni = omni[OMNI_HRO_FEATURES].sort_index()
    if omni.index.has_duplicates:
        omni = omni.groupby(level=0).mean()
    return omni


def _target_index(timestamps: np.ndarray) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """(full, unique-sorted) frame index for a vector of UTC epoch seconds.

    The epochs are true UT (interpolate.py mints them from tz-aware UTC
    datetimes) and the OMNI/Kp indices are naive UT, so the two are directly
    comparable without a tz conversion.
    """
    full = pd.DatetimeIndex(pd.to_datetime(timestamps, unit="s"))
    return full, pd.DatetimeIndex(full.unique()).sort_values()


def _seconds_to_nearest_observation(index: pd.DatetimeIndex,
                                    targets: pd.DatetimeIndex) -> np.ndarray:
    """Seconds from each target to the nearest timestamp in `index`.

    np.inf where `index` is empty. Used to decide whether a filled value is
    actually supported by a nearby observation.
    """
    tgt = targets.to_numpy(dtype="datetime64[ns]").astype(np.int64)
    if len(index) == 0:
        return np.full(len(tgt), np.inf)

    obs = index.to_numpy(dtype="datetime64[ns]").astype(np.int64)
    pos = np.searchsorted(obs, tgt)
    before = np.where(pos > 0, tgt - obs[np.maximum(pos - 1, 0)], np.inf)
    after = np.where(pos < len(obs), obs[np.minimum(pos, len(obs) - 1)] - tgt, np.inf)
    return np.minimum(before, after) / 1e9


def align_omni_to_timestamps(
    omni: pd.DataFrame, timestamps: np.ndarray,
    max_gap_minutes: float = OMNI_MAX_GAP_MINUTES,
) -> tuple[np.ndarray, np.ndarray]:
    """Time-interpolate OMNI drivers onto the dTEC frame timestamps.

    Returns (values [N, n_omni_features] float32, imputed [N, n_omni_features] bool).

    The fill is BOUNDED: a frame further than `max_gap_minutes` from the nearest
    real observation in a channel is left NaN rather than filled. OMNI has
    genuine multi-day plasma outages, and an unbounded time-interpolation draws a
    straight line across one -- fabricating driver history that then passes every
    downstream NaN check. Those NaNs are deliberate; `valid_window_starts` drops
    any window containing one.

    Gaps are measured per channel, because the channels fail independently: an
    IMF outage does not imply a plasma outage.

    `imputed` marks every value that is not an exact observation at that frame,
    so provenance travels with the data.
    """
    target_index, unique_target_index = _target_index(timestamps)
    combined_index = omni.index.union(unique_target_index).sort_values()
    aligned_unique = (
        omni.reindex(combined_index)
        .interpolate(method="time", limit_direction="both")
        .reindex(unique_target_index)
    )

    gap_seconds = np.column_stack([
        _seconds_to_nearest_observation(omni.index[omni[col].notna()], unique_target_index)
        for col in omni.columns
    ])
    imputed = gap_seconds > 0.0

    # copy=True: under pandas' copy-on-write semantics to_numpy can hand back a
    # read-only view, and the rejection below writes in place.
    values = aligned_unique.to_numpy(dtype=np.float64, copy=True)
    values[gap_seconds > max_gap_minutes * 60.0] = np.nan

    indexer = unique_target_index.get_indexer(target_index)
    if (indexer < 0).any():
        raise ValueError("OMNI alignment failed to map every dTEC timestamp")

    return values[indexer].astype(np.float32), imputed[indexer]


def load_kp_3hourly(data_root: Path) -> pd.DataFrame:
    kp_path = DataLayout(data_root).kp_3hourly
    if not kp_path.exists():
        raise FileNotFoundError(
            f"Missing 3-hourly Kp file: {kp_path}. "
            "Run swp-data extract then swp-data parse first."
        )

    kp = pd.read_parquet(kp_path)
    if "kp" not in kp.columns:
        raise ValueError(f"Kp table missing kp column: {list(kp.columns)}")

    kp = kp[["kp"]].sort_index()
    kp.index = pd.to_datetime(kp.index)
    if kp.index.has_duplicates:
        kp = kp.groupby(level=0).mean()
    return kp


def align_kp_to_timestamps(
    kp: pd.DataFrame, timestamps: np.ndarray,
    window_hours: float = KP_WINDOW_HOURS,
) -> tuple[np.ndarray, np.ndarray]:
    """Forward-fill 3-hourly Kp onto the dTEC frame timestamps.

    Returns (values [N, 1] float32, imputed [N, 1] bool).

    Forward-fill, never interpolate: Kp is a step function, so carrying a
    window-start stamp forward across its own window is exact, not an estimate.

    The ffill is BOUNDED at one window for the same reason the OMNI fill is
    bounded: a stamp is only valid for its own 3-hour window, so a frame more
    than `window_hours` past the last stamp means GFZ is missing the covering
    window. Carrying the stale value on would fabricate geomagnetic history, so
    it is left NaN and the window gets dropped.

    Caveat on causality: a frame at 01:00 receives the Kp of the 00:00-03:00
    window, which is not published until 03:00 and encodes activity through
    03:00. That is up to 3 h of lookahead on a 3-9 h forecast horizon -- a real
    (if modest) leak, retained here only because removing it changes the driver
    contract. Stamping at window end would close it.
    """
    target_index, unique_target_index = _target_index(timestamps)

    aligned_unique = (
        kp.reindex(kp.index.union(unique_target_index).sort_values())
        .ffill()
        .reindex(unique_target_index)
    )

    # Age of the carried stamp: one-sided (backward only), unlike the OMNI
    # nearest-observation distance, because ffill only ever looks back.
    tgt = unique_target_index.to_numpy(dtype="datetime64[ns]").astype(np.int64)
    obs = kp.index.to_numpy(dtype="datetime64[ns]").astype(np.int64)
    pos = np.searchsorted(obs, tgt, side="right")   # tgt on a stamp -> age 0
    age_seconds = np.where(pos > 0, tgt - obs[np.maximum(pos - 1, 0)], np.inf) / 1e9

    # copy=True: see align_omni_to_timestamps -- to_numpy may be read-only.
    values = aligned_unique.to_numpy(dtype=np.float64, copy=True)
    values[age_seconds > window_hours * 3600.0] = np.nan
    imputed = (age_seconds > 0.0)[:, None]

    indexer = unique_target_index.get_indexer(target_index)
    if (indexer < 0).any():
        raise ValueError("Kp alignment failed to map every dTEC timestamp")

    return values[indexer].astype(np.float32), imputed[indexer]


def _report_driver_quality(year: int, aligned: np.ndarray, imputed: np.ndarray) -> None:
    """Log per-channel imputation and outage rates.

    Rejected values used to surface as a hard raise. Now that they are left NaN
    for `valid_window_starts` to drop, this log is the only signal that a year is
    thin on real driver coverage -- so it reports every channel, every year.
    """
    n = aligned.shape[0]
    if n == 0:
        return

    rejected = np.isnan(aligned)
    parts = [
        f"{name} {100.0 * imputed[:, i].mean():.0f}%/{100.0 * rejected[:, i].mean():.1f}%"
        for i, name in enumerate(DRIVER_FEATURES)
    ]
    logger.info("%d: driver imputed%%/outage%% -- %s", year, "  ".join(parts))

    frames_lost = rejected.any(axis=1).sum()
    if frames_lost:
        logger.warning(
            "%d: %d/%d frames (%.1f%%) have >=1 driver beyond the fill tolerance; "
            "windows touching them will be dropped",
            year, frames_lost, n, 100.0 * frames_lost / n,
        )


def build_omni_cache(data_root: Path, year: int | None = None, overwrite: bool = False) -> None:
    layout = DataLayout(data_root)
    dtec_dir = layout.dtec_dir
    out_dir = layout.omni_aligned_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    kp = load_kp_3hourly(data_root)

    for y in iter_years(data_root, year):
        dtec_path = dtec_dir / f"{y}.npz"
        dest_path = out_dir / f"{y}.npz"

        if not dtec_path.exists():
            logger.warning("%d: missing %s, skip", y, dtec_path)
            continue

        # Kp is fingerprinted for the same reason F10.7 is in the IRI stage --
        # the GFZ swap regenerated it and nothing downstream noticed.
        source_fp = fingerprint(OMNI_CONTRACT,
                                stored_fingerprint(dtec_path) or "unknown",
                                kp["kp"].to_numpy(dtype=np.float64))
        if not should_rebuild(dest_path, source_fp, overwrite, f"{y} OMNI"):
            continue

        dtec = np.load(dtec_path)
        timestamps = dtec["timestamps"].astype(np.int64)
        omni = load_omni_year(data_root, y)
        aligned_omni, omni_imputed = align_omni_to_timestamps(omni, timestamps)
        aligned_kp, kp_imputed = align_kp_to_timestamps(kp, timestamps)
        aligned = np.concatenate([aligned_omni, aligned_kp], axis=1).astype(np.float32)
        imputed = np.concatenate([omni_imputed, kp_imputed], axis=1)

        _report_driver_quality(y, aligned, imputed)

        np.savez_compressed(
            dest_path,
            omni=aligned,
            imputed=imputed,
            timestamps=timestamps,
            features=np.asarray(DRIVER_FEATURES),
            source=(
                "OMNI HRO 5-minute values time-interpolated to dTEC timestamps; "
                "3-hourly GFZ Kp causally forward-filled to dTEC timestamps"
            ),
            imputation_rule=(
                f"Both fills are bounded. 'imputed' is True where a value is not an "
                f"exact observation at that frame. Values further than "
                f"{OMNI_MAX_GAP_MINUTES:.0f} min (OMNI, nearest observation) or "
                f"{KP_WINDOW_HOURS:.0f} h (Kp, age of the forward-filled stamp) are "
                f"left NaN rather than filled, so windows spanning a real outage are "
                f"dropped by valid_window_starts instead of silently fabricated."
            ),
            input_fingerprint=source_fp,
        )
        logger.info("%d: %d rows -> %s", y, aligned.shape[0], dest_path.name)


def silver_lineage(data_root: Path) -> dict[str, str]:
    """Per-year fingerprints of the silver inputs, for recording in gold.

    Lets any training set be traced back to the exact silver artifacts behind it
    -- the thing that was missing when gold was built two days before the index
    tables it supposedly reflected.
    """
    layout = DataLayout(data_root)
    out: dict[str, str] = {}
    for year in sorted(int(p.stem) for p in layout.dtec_dir.glob("*.npz")):
        out[str(year)] = fingerprint(
            stored_fingerprint(layout.dtec_dir / f"{year}.npz") or "unknown",
            stored_fingerprint(layout.omni_aligned_dir / f"{year}.npz") or "unknown",
        )
    return out


def load_aligned_series(data_root: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    layout = DataLayout(data_root)
    dtec_dir = layout.dtec_dir
    omni_dir = layout.omni_aligned_dir
    years = sorted(int(path.stem) for path in dtec_dir.glob("*.npz"))

    dtec_parts = []
    omni_parts = []
    timestamp_parts = []
    lats = None
    lons = None

    for year in years:
        dtec_path = dtec_dir / f"{year}.npz"
        omni_path = omni_dir / f"{year}.npz"
        if not omni_path.exists():
            logger.warning("%d: missing aligned OMNI, skip", year)
            continue

        dtec = np.load(dtec_path)
        omni = np.load(omni_path)
        timestamps = dtec["timestamps"].astype(np.int64)
        if not np.array_equal(timestamps, omni["timestamps"].astype(np.int64)):
            raise ValueError(f"{year}: dTEC and OMNI timestamps do not match")

        dtec_parts.append(dtec["dtec"].astype(np.float32))
        omni_parts.append(omni["omni"].astype(np.float32))
        timestamp_parts.append(timestamps)

        if lats is None:
            lats = dtec["lats"].astype(np.float32)
            lons = dtec["lons"].astype(np.float32)

    if not dtec_parts:
        raise ValueError("No aligned dTEC/OMNI yearly caches found")

    dtec_all = np.concatenate(dtec_parts)
    omni_all = np.concatenate(omni_parts)
    timestamps_all = np.concatenate(timestamp_parts)

    order = np.argsort(timestamps_all, kind="stable")
    timestamps_all = timestamps_all[order]
    dtec_all = dtec_all[order]
    omni_all = omni_all[order]

    unique_timestamps, unique_idx = np.unique(timestamps_all, return_index=True)
    dropped = len(timestamps_all) - len(unique_timestamps)
    if dropped:
        logger.info("dropped %d duplicate timestamps before windowing", dropped)

    return dtec_all[unique_idx], omni_all[unique_idx], unique_timestamps, lats, lons


def _horizon_labels(cadence_seconds: int) -> tuple[str, str]:
    """History and lead times a cadence implies, e.g. ('12h', '+2/+4/+6h')."""
    step = cadence_seconds / 3600.0
    leads = "/".join(f"+{(i + 1) * step:g}" for i in range(TARGET_STEPS))
    return f"{INPUT_STEPS * step:g}h", f"{leads}h"


def decimate_to_cadence(dtec: np.ndarray, omni: np.ndarray, timestamps: np.ndarray,
                        cadence_seconds: int = TARGET_CADENCE_SECONDS,
                        ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Resample the frame series to a uniform cadence, keeping values untouched.

    Greedy: keep a frame if it is at least `cadence_seconds` after the last one
    kept. This is pure index selection -- no averaging, no interpolation, so
    every retained dTEC map and driver row is bit-identical to its input.

    Deliberately greedy rather than modular (`epoch % cadence == 0`) for two
    reasons:

      - Phase is not constant across the record. CODE's 2000-2002 maps sit on
        ODD UT hours and 2003+ on even hours, so a modular rule would silently
        delete the three highest-activity years in the archive.
      - It resynchronizes after a data gap instead of locking to a grid the
        post-gap frames may not sit on.

    At cadence 3600 nothing is dropped (every gap is already >= 3600). The era
    selection then happens in `valid_window_starts`, which rejects any window
    whose spacing is not exactly the cadence -- so one parameter selects both
    the resampling and the era, with no boundary date hardcoded anywhere.
    """
    if len(timestamps) == 0:
        return dtec, omni, timestamps

    keep = np.zeros(len(timestamps), dtype=bool)
    keep[0] = True
    last = timestamps[0]
    for i in range(1, len(timestamps)):
        if timestamps[i] - last >= cadence_seconds:
            keep[i] = True
            last = timestamps[i]

    return dtec[keep], omni[keep], timestamps[keep]


def valid_window_starts(dtec: np.ndarray, omni: np.ndarray, timestamps: np.ndarray,
                        cadence_seconds: int = TARGET_CADENCE_SECONDS) -> np.ndarray:
    total_steps = INPUT_STEPS + TARGET_STEPS
    if len(timestamps) < total_steps:
        return np.array([], dtype=np.int64)

    diffs = np.diff(timestamps)
    start_count = len(timestamps) - total_steps + 1
    starts = np.arange(start_count, dtype=np.int64)
    valid = np.ones(start_count, dtype=bool)

    # Every gap must equal the target cadence -- not merely match its neighbours.
    # Uniformity alone accepted a 2-hourly window and an hourly window as equally
    # valid, which is how a dataset that was 60% "+2/+4/+6 h" in train and 100%
    # "+1/+2/+3 h" in val/test shipped looking healthy. This also subsumes the
    # old "strictly increasing" check.
    on_cadence = np.ones(start_count, dtype=bool)
    for offset in range(total_steps - 1):
        on_cadence &= diffs[starts + offset] == cadence_seconds

    finite_time = np.isfinite(omni).all(axis=1) & np.isfinite(dtec).all(axis=(1, 2))
    finite_prefix = np.concatenate([[0], np.cumsum(finite_time.astype(np.int64))])
    finite_count = finite_prefix[starts + total_steps] - finite_prefix[starts]
    all_finite = finite_count == total_steps

    valid = on_cadence & all_finite

    # Attribution, not just a total: a silent 40% loss and a healthy build
    # otherwise look identical in the logs. Causes are reported disjointly so the
    # three numbers sum to the candidate count.
    logger.info(
        "windows: %d candidates -> %d kept  (%d off-cadence, %d spanning missing data)",
        start_count, int(valid.sum()), int((~on_cadence).sum()),
        int((on_cadence & ~all_finite).sum()),
    )

    return starts[valid]


def _year_boundary(year: int) -> int:
    """UTC epoch seconds of 00:00 on 1 January of `year`."""
    return int(pd.Timestamp(year=year, month=1, day=1, tz="UTC").timestamp())


def split_window_starts(starts: np.ndarray, timestamps: np.ndarray,
                        train_end_year: int, val_end_year: int,
                        cadence_seconds: int = TARGET_CADENCE_SECONDS,
                        embargo_steps: int = SPLIT_EMBARGO_STEPS,
                        ) -> dict[str, np.ndarray]:
    """Assign windows to splits by their WHOLE extent, with an embargo gap.

    Splitting on the start timestamp alone leaked across every boundary: a window
    beginning 2019-12-31 23:00 is labelled train, but its three target frames land
    in 2020 -- so val's period was being trained on. Confirmed in the shipped
    artifact, where train ended 2019-12-31 23:00 and val began 2020-01-01 00:00.

    Two guards, both standard for time-series cross-validation:

      purge   -- a window must lie ENTIRELY within one split's period. Windows
                 straddling a boundary belong to neither and are dropped.
      embargo -- the later split additionally skips `embargo_steps` frames after
                 the boundary, so its first window is not merely disjoint from the
                 earlier split but decorrelated from it. One window length is the
                 natural choice: no train frame is within a window's reach of any
                 val frame.

    Windows falling in a purge or embargo zone are returned in no split, so the
    three arrays no longer partition `starts`.
    """
    total_steps = INPUT_STEPS + TARGET_STEPS
    first = timestamps[starts]
    last = timestamps[starts + total_steps - 1]

    train_val = _year_boundary(train_end_year + 1)
    val_test = _year_boundary(val_end_year + 1)
    gap = embargo_steps * cadence_seconds

    return {
        "train": starts[last < train_val],
        "val": starts[(first >= train_val + gap) & (last < val_test)],
        "test": starts[first >= val_test + gap],
    }


def window_stats(dtec: np.ndarray, omni: np.ndarray, starts: np.ndarray,
                 chunk_size: int) -> tuple[float, float, np.ndarray, np.ndarray]:
    if len(starts) == 0:
        raise ValueError("Train split has zero windows; cannot compute normalization stats")

    # Over UNIQUE frames, not over windows. Windows overlap at stride 1, so
    # iterating them counts interior frames up to INPUT_STEPS times and edge
    # frames fewer -- yielding a window-multiplicity-weighted mean rather than the
    # train-split mean. Deduplicating is both correct and cheaper.
    input_offsets = np.arange(INPUT_STEPS)
    frames = np.unique((starts[:, None] + input_offsets[None, :]).ravel())

    tec_sum = 0.0
    tec_sumsq = 0.0
    tec_count = 0
    omni_sum = np.zeros(len(DRIVER_FEATURES), dtype=np.float64)
    omni_sumsq = np.zeros(len(DRIVER_FEATURES), dtype=np.float64)
    omni_count = 0

    for begin in range(0, len(frames), chunk_size):
        idx = frames[begin:begin + chunk_size]
        tec_chunk = dtec[idx].astype(np.float64)
        omni_chunk = omni[idx].astype(np.float64)

        tec_sum += tec_chunk.sum()
        tec_sumsq += np.square(tec_chunk).sum()
        tec_count += tec_chunk.size
        omni_sum += omni_chunk.sum(axis=0)
        omni_sumsq += np.square(omni_chunk).sum(axis=0)
        omni_count += omni_chunk.shape[0]

    tec_mean = tec_sum / tec_count
    tec_var = max(tec_sumsq / tec_count - tec_mean ** 2, 1e-12)
    omni_mean = omni_sum / omni_count
    omni_var = np.maximum(omni_sumsq / omni_count - np.square(omni_mean), 1e-12)
    return tec_mean, float(np.sqrt(tec_var)), omni_mean, np.sqrt(omni_var)


def remove_existing_outputs(out_dir: Path) -> None:
    for path in out_dir.glob("*.npy"):
        path.unlink()
    metadata = out_dir / "metadata.json"
    if metadata.exists():
        metadata.unlink()


def write_split_windows(out_dir: Path, split_name: str, dtec: np.ndarray, omni: np.ndarray,
                        timestamps: np.ndarray, starts: np.ndarray, tec_mean: float,
                        tec_std: float, omni_mean: np.ndarray, omni_std: np.ndarray,
                        chunk_size: int) -> None:
    n = len(starts)
    input_offsets = np.arange(INPUT_STEPS)
    target_offsets = np.arange(INPUT_STEPS, INPUT_STEPS + TARGET_STEPS)

    tec_out = np.lib.format.open_memmap(
        out_dir / f"{split_name}_tec_input.npy",
        mode="w+",
        dtype=np.float32,
        shape=(n, INPUT_STEPS, NLAT, NLON),
    )
    omni_out = np.lib.format.open_memmap(
        out_dir / f"{split_name}_omni_input.npy",
        mode="w+",
        dtype=np.float32,
        shape=(n, INPUT_STEPS, len(DRIVER_FEATURES)),
    )
    target_out = np.lib.format.open_memmap(
        out_dir / f"{split_name}_target.npy",
        mode="w+",
        dtype=np.float32,
        shape=(n, TARGET_STEPS, NLAT, NLON),
    )

    for begin in range(0, n, chunk_size):
        end = min(begin + chunk_size, n)
        batch_starts = starts[begin:end]
        input_idx = batch_starts[:, None] + input_offsets[None, :]
        target_idx = batch_starts[:, None] + target_offsets[None, :]

        tec_out[begin:end] = (dtec[input_idx] - tec_mean) / tec_std
        omni_out[begin:end] = (omni[input_idx] - omni_mean) / omni_std
        target_out[begin:end] = (dtec[target_idx] - tec_mean) / tec_std

    tec_out.flush()
    omni_out.flush()
    target_out.flush()
    np.save(out_dir / f"{split_name}_window_start_times.npy", timestamps[starts].astype(np.int64))
    logger.info("%s: %d windows", split_name, n)


def build_windowed_dataset(data_root: Path, out_dir: Path, train_end_year: int,
                           val_end_year: int, overwrite: bool, chunk_size: int,
                           cadence_seconds: int = TARGET_CADENCE_SECONDS) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.glob("*.npy")) and not overwrite:
        raise FileExistsError(f"{out_dir} already has .npy outputs; pass --overwrite to rebuild")
    if overwrite:
        remove_existing_outputs(out_dir)

    dtec, omni, timestamps, lats, lons = load_aligned_series(data_root)

    # Resample BEFORE anything else reads the series: window_stats must see the
    # population that actually ships, or the normalization constants describe a
    # dataset nobody built.
    n_native = len(timestamps)
    dtec, omni, timestamps = decimate_to_cadence(dtec, omni, timestamps, cadence_seconds)
    logger.info("frames: %d native -> %d at %ds cadence (%d dropped)",
                n_native, len(timestamps), cadence_seconds, n_native - len(timestamps))

    starts = valid_window_starts(dtec, omni, timestamps, cadence_seconds)
    splits = split_window_starts(starts, timestamps, train_end_year, val_end_year,
                                 cadence_seconds)
    n_split = sum(len(s) for s in splits.values())
    logger.info("splits: %d of %d windows assigned (%d purged/embargoed at boundaries)",
                n_split, len(starts), len(starts) - n_split)

    logger.info("horizon: see %s, predict %s", *_horizon_labels(cadence_seconds))

    tec_mean, tec_std, omni_mean, omni_std = window_stats(
        dtec, omni, splits["train"], chunk_size
    )

    for split_name in ("train", "val", "test"):
        write_split_windows(
            out_dir, split_name, dtec, omni, timestamps, splits[split_name],
            tec_mean, tec_std, omni_mean, omni_std, chunk_size
        )

    np.save(out_dir / "lats.npy", lats)
    np.save(out_dir / "lons.npy", lons)

    metadata = {
        "format": "disk-backed normalized numpy arrays",
        "lmax": LMAX,
        "nlat": NLAT,
        "nlon": NLON,
        "input_steps": INPUT_STEPS,
        "target_steps": TARGET_STEPS,
        "cadence_seconds": cadence_seconds,
        "horizon": {
            "history": _horizon_labels(cadence_seconds)[0],
            "lead_times": _horizon_labels(cadence_seconds)[1],
            "note": (
                "Every window has exactly this spacing; input_steps/target_steps "
                "are counts, and only cadence_seconds makes them a physical "
                "horizon. Recorded because a mixed-cadence build is otherwise "
                "indistinguishable from a uniform one at every layer."
            ),
        },
        "omni_features": DRIVER_FEATURES,
        "train_end_year": train_end_year,
        "val_end_year": val_end_year,
        "split_rule": {
            "assignment": "a window is assigned only if it lies entirely within one split's period",
            "embargo_steps": SPLIT_EMBARGO_STEPS,
            "embargo_seconds": SPLIT_EMBARGO_STEPS * cadence_seconds,
            "note": (
                "Windows straddling a boundary are purged, and the later split "
                "skips the embargo before its first window, so the three splits "
                "do not partition the valid windows."
            ),
        },
        "normalization": {
            "tec_mean": tec_mean,
            "tec_std": tec_std,
            "omni_mean": omni_mean.tolist(),
            "omni_std": omni_std.tolist(),
            "computed_from": (
                "unique train input frames, each counted once (not weighted by "
                "how many overlapping windows contain it)"
            ),
            "applied_to": "tec_input, omni_input, and target arrays",
        },
        "splits": {name: int(len(split_starts)) for name, split_starts in splits.items()},
        "silver_lineage": silver_lineage(data_root),
        "residual_definition": (
            "dTEC = IONEX vTEC on GL23x45 minus IRI vTEC on GL23x45. "
            "No plasmaspheric correction applied."
        ),
        "window_rule": (
            "A window is kept only if all 9 timestamps are finite, strictly "
            "increasing, and have identical adjacent spacing."
        ),
    }
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
    logger.info("wrote %s", out_dir)
