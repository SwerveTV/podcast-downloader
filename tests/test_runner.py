from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

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


def test_failed_subprocess_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    client = YtDlpClient.__new__(YtDlpClient)
    client.config = AppConfig()
    client.executable = "yt-dlp"

    def fake_run(args: list[str], check: bool, capture_output: bool, text: bool) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "network failure")

    monkeypatch.setattr("podcast_downloader.ytdlp.subprocess.run", fake_run)
    with pytest.raises(DownloadError, match="network failure"):
        client.fetch_episode_metadata("https://youtu.be/id")


def test_streaming_download_reports_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    client = YtDlpClient.__new__(YtDlpClient)
    client.config = AppConfig()
    client.executable = "yt-dlp"
    updates = []

    class FakeProcess:
        stdout = iter(["PD_PROGRESS\t50.0%\t2MiB/s\t00:05\t100\t200\n", "[download] complete\n"])
        returncode = 0

        def wait(self, timeout: int | None = None) -> int:
            return 0

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    def fake_popen(*args: Any, **kwargs: Any) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr("podcast_downloader.ytdlp.subprocess.Popen", fake_popen)

    client.download_episode(
        EpisodeCandidate("id", "title", "url"),
        Path("/tmp/out.%(ext)s"),
        Path("/tmp/archive.txt"),
        updates.append,
    )

    assert len(updates) == 1
    assert updates[0].percent == 50.0


def test_keyboard_interrupt_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    client = YtDlpClient.__new__(YtDlpClient)
    client.config = AppConfig()
    client.executable = "yt-dlp"

    class FakeProcess:
        returncode = None

        @property
        def stdout(self):
            raise KeyboardInterrupt

        def wait(self, timeout: int | None = None) -> int:
            return 0

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    def fake_popen(*args: Any, **kwargs: Any) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr("podcast_downloader.ytdlp.subprocess.Popen", fake_popen)
    with pytest.raises(KeyboardInterrupt):
        client.download_episode(
            EpisodeCandidate("id", "title", "url"),
            Path("/tmp/out.%(ext)s"),
            Path("/tmp/archive.txt"),
        )
