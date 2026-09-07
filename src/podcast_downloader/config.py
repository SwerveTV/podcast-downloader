from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from .progress import ProgressMode


@dataclass(frozen=True)
class AppConfig:
    input: str | None = None
    operator: str | None = None
    episode_count: int = 10
    playlist_scan_depth: int = 50
    minimum_duration_seconds: int = 1200
    exclude_shorts: bool = True
    exclude_livestreams: bool = True
    yt_dlp_path: str = "yt-dlp"
    format_selector: str = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
    output_root: Path | None = None
    lock_timeout_seconds: int = 6 * 60 * 60
    retry_count: int = 3
    filename_max_length: int = 180
    convert_thumbnail_to_jpg: bool = False
    cookie_browser: str | None = None
    cookie_file: Path | None = None
    sleep_interval_seconds: float = 0.0
    clear_stale_locks: bool = False
    progress: ProgressMode = "auto"


def load_config(path: Path | None) -> AppConfig:
    if path is None:
        return AppConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("Configuration YAML must contain a mapping at the top level.")
    known = {f.name for f in fields(AppConfig)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"Unknown configuration keys: {', '.join(unknown)}")
    coerced: dict[str, Any] = {}
    path_fields = {"output_root", "cookie_file"}
    for key, value in data.items():
        value = _strip_wrapping_quotes(value)
        coerced[key] = Path(str(value)).expanduser() if key in path_fields and value else value
    return AppConfig(**coerced)


def merge_config(base: AppConfig, overrides: dict[str, Any]) -> AppConfig:
    values: dict[str, Any] = {f.name: getattr(base, f.name) for f in fields(AppConfig)}
    for key, value in overrides.items():
        if value is not None:
            values[key] = value
    return AppConfig(**values)


def _strip_wrapping_quotes(value: object) -> object:
    if not isinstance(value, str) or len(value) < 2:
        return value
    if value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
