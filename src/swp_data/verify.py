"""Equivalence gates for the CelesTrak -> GFZ driver-index source swap.

The swap must not change the data. Both sources relay the same producers
(GFZ is the Kp producer; both relay NRCan Penticton observed F10.7), so the
GFZ-derived series must equal the retiring CelesTrak-derived series over the
full overlap:

  Gate A: kp_3hourly equality  -> protects omni_input channel 6
  Gate B: f107_daily equality  -> protects the IRI baseline, therefore
                                  tec_input AND target

Usage:
    swp-data verify-gates --staging-root STAGING [--data-root data]

STAGING holds the newly derived parquets (written by `swp-data parse
--staging-root STAGING`); --data-root holds the pre-swap parquets. Exits
non-zero if either gate fails.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .settings import DataLayout

logger = logging.getLogger(__name__)


def _load(path: Path, column: str) -> pd.Series:
    df = pd.read_parquet(path)
    if column not in df.columns:
        raise ValueError(f"{path} missing column {column!r}")
    out = df[column].astype(float)
    out.index = pd.to_datetime(out.index)
    return out.sort_index()


def _gate(name: str, old: pd.Series, new: pd.Series) -> bool:
    """Equality where both present; enumerate every divergence."""
    joined = pd.DataFrame({"old": old, "new": new})
    both = joined.dropna()
    only_old = joined[joined["new"].isna() & joined["old"].notna()]
    only_new = joined[joined["old"].isna() & joined["new"].notna()]

    exact = both["old"].to_numpy() == both["new"].to_numpy()
    close = np.isclose(both["old"], both["new"], rtol=0.0, atol=1e-9)
    mismatch = both[~close]

    logger.info("--- Gate %s ---", name)
    logger.info("old rows: %d  (%s .. %s)", len(old.dropna()), old.index.min(), old.index.max())
    logger.info("new rows: %d  (%s .. %s)", len(new.dropna()), new.index.min(), new.index.max())
    logger.info("overlap (both present): %d", len(both))
    logger.info("bit-exact equal: %d / %d", int(exact.sum()), len(both))
    logger.info("within 1e-9:     %d / %d", int(close.sum()), len(both))
    if len(only_old):
        logger.info("only in old: %d  (%s .. %s)",
                    len(only_old), only_old.index.min(), only_old.index.max())
    if len(only_new):
        logger.info("only in new: %d  (%s .. %s)",
                    len(only_new), only_new.index.min(), only_new.index.max())

    if len(mismatch):
        logger.error("FAIL: %d value mismatches:", len(mismatch))
        with pd.option_context("display.max_rows", 60):
            logger.error("\n%s", mismatch.to_string())
        return False

    logger.info("PASS: no value differences over the overlap")
    return True


def run(data_root: Path, staging_root: Path) -> None:
    old_layout = DataLayout(data_root)
    new_layout = DataLayout(staging_root)
    ok_a = _gate(
        "A (Kp)",
        _load(old_layout.kp_3hourly, "kp"),
        _load(new_layout.kp_3hourly, "kp"),
    )
    ok_b = _gate(
        "B (F10.7)",
        _load(old_layout.f107_daily, "f107_obs"),
        _load(new_layout.f107_daily, "f107_obs"),
    )

    if ok_a and ok_b:
        logger.info("Both gates PASS: the GFZ-derived series equal the CelesTrak-derived "
                    "series. Rebuilding downstream stages must reproduce the prior dataset.")
    else:
        logger.error("Gate FAILURE: do not swap sources until every divergence is explained.")
        sys.exit(1)
