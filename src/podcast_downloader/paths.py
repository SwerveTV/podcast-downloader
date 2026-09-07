from __future__ import annotations

import re
import unicodedata
from pathlib import Path

RESERVED_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE = re.compile(r"\s+")


def safe_component(value: str, fallback: str = "Untitled", max_length: int = 120) -> str:
    normalized = unicodedata.normalize("NFC", value or "").strip()
    normalized = RESERVED_CHARS.sub("_", normalized)
    normalized = WHITESPACE.sub(" ", normalized).strip(" .")
    if not normalized:
        normalized = fallback
    if len(normalized) <= max_length:
        return normalized
    keep = max_length - 1
    return normalized[:keep].rstrip(" .") or fallback


def operator_component(operator: str) -> str:
    return safe_component(operator, fallback="Unassigned", max_length=80).replace(" ", "_")


def episode_stem(upload_date: str, title: str, video_id: str, max_length: int) -> str:
    date = upload_date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", upload_date) else "undated"
    suffix = f" [{video_id}]"
    prefix = f"{date} - "
    budget = max(20, max_length - len(prefix) - len(suffix))
    return f"{prefix}{safe_component(title, 'Episode', budget)}{suffix}"


def ensure_layout(output_root: Path) -> None:
    for relative in (
        "01_Downloading",
        "02_Ready_for_QC",
        "03_Ready_for_Ingest",
        "04_Ingested",
        "archives",
        "logs",
        "manifests",
    ):
        (output_root / relative).mkdir(parents=True, exist_ok=True)
