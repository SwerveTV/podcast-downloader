from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from podcast_downloader.config import AppConfig
from podcast_downloader.exceptions import DependencyError, DownloadError
from podcast_downloader.models import EpisodeCandidate
from podcast_downloader.runner import dry_run_plan, read_archive_ids
from podcast_downloader.ytdlp import YtDlpClient, require_executable


def test_archive_id_parsing(tmp_path):
    archive = tmp_path / "download-archive.txt"
    archive.write_text("youtube abc123\nyoutube xyz789\n")

    assert read_archive_ids(archive) == {"abc123", "xyz789"}


def test_dry_run_plan_filters_without_downloading(fixtures_dir):
    plan = dry_run_plan(str(fixtures_dir / "shows.csv"), "Trey", AppConfig())

    assert [item["show"] for item in plan] == ["Alpha Show", "Beta Show"]


def test_missing_executable_has_actionable_error():
    with pytest.raises(DependencyError, match="Missing yt-dlp"):
        require_executable("/definitely/not/yt-dlp", "yt-dlp")


def test_failed_subprocess_is_reported(monkeypatch):
    client = YtDlpClient.__new__(YtDlpClient)
    client.config = AppConfig()
    client.executable = "yt-dlp"

    def fake_run(args, check, capture_output, text):
        return subprocess.CompletedProcess(args, 1, "", "network failure")

    monkeypatch.setattr("podcast_downloader.ytdlp.subprocess.run", fake_run)
    with pytest.raises(DownloadError, match="network failure"):
        client.fetch_episode_metadata("https://youtu.be/id")


def test_keyboard_interrupt_propagates(monkeypatch):
    client = YtDlpClient.__new__(YtDlpClient)
    client.config = AppConfig()
    client.executable = "yt-dlp"

    def fake_run(args, check, capture_output, text):
        raise KeyboardInterrupt

    monkeypatch.setattr("podcast_downloader.ytdlp.subprocess.run", fake_run)
    with pytest.raises(KeyboardInterrupt):
        client.download_episode(
            EpisodeCandidate("id", "title", "url"),
            Path("/tmp/out.%(ext)s"),
            Path("/tmp/archive.txt"),
        )
