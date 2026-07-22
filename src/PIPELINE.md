# `swp-data` Pipeline

The Medallion (bronze/silver/gold) data pipeline that turns raw global TEC maps
and solar-wind drivers into normalized training windows for the SFNO model.

This document describes `src/swp_data/`, the current pipeline. The older
`data_pull/PIPELINE.md` documents the retired script-per-stage layout that this
package replaced; where the two disagree, this one is authoritative.

## The one-sentence version

Download IONEX TEC maps and solar-wind/geomagnetic drivers → interpolate TEC
onto a Gauss-Legendre sphere → subtract an IRI climatology baseline to get a
residual field → align drivers to that field's timestamps → cut into
fixed-length input/target windows → normalize and serve via a PyTorch
`Dataset`.

```
                        bronze/                  silver/                gold/
                   ┌──────────────┐      ┌────────────────────┐    ┌──────────┐
  CDDIS IONEX ────►│ ionex/       │─────►│ tec_gl23x45/       │─┐  │          │
                   │              │      │  (+ grid.npz)      │ │  │          │
  SPDF OMNI HRO ──►│ omni_hro/    │──┐   │                    │ │  │          │
                   │              │  │   │ iri_gl23x45/       │◄┘  │          │
  GFZ indices ────►│ gfz/         │─┐│   │       │            │    │          │
                   └──────────────┘ ││   │       ▼            │    │          │
                                    ││   │ dtec_gl23x45/      │───►│ training │
                       parse ───────┘│   │       │            │    │ windows/ │
                    (f107_daily,     │   │       ▼            │    │          │
                     kp_3hourly) ────┼──►│ omni_aligned_...   │───►│          │
                                     │   └────────────────────┘    └──────────┘
                                     └────────────┘
```

Stage boundaries map one-to-one onto CLI subcommands:

| Stage | Command | Layer | Writes |
|---|---|---|---|
| 1 | `swp-data extract` | bronze | raw downloads + manifests |
| 2 | `swp-data parse` | silver | `f107_daily.parquet`, `kp_3hourly.parquet` |
| 3 | `swp-data interpolate` | silver | `tec_gl23x45/{year}.npz`, `grid.npz` |
| 4a | `swp-data assemble iri` | silver | `iri_gl23x45/{year}.npz` |
| 4b | `swp-data assemble dtec` | silver | `dtec_gl23x45/{year}.npz` |
| 4c | `swp-data assemble omni` | silver | `omni_aligned_gl23x45/{year}.npz` |
| 4d | `swp-data assemble windows` | gold | `training_windows/*.npy` + `metadata.json` |
| 5 | `swp_data.dataset` (import) | — | PyTorch `Dataset` / `DataLoader` |

---

## Quick start (from scratch)

```bash
pip install -e .

swp-data extract                  # bronze: IONEX + OMNI HRO + GFZ
swp-data parse                    # silver: F10.7 + Kp index tables
swp-data interpolate              # silver: IONEX -> GL23x45 (writes grid.npz)
swp-data assemble iri             # silver: IRI baseline   <-- slowest stage
swp-data assemble dtec            # silver: residual = tec - iri
swp-data assemble omni            # silver: drivers aligned to dtec frames
swp-data assemble windows         # gold:   normalized train/val/test windows
```

Global flags: `--data-root DIR` (overrides `config.yaml`), `--log-level LEVEL`.

### What you must supply

Only three things. Every directory is created on demand
(`mkdir(parents=True, exist_ok=True)`), and every data input is downloaded —
point `data_root` at an empty or nonexistent folder and the pipeline fills it.

**1. The code** — `pyproject.toml`, `config.yaml`, `src/swp_data/`.

**2. `~/.netrc`** — the only credential. CDDIS IONEX is the one authenticated
source; OMNI HRO and GFZ are anonymous.

```
machine urs.earthdata.nasa.gov
login YOUR_USERNAME
password YOUR_PASSWORD
```

