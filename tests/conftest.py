"""Shared fixtures: a synthetic IONEX file with an analytically known TEC field."""
from __future__ import annotations

import io
from datetime import datetime

import numpy as np
import pytest

# Native IONEX grid: 87.5 -> -87.5 by -2.5 (71 bands), -180 -> 180 by 5.0 (73 cols).
SRC_LATS = np.arange(87.5, -87.6, -2.5)
SRC_LONS = np.arange(-180.0, 180.1, 5.0)

EPOCHS = [datetime(2015, 1, 1, 0, 0, 0), datetime(2015, 7, 1, 12, 0, 0)]


def analytic_tec(lat, lon):
    """Smooth, seam-continuous test field (sin is equal at -180 and +180)."""
    return 20.0 + 10.0 * np.sin(np.radians(lon)) + 5.0 * np.cos(np.radians(lat))


def _line(content: str, label: str) -> str:
    """IONEX line: payload in cols 1-60, label in cols 61-80."""
    return f"{content:<60}{label}"


def render_ionex(epochs=EPOCHS) -> str:
    lines = [
        _line("     1.0            IONOSPHERE MAPS", "IONEX VERSION / TYPE"),
        _line("    -1", "EXPONENT"),
        _line(f"  {SRC_LATS[0]:.1f} {SRC_LATS[-1]:.1f} -2.5", "LAT1 / LAT2 / DLAT"),
        _line(f"  {SRC_LONS[0]:.1f} {SRC_LONS[-1]:.1f} 5.0", "LON1 / LON2 / DLON"),
        _line("", "END OF HEADER"),
    ]
    for i, epoch in enumerate(epochs, start=1):
        lines.append(_line(f"{i:6d}", "START OF TEC MAP"))
        lines.append(_line(
            f"{epoch.year:6d}{epoch.month:6d}{epoch.day:6d}"
            f"{epoch.hour:6d}{epoch.minute:6d}{epoch.second:6d}",
            "EPOCH OF CURRENT MAP",
        ))
        for lat in SRC_LATS:
            lines.append(_line(f"  {lat:.1f}-180.0 180.0   5.0 450.0",
                               "LAT/LON1/LON2/DLON/H"))
            # exponent -1, so values are stored as TECU * 10
            values = [int(round(analytic_tec(lat, lon) * 10)) for lon in SRC_LONS]
            for k in range(0, len(values), 16):
                lines.append(" ".join(f"{v:5d}" for v in values[k:k + 16]))
        lines.append(_line(f"{i:6d}", "END OF TEC MAP"))
    lines.append(_line("", "START OF RMS MAP"))
    return "\n".join(lines) + "\n"


@pytest.fixture
def ionex_stream():
    """Factory returning a fresh stream over the synthetic IONEX file."""
    return lambda epochs=EPOCHS: io.StringIO(render_ionex(epochs))
