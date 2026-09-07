from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .atomic import atomic_write
from .models import DownloadedEpisode, ShowJob

MANIFEST_FIELDS = [
    "Network/distributor",
    "Show name",
    "Hosts",
    "Assigned operator",
    "Playlist title",
    "Playlist URL",
    "Playlist ID",
    "Playlist position",
    "Video ID",
    "Episode title",
    "Description",
    "Webpage URL",
    "Original URL",
    "Channel",
    "Channel ID",
    "Channel URL",
    "Uploader",
    "Uploader ID",
    "Upload date",
    "Release date",
    "Timestamp",
    "Release timestamp",
    "Duration in seconds",
    "Duration formatted",
    "Availability",
    "Live status",
    "View count",
    "Like count",
    "Comment count",
    "Categories",
    "Tags",
    "Language",
    "Age limit",
    "Resolution",
    "Width",
    "Height",
    "Frame rate",
    "Video codec",
    "Audio codec",
    "Audio sample rate",
    "Audio channels",
    "Video bitrate",
    "Audio bitrate",
    "Total bitrate",
    "File extension",
    "Final filename",
    "Relative media path",
    "Relative thumbnail path",
    "Relative JSON path",
    "Relative description path",
    "File size",
    "Download status",
    "Error message",
    "Downloaded timestamp",
    "Tool version",
]


def flatten_episode(
    job: ShowJob,
    playlist_id: str,
    playlist_url: str,
    downloaded: DownloadedEpisode,
    show_root: Path,
) -> dict[str, object]:
    ep = downloaded.episode
    meta = ep.metadata
    media_path = downloaded.media_path
    return {
        "Network/distributor": job.network,
        "Show name": job.show_name,
        "Hosts": job.hosts,
        "Assigned operator": job.operator,
        "Playlist title": meta.get("playlist_title") or meta.get("playlist") or "",
        "Playlist URL": playlist_url,
        "Playlist ID": playlist_id,
        "Playlist position": ep.playlist_position or meta.get("playlist_index") or "",
        "Video ID": ep.id,
        "Episode title": ep.title,
        "Description": meta.get("description") or "",
        "Webpage URL": meta.get("webpage_url") or ep.webpage_url,
        "Original URL": meta.get("original_url") or meta.get("url") or ep.webpage_url,
        "Channel": meta.get("channel") or "",
        "Channel ID": meta.get("channel_id") or "",
        "Channel URL": meta.get("channel_url") or "",
        "Uploader": meta.get("uploader") or "",
        "Uploader ID": meta.get("uploader_id") or "",
        "Upload date": ep.upload_date,
        "Release date": ep.release_date,
        "Timestamp": ep.timestamp or "",
        "Release timestamp": ep.release_timestamp or "",
        "Duration in seconds": ep.duration or "",
        "Duration formatted": format_duration(ep.duration),
        "Availability": ep.availability,
        "Live status": ep.live_status,
        "View count": meta.get("view_count") or "",
        "Like count": meta.get("like_count") or "",
        "Comment count": meta.get("comment_count") or "",
        "Categories": serialize_list(meta.get("categories")),
        "Tags": serialize_list(meta.get("tags")),
        "Language": meta.get("language") or "",
        "Age limit": meta.get("age_limit") or "",
        "Resolution": meta.get("resolution") or "",
        "Width": meta.get("width") or "",
        "Height": meta.get("height") or "",
        "Frame rate": meta.get("fps") or "",
        "Video codec": meta.get("vcodec") or "",
        "Audio codec": meta.get("acodec") or "",
        "Audio sample rate": meta.get("asr") or "",
        "Audio channels": meta.get("audio_channels") or "",
        "Video bitrate": meta.get("vbr") or "",
        "Audio bitrate": meta.get("abr") or "",
        "Total bitrate": meta.get("tbr") or "",
        "File extension": meta.get("ext") or (media_path.suffix.lstrip(".") if media_path else ""),
        "Final filename": media_path.name if media_path else "",
        "Relative media path": relative(media_path, show_root),
        "Relative thumbnail path": relative(downloaded.thumbnail_path, show_root),
        "Relative JSON path": relative(downloaded.json_path, show_root),
        "Relative description path": relative(downloaded.description_path, show_root),
        "File size": _file_size(media_path),
        "Download status": downloaded.status,
        "Error message": downloaded.error_message,
        "Downloaded timestamp": downloaded.downloaded_timestamp,
        "Tool version": __version__,
    }


def write_manifest(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    def writer(handle: TextIO) -> None:
        csv_writer = csv.DictWriter(
            handle, fieldnames=MANIFEST_FIELDS, extrasaction="ignore", quoting=csv.QUOTE_MINIMAL
        )
        csv_writer.writeheader()
        for row in rows:
            csv_writer.writerow({field: row.get(field, "") for field in MANIFEST_FIELDS})

    atomic_write(path, writer)


def consolidate_manifests(output_root: Path) -> Path:
    rows_by_id: dict[str, dict[str, str]] = {}
    search_roots = [output_root / "02_Ready_for_QC", output_root / "03_Ready_for_Ingest", output_root / "04_Ingested"]
    for root in search_roots:
        if not root.exists():
            continue
        for manifest in sorted(root.rglob("*.csv")):
            if manifest.name == "master_manifest.csv":
                continue
            with manifest.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    video_id = row.get("Video ID", "")
                    if video_id and video_id not in rows_by_id:
                        rows_by_id[video_id] = row
    rows = sorted(
        rows_by_id.values(),
        key=lambda row: (
            row.get("Network/distributor", "").casefold(),
            row.get("Show name", "").casefold(),
            _reverse_date(row.get("Upload date", "")),
            row.get("Video ID", ""),
        ),
    )
    destination = output_root / "manifests" / "master_manifest.csv"
    write_manifest(destination, rows)
    return destination


def write_json(path: Path, data: Any) -> None:
    from .atomic import atomic_write_text

    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def serialize_list(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps([str(value)], ensure_ascii=False)


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def relative(path: Path | None, root: Path) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _file_size(path: Path | None) -> int | str:
    return path.stat().st_size if path and path.exists() else ""


def _reverse_date(value: str) -> str:
    return "".join(chr(255 - ord(ch)) for ch in value)
