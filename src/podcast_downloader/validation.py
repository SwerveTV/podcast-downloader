from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

from .models import PlaylistRef, ShowJob

PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")
KNOWN_STATUSES = {"", "todo", "ready", "assigned", "downloading", "downloaded", "qc", "ingested", "skip", "hold"}


@dataclass
class ValidationReport:
    total_show_rows: int = 0
    valid_playlist_jobs: int = 0
    missing_playlist_links: list[int] = field(default_factory=list)
    duplicate_playlist_urls: list[str] = field(default_factory=list)
    duplicate_show_names: list[str] = field(default_factory=list)
    malformed_playlist_ids: list[tuple[int, str]] = field(default_factory=list)
    suspicious_playlist_ids: list[tuple[int, str]] = field(default_factory=list)
    missing_operator_assignments: list[int] = field(default_factory=list)
    unknown_statuses: list[tuple[int, str]] = field(default_factory=list)
    section_header_rows: list[int] = field(default_factory=list)
    estimated_max_episode_count: int = 0
    ignored_columns: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.malformed_playlist_ids or self.duplicate_playlist_urls)


def normalize_operator(value: str) -> str:
    return " ".join(value.casefold().split())


def filter_jobs_for_operator(jobs: list[ShowJob], operator: str | None) -> list[ShowJob]:
    valid = [job for job in jobs if job.is_valid_job and not job.is_section_header]
    if not operator:
        return valid
    wanted = normalize_operator(operator)
    with_assignments = any(job.operator.strip() for job in jobs)
    if not with_assignments:
        return valid
    return [job for job in valid if normalize_operator(job.operator) == wanted]


def parse_playlist_ref(url: str) -> PlaylistRef:
    parsed = urllib.parse.urlparse(url.strip())
    query = urllib.parse.parse_qs(parsed.query)
    playlist_id = query.get("list", [""])[0]
    suspicious = False
    if not playlist_id and "youtube.com/playlist" in parsed.netloc + parsed.path:
        suspicious = True
    if not playlist_id:
        raise ValueError("missing list= playlist id")
    if not PLAYLIST_ID_RE.fullmatch(playlist_id):
        raise ValueError(f"malformed playlist id: {playlist_id}")
    if not (playlist_id.startswith(("PL", "UU", "OL", "RD", "PLO")) or len(playlist_id) >= 24):
        suspicious = True
    return PlaylistRef(url=url.strip(), playlist_id=playlist_id, suspicious=suspicious)


def validate_jobs(jobs: list[ShowJob], ignored_columns: list[str], episode_count: int) -> ValidationReport:
    report = ValidationReport(ignored_columns=ignored_columns)
    playlist_seen: dict[str, int] = {}
    show_seen: dict[str, int] = {}
    for job in jobs:
        if job.is_section_header:
            report.section_header_rows.append(job.row_number)
            continue
        if not (job.show_name.strip() or job.playlist_url.strip()):
            continue
        report.total_show_rows += 1
        show_key = " ".join(job.show_name.casefold().split())
        if show_key:
            if show_key in show_seen:
                report.duplicate_show_names.append(job.show_name)
            show_seen[show_key] = job.row_number
        if not job.playlist_url.strip():
            report.missing_playlist_links.append(job.row_number)
        else:
            try:
                ref = parse_playlist_ref(job.playlist_url)
            except ValueError as exc:
                report.malformed_playlist_ids.append((job.row_number, str(exc)))
            else:
                url_key = ref.playlist_id.casefold()
                if url_key in playlist_seen:
                    report.duplicate_playlist_urls.append(ref.playlist_id)
                playlist_seen[url_key] = job.row_number
                if ref.suspicious:
                    report.suspicious_playlist_ids.append((job.row_number, ref.playlist_id))
                if job.show_name.strip():
                    report.valid_playlist_jobs += 1
        if not job.operator.strip():
            report.missing_operator_assignments.append(job.row_number)
        status_key = " ".join(job.status.casefold().split())
        if status_key not in KNOWN_STATUSES:
            report.unknown_statuses.append((job.row_number, job.status))
    report.estimated_max_episode_count = report.valid_playlist_jobs * episode_count
    return report
