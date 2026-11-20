import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from Commands.music import (
    cleanup_source,
    is_supported_youtube_url,
    playable_entry,
    respond,
)


class MusicUrlTests(unittest.TestCase):
    def test_accepts_standard_and_short_youtube_hosts(self):
        for url in (
            "https://youtube.com/watch?v=abc",
            "https://www.youtube.com/watch?v=abc",
            "https://music.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
        ):
            with self.subTest(url=url):
                self.assertTrue(is_supported_youtube_url(url))

    def test_rejects_lookalike_hosts_and_non_http_schemes(self):
        for url in (
            "https://youtube.com.example.test/watch?v=abc",
            "https://notyoutube.com/watch?v=abc",
            "javascript://youtube.com/watch?v=abc",
            "youtube.com/watch?v=abc",
            "",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_supported_youtube_url(url))


class PlayableEntryTests(unittest.TestCase):
    def test_returns_direct_playable_entry(self):
        info = {"url": "https://media.example/audio", "title": "Song"}
        self.assertIs(playable_entry(info), info)

    def test_unwraps_playlist_and_skips_empty_entries(self):
        playable = {"url": "https://media.example/audio"}
        result = playable_entry(
            {"entries": [None, {"entries": [{"title": "missing"}, playable]}]}
        )
        self.assertIs(result, playable)

    def test_rejects_missing_stream_information(self):
        for value in (None, {}, {"entries": []}, {"entries": [None, {}]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                playable_entry(value)


class CleanupTests(unittest.TestCase):
    def test_cleanup_calls_audio_source_cleanup_once(self):
        source = SimpleNamespace(cleanup=Mock())
        cleanup_source(source)
        source.cleanup.assert_called_once_with()

    def test_cleanup_accepts_an_object_without_cleanup(self):
        cleanup_source(object())


class InteractionResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_respond_uses_initial_response_when_available(self):
        interaction = SimpleNamespace(
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await respond(interaction, "hello", ephemeral=True)
        interaction.response.send_message.assert_awaited_once_with(
            "hello", ephemeral=True
        )
        interaction.followup.send.assert_not_awaited()

    async def test_respond_uses_followup_after_defer(self):
        interaction = SimpleNamespace(
            response=SimpleNamespace(is_done=lambda: True, send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await respond(interaction, "hello", ephemeral=True)
        interaction.followup.send.assert_awaited_once_with("hello", ephemeral=True)
        interaction.response.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
