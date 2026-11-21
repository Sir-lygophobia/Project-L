import unittest

from giveaway_utils import chunk_display_values, ended_description, parse_winner_count


class GiveawayUtilityTests(unittest.TestCase):
    def test_parse_winner_count_accepts_current_embed_format(self):
        description = "**Started by:** <@1>\n**Winners:** 12\n**Ends at:** <t:1>"
        self.assertEqual(parse_winner_count(description), 12)

    def test_parse_winner_count_rejects_malformed_embed(self):
        with self.assertRaisesRegex(ValueError, "winner count"):
            parse_winner_count("No winners metadata here")

    def test_ended_description_removes_entry_prompt(self):
        result = ended_description("Header\n\nReact with 🎉 to enter.")
        self.assertEqual(result, "Header\n\nGiveaway ended.")

    def test_display_values_never_exceed_embed_field_limit(self):
        mentions = [f"<@{index:019d}>" for index in range(100)]
        chunks = chunk_display_values(mentions)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(", ".join(chunks), ", ".join(mentions))
        self.assertTrue(all(len(chunk) <= 1024 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
