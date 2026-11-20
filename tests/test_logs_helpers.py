import unittest

from Commands.logs import embed_field_text


class LogHelperTests(unittest.TestCase):
    def test_embed_field_text_limits_long_message_content(self):
        result = embed_field_text("x" * 2000)
        self.assertEqual(len(result), 1024)
        self.assertTrue(result.endswith("…"))

    def test_embed_field_text_never_returns_an_empty_value(self):
        self.assertEqual(embed_field_text(""), "\u200b")


if __name__ == "__main__":
    unittest.main()
