from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from podcast_downloader.config import AppConfig
from podcast_downloader.exceptions import DependencyError, DownloadError, InvalidPlaylistError
from podcast_downloader.models import EpisodeCandidate
from podcast_downloader.runner import (
    _fetch_candidates,
    classify_metadata_lookup_failure,
    dry_run_plan,
    entry_signature,
    find_completed_show,
    is_cache_expired,
    load_cached_candidates,
    process_show,
    read_archive_ids,
    write_candidate_cache,
)
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


def test_ytdlp_ignores_user_config_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = YtDlpClient.__new__(YtDlpClient)
    client.config = AppConfig()
    client.executable = "yt-dlp"
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str], check: bool, capture_output: bool, text: bool) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, '{"entries":[]}', "")

    monkeypatch.setattr("podcast_downloader.ytdlp.subprocess.run", fake_run)
    client.discover_playlist("https://www.youtube.com/playlist?list=PLcache1234567890")

    assert "--ignore-config" in captured["args"]


def test_ytdlp_user_config_can_be_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    client = YtDlpClient.__new__(YtDlpClient)
    client.config = AppConfig(ignore_ytdlp_config=False)
    client.executable = "yt-dlp"
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str], check: bool, capture_output: bool, text: bool) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, '{"entries":[]}', "")

    monkeypatch.setattr("podcast_downloader.ytdlp.subprocess.run", fake_run)
    client.discover_playlist("https://www.youtube.com/playlist?list=PLcache1234567890")

    assert "--ignore-config" not in captured["args"]


def test_youtube_tab_http_400_is_reported_as_invalid_playlist(monkeypatch: pytest.MonkeyPatch) -> None:
    client = YtDlpClient.__new__(YtDlpClient)
    client.config = AppConfig()
    client.executable = "yt-dlp"

    def fake_run(args: list[str], check: bool, capture_output: bool, text: bool) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            1,
            "",
            "ERROR: [youtube:tab] PL1234567890abcdefghi: Unable to download API page: HTTP Error 400: Bad Request",
        )

    monkeypatch.setattr("podcast_downloader.ytdlp.subprocess.run", fake_run)
    with pytest.raises(InvalidPlaylistError, match="sample placeholder"):
        client.discover_playlist("https://www.youtube.com/playlist?list=PL1234567890abcdefghi")


def test_private_video_metadata_error_is_skippable() -> None:
    assert classify_metadata_lookup_failure("ERROR: [youtube] aMdTvV1cjXs: Private video") == "private"


def test_network_metadata_error_is_not_skippable() -> None:
    assert classify_metadata_lookup_failure("ERROR: Unable to download webpage: timed out") is None


def test_candidate_metadata_cache_round_trip(tmp_path: Path) -> None:
    entries: list[dict[str, object]] = [{"id": "new"}, {"id": "old"}]
    candidates = [
        EpisodeCandidate(
            id="new",
            title="New Episode",
            webpage_url="https://youtu.be/new",
            upload_date="2026-01-02",
            duration=3600,
            metadata={"description": "cached"},
        )
    ]
    config = AppConfig(playlist_scan_depth=2)
    cache_path = tmp_path / "metadata-cache" / "playlist-candidates.json"

    write_candidate_cache(cache_path, "PLcache1234567890", entries, candidates, config)
    cached = load_cached_candidates(cache_path, "PLcache1234567890", entries, config)

    assert cached is not None
    assert cached[0].id == "new"
    assert cached[0].metadata["description"] == "cached"


def test_candidate_metadata_cache_misses_on_signature_change(tmp_path: Path) -> None:
    entries: list[dict[str, object]] = [{"id": "new"}]
    config = AppConfig(playlist_scan_depth=1)
    cache_path = tmp_path / "cache.json"
    write_candidate_cache(cache_path, "PLcache1234567890", entries, [], config)

    cached = load_cached_candidates(cache_path, "PLcache1234567890", [{"id": "different"}], config)

    assert cached is None


def test_candidate_metadata_cache_refresh_flag_bypasses_cache(tmp_path: Path) -> None:
    entries: list[dict[str, object]] = [{"id": "new"}]
    cache_path = tmp_path / "cache.json"
    write_candidate_cache(cache_path, "PLcache1234567890", entries, [], AppConfig(playlist_scan_depth=1))

    cached = load_cached_candidates(
        cache_path,
        "PLcache1234567890",
        entries,
        AppConfig(playlist_scan_depth=1, refresh_metadata=True),
    )

    assert cached is None


def test_cache_expiration() -> None:
    assert not is_cache_expired("2026-01-01T00:00:00+00:00", 0)
    assert is_cache_expired("not a timestamp", 86400)


def test_entry_signature_prefers_video_ids() -> None:
    assert entry_signature([{"id": "abc", "url": "fallback"}], 1) == ["abc"]


