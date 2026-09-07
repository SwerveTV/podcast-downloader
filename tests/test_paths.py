from __future__ import annotations

from podcast_downloader.paths import episode_stem, safe_component


def test_safe_component_preserves_unicode_and_removes_unsafe_chars():
    assert safe_component(' Café / "Talk" : Episode? ') == "Café _ _Talk_ _ Episode_"


def test_episode_stem_limits_length_and_includes_video_id():
    stem = episode_stem("2026-01-02", "A" * 500, "abc123", 80)

    assert stem.startswith("2026-01-02 - ")
    assert stem.endswith(" [abc123]")
    assert len(stem) <= 80
