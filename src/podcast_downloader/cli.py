from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .config import load_config, merge_config
from .exceptions import PodcastDownloaderError
from .manifest import consolidate_manifests
from .runner import dry_run_plan, list_jobs, run_downloads
from .spreadsheet import read_spreadsheet
from .validation import ValidationReport, validate_jobs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="podcast-download")
    parser.add_argument("--config", type=Path, help="Optional YAML configuration file.")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate spreadsheet input.")
    validate.add_argument("--input", help="Spreadsheet path or public Google Sheets URL.")

    list_cmd = sub.add_parser("list", help="List valid jobs, optionally filtered by operator.")
    list_cmd.add_argument("--input", help="Spreadsheet path or public Google Sheets URL.")
    list_cmd.add_argument("--operator")

    run = sub.add_parser("run", help="Download assigned shows.")
    run.add_argument("--input", help="Spreadsheet path or public Google Sheets URL.")
    run.add_argument("--operator")
    run.add_argument("--output", type=Path)
    run.add_argument("--dry-run", action="store_true")
    add_config_flags(run)

    consolidate = sub.add_parser("consolidate", help="Create master manifest from completed show manifests.")
    consolidate.add_argument("--output", type=Path)
    return parser


def add_config_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--episode-count", type=int)
    parser.add_argument("--playlist-scan-depth", type=int)
    parser.add_argument("--minimum-duration-seconds", type=int)
    parser.add_argument("--no-minimum-duration", dest="minimum_duration_seconds", action="store_const", const=0)
    parser.add_argument("--include-shorts", dest="exclude_shorts", action="store_false", default=None)
    parser.add_argument("--include-livestreams", dest="exclude_livestreams", action="store_false", default=None)
    parser.add_argument("--yt-dlp-path")
    parser.add_argument("--format-selector")
    parser.add_argument("--lock-timeout-seconds", type=int)
    parser.add_argument("--retry-count", type=int)
    parser.add_argument("--filename-max-length", type=int)
    parser.add_argument("--convert-thumbnail-to-jpg", action="store_true", default=None)
    parser.add_argument("--cookie-browser")
    parser.add_argument("--cookie-file", type=Path)
    parser.add_argument("--sleep-interval-seconds", type=float)
    parser.add_argument("--clear-stale-locks", dest="clear_stale_locks", action="store_true", default=None)
    parser.add_argument(
        "--progress",
        choices=("auto", "rich", "plain", "none"),
        help="Live progress display mode. Defaults to auto.",
    )
    parser.add_argument("--metadata-cache-ttl-seconds", type=int)
    parser.add_argument("--no-metadata-cache", dest="metadata_cache_enabled", action="store_false", default=None)
    parser.add_argument(
        "--refresh-metadata",
        action="store_true",
        default=None,
        help="Ignore cached playlist candidate metadata.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "validate":
            input_source = require_string(args.input or config.input, "--input or input in config")
            jobs, ignored, _ = read_spreadsheet(input_source)
            report = validate_jobs(jobs, ignored, config.episode_count)
            print_validation(report)
            return 2 if report.has_errors else 0
        if args.command == "list":
            input_source = require_string(args.input or config.input, "--input or input in config")
            operator = args.operator if args.operator is not None else config.operator
            for job in list_jobs(input_source, operator):
                print(f"{job.operator or '-'}\t{job.network}\t{job.show_name}\t{job.playlist_url}")
            return 0
        if args.command == "run":
            overrides = vars(args).copy()
            for key in ("command", "config", "output", "dry_run"):
                overrides.pop(key, None)
            overrides["output_root"] = args.output
            config = merge_config(config, overrides)
            input_source = require_string(config.input, "--input or input in config")
            operator = require_string(config.operator, "--operator or operator in config")
            if args.dry_run:
                jobs, ignored, _ = read_spreadsheet(input_source)
                report = validate_jobs(jobs, ignored, config.episode_count)
                if report.has_errors:
                    print_validation(report)
                    return 2
                plan = dry_run_plan(input_source, operator, config)
                print(json.dumps(plan, indent=2, ensure_ascii=False))
                return 0
            summary = run_downloads(input_source, operator, config, dry_run=False)
            print(json.dumps(asdict(summary), indent=2))
            return 1 if summary.episodes_failed else 0
        if args.command == "consolidate":
            output_root = args.output or config.output_root
            if output_root is None:
                raise ValueError("--output or output_root in config is required.")
            path = consolidate_manifests(output_root)
            print(f"Master manifest written: {path}")
            return 0
    except (PodcastDownloaderError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


def print_validation(report: ValidationReport) -> None:
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))


def require_string(value: str | None, label: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{label} is required.")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
