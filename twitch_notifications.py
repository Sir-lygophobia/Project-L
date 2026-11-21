"""Twitch authentication caching and durable notification state."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TwitchTokenCache:
    """Reuse an application token until shortly before Twitch expires it."""

    access_token: str = ""
    refresh_at: float = 0.0

    def invalidate(self) -> None:
        """Discard a token that Twitch rejected before its advertised expiry."""
        self.access_token = ""
        self.refresh_at = 0.0

    def get_access_token(
        self,
        session,
        *,
        client_id: str,
        client_secret: str,
        timeout: float = 15,
        now: float | None = None,
    ) -> str:
        current_time = time.monotonic() if now is None else now
        if self.access_token and current_time < self.refresh_at:
            return self.access_token

        response = session.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        access_token = str(payload.get("access_token", "")).strip()
        if not access_token:
            raise RuntimeError("Twitch did not return an access token.")
        try:
            expires_in = max(0, int(payload.get("expires_in", 0) or 0))
        except (TypeError, ValueError):
            expires_in = 0

        # Refresh at least one minute early. Very short or unspecified tokens
        # are used for only this request and refreshed on the next poll.
        self.access_token = access_token
        self.refresh_at = current_time + max(0, expires_in - 60)
        return access_token


def _ensure_state_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS twitch_notifications ("
        "stream_id TEXT PRIMARY KEY, notified_at INTEGER NOT NULL)"
    )


def load_notified_stream_ids(
    database_path: str | Path,
    *,
    limit: int = 5000,
) -> list[str]:
    """Load retained Twitch stream IDs from oldest to newest."""
    if limit <= 0:
        return []
    with closing(sqlite3.connect(database_path)) as connection:
        _ensure_state_table(connection)
        newest_first = connection.execute(
            "SELECT stream_id FROM twitch_notifications "
            "ORDER BY notified_at DESC, stream_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        connection.commit()
    return [row[0] for row in reversed(newest_first)]


def remember_notified_stream(
    database_path: str | Path,
    stream_id: str,
    *,
    max_entries: int = 5000,
    observed_at: int | None = None,
) -> None:
    """Persist a sent stream ID and prune old entries transactionally."""
    clean_stream_id = str(stream_id or "").strip()
    if not clean_stream_id:
        raise ValueError("stream_id is required")
    if max_entries <= 0:
        raise ValueError("max_entries must be positive")
    timestamp = time.time_ns() if observed_at is None else int(observed_at)
    with closing(sqlite3.connect(database_path)) as connection:
        _ensure_state_table(connection)
        connection.execute(
            "INSERT INTO twitch_notifications(stream_id, notified_at) VALUES(?, ?) "
            "ON CONFLICT(stream_id) DO UPDATE SET notified_at = excluded.notified_at",
            (clean_stream_id, timestamp),
        )
        connection.execute(
            "DELETE FROM twitch_notifications WHERE stream_id NOT IN ("
            "SELECT stream_id FROM twitch_notifications "
            "ORDER BY notified_at DESC, stream_id DESC LIMIT ?)",
            (max_entries,),
        )
        connection.commit()
