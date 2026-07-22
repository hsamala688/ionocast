"""Stage 2: parsing raw Stage 1 files.

  - read_decompress : .Z (Unix LZW) / .gz (gzip) / .asc|.txt (plain) -> text stream
  - parse_ionex     : IONEX v1.0 stream -> list of (datetime, ndarray) TEC maps
  - parse_omni_hro  : OMNI 5-min HRO stream -> DataFrame of the five driver channels
  - parse_gfz       : GFZ combined index file -> daily Kp1-8 / F10.7obs records
  - build_index_tables : parse_gfz output -> the two driver-index parquet
                         artifacts (f107_daily, kp_3hourly) that Stage 4 consumes

parse_gfz + build_index_tables replace the retired CelesTrak parsers
(pull_f107, geomag_pull.py). Same artifact paths and schemas, so Stage 4 is
unchanged.
"""
from __future__ import annotations

import gzip
import io
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import unlzw3

from .config import F107_MAX
from .settings import DataLayout, Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Decompression
# ---------------------------------------------------------------------------


def read_decompress(file_path):
    """Return an in-memory text stream for a raw Stage 1 file.

    Branches on extension: .Z is Unix LZW compress (gzip cannot read it),
    .gz is standard gzip, .asc/.txt are uncompressed text.
    """
    with open(file_path, 'rb') as f:          # read as binary, not text
        raw_bytes = f.read()

    ext = os.path.splitext(file_path)[1].lower()

    if ext == '.z':                            # pre-2023 IONEX
        return io.StringIO(unlzw3.unlzw(raw_bytes).decode('utf-8'))
    elif ext == '.gz':                         # post-2023 IONEX
        with gzip.open(io.BytesIO(raw_bytes), 'rt', encoding='utf-8') as gz:
            return io.StringIO(gz.read())
    elif ext in ('.asc', '.txt'):              # OMNI / GFZ plain text
        return io.StringIO(raw_bytes.decode('utf-8'))
    else:
        raise ValueError(f"Unexpected extension {ext!r} for {file_path}")


# ---------------------------------------------------------------------------
# IONEX v1.0 parser
# ---------------------------------------------------------------------------

# Labels (cols 61-80) that are structural, i.e. NOT value lines.
_LABELS = {
    "EXPONENT", "LAT1 / LAT2 / DLAT", "LON1 / LON2 / DLON",
    "END OF HEADER", "START OF TEC MAP", "EPOCH OF CURRENT MAP",
    "LAT/LON1/LON2/DLON/H", "END OF TEC MAP", "START OF RMS MAP",
}


def parse_ionex(stream):
    """Parse an IONEX v1.0 stream into TEC maps.

    Returns:
        maps : list of (datetime, ndarray[n_lat, n_lon]) in TECU, NaN where missing.
               Datetimes are timezone-aware UTC: IONEX EPOCH lines are UT, and
               stamping them tz-aware is what keeps `.timestamp()` downstream
               from silently reinterpreting them in the machine's local zone.
        lats : ndarray[n_lat] latitudes  (deg, north -> south)
        lons : ndarray[n_lon] longitudes (deg, -180 -> 180)
    """
    exponent = -1                              # IONEX default; header overrides
    lat1 = lat2 = dlat = None
    lon1 = lon2 = dlon = None

    # ---- header ----
    for line in stream:
        label = line[60:].strip()
        if label == "EXPONENT":
            exponent = int(line[:60].split()[0])
        elif label == "LAT1 / LAT2 / DLAT":
            lat1, lat2, dlat = (float(x) for x in line[:60].split())
        elif label == "LON1 / LON2 / DLON":
            lon1, lon2, dlon = (float(x) for x in line[:60].split())
        elif label == "END OF HEADER":
            break

    if dlat is None or dlon is None:
        raise ValueError("IONEX header missing grid definition")

    lats = np.arange(lat1, lat2 + dlat / 2, dlat)   # inclusive of lat2
    lons = np.arange(lon1, lon2 + dlon / 2, dlon)
    n_lat, n_lon = len(lats), len(lons)
    scale = 10.0 ** exponent

    # ---- body (state machine over TEC map blocks) ----
    maps = []
    timestamp = None
    grid = None
    row = -1
    buf = None

    for line in stream:
        label = line[60:].strip()

        if label == "START OF RMS MAP":
            break                                   # done with TEC; ignore RMS maps
        elif label == "START OF TEC MAP":
            grid = np.full((n_lat, n_lon), np.nan)
            row, buf = -1, None
        elif label == "EPOCH OF CURRENT MAP":
            y, mo, d, h, mi, s = (int(x) for x in line[:60].split())
            timestamp = datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
        elif label == "LAT/LON1/LON2/DLON/H":
            row += 1                                # bands march in header order
            buf = []
        elif label == "END OF TEC MAP":
            maps.append((timestamp, grid))
            buf = None
        elif label not in _LABELS:                  # a value line
            # value lines fill cols 1-80, so split the WHOLE line, not line[:60]
            if buf is not None and 0 <= row < n_lat:
                buf.extend(int(v) for v in line.split())
                if len(buf) >= n_lon:
                    vals = np.array(buf[:n_lon], dtype=float)
                    vals[vals == 9999] = np.nan     # NaN before scaling
                    grid[row] = vals * scale
                    buf = None

    return maps, lats, lons


