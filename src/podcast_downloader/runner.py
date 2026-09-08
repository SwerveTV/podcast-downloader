from __future__ import annotations

import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict, cast

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
    completed_show = find_completed_show(config.output_root, job)
    if completed_show is not None:
        progress.show_status(f"already completed at {completed_show}")
        progress.finish_show(completed=True)
        result["completed"] = True
        result["output_locations"] = [str(completed_show)]
        return result
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
        cache_path = show_root / "metadata-cache" / "playlist-candidates.json"
        progress.show_status(
            f"preparing detailed metadata for {len(entries[: config.playlist_scan_depth])} candidate(s)"
        )
        candidates = _fetch_candidates(client, entries, playlist.playlist_id, cache_path, config, progress)
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


def _fetch_candidates(
    client: YtDlpClient,
    entries: list[dict[str, object]],
    playlist_id: str,
    cache_path: Path,
    config: AppConfig,
    progress: ProgressReporter,
) -> list[EpisodeCandidate]:
    scan_entries = entries[: config.playlist_scan_depth]
    candidate_map = load_cached_candidate_map(cache_path, playlist_id, entries, config) or {}
    missing = [
        (position, entry)
        for position, entry in enumerate(scan_entries, start=1)
        if candidate_request_key(entry) not in candidate_map
    ]
    cached_count = len(scan_entries) - len(missing)
    if cached_count:
        progress.show_status(f"using {cached_count} cached candidate metadata record(s)")
    if missing:
        workers = metadata_worker_count(config.metadata_workers, len(missing))
        progress.show_status(f"fetching {len(missing)} candidate metadata record(s) with {workers} worker(s)")
        if workers == 1:
            for position, entry in missing:
                key, candidate = fetch_candidate_for_entry(client, position, entry)
                candidate_map[key] = candidate
                write_candidate_cache(
                    cache_path,
                    playlist_id,
                    entries,
                    ordered_cached_candidates(candidate_map, entries, config.playlist_scan_depth),
                    config,
                )
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(fetch_candidate_for_entry, client, position, entry) for position, entry in missing
                ]
                for completed, future in enumerate(as_completed(futures), start=1):
                    key, candidate = future.result()
                    candidate_map[key] = candidate
                    if completed == len(futures) or completed % workers == 0:
                        progress.show_status(f"metadata fetched {completed}/{len(futures)}")
                    write_candidate_cache(
                        cache_path,
                        playlist_id,
                        entries,
                        ordered_cached_candidates(candidate_map, entries, config.playlist_scan_depth),
                        config,
                    )
    return ordered_cached_candidates(candidate_map, entries, config.playlist_scan_depth)


def fetch_candidate_for_entry(
    client: YtDlpClient, position: int, entry: dict[str, object]
) -> tuple[str, EpisodeCandidate]:
    key = candidate_request_key(entry)
    if not key:
        key = f"position:{position}"
    video_id = str(entry.get("id") or "")
    title = str(entry.get("title") or video_id or "Unavailable video")
    url = str(
        entry.get("url")
        or entry.get("webpage_url")
        or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
    )
    if not url:
        return key, EpisodeCandidate(
            id=video_id,
            title=title,
            webpage_url="",
            availability="unavailable",
            playlist_position=position,
            metadata={"metadata_error": "missing candidate URL"},
        )
    try:
        metadata = client.fetch_episode_metadata(url)
    except DownloadError as exc:
        availability = classify_metadata_lookup_failure(str(exc))
        if availability is None:
            raise
        return key, EpisodeCandidate(
            id=video_id,
            title=title,
            webpage_url=url,
            availability=availability,
            playlist_position=position,
            metadata={"metadata_error": str(exc)},
        )
    return key, client.candidate_from_metadata(metadata, playlist_position=position)


def metadata_worker_count(configured_workers: int, missing_count: int) -> int:
    return max(1, min(configured_workers, missing_count))


def ordered_cached_candidates(
    candidate_map: dict[str, EpisodeCandidate], entries: list[dict[str, object]], depth: int
) -> list[EpisodeCandidate]:
    candidates: list[EpisodeCandidate] = []
    for entry in entries[:depth]:
        key = candidate_request_key(entry)
        if key in candidate_map:
            candidates.append(candidate_map[key])
    return candidates


def candidate_request_key(entry: dict[str, object]) -> str:
    video_id = str(entry.get("id") or "")
    url = str(entry.get("url") or entry.get("webpage_url") or "")
    return video_id or url