`sources/__init__.py` sets `trust_env=True` **only** for the CDDIS session, so
requests reads `~/.netrc` from your home directory. `extract` runs an auth smoke
test (one known-good day, 2010-001) before the main loop and exits immediately
with a credential hint if it fails.

**3. Python ≥ 3.10** with the `pyproject.toml` dependencies: `numpy`, `scipy`,
`pandas`, `pyarrow`, `requests`, `unlzw3`, `PyIRI`, `torch`, `pyyaml`. PyIRI
ships its own coefficient tables (`PyIRI.coeff_dir`), so there is no external
IRI dataset to source.

### What you do *not* need

- **`data/interpolated/`** — the legacy pre-Medallion TEC directory. `settings.py`
  documents `tec_gl23x45` as "was interpolated_gl23x45". A from-scratch build
  skips it and `scripts/migrate_to_medallion.py` entirely.
- **`data_pull/`** — superseded by `src/swp_data/`.
- **A grid file** — `interpolate` writes `grid.npz` itself on the first year.

---

## Configuration

Two files, split on purpose.

**`config.yaml`** — environment-tunable run parameters. Precedence is
*dataclass defaults < config.yaml < environment variables*. Point at a different
file with `SWP_CONFIG_FILE`.

| Key | Default | Env override |
|---|---|---|
| `data_root` | `data` | `SWP_DATA_ROOT` |
| `start_date` | `2000-01-01` | `SWP_START_DATE` |
| `end_date` | `2025-12-31` | `SWP_END_DATE` |
| `center` | `COD` (CODE, Bern) | `SWP_CENTER` |
| `train_end_year` | `2019` | `SWP_TRAIN_END_YEAR` |
| `val_end_year` | `2022` | `SWP_VAL_END_YEAR` |
| `chunk_size` | `512` | `SWP_CHUNK_SIZE` |
| `ionex_base` / `omni_hro_base` / `gfz_index_url` | see file | — |

**`swp_data/config.py`** — the fixed **scientific contract**. Not environment
configuration; changing any of these invalidates existing outputs.

```python
LMAX = 22                                  # SFNO spherical-harmonic truncation
NLAT, NLON = 23, 45                        # Gauss-Legendre x equiangular
AALT = np.arange(80, 2001, 20)             # 97 IRI integration altitudes, km
OMNI_HRO_FEATURES = [b_magnitude, by_gsm, bz_gsm, flow_speed, proton_density]
KP_FEATURE = "kp_3hour"                    # -> 6 driver channels total
INPUT_STEPS, TARGET_STEPS = 6, 3           # 9-frame windows
F107_MAX = 300.0                           # burst-spike threshold, sfu
OMNI_MAX_GAP_MINUTES = 120.0               # bounded driver fill (see 4c)
KP_WINDOW_HOURS = 3.0                      # bounded Kp ffill (see 4c)
```

**`settings.DataLayout`** is the single source of truth for every on-disk path.
Nothing else in the package should build a data path by hand.

---

## Stage 1 — `extract` (bronze)

Raw, source-shaped downloads plus per-source manifests.

### Sources

| Source | Auth | Granularity | Destination |
|---|---|---|---|
| CDDIS IONEX (`COD`) | Earthdata `~/.netrc` | one file per day | `bronze/ionex/{year}/{doy}/` |
| SPDF OMNI HRO 5-min | anonymous | one file per year | `bronze/omni_hro/{year}/omni_5min{year}.asc` |
| GFZ combined index | anonymous | one file, 1932–present | `bronze/gfz/Kp_ap_Ap_SN_F107_since_1932.txt` |

The GFZ file is the **single origin** for both Kp and observed F10.7. The
OMNI2 hourly product that an earlier version pulled is gone deliberately: its
F10.7 is adjusted-to-1AU (unusable for the IRI baseline) and its June 2006 Kp
contradicts the definitive GFZ record. Nothing consumes it anymore. Because the
GFZ file is small and updated daily, it is re-downloaded on every run rather
than tracked per-year.

### The IONEX naming problem

IGS switched from short to long filenames at DOY 219, 2023. `cddis.py` tries
both schemes for every day, preferring the one appropriate to the date:

