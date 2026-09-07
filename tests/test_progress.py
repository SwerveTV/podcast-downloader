from __future__ import annotations

from podcast_downloader.progress import parse_progress_line


def test_parse_progress_line_extracts_ytdlp_fields() -> None:
    progress = parse_progress_line("PD_PROGRESS\t 42.5%\t1.2MiB/s\t00:10\t1024\t2048\n")

    assert progress is not None
    assert progress.percent == 42.5
    assert progress.speed == "1.2MiB/s"
    assert progress.eta == "00:10"
    assert progress.downloaded_bytes == 1024
    assert progress.total_bytes == 2048


def test_parse_progress_line_ignores_regular_output() -> None:
    assert parse_progress_line("[download] Destination: file.mp4") is None