def load_cached_candidate_map(
    cache_path: Path,
    playlist_id: str,
    entries: list[dict[str, object]],
    config: AppConfig,
) -> dict[str, EpisodeCandidate] | None:
    cached = load_cached_candidates(cache_path, playlist_id, entries, config)
    if cached is None:
        return None
    candidate_map: dict[str, EpisodeCandidate] = {}
    for candidate in cached:
        if candidate.playlist_position is None:
            continue
        entry_index = candidate.playlist_position - 1
        if entry_index < 0 or entry_index >= min(len(entries), config.playlist_scan_depth):
            continue
        key = candidate_request_key(entries[entry_index])
        if key:
            candidate_map[key] = candidate
    return candidate_map


def load_cached_candidates(
    cache_path: Path,
    playlist_id: str,
    entries: list[dict[str, object]],
    config: AppConfig,
) -> list[EpisodeCandidate] | None:
    if not config.metadata_cache_enabled or config.refresh_metadata or not cache_path.exists():
        return None
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(cache, dict):
        return None
    if cache.get("playlist_id") != playlist_id:
        return None
    if cache.get("scan_depth") != config.playlist_scan_depth:
        return None
    if cache.get("entry_signature") != entry_signature(entries, config.playlist_scan_depth):
        return None
    created_at = str(cache.get("created_at") or "")
    if is_cache_expired(created_at, config.metadata_cache_ttl_seconds):
        return None
    raw_candidates = cache.get("candidates")
    if not isinstance(raw_candidates, list):
        return None
    candidates: list[EpisodeCandidate] = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            return None
        candidates.append(candidate_from_cache_item(item))
    return candidates


def write_candidate_cache(
    cache_path: Path,
    playlist_id: str,
    entries: list[dict[str, object]],
    candidates: list[EpisodeCandidate],
    config: AppConfig,
) -> None:
    if not config.metadata_cache_enabled:
        return
    payload = {
        "version": 1,
        "created_at": now_iso(),
        "playlist_id": playlist_id,
        "scan_depth": config.playlist_scan_depth,
        "entry_signature": entry_signature(entries, config.playlist_scan_depth),
        "candidates": [asdict(candidate) for candidate in candidates],
    }
    atomic_write_text(cache_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def entry_signature(entries: list[dict[str, object]], depth: int) -> list[str]:
    signature: list[str] = []
    for entry in entries[:depth]:
        video_id = str(entry.get("id") or "")
        url = str(entry.get("url") or entry.get("webpage_url") or "")
        signature.append(video_id or url)
    return signature


def is_cache_expired(created_at: str, ttl_seconds: int) -> bool:
    if ttl_seconds <= 0:
        return False
    try:
        timestamp = datetime.fromisoformat(created_at).astimezone(UTC)
    except ValueError:
        return True
    return (datetime.now(UTC) - timestamp).total_seconds() > ttl_seconds


def candidate_from_cache_item(item: dict[str, object]) -> EpisodeCandidate:
    return EpisodeCandidate(
        id=str(item.get("id") or ""),
        title=str(item.get("title") or ""),
        webpage_url=str(item.get("webpage_url") or ""),
        upload_date=str(item.get("upload_date") or ""),
        timestamp=_optional_int(item.get("timestamp")),
        release_timestamp=_optional_int(item.get("release_timestamp")),
        release_date=str(item.get("release_date") or ""),
        duration=_optional_int(item.get("duration")),
        live_status=str(item.get("live_status") or ""),
        availability=str(item.get("availability") or ""),
        playlist_position=_optional_int(item.get("playlist_position")),
        metadata=cast(dict[str, Any], item.get("metadata")) if isinstance(item.get("metadata"), dict) else {},
    )


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value) if isinstance(value, int | float | str) else None
    except ValueError:
        return None


def classify_metadata_lookup_failure(message: str) -> str | None:
    lower = message.casefold()
    if "premieres in" in lower or ("premiere" in lower and "upcoming" in lower):
        return "is_upcoming"
    if "private video" in lower:
        return "private"
    if "video unavailable" in lower or "this video is unavailable" in lower:
        return "unavailable"
    if "has been removed" in lower or "deleted video" in lower:
        return "deleted"
    if "members-only" in lower or "members only" in lower:
        return "subscriber_only"
    if "sign in" in lower and any(marker in lower for marker in ("confirm", "age", "bot", "not a bot")):
        return "needs_auth"
    return None


def find_completed_show(output_root: Path, job: ShowJob) -> Path | None:
    for stage in ("02_Ready_for_QC", "03_Ready_for_Ingest", "04_Ingested"):
        candidate = (
            output_root
            / stage
            / safe_component(job.network, "Unknown Network")
            / safe_component(job.show_name, "Unknown Show")
        )
        if candidate.exists():
            return candidate
    return None


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
