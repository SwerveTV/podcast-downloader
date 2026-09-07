from __future__ import annotations

import pytest

from podcast_downloader.atomic import atomic_write_text


def test_atomic_write_replaces_content(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("old")

    atomic_write_text(path, "new")

    assert path.read_text() == "new"


def test_atomic_write_preserves_existing_on_failure(monkeypatch, tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("old")

    def fail_replace(src, dst):
        raise RuntimeError("boom")

    monkeypatch.setattr("podcast_downloader.atomic.os.replace", fail_replace)
    with pytest.raises(RuntimeError):
        atomic_write_text(path, "new")

    assert path.read_text() == "old"
