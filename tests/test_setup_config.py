import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from config_loader import ConfigError, load_config, load_role_ids
from setup_config import (
    build_configuration,
    configuration_status,
    flatten_config,
    load_editable_config,
    parse_discord_id,
    parse_role_ids_text,
    parse_twitch_friends_text,
    save_configuration,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = PROJECT_ROOT / "Data" / "config.example.json"


class SetupConfigTests(unittest.TestCase):
    def setUp(self):
        self.example = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        self.values = flatten_config(self.example, [])

    def valid_values(self):
        values = deepcopy(self.values)
        values["token"] = "local-token"
        values["guildId"] = "123456789"
        return values

    def test_minimal_configuration_is_valid(self):
        config, role_ids = build_configuration(
            self.valid_values(), self.example, environ={}
        )
        self.assertEqual(config["guildId"], 123456789)
        self.assertEqual(config["token"], "local-token")
        self.assertEqual(role_ids, [])

    def test_environment_token_is_used_but_never_written_into_config(self):
        values = self.valid_values()
        values["token"] = ""
        config, _role_ids = build_configuration(
            values,
            self.example,
            environ={"DISCORD_BOT_TOKEN": "environment-secret"},
        )
        self.assertEqual(config["token"], "")

    def test_missing_required_values_are_reported(self):
        with self.assertRaisesRegex(ConfigError, "token") as raised:
            build_configuration(self.values, self.example, environ={})
        self.assertIn("guildId", str(raised.exception))

    def test_discord_ids_accept_mentions_and_reject_non_ascii_digits(self):
        self.assertEqual(parse_discord_id("<#123>", "channel"), 123)
        self.assertEqual(parse_discord_id("<@&456>", "role"), 456)
        self.assertEqual(parse_discord_id("<@!789>", "member"), 789)
        self.assertEqual(parse_discord_id("0", "optional"), 0)
        with self.assertRaises(ConfigError):
            parse_discord_id("١٢٣", "channel")

    def test_reaction_roles_are_unique_and_limited_to_25(self):
        self.assertEqual(parse_role_ids_text("123\n456, 789"), [123, 456, 789])
        with self.assertRaisesRegex(ConfigError, "duplicates"):
            parse_role_ids_text("123 123")
        with self.assertRaisesRegex(ConfigError, "at most 25"):
            parse_role_ids_text(" ".join(str(value) for value in range(1, 27)))

    def test_twitch_friend_rows_are_normalized_and_unique(self):
        friends = parse_twitch_friends_text("123, @FirstStreamer\n456, second")
        self.assertEqual(
            friends,
            [
                {"friendChannelId": 123, "friendChannel": "FirstStreamer"},
                {"friendChannelId": 456, "friendChannel": "second"},
            ],
        )
        with self.assertRaisesRegex(ConfigError, "repeats Twitch username"):
            parse_twitch_friends_text("123, Streamer\n456, streamer")
        with self.assertRaisesRegex(ConfigError, "must be"):
            parse_twitch_friends_text("123")

    def test_optional_notification_fields_must_be_complete_pairs(self):
        values = self.valid_values()
        values["ytChannel"] = "@projectl"
        with self.assertRaisesRegex(ConfigError, "YouTube"):
            build_configuration(values, self.example, environ={})

        values = self.valid_values()
        values["twitchChannelName"] = "projectl"
        values["twChannelId"] = "456"
        with self.assertRaisesRegex(ConfigError, "client ID and client secret"):
            build_configuration(values, self.example, environ={})

        config, _role_ids = build_configuration(
            values,
            self.example,
            environ={
                "TWITCH_CLIENT_ID": "environment-client",
                "TWITCH_CLIENT_SECRET": "environment-secret",
            },
        )
        self.assertEqual(config["twClientId"], "")
        self.assertEqual(config["twClientsecret"], "")

    def test_youtube_source_is_normalized_and_invalid_urls_are_rejected(self):
        values = self.valid_values()
        values["ytChannel"] = "@Project_L"
        values["ytChannelId"] = "456"
        config, _role_ids = build_configuration(values, self.example, environ={})
        self.assertEqual(
            config["ytChannel"], "https://www.youtube.com/@Project_L"
        )

        values["ytChannel"] = "https://example.com/not-youtube"
        with self.assertRaisesRegex(ConfigError, "must use youtube.com"):
            build_configuration(values, self.example, environ={})

    def test_primary_twitch_stream_cannot_repeat_a_friend(self):
        values = self.valid_values()
        values.update(
            {
                "twitchChannelName": "SameStreamer",
                "twChannelId": "123",
                "twClientId": "client",
                "twClientsecret": "secret",
                "friendTwitch": "456, samestreamer",
            }
        )
        with self.assertRaisesRegex(ConfigError, "must not also appear"):
            build_configuration(values, self.example, environ={})

    def test_malformed_nested_objects_are_rebuilt_from_form_values(self):
        base = deepcopy(self.example)
        base["tickets"] = []
        config, _role_ids = build_configuration(
            self.valid_values(), base, environ={}
        )
        self.assertEqual(
            config["tickets"],
            {
                "category_help": 0,
                "category_suggestions": 0,
                "category_report": 0,
            },
        )

    def test_save_outputs_loader_compatible_json_and_preserves_unknown_keys(self):
        base = deepcopy(self.example)
        base["futureSetting"] = {"enabled": True}
        config, role_ids = build_configuration(
            self.valid_values(), base, environ={}
        )
        role_ids.extend([123, 456])

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            roles_path = Path(directory) / "roles.json"
            save_configuration(
                config,
                role_ids,
                config_path=config_path,
                roles_path=roles_path,
            )
            with patch.dict(os.environ, {}, clear=True):
                loaded = load_config(config_path)
            loaded_roles = load_role_ids(roles_path)

        self.assertEqual(loaded["futureSetting"], {"enabled": True})
        self.assertEqual(loaded_roles, [123, 456])

    def test_status_honors_environment_token_without_exposing_it(self):
        values = self.valid_values()
        values["token"] = ""
        config, roles = build_configuration(
            values,
            self.example,
            environ={"DISCORD_BOT_TOKEN": "environment-secret"},
        )
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            roles_path = Path(directory) / "roles.json"
            save_configuration(
                config,
                roles,
                config_path=config_path,
                roles_path=roles_path,
            )
            with patch.dict(
                os.environ, {"DISCORD_BOT_TOKEN": "environment-secret"}, clear=True
            ):
                complete, reasons = configuration_status(config_path, roles_path)
        self.assertTrue(complete)
        self.assertEqual(reasons, [])

    def test_editable_load_merges_template_without_environment_secrets(self):
        current = deepcopy(self.example)
        current.pop("streamRole")
        current["futureSetting"] = 7
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            example_path = Path(directory) / "config.example.json"
            config_path.write_text(json.dumps(current), encoding="utf-8")
            example_path.write_text(json.dumps(self.example), encoding="utf-8")
            with patch.dict(
                os.environ, {"DISCORD_BOT_TOKEN": "environment-secret"}, clear=True
            ):
                loaded = load_editable_config(config_path, example_path)
        self.assertEqual(loaded["token"], "")
        self.assertEqual(loaded["streamRole"], 0)
        self.assertEqual(loaded["futureSetting"], 7)


if __name__ == "__main__":
    unittest.main()
