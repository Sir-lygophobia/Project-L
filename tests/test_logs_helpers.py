import unittest
import datetime

from Commands.logs import (
    audit_log_cutoff,
    embed_field_text,
    sanitize_message_log_fields,
)


class LogHelperTests(unittest.TestCase):
    def test_embed_field_text_limits_long_message_content(self):
        result = embed_field_text("x" * 2000)
        self.assertEqual(len(result), 1024)
        self.assertTrue(result.endswith("…"))

    def test_embed_field_text_never_returns_an_empty_value(self):
        self.assertEqual(embed_field_text(""), "\u200b")

    def test_audit_log_cutoff_is_timezone_aware_utc(self):
        now = datetime.datetime(2026, 11, 20, 10, 0, tzinfo=datetime.timezone.utc)
        cutoff = audit_log_cutoff(now=now, window_seconds=15)
        self.assertEqual(
            cutoff,
            datetime.datetime(2026, 11, 20, 9, 59, 45, tzinfo=datetime.timezone.utc),
        )
        self.assertIs(cutoff.tzinfo, datetime.timezone.utc)

    def test_message_content_logging_is_private_by_default(self):
        values = sanitize_message_log_fields(
            {},
            before="old",
            after="new",
            content="deleted",
            media_urls=["https://example.test/private.png"],
        )
        self.assertEqual(values, (None, None, None, None))

    def test_message_content_logging_can_be_explicitly_enabled(self):
        values = sanitize_message_log_fields(
            {"logMessageContent": True},
            before="old",
            after="new",
            content="deleted",
            media_urls=["https://example.test/private.png"],
        )
        self.assertEqual(
            values,
            (
                "old",
                "new",
                "deleted",
                ["https://example.test/private.png"],
            ),
        )


if __name__ == "__main__":
    unittest.main()
