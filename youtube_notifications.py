"""Reliable YouTube channel-source normalization and recent-upload discovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit


CHANNEL_TABS = ("videos", "shorts", "streams")
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
}


class YouTubeSourceError(ValueError):
    """Raised when a configured YouTube source is not a channel reference."""


class YouTubeFetchError(RuntimeError):
    """Raised when no YouTube channel tab can be fetched in a polling cycle."""


class _QuietLogger:
    """Capture yt-dlp failures without printing expected missing-tab errors."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def debug(self, _message: str) -> None:
        pass

    def info(self, _message: str) -> None:
        pass

    def warning(self, _message: str) -> None:
        pass

    def error(self, message: str) -> None:
        cleaned = " ".join(str(message).split())
        self.errors.append(cleaned[:300])


def normalize_youtube_source(source: object) -> str:
    """Return a canonical channel URL for a handle, channel ID, or channel URL."""
    value = str(source or "").strip()
    if not value:
        raise YouTubeSourceError("The YouTube channel source is blank.")

    if value.startswith(("youtube.com/", "www.youtube.com/", "m.youtube.com/")):
        value = f"https://{value}"

    if value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if host not in YOUTUBE_HOSTS:
            raise YouTubeSourceError("The YouTube source URL must use youtube.com.")

        segments = [segment for segment in parsed.path.split("/") if segment]
        if segments and segments[-1].lower() in (*CHANNEL_TABS, "featured"):
            segments.pop()
        if not segments or segments[0].lower() in {"watch", "shorts", "live"}:
            raise YouTubeSourceError(
                "Use a YouTube channel URL, @handle, or UC... channel ID—not a video URL."
            )
        path = "/" + "/".join(segments)
        return urlunsplit(("https", "www.youtube.com", path, "", ""))

    if value.startswith("UC") and len(value) == 24:
        return f"https://www.youtube.com/channel/{quote(value, safe='_-')}"

    handle = value.lstrip("@").strip()
    if not handle or any(character.isspace() for character in handle) or "/" in handle:
        raise YouTubeSourceError(
            "Use a YouTube @handle, UC... channel ID, or full channel URL."
        )
    return f"https://www.youtube.com/@{quote(handle, safe='._-')}"


def fetch_recent_youtube_content(
    source: object,
    limit: int,
    *,
    ydl_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> list[list[dict[str, str]]]:
    """Fetch recent video IDs from available channel tabs without downloading media."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("YouTube fetch limit must be a positive integer.")

    channel_url = normalize_youtube_source(source)
    logger = _QuietLogger()
    options: dict[str, Any] = {
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
        "logger": logger,
        "no_warnings": True,
        "playlistend": limit,
        "quiet": True,
        "retries": 2,
        "extractor_retries": 2,
        "skip_download": True,
        "socket_timeout": 15,
    }
    try:
        import yt_dlp
    except ImportError as exc:
        raise YouTubeFetchError(
            "yt-dlp is not installed. Run setup.ps1 to install dependencies."
        ) from exc
    factory = yt_dlp.YoutubeDL if ydl_factory is None else ydl_factory
    results: list[list[dict[str, str]]] = []
    successful_tabs = 0

    try:
        with factory(options) as ydl:
            for tab in CHANNEL_TABS:
                try:
                    information = ydl.extract_info(
                        f"{channel_url}/{tab}", download=False
                    )
                except yt_dlp.utils.DownloadError as exc:
                    logger.error(str(exc))
                    information = None

                tab_results: list[dict[str, str]] = []
                if isinstance(information, Mapping):
                    successful_tabs += 1
                    entries = information.get("entries") or []
                    for entry in entries:
                        if not isinstance(entry, Mapping):
                            continue
                        video_id = str(entry.get("id") or "").strip()
                        if video_id:
                            tab_results.append({"videoId": video_id})
                        if len(tab_results) >= limit:
                            break
                results.append(tab_results)
    except (OSError, yt_dlp.utils.DownloadError) as exc:
        raise YouTubeFetchError(f"Could not contact YouTube: {exc}") from exc

    if successful_tabs == 0:
        detail = logger.errors[-1] if logger.errors else "no channel data was returned"
        raise YouTubeFetchError(
            f"YouTube did not return an available channel tab ({detail})."
        )
    return results
