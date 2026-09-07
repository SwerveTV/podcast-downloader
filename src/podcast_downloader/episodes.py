from __future__ import annotations

from .config import AppConfig
from .models import EpisodeCandidate, EpisodeSelection

REJECTED_LIVE_STATUSES = {"is_live", "is_upcoming", "post_live"}
UNAVAILABLE_MARKERS = {"private", "premium_only", "subscriber_only", "needs_auth", "unlisted", "unavailable", "deleted"}


def is_short_like(episode: EpisodeCandidate) -> bool:
    title = episode.title.casefold()
    url = episode.webpage_url.casefold()
    return "/shorts/" in url or "#shorts" in title or " shorts" in title


def rejection_reason(episode: EpisodeCandidate, config: AppConfig) -> str | None:
    if not episode.id:
        return "missing video id"
    availability = episode.availability.casefold()
    if availability in UNAVAILABLE_MARKERS:
        return f"unavailable: {episode.availability}"
    if config.exclude_livestreams and episode.live_status in REJECTED_LIVE_STATUSES:
        return f"livestream status: {episode.live_status}"
    if config.exclude_shorts and is_short_like(episode):
        return "shorts-like video"
    if (
        config.minimum_duration_seconds
        and episode.duration is not None
        and episode.duration < config.minimum_duration_seconds
    ):
        return f"duration {episode.duration}s below minimum {config.minimum_duration_seconds}s"
    title = episode.title.casefold()
    clip_words = ("clip", "trailer", "preview", "teaser")
    if any(word in title for word in clip_words) and episode.duration is not None and episode.duration < 1800:
        return "short clip/trailer-like title"
    return None


def chronological_key(episode: EpisodeCandidate) -> tuple[int, str]:
    stamp = episode.release_timestamp or episode.timestamp or 0
    if stamp:
        return stamp, episode.id
    date = episode.release_date or episode.upload_date
    compact = date.replace("-", "")
    return (int(compact) if compact.isdigit() else 0), episode.id


def select_newest_eligible(episodes: list[EpisodeCandidate], config: AppConfig) -> EpisodeSelection:
    accepted: list[EpisodeCandidate] = []
    rejected: list[tuple[EpisodeCandidate, str]] = []
    for episode in episodes:
        reason = rejection_reason(episode, config)
        if reason:
            rejected.append((episode, reason))
        else:
            accepted.append(episode)
    accepted.sort(key=chronological_key, reverse=True)
    return EpisodeSelection(selected=accepted[: config.episode_count], rejected=rejected)
