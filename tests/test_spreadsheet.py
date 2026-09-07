from __future__ import annotations

from podcast_downloader.spreadsheet import read_spreadsheet
from podcast_downloader.validation import filter_jobs_for_operator, validate_jobs


def test_spreadsheet_parses_headers_blanks_section_rows_and_ignored_columns(fixtures_dir):
    jobs, ignored, headers = read_spreadsheet(fixtures_dir / "shows.csv")

    assert "Unnamed: 9" not in ignored
    assert "Show Name" in headers
    assert [job.row_number for job in jobs if job.is_section_header] == [2, 7]
    assert len([job for job in jobs if job.is_valid_job]) == 3
    assert jobs[2].network == "Network Alpha"


def test_operator_filter_is_case_and_whitespace_tolerant(fixtures_dir):
    jobs, _, _ = read_spreadsheet(fixtures_dir / "shows.csv")

    filtered = filter_jobs_for_operator(jobs, "trey")

    assert [job.show_name for job in filtered] == ["Alpha Show", "Beta Show"]


def test_validation_reports_expected_counts_and_warnings(fixtures_dir):
    jobs, ignored, _ = read_spreadsheet(fixtures_dir / "shows.csv")

    report = validate_jobs(jobs, ignored, episode_count=10)

    assert report.total_show_rows == 4
    assert report.valid_playlist_jobs == 3
    assert report.missing_playlist_links == [6]
    assert report.section_header_rows == [2, 7]
    assert report.estimated_max_episode_count == 30