# ---------------------------------------------------------------------------
# OMNI 5-min HRO parser
# ---------------------------------------------------------------------------

"""
TODO - Currently parses through all 49 fields, based on true final construction, 
most definitely won't need to can afford to slim down whole process

COSMETIC: 
- None
"""


# Full record per HRO_format.txt: 46 base fields + 3 GOES flux fields (5-min only) = 49.
_OMNI_COLUMNS = [
    "year", "day", "hour", "minute", "id_imf", "id_sw",
    "num_pts_imf", "num_pts_sw", "percent_interp", "timeshift",
    "rms_timeshift", "rms_phase", "time_between_obs", "b_magnitude",
    "bx_gse", "by_gse", "bz_gse", "by_gsm", "bz_gsm", "rms_b_scalar",
    "rms_b_vector", "flow_speed", "vx_gse", "vy_gse", "vz_gse",
    "proton_density", "temperature", "flow_pressure", "e_field",
    "beta", "mach_number", "x_gse", "y_gse", "z_gse",
    "bsn_x_gse", "bsn_y_gse", "bsn_z_gse",
    "ae_index", "al_index", "au_index",
    "sym_d", "sym_h", "asy_d", "asy_h",
    "pc_n_index", "magnetosonic_mach",
    "pr_flux_10", "pr_flux_30", "pr_flux_60",   # 5-min only
]  

# don't need all of these columns, but possibly need in the future

# The five IMF/solar-wind channels that feed the model (omni_input contract).
_OMNI_KEEP = ["b_magnitude", "by_gsm", "bz_gsm", "flow_speed", "proton_density"]

# Fill sentinel per kept field (the max-magnitude value for that field width).
_OMNI_FILL = {
    "b_magnitude": 9999.99,     # F8.2
    "by_gsm": 9999.99,          # F8.2
    "bz_gsm": 9999.99,          # F8.2
    "flow_speed": 99999.9,      # F8.1
    "proton_density": 999.99,   # F7.2
}


def parse_omni_hro(stream):
    """Parse an OMNI 5-min HRO stream into a timestamped driver frame.

    Parses all 49 fields to keep column alignment honest, then returns only the
    five driver channels with fill values converted to NaN.

    Returns:
        DataFrame indexed by timestamp with columns:
        b_magnitude, by_gsm, bz_gsm, flow_speed, proton_density
    """
    # Fail loud if this file is not the 49-field 5-min product (e.g. a 1-min file).
    n_tokens = len(stream.readline().split())
    stream.seek(0)
    if n_tokens != len(_OMNI_COLUMNS):
        raise ValueError(
            f"OMNI parse: expected {len(_OMNI_COLUMNS)} fields per record, "
            f"got {n_tokens} - column list misaligned with this product."
        )

    df = pd.read_csv(stream, sep=r"\s+", header=None, names=_OMNI_COLUMNS) # fine, good line

    # Timestamp from year + day-of-year + hour + minute (day is DOY, not month/day).
    ts = (
        pd.to_datetime((df["year"] * 1000 + df["day"]).astype(str), format="%Y%j")
        + pd.to_timedelta(df["hour"], unit="h")
        + pd.to_timedelta(df["minute"], unit="m")
    )

    out = df[_OMNI_KEEP].copy() # df of 5 col
    out.insert(0, "timestamp", ts)
    out = out.set_index("timestamp")

    # Fills are the max-magnitude sentinel per field; mask by threshold, not ==,
    # to avoid float-equality misses. Real values never approach these magnitudes.
    for col, fill in _OMNI_FILL.items():
        out.loc[out[col] >= fill, col] = np.nan

    return out


