from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from podcast_downloader.exceptions import LockedShowError
from podcast_downloader.locks import ShowLock


def test_lock_creation_and_release(tmp_path):
    path = tmp_path / ".download.lock"

    with ShowLock(path, "Trey", 3600):
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["operator"] == "Trey"

    assert not path.exists()


def test_active_lock_refuses_second_owner(tmp_path):
    path = tmp_path / ".download.lock"
    first = ShowLock(path, "Trey", 3600).acquire()
    try:
        with pytest.raises(LockedShowError, match="locked by"):
            ShowLock(path, "Other", 3600).acquire()
    finally:
        first.release()


def test_stale_lock_requires_explicit_clear(tmp_path):
    path = tmp_path / ".download.lock"
    path.write_text(
        json.dumps(
            {
                "operator": "Old",
                "hostname": "h",
                "pid": 1,
                "timestamp": (datetime.now(UTC) - timedelta(hours=8)).isoformat(),
            }
        )
    )

    with pytest.raises(LockedShowError, match="Stale lock"):
        ShowLock(path, "Trey", 1, clear_stale=False).acquire()

    lock = ShowLock(path, "Trey", 1, clear_stale=True).acquire()
    lock.release()
    assert not path.exists()
