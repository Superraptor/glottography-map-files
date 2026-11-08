#!/usr/bin/env python3
"""
Glottography to Wikimedia Commons Pipeline CLI.
==============================================

Automates downloading Glottography language boundary datasets from Zenodo,
transforming them into Wikimedia Commons Map Data (.map) files per language,
and uploading them to Wikimedia Commons under 'Category:Map data by language'.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from glottography_to_commons.config import Config
from glottography_to_commons.exporter import ManualUploadExporter
from glottography_to_commons.pipeline import Pipeline
from glottography_to_commons.state_tracker import StateTracker


def setup_logging(verbose: bool = False) -> None:
    """Configures structured console logging."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%Y-%m-%d %H:%M:%S")


def cmd_download(args, config: Config) -> None:
    """Execute download step."""
    pipeline = Pipeline(config)
    pipeline.download_datasets(limit=args.limit)


def cmd_process(args, config: Config) -> None:
    """Execute parsing and .map generation step."""
    pipeline = Pipeline(config)
    pipeline.process_datasets(limit=args.limit)


def cmd_upload(args, config: Config) -> None:
    """Execute upload to Wikimedia Commons step."""
    pipeline = Pipeline(config)
    pipeline.upload_maps(limit=args.limit)


def cmd_run(args, config: Config) -> None:
    """Execute complete end-to-end pipeline."""
    pipeline = Pipeline(config)
    pipeline.run_all(limit=args.limit)


def cmd_export(args, config: Config) -> None:
    """Generate manual upload package, provenance descriptions, and HTML portal."""
    exporter = ManualUploadExporter(config)
    items = exporter.export()
    print("\n" + "=" * 65)
    print(" Manual Upload Package Successfully Prepared! ")
    print("=" * 65)
    print(f" Output Directory : {config.export_dir}")
    print(f" Total Maps Ready : {len(items)}")
    print(f" HTML Portal      : {config.export_dir / 'index.html'}")
    print(f" Markdown Manifest: {config.export_dir / 'manifest.md'}")
    print(f" CSV Spreadsheet  : {config.export_dir / 'manifest.csv'}")
    print("=" * 65 + "\n")
    print("Open 'index.html' in your browser to browse maps, copy JSON in 1 click,")
    print("and open direct Wikimedia Commons edit pages with pre-filled titles!\n")


def cmd_status(args, config: Config) -> None:
    """Display current database state and progress statistics."""
    tracker = StateTracker(config.state_db_path)
    stats = tracker.get_statistics()

    print("\n" + "=" * 55)
    print(" Glottography to Commons Pipeline - Progress Status ")
    print("=" * 55)
    print(f" SQLite Database:      {config.state_db_path}")
    print(f" Zenodo Community:     {config.zenodo_community}")
    print(f" Target Commons Cat:   Category:{config.category_name}")
    print(f" Parent Commons Cat:   Category:{config.parent_category}")
    print("-" * 55)
    print(f" Total Datasets:       {stats['total_datasets']}")
    print(f" Downloaded Datasets:  {stats['downloaded_datasets']}")
    print(f" Total Generated Maps: {stats['total_maps']}")
    print(f" Uploaded to Commons:  {stats['uploaded_maps']}")
    print(f" Pending Upload:       {stats['pending_maps']}")
    print(f" Unique Languages:     {stats['unique_languages']}")
    print("=" * 55 + "\n")

    datasets = tracker.get_all_datasets()
    if datasets:
        print("Discovered Datasets:")
        for d in datasets:
            print(f" - [{d['status']:<10}] {d['record_id']}: {d['title'][:55]} ({d['doi']})")
        print()


def cmd_preview(args, config: Config) -> None:
    """Inspect a generated .map file."""
    maps_dir = config.maps_output_dir
    matches = list(maps_dir.glob(f"*{args.query}*.map"))
    if not matches:
        print(f"No generated .map file found matching query: {args.query} in {maps_dir}")
        return

    sample = matches[0]
    print(f"\n--- Previewing: {sample.name} ---")
    with open(sample, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download Glottography datasets and upload as .map files to Wikimedia Commons."
    )
    # Common arguments template
    common_args = argparse.ArgumentParser(add_help=False)
    common_args.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")
    common_args.add_argument("--dry-run", action="store_true", help="Simulate uploads without modifying Wikimedia Commons")
    common_args.add_argument("--delay", type=float, default=None, help="Delay in seconds between upload requests (default 2.0s)")
    common_args.add_argument("--limit", type=int, default=None, help="Limit number of items to process")
    common_args.add_argument("--env-file", type=str, default=".env", help="Path to .env configuration file")

    # Top-level arguments
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")
    parser.add_argument("--dry-run", action="store_true", help="Simulate uploads without modifying Wikimedia Commons")
    parser.add_argument("--delay", type=float, default=None, help="Delay in seconds between upload requests (default 2.0s)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of items to process")
    parser.add_argument("--env-file", type=str, default=".env", help="Path to .env configuration file")

    subparsers = parser.add_subparsers(dest="command", help="Pipeline subcommands")

    # Subcommand: download
    p_dl = subparsers.add_parser("download", parents=[common_args], help="Download datasets from Zenodo Glottography community")
    p_dl.set_defaults(func=cmd_download)

    # Subcommand: process
    p_proc = subparsers.add_parser("process", parents=[common_args], help="Extract and build .map files per language")
    p_proc.set_defaults(func=cmd_process)

    # Subcommand: upload
    p_up = subparsers.add_parser("upload", parents=[common_args], help="Upload generated .map files to Wikimedia Commons")
    p_up.set_defaults(func=cmd_upload)

    # Subcommand: export (manual upload package)
    p_exp = subparsers.add_parser("export", parents=[common_args], help="Generate manual upload package, descriptions, and HTML portal")
    p_exp.set_defaults(func=cmd_export)

    # Subcommand: run
    p_run = subparsers.add_parser("run", parents=[common_args], help="Run end-to-end: download -> process -> upload")
    p_run.set_defaults(func=cmd_run)

    # Subcommand: status
    p_stat = subparsers.add_parser("status", parents=[common_args], help="Show pipeline database status and statistics")
    p_stat.set_defaults(func=cmd_status)

    # Subcommand: preview
    p_prev = subparsers.add_parser("preview", parents=[common_args], help="Preview a generated .map file")
    p_prev.add_argument("query", type=str, help="Language name, glottocode, or dataset name to preview")
    p_prev.set_defaults(func=cmd_preview)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    setup_logging(args.verbose)

    # Build config
    overrides = {}
    if args.dry_run:
        overrides["dry_run"] = True
    if args.delay is not None:
        overrides["request_delay"] = args.delay

    config = Config.from_env(env_path=Path(args.env_file), **overrides)

    try:
        args.func(args, config)
        return 0
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        return 130
    except Exception as e:
        logging.getLogger(__name__).error("Fatal error: %s", e, exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())

