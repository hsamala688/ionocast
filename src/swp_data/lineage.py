"""Input fingerprints, so a stage can tell when its output is stale.

Every stage used to resume on `dest.exists()` alone, which made the whole
pipeline blind to upstream change. Regenerating the F10.7 and Kp tables left
`iri_gl23x45` and `omni_aligned_gl23x45` untouched, so the shipped gold layer was
built on CelesTrak-derived indices two days *after* silver had been rebuilt from
GFZ -- and every stage logged `skip (exists)` and reported success.

Modification times cannot catch this. Copying or moving a tree rewrites them,
which is exactly what the migration script did, and a restored backup would look
newer than the thing it feeds.

So each stage records a fingerprint of its inputs *inside* its output. On a
resumed run the fingerprint is recomputed from the current inputs and compared;
a mismatch means the output no longer follows from what is on disk, and it is
rebuilt rather than skipped.

Fingerprints chain: a stage hashes its upstream's fingerprint along with the
upstream data it reads, so a change at any depth propagates the whole way down
without re-hashing gigabytes at every step.
"""
from __future__ import annotations

import hashlib
import logging
import zipfile
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_FIELD = "input_fingerprint"


def fingerprint(*parts) -> str:
    """Stable short digest of the inputs a stage consumed.

    Arrays hash by exact bytes; everything else by its string form. Parts are
    separated so that ("ab", "c") and ("a", "bc") differ.
    """
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, np.ndarray):
            digest.update(np.ascontiguousarray(part).tobytes())
        elif isinstance(part, (list, tuple)):
            for item in part:
                digest.update(str(item).encode())
                digest.update(b"\x1f")
        else:
            digest.update(str(part).encode())
        digest.update(b"\x00")
    return digest.hexdigest()[:16]


def dependency_version(name: str) -> str:
    """Installed version of a package whose output *is* the data.

    PyIRI computes the climatology baseline, so an upgrade changes every dTEC
    value with no input file and no line of our code changing. Folding it into
    the IRI fingerprint means the rebuild happens on its own.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version(name)
        except PackageNotFoundError:
            return "absent"
    except ImportError:  # pragma: no cover
        return "unknown"


def stored_fingerprint(path: Path) -> str | None:
    """Fingerprint recorded inside an .npz output, or None if absent/unreadable."""
    if not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            if _FIELD not in data.files:
                return None
            return str(data[_FIELD].item())
    except (OSError, ValueError, EOFError, zipfile.BadZipFile):
        # A truncated file from an interrupted write is, for our purposes, stale.
        # BadZipFile derives straight from Exception, not OSError, so it needs
        # naming explicitly -- an .npz is a zip archive underneath.
        return None


def should_rebuild(dest: Path, expected: str, overwrite: bool, label: str) -> bool:
    """Decide whether to (re)build `dest`, and say why when the answer is yes."""
    if overwrite:
        return True
    if not dest.exists():
        return True

    stored = stored_fingerprint(dest)
    if stored is None:
        logger.warning(
            "%s: rebuilding -- no input fingerprint recorded, so its provenance "
            "cannot be verified (built before lineage tracking, or truncated)",
            label,
        )
        return True
    if stored != expected:
        logger.warning(
            "%s: rebuilding -- inputs changed since it was built (%s -> %s)",
            label, stored, expected,
        )
        return True

    logger.info("%s: skip (inputs unchanged)", label)
    return False
