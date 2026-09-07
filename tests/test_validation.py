from __future__ import annotations

import pytest

from podcast_downloader.validation import parse_playlist_ref


def test_parse_playlist_ref_extracts_id():
    ref = parse_playlist_ref("https://www.youtube.com/playlist?list=PLabc1234567890_def")

    assert ref.playlist_id == "PLabc1234567890_def"


def test_parse_playlist_ref_rejects_missing_list():
    with pytest.raises(ValueError, match="missing list"):
        parse_playlist_ref("https://www.youtube.com/watch?v=abc")


def test_parse_playlist_ref_rejects_bad_id():
    with pytest.raises(ValueError, match="malformed"):
        parse_playlist_ref("https://www.youtube.com/playlist?list=bad!")
