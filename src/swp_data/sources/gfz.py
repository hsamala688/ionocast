"""GFZ Potsdam combined index file: single origin for Kp and observed F10.7.

One file covers 1932-present and is updated daily, so it is re-downloaded on
every extract run rather than tracked per-year. Rows carry a definitive flag
(D=2 fully definitive); the retrospective 2000-2025 range is definitive.
"""
from __future__ import annotations

import requests

from . import download
from ..settings import Settings


def pull_gfz(session: requests.Session, settings: Settings) -> dict:
    dest = settings.layout.gfz_file(settings.gfz_index_filename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return download(session, settings.gfz_index_url, dest)
