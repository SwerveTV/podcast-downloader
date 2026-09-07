from __future__ import annotations

from podcast_downloader.config import AppConfig
from podcast_downloader.episodes import rejection_reason, select_newest_eligible
from podcast_downloader.models import EpisodeCandidate


def ep(video_id: str, date: str, duration: int = 3600, title: str = "Full Episode") -> EpisodeCandidate:
    return EpisodeCandidate(
        id=video_id, title=title, webpage_url=f"https://youtu.be/{video_id}", upload_date=date, duration=duration
    )


def test_episode_filters_reject_short_livestream_and_shorts():
    config = AppConfig(minimum_duration_seconds=1200)

    assert "below minimum" in (rejection_reason(ep("short", "2026-01-01", 60), config) or "")
    assert "shorts" in (rejection_reason(ep("s", "2026-01-01", 3600, "Daily #Shorts"), config) or "")
    live = EpisodeCandidate(
        id="live", title="Live", webpage_url="x", upload_date="2026-01-01", duration=3600, live_status="is_live"
    )
    assert "livestream" in (rejection_reason(live, config) or "")


def test_selects_newest_first_and_limits_count():
    episodes = [ep("old", "2026-01-01"), ep("new", "2026-01-03"), ep("mid", "2026-01-02")]
    selected = select_newest_eligible(episodes, AppConfig(episode_count=2))

    assert [episode.id for episode in selected.selected] == ["new", "mid"]


def test_handles_fewer_than_requested():
    selected = select_newest_eligible([ep("only", "2026-01-01")], AppConfig(episode_count=10))

    assert [episode.id for episode in selected.selected] == ["only"]
