"""Package logging configuration.

`setup_logging` is called once from `cli.main`. Modules obtain their own logger
with `logging.getLogger(__name__)` and never configure handlers themselves, so
importing the package as a library does not hijack the root logger.
"""
from __future__ import annotations

import logging

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def setup_logging(level: str | int = "INFO") -> None:
    """Configure the root logger once with a timestamped, single-line format."""
    if isinstance(level, str):
        level = logging.getLevelName(level.upper())
    logging.basicConfig(level=level, format=_FORMAT, datefmt=_DATEFMT)