```
legacy     codg{doy}0.{yy}i.Z                                  (Unix LZW)
long_name  COD0OPSFIN_{yyyy}{doy}0000_01D_01H_GIM.INX.gz       (gzip)
```

The rename boundary is messy in practice, so the first successful download wins
and becomes that day's manifest row.

### Manifests and resumability

Append-mode CSV per source at `bronze/_manifests/{source}_manifest.csv`; on
read, the last row per key wins. Columns: `source, key, expected_filename,
status, reason, n_bytes, checked_at`.

Skip logic (`extract.py`):
- `status` in `(present, downloaded)` **and** the file exists → skip.
- `status == failed` with `reason == "404"` → skip permanently. A 404 is a real
  data gap at the analysis center, not a transient error; retrying it forever
  would waste the whole run.
- Anything else → retry.

Downloads are atomic (`.part` file, then rename) with an integrity check that
rejects HTML error pages masquerading as data.

### Flags

```bash
swp-data extract --verify         # coverage report only, no downloads
swp-data extract --ionex-only     # IONEX then report
swp-data extract --indices-only   # just the small GFZ file
swp-data extract --start-date 2020-01-01 --end-date 2020-12-31
```

---

## Stage 2 — `parse` (silver index tables)

Derives the two driver-index artifacts from the raw bronze GFZ file. These
replace the retired CelesTrak parsers at identical paths and schemas, so
downstream stages were unchanged by the swap.

### `f107_daily.parquet` — daily observed F10.7

Feeds the IRI baseline (PyIRI ships no index of its own).

- **Column mapping is asserted, not assumed.** `_assert_observed_not_adjusted`
  proves `f107_obs` is observed rather than 1AU-adjusted by checking the orbital
  signature: `obs/adj` tracks `(1AU/r)²`, so the ratio must exceed 1.015 in
  January (perihelion) and fall below 0.985 in July. If the columns were swapped
  or identical the signature disappears and the build aborts.
- Values above `F107_MAX = 300` sfu are burst-inflated single-day spikes, not a
  valid daily EUV proxy. They are set to NaN and **time-interpolated over, not
  clipped**.

### `kp_3hourly.parquet` — causal 3-hourly Kp

One row per 3-hour UT window, stamped at the window **start**. GFZ stores Kp in
decimal thirds (`2.667`); values are rounded to the same one-decimal encoding
the retiring CelesTrak path used (`27 → 2.7`) so the two series are
bit-identical. Missing sentinels (the `-1` class) are masked by sign.

### Staging mode

```bash
swp-data parse --staging-root STAGING
```

