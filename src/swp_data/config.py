"""Fixed scientific contract: grid, driver features, and step counts.

This module holds only values that are part of the *data contract* and do not
change between environments or runs. Environment-tunable parameters (data root,
date range, source URLs, split years, chunk size) live in ``settings.py``; every
on-disk path is resolved through ``settings.DataLayout``.
"""
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Grid contract (the SFNO transform's Gauss-Legendre grid)
# ---------------------------------------------------------------------------

LMAX = 22
NLAT = 23
NLON = 45
AALT = np.arange(80, 2001, 20).astype(float)  # IRI integration altitudes, km

# ---------------------------------------------------------------------------
# Driver contract
# ---------------------------------------------------------------------------

OMNI_HRO_FEATURES = ["b_magnitude", "by_gsm", "bz_gsm", "flow_speed", "proton_density"]
KP_FEATURE = "kp_3hour"
DRIVER_FEATURES = OMNI_HRO_FEATURES + [KP_FEATURE]
INPUT_STEPS = 6
TARGET_STEPS = 3

# ---------------------------------------------------------------------------
# Frame cadence -- and therefore the forecast horizon
# ---------------------------------------------------------------------------
#
# The IONEX record is NOT uniformly sampled: CODE switched from 2-hourly to
# hourly maps on 2014-10-19. Window length is fixed at INPUT_STEPS/TARGET_STEPS,
# so cadence is what sets the physical horizon:
#
#     7200 s -> see 12 h, predict +2/+4/+6 h   (full 2000-2025 record)
#     3600 s -> see  6 h, predict +1/+2/+3 h   (post-2014-10-19 only)
#
# Mixing them trains one model on two different problems with no channel telling
# it which. Frames are resampled to this cadence and windows are then required to
# match it exactly, so a single value selects the era AND enforces the contract.
#
# 7200 keeps the full 26-year span. That matters because the hourly era is
# monotonic in solar activity -- it runs from cycle 24's decline through minimum
# to cycle 25's maximum -- so any temporal split of it alone trains on quiet Sun
# and tests on active Sun (train F10.7 p95 130 vs test p95 236). The full record
# spans 2.5 cycles, letting the training split contain a genuine solar maximum.
TARGET_CADENCE_SECONDS = 7200

# Frames skipped after each split boundary. Windows are first purged (one must
# lie entirely within a split's period, so none straddles a boundary), then the
# later split skips this many frames so its first window is decorrelated from the
# earlier split rather than merely disjoint. One window length is the natural
# choice: no train frame then lies within a window's reach of any val frame.
SPLIT_EMBARGO_STEPS = INPUT_STEPS + TARGET_STEPS


# Daily F10.7 above this is a burst-inflated single-day spike, not a valid EUV
# proxy: set to NaN and time-interpolate (do NOT clip).
F107_MAX = 300.0

# ---------------------------------------------------------------------------
# Driver alignment tolerances
# ---------------------------------------------------------------------------
#
# Both driver families are filled onto the dTEC frame timestamps, and both fills
# are BOUNDED. OMNI has genuine multi-day plasma outages and GFZ occasionally
# drops a Kp window; filling across those fabricates driver history that no
# downstream check can distinguish from real data.
#
# A driver value further than its tolerance from a real observation is left NaN
# rather than filled, which is what makes the finite-check in
# `valid_window_starts` meaningful: windows touching a real outage get dropped.

# OMNI HRO is 5-minute cadence and time-interpolated. 2 h is the outer edge of
# defensible solar-wind persistence and is commensurate with the 1-2 h TEC cadence.
OMNI_MAX_GAP_MINUTES = 120.0

# Kp is a step function forward-filled from 3-hourly window-start stamps. A stamp
# is only valid for its own window, so a frame more than one window past the last
# stamp means the covering window is missing from GFZ.
KP_WINDOW_HOURS = 3.0

# ---------------------------------------------------------------------------
# Stage contracts -- what makes an output stale without its data inputs changing
# ---------------------------------------------------------------------------
#
# Lineage fingerprints (see lineage.py) hash a stage's data inputs, so a resumed
# run rebuilds instead of skipping when those inputs move. Data alone is not
# enough: changing NLAT, AALT or a fill tolerance changes the output while every
# input file stays byte-identical. The stage would log "skip (inputs unchanged)"
# and keep the stale result -- the very failure the fingerprints were added to
# prevent, triggered by a constant instead of a table.
#
# So each stage folds its governing constants into its fingerprint. The leading
# integer is a manual version: BUMP IT when the transformation's semantics change
# but none of the constants do -- a corrected epoch conversion, a fixed seam
# rule. Everything else updates itself.
#
# Defined last because they reference constants from every section above.
# Bumping IRI_CONTRACT costs a full rebuild of the slowest stage. That is the
# point, not a reason to avoid it.
INTERPOLATE_CONTRACT = (1, LMAX, NLAT, NLON)
IRI_CONTRACT = (1, tuple(AALT.tolist()))
DTEC_CONTRACT = (1,)
OMNI_CONTRACT = (1, tuple(DRIVER_FEATURES), OMNI_MAX_GAP_MINUTES, KP_WINDOW_HOURS)
