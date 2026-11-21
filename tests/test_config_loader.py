import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config_loader import (
    ConfigError,
    load_config,
    load_role_ids,
    missing_required_settings,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = PROJECT_ROOT / "Data" / "config.example.json"


class ConfigLoaderTests(unittest.TestCase):
    def setUp(self):
        self.example = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))

    def write_config(self, directory: str, config) -> Path:
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def test_example_config_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, self.example)
            with patch.dict(os.environ, {}, clear=True):
                loaded = load_config(path)
        self.assertEqual(loaded["guildId"], 0)
        self.assertIn("token (or the DISCORD_BOT_TOKEN environment variable)", missing_required_settings(loaded))

    def test_secrets_can_come_from_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, self.example)
            environment = {
                "DISCORD_BOT_TOKEN": "environment-token",
                "TWITCH_CLIENT_ID": "environment-client",
                "TWITCH_CLIENT_SECRET": "environment-secret",
            }
            with patch.dict(os.environ, environment, clear=True):
                loaded = load_config(path)
        self.assertEqual(loaded["token"], "environment-token")
        self.assertEqual(loaded["twClientId"], "environment-client")
        self.assertEqual(loaded["twClientsecret"], "environment-secret")

    def test_invalid_id_type_is_rejected(self):
        self.example["guildId"] = "123"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, self.example)
            with self.assertRaisesRegex(ConfigError, "guildId"):
                load_config(path)

    def test_missing_nested_setting_is_rejected(self):
        del self.example["tickets"]["category_help"]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, self.example)
            with self.assertRaisesRegex(ConfigError, "category_help"):
                load_config(path)

    def test_role_ids_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            valid_path = Path(directory) / "roles.json"
            valid_path.write_text("[123, 456]", encoding="utf-8")
            self.assertEqual(load_role_ids(valid_path), [123, 456])

            invalid_path = Path(directory) / "invalid-roles.json"
            invalid_path.write_text('[123, "456"]', encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_role_ids(invalid_path)


if __name__ == "__main__":
    unittest.main()