# ---------------------------------------------------------------------------
# GFZ combined Kp/ap/Ap/SN/F10.7 parser
# ---------------------------------------------------------------------------

# One data row per UT day, blank-separated, after '#' header lines. Word
# positions pinned against the file's own header block (kp.gfz.de):
#   0:YYYY 1:MM 2:DD 3:days 4:days_m 5:BSR 6:dB
#   7-14:Kp1..Kp8 (decimal thirds, e.g. 2.667; missing -1.000)
#   15-22:ap1..ap8  23:Ap  24:SN
#   25:F10.7obs  26:F10.7adj (sfu; missing -1.0)
#   27:D (0 Kp+SN preliminary, 1 Kp definitive, 2 both definitive)
_GFZ_N_FIELDS = 28
_GFZ_KP = slice(7, 15)
_GFZ_F107_OBS = 25
_GFZ_F107_ADJ = 26
_GFZ_DEF_FLAG = 27


def parse_gfz(stream) -> pd.DataFrame:
    """Parse the GFZ combined index file into one record per UT day.

    Returns a date-indexed DataFrame with columns kp_1..kp_8 (Kp in decimal
    thirds, NaN where missing), f107_obs, f107_adj (sfu, NaN where missing),
    and definitive (int flag).
    """
    dates, kp_rows, f107_obs, f107_adj, def_flag = [], [], [], [], []
    n_checked = False

    for line in stream:
        if line.startswith("#") or not line.strip():
            continue
        f = line.split()
        # Defensive first-line assertion, same idea as parse_omni_hro: fail
        # loud if this is not the 28-word combined product.
        if not n_checked:
            if len(f) != _GFZ_N_FIELDS:
                raise ValueError(
                    f"GFZ parse: expected {_GFZ_N_FIELDS} fields per record, "
                    f"got {len(f)} - not the combined Kp/ap/Ap/SN/F10.7 file."
                )
            n_checked = True
        if len(f) != _GFZ_N_FIELDS:
            continue

        dates.append(f"{f[0]}-{f[1]}-{f[2]}")
        kp_rows.append([float(x) for x in f[_GFZ_KP]])
        f107_obs.append(float(f[_GFZ_F107_OBS]))
        f107_adj.append(float(f[_GFZ_F107_ADJ]))
        def_flag.append(int(f[_GFZ_DEF_FLAG]))

    kp = np.asarray(kp_rows, dtype=float)
    df = pd.DataFrame({
        **{f"kp_{i + 1}": kp[:, i] for i in range(8)},
        "f107_obs": f107_obs,
        "f107_adj": f107_adj,
        "definitive": def_flag,
    }, index=pd.to_datetime(dates))
    df.index.name = "date"

    # Missing sentinels are -1 class; mask by sign, all real values are >= 0.
    for col in [f"kp_{i + 1}" for i in range(8)] + ["f107_obs", "f107_adj"]:
        df.loc[df[col] < 0, col] = np.nan

    return df

# _assert_observed_not_adjusted not necessary

