from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Any, Literal, Protocol

ProgressMode = Literal["auto", "rich", "plain", "none"]


@dataclass(frozen=True)
class DownloadProgress:
    percent: float | None = None
    speed: str = ""
    eta: str = ""
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    raw_line: str = ""


class ProgressReporter(Protocol):
    def start_run(self, total_shows: int) -> None: ...

    def start_show(self, show_name: str, index: int, total: int) -> None: ...

    def show_status(self, message: str) -> None: ...

    def start_episode(self, title: str, index: int, total: int) -> None: ...

    def update_download(self, progress: DownloadProgress) -> None: ...

    def finish_episode(self, status: str) -> None: ...

    def finish_show(self, completed: bool) -> None: ...

    def finish_run(self) -> None: ...


class NoOpProgress:
    def start_run(self, total_shows: int) -> None:
        pass

    def start_show(self, show_name: str, index: int, total: int) -> None:
        pass

    def show_status(self, message: str) -> None:
        pass

    def start_episode(self, title: str, index: int, total: int) -> None:
        pass

    def update_download(self, progress: DownloadProgress) -> None:
        pass

    def finish_episode(self, status: str) -> None:
        pass

    def finish_show(self, completed: bool) -> None:
        pass

    def finish_run(self) -> None:
        pass


class PlainProgress(NoOpProgress):
    def __init__(self) -> None:
        self._last_percent: int | None = None

    def start_run(self, total_shows: int) -> None:
        print(f"Starting run: {total_shows} show(s)", file=sys.stderr)

    def start_show(self, show_name: str, index: int, total: int) -> None:
        print(f"[{index}/{total}] Show: {show_name}", file=sys.stderr)

    def show_status(self, message: str) -> None:
        print(f"  {message}", file=sys.stderr)

    def start_episode(self, title: str, index: int, total: int) -> None:
        self._last_percent = None
        print(f"  [{index}/{total}] Episode: {title}", file=sys.stderr)

    def update_download(self, progress: DownloadProgress) -> None:
        if progress.percent is None:
            return
        percent = int(progress.percent)
        if self._last_percent is not None and percent < self._last_percent + 5 and percent < 100:
            return
        self._last_percent = percent
        details = " ".join(
            part for part in (f"{percent}%", progress.speed, f"ETA {progress.eta}" if progress.eta else "") if part
        )
        print(f"    download {details}", file=sys.stderr)

    def finish_episode(self, status: str) -> None:
        print(f"    {status}", file=sys.stderr)

    def finish_show(self, completed: bool) -> None:
        print(f"  show {'complete' if completed else 'incomplete'}", file=sys.stderr)

    def finish_run(self) -> None:
        print("Run finished", file=sys.stderr)


class RichProgress(NoOpProgress):
    def __init__(self) -> None:
        from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn

        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            transient=False,
        )
        self._show_task: Any | None = None
        self._episode_task: Any | None = None
        self._download_task: Any | None = None

    def start_run(self, total_shows: int) -> None:
        self._progress.start()
        self._show_task = self._progress.add_task("Shows", total=total_shows)

    def start_show(self, show_name: str, index: int, total: int) -> None:
        if self._show_task is not None:
            self._progress.update(self._show_task, description=f"Shows [{index}/{total}] {show_name}")

    def show_status(self, message: str) -> None:
        self._progress.console.log(message)

    def start_episode(self, title: str, index: int, total: int) -> None:
        if self._episode_task is not None:
            self._progress.remove_task(self._episode_task)
        if self._download_task is not None:
            self._progress.remove_task(self._download_task)
        self._episode_task = self._progress.add_task(
            f"Episodes [{index}/{total}] {title}", total=total, completed=index - 1
        )
        self._download_task = self._progress.add_task("Download", total=100)

    def update_download(self, progress: DownloadProgress) -> None:
        if self._download_task is None or progress.percent is None:
            return
        description = "Download"
        if progress.speed or progress.eta:
            description = f"Download {progress.speed} ETA {progress.eta}".strip()
        self._progress.update(self._download_task, completed=min(progress.percent, 100.0), description=description)

    def finish_episode(self, status: str) -> None:
        if self._download_task is not None:
            self._progress.update(self._download_task, completed=100, description=status)
        if self._episode_task is not None:
            self._progress.advance(self._episode_task, 1)

    def finish_show(self, completed: bool) -> None:
        if self._show_task is not None:
            self._progress.advance(self._show_task, 1)
        if self._download_task is not None:
            self._progress.remove_task(self._download_task)
            self._download_task = None
        if self._episode_task is not None:
            self._progress.remove_task(self._episode_task)
            self._episode_task = None

    def finish_run(self) -> None:
        self._progress.stop()


def create_progress_reporter(mode: ProgressMode) -> ProgressReporter:
    if mode == "none":
        return NoOpProgress()
    if mode == "plain":
        return PlainProgress()
    if mode == "rich" or (mode == "auto" and sys.stderr.isatty()):
        try:
            return RichProgress()
        except ImportError:
            if mode == "rich":
                raise
    return PlainProgress()


PROGRESS_PREFIX = "PD_PROGRESS\t"
PROGRESS_TEMPLATE = (
    f"download:{PROGRESS_PREFIX}"
    "%(progress._percent_str)s\t"
    "%(progress._speed_str)s\t"
    "%(progress._eta_str)s\t"
    "%(progress.downloaded_bytes)s\t"
    "%(progress.total_bytes)s"
)


def parse_progress_line(line: str) -> DownloadProgress | None:
    if not line.startswith(PROGRESS_PREFIX):
        return None
    parts = line.rstrip("\n").split("\t")
    if len(parts) < 6:
        return DownloadProgress(raw_line=line)
    return DownloadProgress(
        percent=parse_percent(parts[1]),
        speed=parts[2].strip(),
        eta=parts[3].strip(),
        downloaded_bytes=parse_int(parts[4]),
        total_bytes=parse_int(parts[5]),
        raw_line=line,
    )


def parse_percent(value: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)", value)
    return float(match.group(1)) if match else None


def parse_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
