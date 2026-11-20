import unittest

from youtube_notifications import (
    YouTubeFetchError,
    YouTubeSourceError,
    fetch_recent_youtube_content,
    normalize_youtube_source,
)


class FakeYoutubeDL:
    def __init__(self, options, responses):
        self.options = options
        self.responses = responses
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def extract_info(self, url, *, download):
        self.calls.append((url, download))
        response = self.responses[url.rsplit("/", maxsplit=1)[-1]]
        if response is None:
            self.options["logger"].error(f"No {url.rsplit('/', maxsplit=1)[-1]} tab")
        return response


class YoutubeNotificationTests(unittest.TestCase):
    def test_normalizes_handle_channel_id_and_channel_url(self):
        self.assertEqual(
            normalize_youtube_source("@Project_L"),
            "https://www.youtube.com/@Project_L",
        )
        self.assertEqual(
            normalize_youtube_source("Project_L"),
            "https://www.youtube.com/@Project_L",
        )
        channel_id = "UC" + "a" * 22
        self.assertEqual(
            normalize_youtube_source(channel_id),
            f"https://www.youtube.com/channel/{channel_id}",
        )
        self.assertEqual(
            normalize_youtube_source(
                "https://m.youtube.com/@Project_L/videos?view=0"
            ),
            "https://www.youtube.com/@Project_L",
        )

    def test_rejects_blank_foreign_and_video_urls(self):
        for source in (
            "",
            "https://example.com/@Project_L",
            "https://www.youtube.com/watch?v=abcdefghijk",
            "https://www.youtube.com/shorts/abcdefghijk",
        ):
            with self.subTest(source=source), self.assertRaises(YouTubeSourceError):
                normalize_youtube_source(source)

    def test_fetches_available_tabs_and_silently_skips_a_missing_tab(self):
        responses = {
            "videos": {
                "entries": [
                    {"id": "video-one"},
                    {"id": "video-two"},
                    {"id": "video-three"},
                ]
            },
            "shorts": None,
            "streams": {"entries": [{"id": "stream-one"}]},
        }
        instances = []

        def factory(options):
            instance = FakeYoutubeDL(options, responses)
            instances.append(instance)
            return instance

        results = fetch_recent_youtube_content(
            "@Project_L", 2, ydl_factory=factory
        )

        self.assertEqual(
            results,
            [
                [{"videoId": "video-one"}, {"videoId": "video-two"}],
                [],
                [{"videoId": "stream-one"}],
            ],
        )
        self.assertEqual(len(instances[0].calls), 3)
        self.assertTrue(all(download is False for _url, download in instances[0].calls))
        self.assertEqual(instances[0].options["playlistend"], 2)
        self.assertEqual(instances[0].options["socket_timeout"], 15)

    def test_all_failed_tabs_raise_a_safe_fetch_error(self):
        responses = {"videos": None, "shorts": None, "streams": None}

        def factory(options):
            return FakeYoutubeDL(options, responses)

        with self.assertRaisesRegex(YouTubeFetchError, "did not return"):
            fetch_recent_youtube_content("@Project_L", 2, ydl_factory=factory)

    def test_fetch_limit_must_be_positive(self):
        with self.assertRaises(ValueError):
            fetch_recent_youtube_content("@Project_L", 0)


if __name__ == "__main__":
    unittest.main()