def _assert_observed_not_adjusted(df: pd.DataFrame) -> None:
    """Prove f107_obs is the observed (not 1AU-adjusted) column.

    Observed flux exceeds adjusted in January (Earth near perihelion) and
    falls below it in July, by ~3.4% peak-to-peak: obs/adj tracks (1AU/r)^2.
    If the columns were swapped or identical this signature disappears.
    """
    both = df.dropna(subset=["f107_obs", "f107_adj"])
    both = both[(both["f107_obs"] > 0) & (both["f107_adj"] > 0)]
    ratio = both["f107_obs"] / both["f107_adj"]
    jan = ratio[both.index.month == 1].mean()
    jul = ratio[both.index.month == 7].mean()
    if not (jan > 1.015 and jul < 0.985):
        raise ValueError(
            f"F10.7 observed/adjusted seasonal signature wrong "
            f"(Jan {jan:.4f}, Jul {jul:.4f}): expected obs/adj > 1 in January "
            f"and < 1 in July. Column mapping is suspect; do not proceed."
        )


def derive_f107_daily(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """Daily observed F10.7 for the IRI baseline; same schema pull_f107 wrote.

    Values above F107_MAX sfu are burst-inflated single-day spikes, not a
    valid daily EUV proxy: set to NaN and time-interpolate over them (do NOT
    clip). Applied after masking GFZ's own -1 fill.
    """
    _assert_observed_not_adjusted(df)

    out = df.loc[(df.index >= start) & (df.index <= end), ["f107_obs"]].copy()
    out.index.name = "date"
    n_spikes = int((out["f107_obs"] > F107_MAX).sum())
    out.loc[out["f107_obs"] > F107_MAX, "f107_obs"] = np.nan
    out["f107_obs"] = out["f107_obs"].interpolate(method="time")
    if n_spikes:
        logger.info("f107: %d burst days (>%.0f sfu) interpolated", n_spikes, F107_MAX)
    return out


def derive_kp_3hourly(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """Causal 3-hourly Kp; same schema geomag_pull.py's 3-hourly output had.

    One row per 3-hour UT window, stamped at the window START. Stage 4's
    align_kp_to_timestamps forward-fills from these stamps, which preserves
    the causal convention: a window's Kp applies from its start onward, and
    no in-progress window's value exists before its stamp.

    GFZ stores Kp as decimal thirds (2.667); the retiring CelesTrak path
    stored Kp*10 integers (27 -> 2.7). Round to the same one-decimal encoding
    so the series are bit-identical.
    """
    day_mask = (df.index >= start) & (df.index <= end)
    days = df.index[day_mask]
    kp = df.loc[day_mask, [f"kp_{i + 1}" for i in range(8)]].to_numpy()

    timestamps = (np.repeat(days.values, 8)
                  + np.tile((np.arange(8) * 3).astype("timedelta64[h]"), len(days)))
    values = np.round(kp * 10.0).ravel() / 10.0

    out = pd.DataFrame({"timestamp": timestamps, "kp": values}).set_index("timestamp")
    return out[out["kp"].notna()]


def build_index_tables(settings: Settings, out_root: Path | None = None) -> tuple[Path, Path]:
    """Derive the two silver driver-index artifacts from the raw bronze GFZ file.

    Reads the GFZ file from bronze and writes the f107_daily / kp_3hourly
    parquets into silver. When out_root is given, writes under that root
    instead (staging for verification against a prior build).
    """
    src_layout = settings.layout
    gfz_path = src_layout.gfz_file(settings.gfz_index_filename)
    df = parse_gfz(read_decompress(str(gfz_path)))

    out_layout = DataLayout(out_root) if out_root is not None else src_layout
    f107_out = out_layout.f107_daily
    kp_out = out_layout.kp_3hourly
    f107_out.parent.mkdir(parents=True, exist_ok=True)
    kp_out.parent.mkdir(parents=True, exist_ok=True)

    start = settings.start_date.isoformat()
    end = settings.end_date.isoformat()
    f107 = derive_f107_daily(df, start, end)
    kp = derive_kp_3hourly(df, start, end)

    f107.to_parquet(f107_out)
    kp.to_parquet(kp_out)
    logger.info("wrote %s  (%d days, %s..%s)", f107_out, len(f107),
                f107.index[0].date(), f107.index[-1].date())
    logger.info("wrote %s  (%d rows)", kp_out, len(kp))
    return f107_out, kp_out
