from __future__ import annotations

import json
import os
import socket
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .exceptions import LockedShowError


@dataclass(frozen=True)
class LockInfo:
    operator: str
    hostname: str
    pid: int
    timestamp: str


class ShowLock:
    def __init__(self, path: Path, operator: str, timeout_seconds: int, clear_stale: bool = False):
        self.path = path
        self.operator = operator
        self.timeout_seconds = timeout_seconds
        self.clear_stale = clear_stale
        self.info = LockInfo(
            operator=operator,
            hostname=socket.gethostname(),
            pid=os.getpid(),
            timestamp=datetime.now(UTC).isoformat(),
        )
        self._owned = False

    def acquire(self) -> ShowLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.path, flags, 0o644)
        except FileExistsError as exc:
            if self.is_stale():
                if not self.clear_stale:
                    raise LockedShowError(
                        f"Stale lock exists at {self.path}; rerun with --clear-stale-locks to replace it."
                    ) from None
                self.path.unlink()
                return self.acquire()
            existing = self.read_existing()
            raise LockedShowError(
                f"Show is locked by {existing.get('operator', 'unknown')} on "
                f"{existing.get('hostname', 'unknown')} pid {existing.get('pid', 'unknown')}."
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(self.info), handle, indent=2)
            handle.write("\n")
        self._owned = True
        return self

    def read_existing(self) -> dict[str, object]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return cast(dict[str, object], data) if isinstance(data, dict) else {}

    def is_stale(self) -> bool:
        data = self.read_existing()
        timestamp = str(data.get("timestamp", ""))
        try:
            lock_time = datetime.fromisoformat(timestamp)
        except ValueError:
            return True
        age = datetime.now(UTC) - lock_time.astimezone(UTC)
        return age.total_seconds() > self.timeout_seconds

    def release(self) -> None:
        if not self._owned:
            return
        data = self.read_existing()
        if data.get("pid") == self.info.pid and data.get("hostname") == self.info.hostname:
            with suppress(FileNotFoundError):
                self.path.unlink()
        self._owned = False

    def __enter__(self) -> ShowLock:
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()
