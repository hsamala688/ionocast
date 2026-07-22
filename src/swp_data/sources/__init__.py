"""Shared HTTP download plumbing for all Stage 1 sources.

Every download goes through `download()`: retries with exponential backoff on
transient faults (never on 404), rejects tiny or HTML responses (a saved login
page is not data), and writes atomically via a .part file.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

_MIN_BYTES = 2048
_HTML_TELLS = (b"<!DOCTYPE", b"<!doctype", b"<html", b"<HTML")


def make_session(netrc: bool = False) -> requests.Session:
    s = requests.Session()
    s.trust_env = netrc  # reads ~/.netrc (CDDIS Earthdata auth) when True
    return s


def _is_html(data: bytes) -> bool:
    head = data[:256].lstrip()
    return any(head.startswith(t) for t in _HTML_TELLS)


def download(session: requests.Session, url: str, dest: Path, retries: int = 3,
             min_bytes: int = _MIN_BYTES) -> dict:
    """Atomic download with integrity check. Returns {status, reason, n_bytes}."""
    part = Path(str(dest) + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=60, allow_redirects=True)
        except requests.exceptions.Timeout:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return {"status": "failed", "reason": "timeout", "n_bytes": 0}
        except requests.exceptions.RequestException as exc:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return {"status": "failed", "reason": f"connection:{exc}", "n_bytes": 0}

        if resp.status_code == 404:
            return {"status": "failed", "reason": "404", "n_bytes": 0}
        if resp.status_code in (401, 403):
            return {"status": "failed", "reason": "auth", "n_bytes": 0}
        if resp.status_code >= 500:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return {"status": "failed", "reason": f"http_{resp.status_code}", "n_bytes": 0}
        if resp.status_code != 200:
            return {"status": "failed", "reason": f"http_{resp.status_code}", "n_bytes": 0}

        data = resp.content
        if len(data) < min_bytes:
            return {"status": "failed", "reason": "bad_content:too_small", "n_bytes": len(data)}
        if _is_html(data):
            return {"status": "failed", "reason": "bad_content:html_page", "n_bytes": len(data)}

        part.write_bytes(data)
        os.replace(part, dest)
        return {"status": "downloaded", "reason": "", "n_bytes": len(data)}

    return {"status": "failed", "reason": "max_retries", "n_bytes": 0}
