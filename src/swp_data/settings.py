"""Runtime configuration and the Medallion (bronze/silver/gold) path layout.

Two concerns live here, kept apart on purpose:

  DataLayout  - the single source of truth for every on-disk path, derived from
                a data root. Bronze = raw ingested, Silver = cleaned/derived,
                Gold = ML-ready. Nothing else in the package should build a
                data path by hand.

  Settings    - environment-tunable run parameters (data root, date range,
                source URLs, split years, chunk size), loaded from config.yaml
                with environment-variable overrides. The fixed scientific
                contract (grid, driver features, step counts) stays in
                config.py; it is not environment configuration.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import Path

import yaml

# Repo-root config file. Resolves only for an editable install (src/ layout);
# for a wheel this points into site-packages, where no config.yaml is shipped.
# See resolve_config_file for the full search order.
_PACKAGE_CONFIG_FILE = Path(__file__).resolve().parents[2] / "config.yaml"


# ---------------------------------------------------------------------------
# Medallion path layout
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataLayout:
    """Resolve every pipeline path under a data root, by Medallion layer.

    Bronze holds raw, source-shaped downloads and the ingestion manifests.
    Silver holds cleaned/derived, analysis-ready artifacts. Gold holds the
    ML-ready training windows. Run manifests land under ``_runs``.
    """

    root: Path

    def __post_init__(self) -> None:
        # Allow DataLayout("data") as well as DataLayout(Path("data")).
        object.__setattr__(self, "root", Path(self.root))

    # -- layer roots --
    @property
    def bronze(self) -> Path:
        return self.root / "bronze"

    @property
    def silver(self) -> Path:
        return self.root / "silver"

    @property
    def gold(self) -> Path:
        return self.root / "gold"

    @property
    def runs_dir(self) -> Path:
        return self.root / "_runs"

    # -- bronze: raw ingested + ingestion metadata --
    def ionex_day_dir(self, year: int, doy: int) -> Path:
        return self.bronze / "ionex" / str(year) / f"{doy:03d}"

    def omni_hro_dir(self, year: int) -> Path:
        return self.bronze / "omni_hro" / str(year)

    def gfz_file(self, filename: str) -> Path:
        return self.bronze / "gfz" / filename

    def manifest_file(self, source: str) -> Path:
        return self.bronze / "_manifests" / f"{source}_manifest.csv"

    # -- silver: cleaned / derived --
    @property
    def f107_daily(self) -> Path:
        return self.silver / "f107_daily.parquet"

    @property
    def kp_3hourly(self) -> Path:
        return self.silver / "kp_3hourly.parquet"

    @property
    def tec_dir(self) -> Path:
        """IONEX vTEC interpolated onto the GL23x45 grid (was interpolated_gl23x45)."""
        return self.silver / "tec_gl23x45"

    @property
    def iri_dir(self) -> Path:
        return self.silver / "iri_gl23x45"

    @property
    def dtec_dir(self) -> Path:
        return self.silver / "dtec_gl23x45"

    @property
    def omni_aligned_dir(self) -> Path:
        return self.silver / "omni_aligned_gl23x45"

    @property
    def grid_file(self) -> Path:
        return self.tec_dir / "grid.npz"

    # -- gold: ML-ready --
    @property
    def training_windows(self) -> Path:
        return self.gold / "training_windows"


# ---------------------------------------------------------------------------
# Environment-tunable settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    """Run parameters loaded from config.yaml, overridable by environment."""

    data_root: Path = Path("data")
    start_date: date = date(2000, 1, 1)
    end_date: date = date(2025, 12, 31)
    center: str = "COD"
    train_end_year: int = 2019
    val_end_year: int = 2022
    chunk_size: int = 512

    # The config file these values came from, or None if defaults were used.
    # Not itself configurable; recorded for reporting and provenance.
    config_file: Path | None = None

    # Remote sources
    ionex_base: str = "https://cddis.nasa.gov/archive/gnss/products/ionex/"
    omni_hro_base: str = "https://spdf.gsfc.nasa.gov/pub/data/omni/high_res_omni/"
    gfz_index_url: str = "https://kp.gfz.de/app/files/Kp_ap_Ap_SN_F107_since_1932.txt"
    gfz_index_filename: str = "Kp_ap_Ap_SN_F107_since_1932.txt"

    @property
    def layout(self) -> DataLayout:
        return DataLayout(self.data_root)


# Environment overrides: name -> (settings field, coercion).
_ENV_OVERRIDES: dict[str, tuple[str, callable]] = {
    "SWP_DATA_ROOT": ("data_root", Path),
    "SWP_START_DATE": ("start_date", date.fromisoformat),
    "SWP_END_DATE": ("end_date", date.fromisoformat),
    "SWP_CENTER": ("center", str),
    "SWP_TRAIN_END_YEAR": ("train_end_year", int),
    "SWP_VAL_END_YEAR": ("val_end_year", int),
    "SWP_CHUNK_SIZE": ("chunk_size", int),
}

# YAML key -> coercion for fields that are not plain strings/ints.
_YAML_COERCE: dict[str, callable] = {
    "data_root": Path,
    "start_date": lambda v: v if isinstance(v, date) else date.fromisoformat(str(v)),
    "end_date": lambda v: v if isinstance(v, date) else date.fromisoformat(str(v)),
}


def config_candidates() -> list[Path]:
    """Where config.yaml may live, most specific first.

    The working directory comes first so a build can be driven from wherever it
    runs. The package-relative path is second and only resolves for an editable
    install -- for a wheel it points into site-packages, where no config.yaml is
    shipped.
    """
    return [Path.cwd() / "config.yaml", _PACKAGE_CONFIG_FILE]


def resolve_config_file(config_file: Path | None = None) -> Path | None:
    """Locate the config file, or None if there genuinely isn't one.

    An explicitly requested file that does not exist is an error, not a silent
    fallback to defaults: `pip install .` (rather than `-e .`) puts the package
    in site-packages where the package-relative path resolves to nothing, so an
    edited config.yaml used to be discarded without a word. Because the shipped
    defaults equal the shipped file, that was invisible until someone changed a
    value -- and then they got the wrong data with no signal.
    """
    explicit = config_file
    if explicit is None:
        # An empty or whitespace-only env var means "unset", not "look for a
        # file named ''" -- shells routinely export it that way.
        explicit = os.environ.get("SWP_CONFIG_FILE", "").strip() or None

    if explicit is not None:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(
                f"Config file not found: {path}. It was requested explicitly "
                "(argument or SWP_CONFIG_FILE), so it is not being ignored in "
                "favour of the defaults."
            )
        return path

    return next((p for p in config_candidates() if p.exists()), None)


def load_settings(config_file: Path | None = None) -> Settings:
    """Build Settings from config.yaml (if found) then environment overrides.

    Precedence: dataclass defaults < config.yaml < environment variables.
    The file actually used is recorded on the returned Settings so callers can
    report it once logging is configured.
    """
    values: dict = {}
    valid = {f.name for f in fields(Settings)}

    path = resolve_config_file(config_file)
    if path is not None:
        raw = yaml.safe_load(path.read_text()) or {}
        for key, val in raw.items():
            if key not in valid or key == "config_file":
                continue
            values[key] = _YAML_COERCE.get(key, lambda v: v)(val)

    for env_name, (field_name, coerce) in _ENV_OVERRIDES.items():
        if env_name in os.environ:
            values[field_name] = coerce(os.environ[env_name])

    return Settings(**values, config_file=path)
