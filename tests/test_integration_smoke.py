from __future__ import annotations

import os
import shutil
import subprocess

import pytest


@pytest.mark.integration
def test_opt_in_playlist_metadata_smoke():
    playlist = os.environ.get("PODCAST_DOWNLOADER_SMOKE_PLAYLIST")
    if not playlist:
        pytest.skip("Set PODCAST_DOWNLOADER_SMOKE_PLAYLIST to run this opt-in smoke test.")
    yt_dlp = os.environ.get("PODCAST_DOWNLOADER_YT_DLP") or shutil.which("yt-dlp")
    if not yt_dlp:
        pytest.skip("yt-dlp is not installed.")

    result = subprocess.run(
        [yt_dlp, "--flat-playlist", "--dump-single-json", "--playlist-end", "3", playlist],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0
    assert '"entries"' in result.stdout
