"""Generate Predicted Absolute TEC under this folder (predicted_data/predicted/).

Layout (option A: year NPZ + frontend day packs; R2-ready under predicted/):

  predicted_data/predicted/
    generate.py
    pred_abs_tec_{year}.npz           # year source of truth, GL 23x45
    {year}/{YYYY-MM-DD}.f16.gz         # site packs, float16[24,71,73]

Inputs: ../change_tec_*.npz, ../iri_*.npz, ../omni_aligned_*.npz,
        ../metadata.json, ../../ml/best_model.pt

Hours whose OMNI/dTEC history contains NaN (real outages) are left missing;
day packs are written as soon as each calendar day is complete (not after the
whole year finishes).

Examples (repo root, .venv):
  .venv\\Scripts\\python.exe predicted_data\\predicted\\generate.py --smoke
  .venv\\Scripts\\python.exe predicted_data\\predicted\\generate.py --years 2023
  .venv\\Scripts\\python.exe predicted_data\\predicted\\generate.py --years 2023,2024,2025
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
PRED_DATA = HERE.parent
ROOT = PRED_DATA.parent
ML = ROOT / "ml"
CKPT_PATH = ML / "best_model.pt"
OUT_DIR = HERE

sys.path.insert(0, str(ML))
from make_one_day_map import NOTE, gl_to_ionex, load_model  # noqa: E402


class InputStore:
    """Year arrays kept in place; lookup by UTC timestamp."""

    def __init__(self) -> None:
        self.index: dict[int, tuple[int, int]] = {}
        self.years: dict[int, dict] = {}
        self.lats: np.ndarray | None = None
        self.lons: np.ndarray | None = None
        self.tec_mean = 0.0
        self.tec_std = 1.0
        self.omni_mean: np.ndarray | None = None
        self.omni_std: np.ndarray | None = None

    def load_year(self, year: int) -> None:
        if year in self.years:
            return
        chg = np.load(PRED_DATA / f"change_tec_{year}.npz")
        iri = np.load(PRED_DATA / f"iri_{year}.npz")
        omn = np.load(PRED_DATA / f"omni_aligned_{year}.npz")
        meta = json.loads((PRED_DATA / "metadata.json").read_text())
        ts = chg["timestamps"].astype(np.int64)
        self.years[year] = {
            "dtec": np.asarray(chg["dtec"], dtype=np.float32),
            "iri": np.asarray(iri["iri"], dtype=np.float32),
            "omni": np.asarray(omn["omni"], dtype=np.float32),
            "timestamps": ts,
        }
        for i, t in enumerate(ts):
            self.index[int(t)] = (year, i)
        self.lats = chg["lats"].astype(np.float32)
        self.lons = chg["lons"].astype(np.float32)
        self.tec_mean = float(meta["normalization"]["tec_mean"])
        self.tec_std = float(meta["normalization"]["tec_std"])
        self.omni_mean = np.asarray(meta["normalization"]["omni_mean"], np.float32)
        self.omni_std = np.asarray(meta["normalization"]["omni_std"], np.float32)

    def has(self, t: int) -> bool:
        return t in self.index

    def stack(self, times: list[int], which: str) -> np.ndarray:
        rows = []
        for t in times:
            year, i = self.index[t]
            rows.append(self.years[year][which][i])
        return np.stack(rows, axis=0)

    def window_ok(self, target: int) -> bool:
        """True if +2h SFNO window for target has finite dTEC, OMNI, and IRI."""
        window_end = target - 7200
        hist = [window_end - (5 - i) * 7200 for i in range(6)]
        if not all(self.has(h) for h in hist) or not self.has(target):
            return False
        if not np.isfinite(self.stack(hist, "dtec")).all():
            return False
        if not np.isfinite(self.stack(hist, "omni")).all():
            return False
        if not np.isfinite(self.stack([target], "iri")).all():
            return False
        return True


def parse_years(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def daterange(start: date, end: date) -> list[date]:
    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def day_unix(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def ensure_years_loaded(
    store: InputStore, loaded: set[int], years: list[int], *, need_prior: bool
) -> None:
    need: set[int] = set(years)
    if need_prior:
        for y in years:
            if (PRED_DATA / f"change_tec_{y - 1}.npz").exists():
                need.add(y - 1)
    for y in sorted(need):
        if y not in loaded:
            print(f"loading inputs for {y}...", flush=True)
            store.load_year(y)
            loaded.add(y)


def predict_day_maps(model, store: InputStore, d: date, device: torch.device) -> np.ndarray:
    """Absolute TEC (24, 23, 45). Missing SFNO windows stay NaN."""
    day0 = day_unix(d)
    tec_mean, tec_std = store.tec_mean, store.tec_std
    omni_mean, omni_std = store.omni_mean, store.omni_std
    assert omni_mean is not None and omni_std is not None

    abs_maps = np.full((24, 23, 45), np.nan, dtype=np.float32)
    with torch.no_grad():
        for H in range(0, 24, 2):
            target = day0 + H * 3600
            if not store.window_ok(target):
                continue
            window_end = target - 7200
            hist = [window_end - (5 - i) * 7200 for i in range(6)]
            tec_in = (store.stack(hist, "dtec") - tec_mean) / tec_std
            omni_in = (store.stack(hist, "omni") - omni_mean) / omni_std
            pred = model(
                torch.from_numpy(tec_in[None].astype(np.float32)).to(device),
                torch.from_numpy(omni_in[None].astype(np.float32)).to(device),
            )[0, 0].cpu().numpy()
            abs_maps[H] = pred * tec_std + tec_mean + store.stack([target], "iri")[0]
    for H in range(1, 23, 2):
        a, b = abs_maps[H - 1], abs_maps[H + 1]
        if np.isfinite(a).all() and np.isfinite(b).all():
            abs_maps[H] = 0.5 * (a + b)
    if np.isfinite(abs_maps[22]).all():
        abs_maps[23] = abs_maps[22]
    return abs_maps


def write_daypack(day_gl: np.ndarray, lats: np.ndarray, lons: np.ndarray, d: date) -> Path:
    packed = gl_to_ionex(day_gl, lats, lons)
    assert packed.shape == (24, 71, 73)
    out_dir = OUT_DIR / f"{d.year}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{d.isoformat()}.f16.gz"
    with gzip.open(out_path, "wb", compresslevel=6) as f:
        f.write(np.ascontiguousarray(packed, dtype="<f2").tobytes())
    return out_path


def build_year(
    model, store: InputStore, year: int, device: torch.device, batch_size: int
) -> dict:
    """Infer year Absolute TEC day-by-day; write each .f16.gz as soon as the day is complete."""
    ydata = store.years[year]
    timestamps = ydata["timestamps"]
    n = len(timestamps)
    tec = np.full((n, 23, 45), np.nan, dtype=np.float32)
    leads = np.full(n, np.inf, dtype=np.float64)
    index = {int(t): i for i, t in enumerate(timestamps)}
    assert store.lats is not None and store.lons is not None

    days = daterange(date(year, 1, 1), date(year, 12, 31))
    n_packed = 0
    n_skipped = 0
    n_resume = 0
    t0 = time.time()

    for di, d in enumerate(days):
        pack_path = OUT_DIR / f"{d.year}" / f"{d.isoformat()}.f16.gz"
        day0 = day_unix(d)
        hours = [day0 + h * 3600 for h in range(24)]
        if not all(h in index for h in hours):
            n_skipped += 1
            continue

        # Resume: pack already on disk — still infer into year array, skip rewrite.
        if pack_path.exists():
            n_resume += 1

        day_gl = predict_day_maps(model, store, d, device)
        for h, t in enumerate(hours):
            tec[index[t]] = day_gl[h]
            if h % 2 == 0 and np.isfinite(day_gl[h]).all():
                leads[index[t]] = 7200.0

        if not np.isfinite(day_gl).all():
            n_bad = 24 - sum(1 for h in range(24) if np.isfinite(day_gl[h]).all())
            n_skipped += 1
            if (di + 1) % 7 == 0 or di == 0 or di == len(days) - 1:
                print(
                    f"    {d.isoformat()}: skip ({n_bad}/24 hrs missing)  "
                    f"packed={n_packed} skipped={n_skipped} resume_existed={n_resume}",
                    flush=True,
                )
            continue

        if not pack_path.exists():
            write_daypack(day_gl, store.lats, store.lons, d)
            n_packed += 1
            print(
                f"    wrote {pack_path.relative_to(ROOT)}  "
                f"({n_packed} new, {di + 1}/{len(days)} days, "
                f"{(di + 1) / max(time.time() - t0, 1e-6):.2f} days/s)",
                flush=True,
            )
        else:
            if (di + 1) % 14 == 0 or di == len(days) - 1:
                print(
                    f"    keep {pack_path.name}  "
                    f"({di + 1}/{len(days)} days)",
                    flush=True,
                )

    finite = int(np.isfinite(tec).all(axis=(1, 2)).sum())
    print(
        f"  {year}: {finite}/{n} hours finite; "
        f"{n_packed} new packs, {n_skipped} incomplete days, "
        f"{n_resume} packs already on disk",
        flush=True,
    )
    return {
        "tec": tec,
        "timestamps": timestamps,
        "lats": store.lats,
        "lons": store.lons,
        "leads_seconds": leads,
        "note": NOTE,
        "n_packed": n_packed,
    }


def run_smoke(model, store: InputStore, device: torch.device, days: list[date]) -> None:
    assert store.lats is not None and store.lons is not None
    print(f"smoke: {days[0]} -> {days[-1]} ({len(days)} days)")
    ok = 0
    for d in days:
        print(f"  infer {d.isoformat()}...")
        day = predict_day_maps(model, store, d, device)
        n_bad = 24 - sum(1 for h in range(24) if np.isfinite(day[h]).all())
        if n_bad:
            print(f"    skip pack ({n_bad}/24 hours missing; likely OMNI outage)")
            continue
        path = write_daypack(day, store.lats, store.lons, d)
        with gzip.open(path, "rb") as f:
            raw = f.read()
        arr = np.frombuffer(raw, dtype="<f2").reshape(24, 71, 73)
        print(
            f"    wrote {path.relative_to(ROOT)}  "
            f"shape={arr.shape} h12_mean={float(arr[12].mean()):.2f}"
        )
        ok += 1
    if ok == 0:
        raise SystemExit("smoke failed: no complete day packs written")
    print(f"smoke OK ({ok}/{len(days)} days packed)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--smoke",
        action="store_true",
        help="smoke test: 2023-01-06..08 day packs (skips incomplete days)",
    )
    g.add_argument(
        "--years",
        help="years -> year NPZ + day packs (e.g. 2023,2024,2025)",
    )
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--device", default="cpu")
    args = p.parse_args(argv)

    if not CKPT_PATH.exists():
        raise SystemExit(f"missing checkpoint: {CKPT_PATH}")

    device = torch.device(args.device)
    print(f"loading {CKPT_PATH.name} on {device}...", flush=True)
    model = load_model(device)

    store = InputStore()
    loaded: set[int] = set()

    if args.smoke:
        days = daterange(date(2023, 1, 6), date(2023, 1, 8))
        ensure_years_loaded(store, loaded, [2023], need_prior=False)
        run_smoke(model, store, device, days)
        return 0

    years = parse_years(args.years)
    store = InputStore()
    loaded: set[int] = set()
    for year in years:
        npz_path = OUT_DIR / f"pred_abs_tec_{year}.npz"
        if npz_path.exists() and (OUT_DIR / f"{year}").exists():
            n_packs = len(list((OUT_DIR / f"{year}").glob("*.f16.gz")))
            if n_packs > 300:
                print(
                    f"skip {year}: {npz_path.name} exists with {n_packs} day packs",
                    flush=True,
                )
                continue
        # Load only this year (+ prior if present) to limit RAM.
        for y in list(loaded):
            if y not in (year, year - 1):
                store.years.pop(y, None)
                store.index = {
                    t: v for t, v in store.index.items() if v[0] in store.years
                }
                loaded.discard(y)
        ensure_years_loaded(store, loaded, [year], need_prior=True)
        print(f"building year {year}...", flush=True)
        result = build_year(model, store, year, device, args.batch_size)
        np.savez_compressed(
            npz_path,
            tec=result["tec"],
            timestamps=result["timestamps"],
            lats=result["lats"],
            lons=result["lons"],
            leads_seconds=result["leads_seconds"],
            note=np.array(result["note"]),
        )
        print(
            f"  wrote {npz_path.relative_to(ROOT)} "
            f"({result.get('n_packed', 0)} new day packs this year)",
            flush=True,
        )
        del result
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    # Windows pipes buffer forever otherwise; force line-buffered logs.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
        sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass
    sys.exit(main())
