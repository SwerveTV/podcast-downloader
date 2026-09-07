from __future__ import annotations


class PodcastDownloaderError(Exception):
    """Base exception for actionable CLI errors."""


class InvalidSpreadsheetError(PodcastDownloaderError):
    """The input sheet cannot be parsed or does not contain usable columns."""


class InvalidPlaylistError(PodcastDownloaderError):
    """A row contains an invalid or unsupported playlist URL."""


class LockedShowError(PodcastDownloaderError):
    """The show directory is locked by another active process."""


class DependencyError(PodcastDownloaderError):
    """An external executable dependency is missing or unusable."""


class DownloadError(PodcastDownloaderError):
    """A yt-dlp subprocess failed."""
