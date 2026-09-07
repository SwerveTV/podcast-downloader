from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from .atomic import atomic_write_text
from .config import AppConfig
from .episodes import select_newest_eligible
from .exceptions import DownloadError
from .locks import ShowLock
from .manifest import flatten_episode, write_json, write_manifest
from .models import DownloadedEpisode, EpisodeCandidate, RunSummary, ShowJob
from .paths import ensure_layout, episode_stem, operator_component, safe_component
from .progress import ProgressReporter, create_progress_reporter
from .spreadsheet import read_spreadsheet
from .validation import filter_jobs_for_operator, parse_playlist_ref, validate_jobs
from .ytdlp import YtDlpClient, check_dependencies


class ShowProcessResult(TypedDict):
    completed: bool
    episodes_discovered: int
    episodes_selected: int
    episodes_downloaded: int
    episodes_skipped_archived: int
    episodes_excluded: int
    episodes_failed: int
    output_locations: list[str]


def list_jobs(input_path: str, operator: str | None) -> list[ShowJob]:
    jobs, _, _ = read_spreadsheet(input_path)
    return filter_jobs_for_operator(jobs, operator)


def run_downloads(input_path: str, operator: str, config: AppConfig, dry_run: bool = False) -> RunSummary:
    if config.output_root is None:
        raise ValueError("--output or output_root config is required for run.")
    output_root = config.output_root
    ensure_layout(output_root)
    jobs, ignored_columns, _ = read_spreadsheet(input_path)
    validation = validate_jobs(jobs, ignored_columns, config.episode_count)
    if validation.has_errors:
        raise ValueError("Input validation failed; run `podcast-download validate` for details.")
    selected_jobs = filter_jobs_for_operator(jobs, operator)
    started = time.monotonic()
    summary = RunSummary(started_at=now_iso())
    client: YtDlpClient | None = None
    progress = create_progress_reporter(config.progress)
    progress.start_run(len(selected_jobs))
    if not dry_run:
        check_dependencies(config)
        client = YtDlpClient(config)

    try:
        for index, job in enumerate(selected_jobs, start=1):
            summary.shows_attempted += 1
            progress.start_show(job.show_name, index, len(selected_jobs))
            show_result = process_show(job, operator, config, client, progress, dry_run=dry_run)
            summary.episodes_discovered += show_result["episodes_discovered"]
            summary.episodes_selected += show_result["episodes_selected"]
            summary.episodes_downloaded += show_result["episodes_downloaded"]
            summary.episodes_skipped_archived += show_result["episodes_skipped_archived"]
            summary.episodes_excluded += show_result["episodes_excluded"]
            summary.episodes_failed += show_result["episodes_failed"]
            summary.output_locations.extend(show_result["output_locations"])
            if show_result["completed"]:
                summary.shows_completed += 1
    finally:
        progress.finish_run()

    summary.finished_at = now_iso()
    summary.runtime_seconds = round(time.monotonic() - started, 3)
    report_path = output_root / "logs" / f"run-report-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    atomic_write_text(report_path, json.dumps(asdict(summary), indent=2) + "\n")
    summary_path = output_root / "logs" / f"run-summary-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.txt"
    atomic_write_text(summary_path, format_summary(summary))
    return summary