def test_fetch_candidates_uses_partial_cache_for_missing_only(tmp_path: Path) -> None:
    from podcast_downloader.progress import NoOpProgress

    class FakeClient:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def fetch_episode_metadata(self, url_or_id: str) -> dict[str, object]:
            self.urls.append(url_or_id)
            return {
                "id": "missing",
                "title": "Missing Episode",
                "webpage_url": url_or_id,
                "upload_date": "20260102",
                "duration": 3600,
            }

        def candidate_from_metadata(
            self, metadata: dict[str, object], playlist_position: int | None = None
        ) -> EpisodeCandidate:
            return EpisodeCandidate(
                id=str(metadata["id"]),
                title=str(metadata["title"]),
                webpage_url=str(metadata["webpage_url"]),
                upload_date="2026-01-02",
                duration=3600,
                playlist_position=playlist_position,
                metadata=metadata,
            )

    entries: list[dict[str, object]] = [{"id": "cached"}, {"id": "missing"}]
    cache_path = tmp_path / "metadata-cache" / "playlist-candidates.json"
    config = AppConfig(playlist_scan_depth=2, metadata_workers=1)
    write_candidate_cache(
        cache_path,
        "PLcache1234567890",
        entries,
        [
            EpisodeCandidate(
                id="cached",
                title="Cached Episode",
                webpage_url="https://youtu.be/cached",
                upload_date="2026-01-01",
                duration=3600,
                playlist_position=1,
            )
        ],
        config,
    )
    client = FakeClient()

    candidates = _fetch_candidates(  # type: ignore[arg-type]
        client, entries, "PLcache1234567890", cache_path, config, NoOpProgress()
    )

    assert [candidate.id for candidate in candidates] == ["cached", "missing"]
    assert client.urls == ["https://www.youtube.com/watch?v=missing"]


def test_fetch_candidates_uses_configured_worker_limit(tmp_path: Path) -> None:
    from podcast_downloader.progress import NoOpProgress

    class FakeClient:
        def fetch_episode_metadata(self, url_or_id: str) -> dict[str, object]:
            video_id = url_or_id.rsplit("=", 1)[-1]
            return {"id": video_id, "title": video_id, "webpage_url": url_or_id, "upload_date": "20260101"}

        def candidate_from_metadata(
            self, metadata: dict[str, object], playlist_position: int | None = None
        ) -> EpisodeCandidate:
            return EpisodeCandidate(
                id=str(metadata["id"]),
                title=str(metadata["title"]),
                webpage_url=str(metadata["webpage_url"]),
                upload_date="2026-01-01",
                duration=3600,
                playlist_position=playlist_position,
                metadata=metadata,
            )

    entries: list[dict[str, object]] = [{"id": "a"}, {"id": "b"}, {"id": "c"}]

    candidates = _fetch_candidates(  # type: ignore[arg-type]
        FakeClient(),
        entries,
        "PLcache1234567890",
        tmp_path / "cache.json",
        AppConfig(playlist_scan_depth=3, metadata_workers=2),
        NoOpProgress(),
    )

    assert sorted(candidate.id for candidate in candidates) == ["a", "b", "c"]


def test_find_completed_show_checks_downstream_stages(tmp_path: Path) -> None:
    from podcast_downloader.models import ShowJob

    job = ShowJob(
        row_number=2,
        network="Network",
        show_name="Show",
        playlist_url="https://www.youtube.com/playlist?list=PLcache1234567890",
    )
    completed = tmp_path / "03_Ready_for_Ingest" / "Network" / "Show"
    completed.mkdir(parents=True)

    assert find_completed_show(tmp_path, job) == completed


def test_process_show_skips_already_completed_show(tmp_path: Path) -> None:
    from podcast_downloader.models import ShowJob
    from podcast_downloader.progress import NoOpProgress

    job = ShowJob(
        row_number=2,
        network="Network",
        show_name="Show",
        playlist_url="https://www.youtube.com/playlist?list=PLcache1234567890",
    )
    completed = tmp_path / "02_Ready_for_QC" / "Network" / "Show"
    completed.mkdir(parents=True)

    result = process_show(
        job,
        "Trey",
        AppConfig(output_root=tmp_path),
        client=None,
        progress=NoOpProgress(),
        dry_run=True,
    )

    assert result["completed"] is True
    assert result["output_locations"] == [str(completed)]


def test_process_show_uses_valid_metadata_cache(tmp_path: Path) -> None:
    from podcast_downloader.models import ShowJob
    from podcast_downloader.progress import NoOpProgress

    class FakeClient:
        def __init__(self) -> None:
            self.metadata_calls = 0

        def discover_playlist(self, playlist_url: str) -> list[dict[str, object]]:
            return [{"id": "cached"}]

        def fetch_episode_metadata(self, url_or_id: str) -> dict[str, object]:
            self.metadata_calls += 1
            return {}

        def download_episode(self, *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, "", "")

    job = ShowJob(
        row_number=2,
        network="Network",
        show_name="Show",
        playlist_url="https://www.youtube.com/playlist?list=PLcache1234567890",
    )
    show_root = tmp_path / "01_Downloading" / "Trey" / "Network" / "Show"
    cache_path = show_root / "metadata-cache" / "playlist-candidates.json"
    write_candidate_cache(
        cache_path,
        "PLcache1234567890",
        [{"id": "cached"}],
        [
            EpisodeCandidate(
                id="cached",
                title="Cached Episode",
                webpage_url="https://youtu.be/cached",
                upload_date="2026-01-01",
                duration=3600,
                playlist_position=1,
            )
        ],
        AppConfig(output_root=tmp_path, episode_count=1, playlist_scan_depth=1),
    )
    client = FakeClient()

    result = process_show(
        job,
        "Trey",
        AppConfig(output_root=tmp_path, episode_count=1, playlist_scan_depth=1),
        client=client,  # type: ignore[arg-type]
        progress=NoOpProgress(),
    )

    assert result["completed"] is True
    assert client.metadata_calls == 0


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

    captured: dict[str, Any] = {}

    def fake_popen(*args: Any, **kwargs: Any) -> FakeProcess:
        captured["args"] = args[0]
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
    assert "--ignore-config" in captured["args"]
    assert "--continue" in captured["args"]
    assert "--continuedl" not in captured["args"]


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
