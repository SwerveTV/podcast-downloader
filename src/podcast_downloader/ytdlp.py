from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import AppConfig
from .exceptions import DependencyError, DownloadError
from .models import EpisodeCandidate


def require_executable(path_or_name: str, label: str) -> str:
    if Path(path_or_name).is_file():
        return str(Path(path_or_name))
    resolved = shutil.which(path_or_name)
    if not resolved:
        raise DependencyError(f"Missing {label}: {path_or_name}. Install it or pass an explicit path.")
    return resolved


def check_dependencies(config: AppConfig, require_ffmpeg: bool = True) -> None:
    require_executable(config.yt_dlp_path, "yt-dlp")
    if require_ffmpeg:
        require_executable("ffmpeg", "ffmpeg")


class YtDlpClient:
    def __init__(self, config: AppConfig):
        self.config = config
        self.executable = require_executable(config.yt_dlp_path, "yt-dlp")

    def discover_playlist(self, playlist_url: str) -> list[dict[str, Any]]:
        args = [
            self.executable,
            "--flat-playlist",
            "--dump-single-json",
            "--playlist-end",
            str(self.config.playlist_scan_depth),
            "--no-warnings",
            playlist_url,
        ]
        result = self._run(args)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise DownloadError(f"Metadata parsing failure for playlist: {exc}") from exc
        entries = data.get("entries", [])
        if not isinstance(entries, list):
            raise DownloadError("Playlist metadata did not contain an entries list.")
        return [entry for entry in entries if isinstance(entry, dict)]

    def fetch_episode_metadata(self, url_or_id: str) -> dict[str, Any]:
        args = [self.executable, "--dump-single-json", "--no-playlist", "--skip-download", "--no-warnings", url_or_id]
        result = self._run(args)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise DownloadError(f"Metadata parsing failure for episode: {exc}") from exc
        if not isinstance(data, dict):
            raise DownloadError("Episode metadata was not a JSON object.")
        return data

    def candidate_from_metadata(
        self, metadata: dict[str, Any], playlist_position: int | None = None
    ) -> EpisodeCandidate:
        video_id = str(metadata.get("id") or "")
        webpage_url = str(
            metadata.get("webpage_url") or metadata.get("url") or f"https://www.youtube.com/watch?v={video_id}"
        )
        release_timestamp = _int_or_none(metadata.get("release_timestamp"))
        return EpisodeCandidate(
            id=video_id,
            title=str(metadata.get("title") or video_id or "Untitled"),
            webpage_url=webpage_url,
            upload_date=_date_string(metadata.get("upload_date")),
            timestamp=_int_or_none(metadata.get("timestamp")),
            release_timestamp=release_timestamp,
            release_date=_date_string(metadata.get("release_date")) or _date_from_timestamp(release_timestamp),
            duration=_int_or_none(metadata.get("duration")),
            live_status=str(metadata.get("live_status") or ""),
            availability=str(metadata.get("availability") or ""),
            playlist_position=playlist_position,
            metadata=metadata,
        )

    def download_episode(
        self, episode: EpisodeCandidate, output_template: Path, archive_path: Path
    ) -> subprocess.CompletedProcess[str]:
        args = [
            self.executable,
            "--format",
            self.config.format_selector,
            "--merge-output-format",
            "mp4",
            "--retries",
            str(self.config.retry_count),
            "--continuedl",
            "--no-overwrites",
            "--download-archive",
            str(archive_path),
            "--write-thumbnail",
            "--write-info-json",
            "--write-description",
            "--paths",
            str(output_template.parent),
            "--output",
            output_template.name,
        ]
        if self.config.convert_thumbnail_to_jpg:
            args.extend(["--convert-thumbnails", "jpg"])
        if self.config.cookie_file:
            args.extend(["--cookies", str(self.config.cookie_file)])
        if self.config.cookie_browser:
            args.extend(["--cookies-from-browser", self.config.cookie_browser])
        if self.config.sleep_interval_seconds:
            args.extend(["--sleep-interval", str(self.config.sleep_interval_seconds)])
        args.append(episode.webpage_url)
        return self._run(args)

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(args, check=False, capture_output=True, text=True)
        except KeyboardInterrupt:
            raise
        except FileNotFoundError as exc:
            raise DependencyError(f"Missing executable: {args[0]}") from exc
        except OSError as exc:
            raise DownloadError(f"Failed to start yt-dlp: {exc}") from exc
        if result.returncode != 0:
            stderr = _redact(result.stderr.strip())
            raise DownloadError(stderr or f"yt-dlp exited with status {result.returncode}")
        return result


def _redact(value: str) -> str:
    redacted = value
    for marker in ("--cookies", "--cookies-from-browser", "Authorization:", "Cookie:"):
        redacted = redacted.replace(marker, "[redacted]")
    return redacted


def _int_or_none(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float | str):
            return int(value)
        return None
    except (TypeError, ValueError):
        return None


def _date_string(value: object) -> str:
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    return ""


def _date_from_timestamp(value: int | None) -> str:
    if value is None:
        return ""
    from datetime import UTC, datetime

    return datetime.fromtimestamp(value, tz=UTC).strftime("%Y-%m-%d")
