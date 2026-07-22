"""SPDF OMNI HRO (5-minute solar wind), anonymous download.

The OMNI2 hourly download that Stage 1 used to make is gone on purpose: its
F10.7 is adjusted-to-1AU (unusable for the IRI baseline) and its June 2006 Kp
contradicts the definitive GFZ record. Nothing consumes it anymore.
"""
from __future__ import annotations

from ..settings import DataLayout


def hro_filename(year: int) -> str:
    return f"omni_5min{year}.asc"


def hro_url(omni_hro_base: str, year: int) -> str:
    return omni_hro_base + hro_filename(year)


def hro_dest(layout: DataLayout, year: int):
    dest_dir = layout.omni_hro_dir(year)
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir / hro_filename(year)
