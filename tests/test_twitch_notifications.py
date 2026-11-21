import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from twitch_notifications import (
    TwitchTokenCache,
    load_notified_stream_ids,
    remember_notified_stream,
)


class TwitchTokenCacheTests(unittest.TestCase):
    def test_token_is_reused_until_refresh_window(self):
        response = Mock()
        response.json.return_value = {"access_token": "first", "expires_in": 3600}
        session = Mock()
        session.post.return_value = response
        cache = TwitchTokenCache()

        self.assertEqual(
            cache.get_access_token(
                session,
                client_id="client",
                client_secret="secret",
                now=100,
            ),
            "first",
        )
        self.assertEqual(
            cache.get_access_token(
                session,
                client_id="client",
                client_secret="secret",
                now=200,
            ),
            "first",
        )
        session.post.assert_called_once()

    def test_token_refreshes_after_expiry_margin(self):
        first = Mock()
        first.json.return_value = {"access_token": "first", "expires_in": 120}
        second = Mock()
        second.json.return_value = {"access_token": "second", "expires_in": 120}
        session = Mock()
        session.post.side_effect = [first, second]
        cache = TwitchTokenCache()

        cache.get_access_token(
            session, client_id="client", client_secret="secret", now=100
        )
        token = cache.get_access_token(
            session, client_id="client", client_secret="secret", now=161
        )

        self.assertEqual(token, "second")
        self.assertEqual(session.post.call_count, 2)

    def test_invalidated_token_is_refreshed_immediately(self):
        first = Mock()
        first.json.return_value = {"access_token": "first", "expires_in": 3600}
        second = Mock()
        second.json.return_value = {"access_token": "second", "expires_in": 3600}
        session = Mock()
        session.post.side_effect = [first, second]
        cache = TwitchTokenCache()

        cache.get_access_token(
            session, client_id="client", client_secret="secret", now=100
        )
        cache.invalidate()
        token = cache.get_access_token(
            session, client_id="client", client_secret="secret", now=101
        )

        self.assertEqual(token, "second")
        self.assertEqual(session.post.call_count, 2)


class TwitchNotificationStateTests(unittest.TestCase):
    def test_stream_ids_survive_restart_and_are_pruned(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.db"
            remember_notified_stream(database, "one", max_entries=2, observed_at=1)
            remember_notified_stream(database, "two", max_entries=2, observed_at=2)
            remember_notified_stream(database, "three", max_entries=2, observed_at=3)

            self.assertEqual(
                load_notified_stream_ids(database, limit=10), ["two", "three"]
            )

    def test_invalid_state_requests_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.db"
            with self.assertRaises(ValueError):
                remember_notified_stream(database, "")
            with self.assertRaises(ValueError):
                remember_notified_stream(database, "one", max_entries=0)


if __name__ == "__main__":
    unittest.main()
