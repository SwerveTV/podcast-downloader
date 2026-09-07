from __future__ import annotations

import csv

from podcast_downloader.manifest import consolidate_manifests, flatten_episode, write_manifest
from podcast_downloader.models import DownloadedEpisode, EpisodeCandidate, ShowJob


def test_metadata_flattening_csv_quoting_and_unicode(tmp_path):
    show_root = tmp_path / "show"
    media = show_root / "media" / "épisode.mp4"
    media.parent.mkdir(parents=True)
    media.write_text("media")
    episode = EpisodeCandidate(
        id="vid1",
        title="Café, Episode",
        webpage_url="https://youtu.be/vid1",
        upload_date="2026-01-01",
        duration=3661,
        metadata={"description": "hello, csv", "categories": ["News", "Talk"], "tags": ["one", "two"]},
    )
    job = ShowJob(2, "Net", "Show", hosts="Host", playlist_url="https://www.youtube.com/playlist?list=PLabc1234567890")
    row = flatten_episode(
        job, "PLabc1234567890", job.playlist_url, DownloadedEpisode(episode, "downloaded", media_path=media), show_root
    )
    manifest = show_root / "Show.csv"

    write_manifest(manifest, [row])

    parsed = list(csv.DictReader(manifest.open(encoding="utf-8", newline="")))
    assert parsed[0]["Episode title"] == "Café, Episode"
    assert parsed[0]["Duration formatted"] == "01:01:01"
    assert parsed[0]["Categories"] == '["News", "Talk"]'
    assert parsed[0]["Relative media path"] == "media/épisode.mp4"


def test_consolidation_deduplicates_and_sorts(tmp_path):
    root = tmp_path / "Podcast_Ingest"
    first = root / "02_Ready_for_QC" / "B" / "Show B" / "Show B.csv"
    second = root / "02_Ready_for_QC" / "A" / "Show A" / "Show A.csv"
    rows = [
        {"Network/distributor": "B", "Show name": "Show B", "Upload date": "2026-01-01", "Video ID": "b"},
        {"Network/distributor": "A", "Show name": "Show A", "Upload date": "2026-02-01", "Video ID": "a"},
    ]
    write_manifest(first, [rows[0]])
    write_manifest(second, [rows[1], rows[1]])

    master = consolidate_manifests(root)

    parsed = list(csv.DictReader(master.open(encoding="utf-8", newline="")))
    assert [row["Video ID"] for row in parsed] == ["a", "b"]