Writes the parquets under a different root instead of `data_root`, for gate
verification against a prior build (see [Verification gates](#verification-gates)).

---

## Stage 3 — `interpolate` (silver TEC)

Interpolates native IONEX 71×73 TEC maps onto the Gauss-Legendre 23×45 grid the
SFNO transform expects. One `.npz` per year, plus `grid.npz` written once.

### Grid contract

- **Latitudes**: Gauss-Legendre nodes, `nlat=23`, cell-centered — GL nodes are
  interior to (−1, 1), so they never sit exactly at a pole. Actual extent is
  **±84.14°**, ordered **ascending, south → north** (row 0 of every TEC / dTEC /
  target map is the southernmost band). Note this is the *opposite* of IONEX's
  native N→S ordering; `grid.npz` and `lats.npy` carry the truth, so plot against
  those rather than assuming.
- **Longitudes**: equiangular, `nlon=45`, 0–360 convention, `endpoint=False`.
- **Seam handling**: source longitudes are converted from −180…180 to 0…360 and
  wrap-padded on both sides so the interpolator sees continuity across 0/360.
- **Latitude order**: IONEX runs 87.5 → −87.5 descending;
  `RegularGridInterpolator` requires ascending, so rows are re-sorted.

> **Note — the pole-collapse branch is dead code.** `interpolate_map` contains a
> "collapse-to-point" fallback that fills target rows outside native coverage
> with the mean of the nearest native edge ring. For the GL23 grid it never
> fires: all 23 latitudes lie within ±84.14°, comfortably inside IONEX's ±87.5°
> coverage. It would only activate if `NLAT` grew large enough to push nodes past
> ±87.5°. Verified against the on-disk `grid.npz`.

### Parsing

`parse_ionex` is a state machine over IONEX v1.0 TEC map blocks. Three details
matter:

- Value lines fill columns 1–80, so the **whole line** is split, not `line[:60]`
  (columns 61–80 hold labels only on structural lines).
- `9999` is the missing sentinel and is converted to NaN **before** the exponent
  scaling is applied.
- **Epochs are stamped tz-aware UTC.** IONEX `EPOCH OF CURRENT MAP` lines are UT,
  and `interpolate_to_gl` is the single place the pipeline mints epoch seconds
  (every downstream stage reads them back as UTC). `datetime.timestamp()` on a
  *naive* datetime silently reinterprets it in the machine's local zone — which
  would shift every frame by the local UTC offset, by a *different* amount either
  side of a DST boundary, while leaving all downstream equality checks passing.
  `_to_epoch_utc` raises on a naive datetime rather than guess.

RMS maps are ignored — parsing stops at `START OF RMS MAP`.

### Resumability

Reads the IONEX manifest for present days, groups them by year, skips 404 gaps,
and skips years whose `.npz` already exists unless `--overwrite` is passed.
`--year Y` restricts to a single year.

---

## Stage 4 — `assemble` (silver residual + drivers, gold windows)

Four subcommands that **must run in order**.

### 4a. `assemble iri` — the climatology baseline

For each frame timestamp, evaluates PyIRI on the GL grid and integrates electron
density over the 97 altitudes in `AALT` (80–2000 km) to a vertical TEC.

- Frames are grouped **by day**, since `IRI_density_1day` takes one date plus a
  vector of UT values — this is what makes the stage tractable at all.
- F10.7 for the day comes from `f107_daily`. A missing day falls back to the
  previous day (this covers boundary timestamps) and raises otherwise.

**This is the slowest stage by a wide margin** — on the order of a few minutes
per year, so roughly an hour for 2000–2025.

### 4b. `assemble dtec` — the residual

```
dTEC = IONEX vTEC on GL23x45  −  IRI vTEC on GL23x45
```

Hard-fails if the IONEX and IRI timestamp vectors are not element-wise equal.

The plasmaspheric offset in IONEX-minus-IRI is **intentionally retained**. It is
expected to be mostly zonal and therefore representable by the SFNO `m=0` modes;
a learned zonal correction can be added later. The `residual_definition` string
is embedded in every output file so the choice travels with the data.

### 4c. `assemble omni` — driver alignment

Produces a `[N, 6]` driver matrix aligned to the dTEC frame timestamps. The two
driver families are aligned by **deliberately different rules**:

| Channels | Source | Rule | Why |
|---|---|---|---|
| `b_magnitude, by_gsm, bz_gsm, flow_speed, proton_density` | OMNI HRO 5-min | **time-interpolate** | continuous physical quantities |
| `kp_3hour` | GFZ 3-hourly | **forward-fill only** | Kp is a step function known only *after* its 3-hour window completes; interpolating would leak the future |

Both raise if any dTEC timestamp fails to map. OMNI fill sentinels are masked by
**threshold, not equality** (`>= 9999.99` etc.) to avoid float-equality misses;
real values never approach those magnitudes. `parse_omni_hro` also asserts the
file has exactly 49 fields per record, so pointing it at a 1-minute product fails
loudly instead of silently misaligning columns.

#### Both fills are bounded

An unbounded `interpolate(method="time")` draws a straight line across an
arbitrarily long outage, and OMNI has genuine multi-day plasma gaps. Because the
fill leaves no NaN behind, that fabricated driver history then passes every
downstream check — the old "raise if any NaN survives" guard could essentially
never fire, and neither could `valid_window_starts`' finite test.

So each family gets a tolerance, and values beyond it are left **NaN rather than
filled**:

| Family | Tolerance | Measured as |
|---|---|---|
| OMNI HRO | `OMNI_MAX_GAP_MINUTES` (120) | distance to the nearest real observation, **per channel** — an IMF outage does not imply a plasma outage |
| Kp | `KP_WINDOW_HOURS` (3) | age of the forward-filled stamp; older than one window means GFZ is missing the covering window |

Those NaNs are deliberate: they make the finite test in `valid_window_starts`
meaningful again, so windows spanning a real outage get **dropped** instead of
silently invented. `build_omni_cache` logs per-channel imputed/outage rates every
year, and warns with counts when any frame is rejected — that log is now the only
signal that a year is thin on real driver coverage.

Each `omni_aligned_gl23x45/{year}.npz` carries an `imputed [N, 6]` bool array
(True where a value is not an exact observation at that frame) plus an
`imputation_rule` string, so provenance travels with the data the way
`residual_definition` does for dTEC.

> **Kp causality caveat.** Forward-filling from window-*start* stamps means a
> frame at 01:00 receives the Kp of the 00:00–03:00 window — which is not
> published until 03:00 and encodes activity through 03:00. That is up to 3 h of
> lookahead on a 3–9 h forecast horizon. It is retained only because changing it
> alters the driver contract; stamping at window end would close it.

### 4d. `assemble windows` — the gold dataset

Concatenates all years, sorts by timestamp, drops duplicates, then cuts
9-frame windows (6 input + 3 target).

**Window validity rule** — a window is kept only if all 9 timestamps are:
1. finite in both dTEC and drivers (no NaN anywhere in the window),
2. strictly increasing, and
3. **identically spaced** — every adjacent gap equals the first gap.

Rule 3 is what prevents a window from silently straddling a data gap or a
cadence change (the IONEX cadence changes from 2-hourly to hourly partway
through the record).

**Splits** are temporal, by the window's *start* year:

```
year <= train_end_year (2019)              -> train
train_end_year < year <= val_end_year (2022) -> val
year > val_end_year                        -> test
```

**Normalization statistics are computed from the training split only** —
specifically from `train_tec_input` and `train_omni_input` — then applied to
inputs *and* targets across all three splits. This is the standard guard against
val/test leakage. Stats are accumulated in chunks (sum / sum-of-squares) so the
full array never has to be resident.

Outputs are written with `open_memmap` and filled chunk-wise, so peak memory
stays bounded regardless of dataset size.

Refuses to overwrite existing `.npy` outputs unless `--overwrite` is passed.

---

## Stage 5 — `dataset` (gold consumer)

`swp_data.dataset.DTECWindowDataset` is a memory-mapped PyTorch `Dataset`.

```python
from swp_data.dataset import DTECWindowDataset, make_dataloader

loader = make_dataloader(split="train", batch_size=16)   # shuffles by default
```

Each item:

| Key | Shape | dtype |
|---|---|---|
| `tec_input` | `[6, 23, 45]` | float32 |
| `omni_input` | `[6, 6]` | float32 |
| `target` | `[3, 23, 45]` | float32 |
| `timestamp` | scalar | int64 (epoch seconds, window start) |

Shapes are validated against `metadata.json` at construction, so a stale or
half-built gold directory fails immediately rather than mid-epoch.

---

## On-disk layout

```
<data_root>/
├── bronze/                                   # raw, source-shaped
│   ├── ionex/{year}/{doy}/{file}.Z|.gz
│   ├── omni_hro/{year}/omni_5min{year}.asc
│   ├── gfz/Kp_ap_Ap_SN_F107_since_1932.txt
│   └── _manifests/{ionex,omni_hro,gfz}_manifest.csv
├── silver/                                   # cleaned / derived
│   ├── f107_daily.parquet
│   ├── kp_3hourly.parquet
│   ├── tec_gl23x45/{year}.npz                # tec [N,23,45], timestamps [N] (UTC epoch s)
│   ├── tec_gl23x45/grid.npz                  # lats [23], lons [45]
│   ├── iri_gl23x45/{year}.npz                # iri [N,23,45], + altitude_km [97]
│   ├── dtec_gl23x45/{year}.npz               # dtec [N,23,45], + residual_definition
│   └── omni_aligned_gl23x45/{year}.npz       # omni [N,6], imputed [N,6] bool, features [6]
├── gold/
│   └── training_windows/
│       ├── {train,val,test}_tec_input.npy    # [n, 6, 23, 45]
│       ├── {train,val,test}_omni_input.npy   # [n, 6, 6]
│       ├── {train,val,test}_target.npy       # [n, 3, 23, 45]
│       ├── {train,val,test}_window_start_times.npy
│       ├── lats.npy, lons.npy
│       └── metadata.json
└── _runs/                                    # per-stage run manifests
```

### Scale of a full 2000–2025 build

| Layer | Size |
|---|---|
| `silver/tec_gl23x45/` | 675 MB |
| `silver/iri_gl23x45/` | 575 MB |
| `silver/dtec_gl23x45/` | 620 MB |
| `gold/training_windows/` | 5.6 GB |

Budget roughly **8–10 GB** for silver + gold, plus the bronze IONEX archive.

- **170,628** frames across 26 years (cadence rises from ~4,400/yr in 2000 to
  ~9,125/yr from 2015 on).
- **176,599,980** residual dTEC values (170,628 × 23 × 45), of which
  176,599,927 are finite — 53 NaN, all in 2001.
- **161,800** valid windows: 110,124 train / 25,406 val / 26,270 test.

---

## Run manifests

Every CLI subcommand is wrapped in a `RunManifest` context manager that writes a
JSON record to `<data_root>/_runs/`. Each record captures the stage name, the
parsed argv, the git SHA, the installed package version, platform, timing, and
the outcome (success or the exception). Output paths are attached via
`record_outputs`, so each manifest also states what it produced.

It also captures two things that code version alone cannot express:

- **`dependencies`** — versions of `PyIRI`, `numpy`, `scipy`, `pandas`. An
  unpinned PyIRI bump changes every dTEC value without touching a line of code,
  so a git SHA is not sufficient provenance.
- **`timezone`** — `TZ`, `tzname`, and the UTC offset. Frame timestamps are UTC
  epoch seconds by contract, but that contract was once violated silently by
  `datetime.timestamp()` on naive datetimes, and nothing in the manifest could
  have revealed it.

This is the audit trail: given any artifact, you can recover which code version
*and which numerical stack* built it, and with what arguments.

---

## Verification gates

`swp-data verify-gates` guards the CelesTrak → GFZ driver-index source swap. The
premise is that the swap must not change the data: both sources relay the same
producers (GFZ *is* the Kp producer; both relay NRCan Penticton observed F10.7),
so the derived series must be equal over the full overlap.

| Gate | Series | Protects |
|---|---|---|
| A | `kp_3hourly` | `omni_input` channel 6 |
| B | `f107_daily` | the IRI baseline — therefore `tec_input` **and** `target` |

```bash
swp-data parse --staging-root STAGING        # derive new parquets
swp-data verify-gates --staging-root STAGING # compare against data_root
```

Exits non-zero and enumerates every divergence on failure. Gate B is the reason
`parse` asserts the observed/adjusted column mapping — an F10.7 error propagates
into the baseline and therefore contaminates both the inputs and the targets.

## Dependency pinning

Two tiers. **`pyproject.toml` ranges** say what the pipeline supports;
**`requirements.lock`** records what it was actually built with. Ranges alone are
not reproducible; a lock alone is not installable elsewhere.

Tightness is `(likelihood of semantic drift) x (blast radius on the data)`, not a
uniform policy:

| Package | In the numerical path? | Drift risk | Pin |
|---|---|---|---|
| `PyIRI` | **It *is* the baseline** — lands in inputs *and* targets | **High** (`0.x`; ships its own coefficient tables) | `==0.1.6` |
| `pandas` | Yes — `interpolate(method="time")` for F10.7 spikes and OMNI alignment | Low on numerics, real on API (3.0 made `to_numpy()` read-only under copy-on-write) | `>=2.2,<4` |
| `numpy`, `scipy` | Deterministic math only (`leggauss`, `RegularGridInterpolator` linear) | Low | major bounds |
| `pyarrow`, `requests`, `unlzw3`, `pyyaml` | No | Low | floor only |
| `torch` | No — consumer only, reads finished `.npy` | n/a | floor only |

### Moving the PyIRI pin

Don't reason about whether a bump is safe — **measure it**. Same premise as the
[verification gates](#verification-gates): rebuild and prove the data didn't move.

```bash
SWP_DATA_ROOT=/tmp/pin_probe swp-data assemble iri --year 2015 --overwrite
```

```python
import numpy as np
old = np.load("data/silver/iri_gl23x45/2015.npz")["iri"]
new = np.load("/tmp/pin_probe/silver/iri_gl23x45/2015.npz")["iri"]
print("bit-identical:", np.array_equal(old, new))
print("max abs diff :", np.nanmax(np.abs(old - new)))
print("p99 abs diff :", np.nanpercentile(np.abs(old - new), 99))
```

Bit-identical → move the pin. Not identical → the honest response is a version
bump on the *dataset*, not just the dependency. Probe a solar-max year (2015)
rather than a quiet one; that is where a model change shows up largest.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

`tests/` covers the two conventions that are easiest to break silently, because
neither produces a visible error when wrong:

- **`test_timestamps.py`** — frame epochs are true UT under every machine
  timezone, are byte-identical across timezones (a UTC cloud build must equal a
  laptop build), stay evenly spaced across a DST transition, and a naive datetime
  is rejected outright. Fixtures render a synthetic IONEX file with an
  analytically known TEC field.
- **`test_driver_alignment.py`** — long outages are left NaN rather than filled,
  short extrapolations still fill, gaps are measured per channel, the `imputed`
  mask is accurate, and no surviving window contains a rejected driver value.

---

## Operational notes

**Ordering is strict.** `dtec` needs both `tec` and `iri` on disk and hard-fails
on mismatched timestamps; `omni` reads the dTEC frames to align against;
`windows` needs every year's `dtec` *and* `omni`. Do not run these out of order
or in parallel.

**Everything is resumable.** Each stage skips years whose output already exists
unless `--overwrite` is passed, and `extract` is manifest-driven, so a
re-run only fetches what is missing. `--year Y` narrows any `interpolate` or
`assemble {iri,dtec,omni}` run to a single year.

**Regenerating a stage does not cascade.** Because the skip check is
"does the output file exist", changing an upstream input will *not* propagate
downstream on its own. If you change the F10.7 source, you must delete or
`--overwrite` the `iri_gl23x45` year files — otherwise `assemble iri` logs
`skip IRI (exists)` and the stale baseline survives, silently invalidating every
residual built on top of it.

**Cost profile.** `extract` is network-bound (~9,500 IONEX days). `assemble iri`
is CPU-bound and dominates local compute. Everything else is I/O-bound.

---

## Known gaps

- **`interpolate.py` carries two `# Need to understand this function better`
  markers** on `interpolate_to_gl` and `build_interpolated`. Both work, but they
  are flagged as not fully reviewed.
- **`parse_omni_hro` parses all 49 OMNI fields** and then keeps 5. The TODO in
  the source notes this can be slimmed once the driver contract is final.
- **`_assert_observed_not_adjusted` is marked "not necessary"** in a comment yet
  is still called on every `parse` run. It is cheap and it protects Gate B, so
  the comment is the thing that is wrong, not the call.
- **The plasmaspheric offset is uncorrected** by design. If the SFNO `m=0` modes
  turn out not to absorb it, a learned zonal correction is the intended fix.
- **`dtec` compares timestamps but `windows` re-checks them** — the invariant is
  enforced in two places with different error messages. Not a bug, but a single
  helper would be clearer.
