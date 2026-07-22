"""CDDIS IONEX: URL/path builders, Earthdata-authenticated session, smoke test."""
from __future__ import annotations

import logging
import sys
from datetime import date

import requests

from . import download, make_session
from ..settings import DataLayout, Settings

logger = logging.getLogger(__name__)

# IGS switched to long-name format on this date (DOY 219, 2023).
_RENAME_BOUNDARY = date(2023, 8, 7)


def make_cddis_session() -> requests.Session:
    return make_session(netrc=True)


def ionex_targets(obs_date: date, center: str) -> list[tuple[str, str]]:
    """Return candidate (relative_url, filename) pairs for one IONEX day.

    The 2023 IGS rename boundary is messy in practice, so we try both the
    preferred name for the date and the alternate naming scheme. The first
    successful download becomes the manifest row for that day.
    """
    doy = obs_date.timetuple().tm_yday
    yyyy = obs_date.year
    yy = yyyy % 100

    legacy = f"{center.lower()}g{doy:03d}0.{yy:02d}i.Z"
    long_name = f"{center.upper()}0OPSFIN_{yyyy}{doy:03d}0000_01D_01H_GIM.INX.gz"

    names = [legacy, long_name] if obs_date < _RENAME_BOUNDARY else [long_name, legacy]
    return [(f"{yyyy}/{doy:03d}/{fname}", fname) for fname in names]


def ionex_dest(layout: DataLayout, obs_date: date, fname: str):
    doy = obs_date.timetuple().tm_yday
    dest_dir = layout.ionex_day_dir(obs_date.year, doy)
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir / fname


def smoke_test(settings: Settings, session: requests.Session) -> None:
    """Download one known-good IONEX day. Aborts immediately on auth failure."""
    test_date = date(2010, 1, 1)
    rel_url, fname = ionex_targets(test_date, settings.center)[0]
    dest = ionex_dest(settings.layout, test_date, fname)
    if dest.exists():
        logger.info("smoke test: file already on disk, skipping fetch.")
        return
    url = settings.ionex_base + rel_url
    logger.info("smoke test: %s", url)
    result = download(session, url, dest)
    if result["status"] != "downloaded":
        sys.exit(
            f"\nAuth smoke test FAILED: {result['reason']}\n"
            "Check ~/.netrc — must contain:\n"
            "  machine urs.earthdata.nasa.gov\n"
            "  login YOUR_USERNAME\n"
            "  password YOUR_PASSWORD"
        )
    logger.info("smoke test passed (%s bytes).", f"{result['n_bytes']:,}")
