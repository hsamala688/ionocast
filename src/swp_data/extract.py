"""Bronze stage: raw data extraction with manifests and a coverage report.

Sources: CDDIS IONEX (authenticated), SPDF OMNI HRO (anonymous),
GFZ combined Kp/F10.7 index file (anonymous). All downloads land in the bronze
layer; the per-source manifests live under bronze/_manifests.
"""
from __future__ import annotations

import csv
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .settings import DataLayout, Settings
from .sources import download, make_session
from .sources.cddis import ionex_dest, ionex_targets, make_cddis_session, smoke_test
from .sources.gfz import pull_gfz
from .sources.omni import hro_dest, hro_filename, hro_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _daterange(start: date, end: date) -> Iterator[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _years(start: date, end: date) -> range:
    return range(start.year, end.year + 1)


# ---------------------------------------------------------------------------
# Manifest (append-mode CSV; last row per key wins on read)
# ---------------------------------------------------------------------------

_COLS = ["source", "key", "expected_filename", "status", "reason", "n_bytes", "checked_at"]


def _manifest_path(layout: DataLayout, source: str) -> Path:
    p = layout.manifest_file(source)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_manifest(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        return {r["key"]: r for r in csv.DictReader(f)}


def _append_row(path: Path, manifest: dict, **row) -> None:
    manifest[row["key"]] = row
    write_header = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_COLS)
        if write_header:
            w.writeheader()
        w.writerow(row)


def _is_credential_failure(reason: str) -> bool:
    """Reasons that mean "your session is bad", not "this file is bad".

    A 401/403 is the obvious one. An HTML body returned with HTTP 200 is the
    other: CDDIS serves the Earthdata login page that way, so `download` records
    it as bad content rather than an auth error, and the run used to continue
    through every remaining day collecting garbage.
    """
    return reason in ("auth", "bad_content:html_page")


def _is_permanent_gap(row: dict | None) -> bool:
    """A 404 is a real data gap at the analysis center, not a transient error.

    Retrying one costs two requests per gap day on every single run, forever, and
    will never succeed. Shared by every source so the rule cannot drift.
    """
    return row is not None and row["status"] == "failed" and row["reason"] == "404"


def _skip(key: str, manifest: dict, dest: Path) -> bool:
    row = manifest.get(key)
    if row is None:
        return False
    if row["status"] in ("present", "downloaded") and dest.exists():
        return True
    return _is_permanent_gap(row)


# ---------------------------------------------------------------------------
# Per-source orchestrators
# ---------------------------------------------------------------------------


def pull_ionex(settings: Settings, session, start: date, end: date) -> None:
    layout = settings.layout
    mpath = _manifest_path(layout, "ionex")
    manifest = _read_manifest(mpath)

    for obs_date in _daterange(start, end):
        doy = obs_date.timetuple().tm_yday
        key = f"{obs_date.year}-{doy:03d}"
        targets = ionex_targets(obs_date, settings.center)

        if doy == 1:
            logger.info("IONEX %d ...", obs_date.year)

        row = manifest.get(key)
        if _is_permanent_gap(row):
            continue
        if row and row["status"] in ("present", "downloaded"):
            existing = ionex_dest(layout, obs_date, row["expected_filename"])
            if existing.exists():
                continue

        result = None
        used_fname = targets[0][1]
        for rel_url, candidate_fname in targets:
            candidate_dest = ionex_dest(layout, obs_date, candidate_fname)
            result = download(session, settings.ionex_base + rel_url, candidate_dest)
            used_fname = candidate_fname
            if result["status"] == "downloaded" or result["reason"] in ("auth",):
                break
            if result["reason"] != "404":
                break

        _append_row(mpath, manifest,
            source="ionex", key=key, expected_filename=used_fname,
            status=result["status"], reason=result["reason"],
            n_bytes=result["n_bytes"], checked_at=datetime.now(timezone.utc).isoformat())

        if _is_credential_failure(result["reason"]):
            sys.exit(
                f"\nCredential failure at {key} ({result['reason']}).\n"
                "Aborting rather than continuing: a mid-run credential expiry "
                "would otherwise write thousands of failure rows to the manifest, "
                "each of which is retried on the next run.\n"
                "Check that ~/.netrc has your Earthdata login and that the CDDIS "
                "application is authorized in your Earthdata profile."
            )

        if result["status"] != "downloaded" and result["reason"] != "404":
            logger.warning("IONEX %s: %s", key, result["reason"])


def pull_omni_hro(settings: Settings, start: date, end: date) -> None:
    layout = settings.layout
    mpath = _manifest_path(layout, "omni_hro")
    manifest = _read_manifest(mpath)
    session = make_session()

    for year in _years(start, end):
        key = str(year)
        dest = hro_dest(layout, year)

        if _skip(key, manifest, dest):
            logger.info("omni_hro %d: skip", year)
            continue

        result = download(session, hro_url(settings.omni_hro_base, year), dest)
        _append_row(mpath, manifest,
            source="omni_hro", key=key, expected_filename=hro_filename(year),
            status=result["status"], reason=result["reason"],
            n_bytes=result["n_bytes"], checked_at=datetime.now(timezone.utc).isoformat())
        label = result["reason"] or f"{result['n_bytes']:,} bytes"
        logger.info("omni_hro %d: %s (%s)", year, result["status"], label)


def pull_gfz_indices(settings: Settings) -> None:
    """Always re-download: one small file, 1932-present, updated daily."""
    mpath = _manifest_path(settings.layout, "gfz")
    manifest = _read_manifest(mpath)
    session = make_session()

    result = pull_gfz(session, settings)
    _append_row(mpath, manifest,
        source="gfz", key="all", expected_filename=settings.gfz_index_filename,
        status=result["status"], reason=result["reason"],
        n_bytes=result["n_bytes"], checked_at=datetime.now(timezone.utc).isoformat())
    label = result["reason"] or f"{result['n_bytes']:,} bytes"
    logger.info("gfz indices: %s (%s)", result["status"], label)


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


def report(settings: Settings, start: date, end: date) -> None:
    layout = settings.layout
    logger.info("=== Coverage Report ===")

    manifest = _read_manifest(_manifest_path(layout, "ionex"))
    total = sum(1 for _ in _daterange(start, end))
    present = [k for k, v in manifest.items() if v["status"] in ("present", "downloaded")]
    gaps = [k for k, v in manifest.items() if v["reason"] == "404"]
    failures = [k for k, v in manifest.items()
                if v["status"] == "failed" and v["reason"] != "404"]
    logger.info("IONEX     %5d/%d days  |  %d real gaps (404)  |  %d other failures",
                len(present), total, len(gaps), len(failures))
    if failures:
        logger.warning("IONEX needs attention: %s", failures[:5])

    manifest = _read_manifest(_manifest_path(layout, "omni_hro"))
    total = len(list(_years(start, end)))
    present = [k for k, v in manifest.items() if v["status"] in ("present", "downloaded")]
    failures = [k for k, v in manifest.items() if v["status"] == "failed"]
    logger.info("omni_hro  %5d/%d years |  %d failures",
                len(present), total, len(failures))

    manifest = _read_manifest(_manifest_path(layout, "gfz"))
    row = manifest.get("all")
    if row and row["status"] == "downloaded":
        logger.info("gfz       ok  (%s bytes at %s)", row["n_bytes"], row["checked_at"])
    elif row:
        logger.warning("gfz       MISSING")
    else:
        logger.warning("gfz       never pulled")


# ---------------------------------------------------------------------------
# Entry point (wired through cli.py)
# ---------------------------------------------------------------------------


def run(settings: Settings, verify_only: bool = False, ionex_only: bool = False,
        indices_only: bool = False, start: date | None = None,
        end: date | None = None) -> None:
    start = start or settings.start_date
    end = end or settings.end_date
    logger.info("=== Bronze stage: Raw Data Pull ===")
    logger.info("Center: %s  |  %s to %s", settings.center, start, end)
    logger.info("Data root: %s", settings.data_root.resolve())

    if verify_only:
        report(settings, start, end)
        return

    if indices_only:
        logger.info("--- GFZ indices ---")
        pull_gfz_indices(settings)
        return

    session = make_cddis_session()
    logger.info("--- Auth smoke test ---")
    smoke_test(settings, session)

    logger.info("--- IONEX (CDDIS, authenticated) ---")
    pull_ionex(settings, session, start, end)

    if ionex_only:
        report(settings, start, end)
        return

    logger.info("--- OMNI HRO (SPDF, anonymous) ---")
    pull_omni_hro(settings, start, end)

    logger.info("--- GFZ indices ---")
    pull_gfz_indices(settings)

    report(settings, start, end)
