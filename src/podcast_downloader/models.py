from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ShowJob:
    row_number: int
    network: str
    show_name: str
    hosts: str = ""
    number_of_episodes: str = ""
    subscriber_count: str = ""
    playlist_url: str = ""
    operator: str = ""
    status: str = ""
    raw: dict[str, str] = field(default_factory=dict)
    is_section_header: bool = False

    @property
    def is_valid_job(self) -> bool:
        return bool(self.show_name.strip() and self.playlist_url.strip())


@dataclass(frozen=True)
class PlaylistRef:
    url: str
    playlist_id: str
    suspicious: bool = False


@dataclass(frozen=True)
class EpisodeCandidate:
    id: str
    title: str
    webpage_url: str
    upload_date: str = ""
    timestamp: int | None = None
    release_timestamp: int | None = None
    release_date: str = ""
    duration: int | None = None
    live_status: str = ""
    availability: str = ""
    playlist_position: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EpisodeSelection:
    selected: list[EpisodeCandidate]
    rejected: list[tuple[EpisodeCandidate, str]]


@dataclass
class DownloadedEpisode:
    episode: EpisodeCandidate
    status: str
    error_message: str = ""
    media_path: Path | None = None
    thumbnail_path: Path | None = None
    json_path: Path | None = None
    description_path: Path | None = None
    downloaded_timestamp: str = ""


@dataclass
class RunSummary:
    shows_attempted: int = 0
    shows_completed: int = 0
    episodes_discovered: int = 0
    episodes_selected: int = 0
    episodes_downloaded: int = 0
    episodes_skipped_archived: int = 0
    episodes_excluded: int = 0
    episodes_failed: int = 0
    output_locations: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    runtime_seconds: float = 0.0