def process_show(
    job: ShowJob,
    operator: str,
    config: AppConfig,
    client: YtDlpClient | None,
    progress: ProgressReporter,
    dry_run: bool = False,
) -> ShowProcessResult:
    assert config.output_root is not None
    playlist = parse_playlist_ref(job.playlist_url)
    operator_root = config.output_root / "01_Downloading" / operator_component(operator)
    show_root = (
        operator_root / safe_component(job.network, "Unknown Network") / safe_component(job.show_name, "Unknown Show")
    )
    lock_path = show_root / ".download.lock"
    result: ShowProcessResult = {
        "completed": False,
        "episodes_discovered": 0,
        "episodes_selected": 0,
        "episodes_downloaded": 0,
        "episodes_skipped_archived": 0,
        "episodes_excluded": 0,
        "episodes_failed": 0,
        "output_locations": [str(show_root)],
    }
    if dry_run:
        progress.show_status("dry-run: validation only, no media downloads")
        progress.finish_show(completed=False)
        return result

    if client is None:
        raise ValueError("client is required when dry_run is false")
    with ShowLock(lock_path, operator, config.lock_timeout_seconds, config.clear_stale_locks):
        _create_show_dirs(show_root)
        archive_path = show_root / "download-archive.txt"
        progress.show_status("discovering playlist entries")
        entries = client.discover_playlist(job.playlist_url)
        result["episodes_discovered"] = len(entries)
        progress.show_status(
            f"fetching detailed metadata for {len(entries[: config.playlist_scan_depth])} candidate(s)"
        )
        candidates = _fetch_candidates(client, entries, config.playlist_scan_depth)
        selection = select_newest_eligible(candidates, config)
        result["episodes_selected"] = len(selection.selected)
        result["episodes_excluded"] = len(selection.rejected)
        progress.show_status(f"selected {len(selection.selected)} episode(s), excluded {len(selection.rejected)}")
        write_json(
            show_root / "rejected-episodes.json",
            [
                {
                    "video_id": episode.id,
                    "title": episode.title,
                    "webpage_url": episode.webpage_url,
                    "reason": reason,
                }
                for episode, reason in selection.rejected
            ],
        )
        rows: list[dict[str, object]] = []
        failures = 0
        skipped = 0
        downloaded_count = 0
        archived_ids = read_archive_ids(archive_path)
        for episode_index, episode in enumerate(selection.selected, start=1):
            progress.start_episode(episode.title, episode_index, len(selection.selected))
            if episode.id in archived_ids:
                skipped += 1
                downloaded = DownloadedEpisode(
                    episode=episode, status="skipped_archived", downloaded_timestamp=now_iso()
                )
                progress.finish_episode("skipped: already archived")
            else:
                try:
                    downloaded = _download_one(client, episode, show_root, archive_path, config, progress)
                    downloaded_count += 1
                    progress.finish_episode("downloaded")
                except (DownloadError, OSError) as exc:
                    failures += 1
                    downloaded = DownloadedEpisode(
                        episode=episode, status="failed", error_message=str(exc), downloaded_timestamp=now_iso()
                    )
                    progress.finish_episode("failed")
            rows.append(flatten_episode(job, playlist.playlist_id, job.playlist_url, downloaded, show_root))
        result["episodes_skipped_archived"] = skipped
        result["episodes_downloaded"] = downloaded_count
        result["episodes_failed"] = failures
        write_manifest(show_root / f"{safe_component(job.show_name)}.csv", rows)
        if failures == 0 and len(selection.selected) > 0:
            destination = (
                config.output_root
                / "02_Ready_for_QC"
                / safe_component(job.network, "Unknown Network")
                / safe_component(job.show_name)
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise FileExistsError(f"QC destination already exists: {destination}")
            os.replace(show_root, destination)
            result["completed"] = True
            result["output_locations"] = [str(destination)]
    progress.finish_show(completed=result["completed"])
    return result


def dry_run_plan(input_path: str, operator: str | None, config: AppConfig) -> list[dict[str, str]]:
    jobs, _, _ = read_spreadsheet(input_path)
    selected = filter_jobs_for_operator(jobs, operator)
    return [
        {
            "network": job.network,
            "show": job.show_name,
            "operator": job.operator,
            "playlist": job.playlist_url,
            "planned_episodes": str(config.episode_count),
        }
        for job in selected
    ]


def read_archive_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "youtube":
            ids.add(parts[1])
        elif parts:
            ids.add(parts[-1])
    return ids


def _fetch_candidates(client: YtDlpClient, entries: list[dict[str, object]], depth: int) -> list[EpisodeCandidate]:
    candidates: list[EpisodeCandidate] = []
    for position, entry in enumerate(entries[:depth], start=1):
        video_id = str(entry.get("id") or "")
        url = str(
            entry.get("url")
            or entry.get("webpage_url")
            or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
        )
        if not url:
            continue
        metadata = client.fetch_episode_metadata(url)
        candidates.append(client.candidate_from_metadata(metadata, playlist_position=position))
    return candidates


def _download_one(
    client: YtDlpClient,
    episode: EpisodeCandidate,
    show_root: Path,
    archive_path: Path,
    config: AppConfig,
    progress: ProgressReporter,
) -> DownloadedEpisode:
    _check_free_space(show_root)
    stem = episode_stem(episode.upload_date, episode.title, episode.id, config.filename_max_length)
    output_template = show_root / "media" / f"{stem}.%(ext)s"
    client.download_episode(episode, output_template, archive_path, progress.update_download)
    media = _find_first(show_root / "media", stem, [".mp4", ".mkv", ".webm", ".mov"])
    thumbnail = _find_first(show_root / "media", stem, [".jpg", ".jpeg", ".png", ".webp"])
    json_path = _find_first(show_root / "media", stem, [".info.json"])
    description = _find_first(show_root / "media", stem, [".description"])
    final_thumb = _move_associated(thumbnail, show_root / "thumbnails") if thumbnail else None
    final_json = _move_associated(json_path, show_root / "metadata-json") if json_path else None
    final_description = _move_associated(description, show_root / "descriptions") if description else None
    if final_json is None:
        final_json = show_root / "metadata-json" / f"{stem}.info.json"
        write_json(final_json, episode.metadata)
    if final_description is None and episode.metadata.get("description"):
        final_description = show_root / "descriptions" / f"{stem}.description.txt"
        atomic_write_text(final_description, str(episode.metadata.get("description")))
    return DownloadedEpisode(
        episode=episode,
        status="downloaded",
        media_path=media,
        thumbnail_path=final_thumb,
        json_path=final_json,
        description_path=final_description,
        downloaded_timestamp=now_iso(),
    )


def _create_show_dirs(show_root: Path) -> None:
    for name in ("media", "thumbnails", "metadata-json", "descriptions"):
        (show_root / name).mkdir(parents=True, exist_ok=True)


def _find_first(root: Path, stem: str, suffixes: list[str]) -> Path | None:
    for suffix in suffixes:
        candidate = root / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    matches = sorted(root.glob(f"{stem}*"))
    for match in matches:
        if any(str(match).endswith(suffix) for suffix in suffixes):
            return match
    return None


def _move_associated(path: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        return target
    shutil.move(str(path), str(target))
    return target


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def format_summary(summary: RunSummary) -> str:
    lines = [
        f"Started: {summary.started_at}",
        f"Finished: {summary.finished_at}",
        f"Runtime seconds: {summary.runtime_seconds}",
        f"Shows attempted: {summary.shows_attempted}",
        f"Shows completed: {summary.shows_completed}",
        f"Episodes discovered: {summary.episodes_discovered}",
        f"Episodes selected: {summary.episodes_selected}",
        f"Episodes downloaded: {summary.episodes_downloaded}",
        f"Episodes skipped as already archived: {summary.episodes_skipped_archived}",
        f"Episodes excluded by filters: {summary.episodes_excluded}",
        f"Episodes failed: {summary.episodes_failed}",
        "Output locations:",
        *[f"- {location}" for location in summary.output_locations],
    ]
    return "\n".join(lines) + "\n"


def _check_free_space(path: Path, minimum_free_bytes: int = 1_000_000_000) -> None:
    path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path).free
    if free < minimum_free_bytes:
        raise OSError(f"Insufficient disk space at {path}: {free} bytes free.")
