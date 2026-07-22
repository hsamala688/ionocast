"""swp-data entry point (installed console script: `swp-data`).

    swp-data extract     [--verify] [--ionex-only] [--indices-only]
                         [--start-date Y-M-D] [--end-date Y-M-D]
    swp-data parse       [--staging-root DIR]
    swp-data interpolate [--year Y] [--overwrite]
    swp-data assemble iri     [--year Y] [--overwrite]
    swp-data assemble dtec    [--year Y] [--overwrite]
    swp-data assemble omni    [--year Y] [--overwrite]
    swp-data assemble windows [--train-end-year Y] [--val-end-year Y]
                              [--cadence-seconds S] [--overwrite]
    swp-data verify-gates --staging-root DIR

Global: --data-root DIR (overrides config.yaml), --log-level LEVEL.
Run parameters come from config.yaml + environment (see swp_data/settings.py);
every stage is wrapped in a run manifest written under <data_root>/_runs.
Heavy imports (PyIRI, torch, scipy) are deferred to the subcommand that needs
them.
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
from datetime import date
from pathlib import Path

from .config import TARGET_CADENCE_SECONDS
from .logging_setup import setup_logging
from .runlog import RunManifest
from .settings import Settings, config_candidates, load_settings


def _build_parser(defaults: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swp-data")
    parser.add_argument("--data-root", help="override config.yaml data_root")
    parser.add_argument("--log-level", default="INFO",
                        help="DEBUG, INFO, WARNING, ERROR (default INFO)")
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract", help="Bronze: raw downloads + manifests")
    extract.add_argument("--verify", action="store_true", help="coverage report only")
    extract.add_argument("--ionex-only", action="store_true")
    extract.add_argument("--indices-only", action="store_true", help="GFZ index file only")
    extract.add_argument("--start-date")
    extract.add_argument("--end-date")

    parse = sub.add_parser("parse", help="Silver: derive F10.7/Kp tables from GFZ")
    parse.add_argument("--staging-root",
                       help="write parquets under this root instead of --data-root "
                            "(for gate verification against a prior build)")

    interpolate = sub.add_parser("interpolate", help="Silver: IONEX -> GL23x45")
    interpolate.add_argument("--year", type=int)
    interpolate.add_argument("--overwrite", action="store_true")

    assemble = sub.add_parser("assemble", help="Silver/Gold: baseline/residual/drivers/windows")
    assemble_sub = assemble.add_subparsers(dest="step", required=True)
    for name in ("iri", "dtec", "omni"):
        s = assemble_sub.add_parser(name)
        s.add_argument("--year", type=int)
        s.add_argument("--overwrite", action="store_true")
    windows = assemble_sub.add_parser("windows")
    windows.add_argument("--out-dir",
                         help="gold output dir (default: <data_root>/gold/training_windows)")
    windows.add_argument("--train-end-year", type=int, default=defaults.train_end_year)
    windows.add_argument("--val-end-year", type=int, default=defaults.val_end_year)
    windows.add_argument("--chunk-size", type=int, default=defaults.chunk_size)
    windows.add_argument(
        "--cadence-seconds", type=int, default=TARGET_CADENCE_SECONDS,
        help=f"frame spacing, which sets the forecast horizon (default "
             f"{TARGET_CADENCE_SECONDS}). 7200 keeps the full 2000-2025 record "
             f"at +2/+4/+6h; 3600 keeps only the post-2014-10-19 hourly era at "
             f"+1/+2/+3h. Frames are resampled to this and windows must match "
             f"it exactly.")
    windows.add_argument("--overwrite", action="store_true")

    gates = sub.add_parser("verify-gates", help="CelesTrak->GFZ equivalence gates")
    gates.add_argument("--staging-root", required=True)

    return parser


def _stage_name(args: argparse.Namespace) -> str:
    if args.command == "assemble":
        return f"assemble-{args.step}"
    return args.command


def main() -> None:
    settings = load_settings()
    parser = _build_parser(settings)
    args = parser.parse_args()

    setup_logging(args.log_level)

    # Reported only now, because load_settings has to run before the parser can
    # take its defaults from it -- and that is before logging is configured.
    log = logging.getLogger(__name__)
    if settings.config_file is None:
        log.warning(
            "no config.yaml found, using built-in defaults (looked in: %s). "
            "If you edited a config file, note that a non-editable install "
            "cannot see the package-relative one.",
            ", ".join(str(p) for p in config_candidates()),
        )
    else:
        log.info("config: %s", settings.config_file)

    if args.data_root:
        settings = dataclasses.replace(settings, data_root=Path(args.data_root))

    manifest_args = {k: str(v) for k, v in vars(args).items() if v is not None}
    manifest_args["config_file"] = str(settings.config_file)
    with RunManifest(_stage_name(args), settings.layout.runs_dir, manifest_args) as run_manifest:
        _dispatch(args, settings, run_manifest)


def _dispatch(args: argparse.Namespace, settings: Settings, run_manifest: RunManifest) -> None:
    layout = settings.layout
    data_root = settings.data_root

    if args.command == "extract":
        from .extract import run
        run(
            settings,
            verify_only=args.verify,
            ionex_only=args.ionex_only,
            indices_only=args.indices_only,
            start=date.fromisoformat(args.start_date) if args.start_date else None,
            end=date.fromisoformat(args.end_date) if args.end_date else None,
        )
        run_manifest.record_outputs([layout.bronze])

    elif args.command == "parse":
        from .parse import build_index_tables
        f107_out, kp_out = build_index_tables(
            settings,
            out_root=Path(args.staging_root) if args.staging_root else None,
        )
        run_manifest.record_outputs([f107_out, kp_out])

    elif args.command == "interpolate":
        from .interpolate import build_interpolated
        build_interpolated(settings, year=args.year, overwrite=args.overwrite)
        run_manifest.record_outputs([layout.tec_dir])

    elif args.command == "assemble":
        from . import assemble as asm
        if args.step == "iri":
            asm.build_iri_cache(data_root, year=args.year, overwrite=args.overwrite)
            run_manifest.record_outputs([layout.iri_dir])
        elif args.step == "dtec":
            asm.build_dtec_cache(data_root, year=args.year, overwrite=args.overwrite)
            run_manifest.record_outputs([layout.dtec_dir])
        elif args.step == "omni":
            asm.build_omni_cache(data_root, year=args.year, overwrite=args.overwrite)
            run_manifest.record_outputs([layout.omni_aligned_dir])
        elif args.step == "windows":
            out_dir = Path(args.out_dir) if args.out_dir else layout.training_windows
            asm.build_windowed_dataset(
                data_root=data_root,
                out_dir=out_dir,
                train_end_year=args.train_end_year,
                val_end_year=args.val_end_year,
                overwrite=args.overwrite,
                chunk_size=args.chunk_size,
                cadence_seconds=args.cadence_seconds,
            )
            run_manifest.record_outputs([out_dir])

    elif args.command == "verify-gates":
        from .verify import run
        run(data_root, Path(args.staging_root))


if __name__ == "__main__":
    main()
