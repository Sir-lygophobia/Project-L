import json
import unittest
from copy import deepcopy
from pathlib import Path

from config_loader import AUTOMATIC_SETUP_VERSION
from discord_provisioning import (
    DiscordProvisioningError,
    provision_guild_resources,
    validate_provisioning_access,
)
from discord_validation import PERMISSION_BITS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = PROJECT_ROOT / "Data" / "config.example.json"


class FakeDiscordApi:
    def __init__(self, *, allow_manage_channels=True):
        self.guild_id = 100
        self.bot_id = 200
        self.next_id = 1000
        self.channels = []
        bot_permissions = sum(
            PERMISSION_BITS[name]
            for name in (
                "view_channel",
                "send_messages",
                "embed_links",
                "attach_files",
                "read_message_history",
                "manage_roles",
                "connect",
                "speak",
            )
        )
        if allow_manage_channels:
            bot_permissions |= PERMISSION_BITS["manage_channels"]
        self.roles = [
            {
                "id": str(self.guild_id),
                "name": "@everyone",
                "permissions": str(PERMISSION_BITS["view_channel"]),
                "position": 0,
            },
            {
                "id": "201",
                "name": "Project L",
                "permissions": str(bot_permissions),
                "position": 10,
            },
            {
                "id": "202",
                "name": "Staff",
                "permissions": str(PERMISSION_BITS["manage_channels"]),
                "position": 5,
            },
        ]
        self.bot_member = {"roles": ["201"]}
        self.calls = []

    def __call__(self, method, path, token, timeout, payload=None):
        self.calls.append((method, path, deepcopy(payload)))
        if method == "GET" and path == "users/@me":
            return {"id": str(self.bot_id), "username": "Project L"}
        if method == "GET" and path == f"guilds/{self.guild_id}/channels":
            return deepcopy(self.channels)
        if method == "GET" and path == f"guilds/{self.guild_id}/roles":
            return deepcopy(self.roles)
        if method == "GET" and path == f"guilds/{self.guild_id}/members/{self.bot_id}":
            return deepcopy(self.bot_member)
        if method == "POST" and path == f"guilds/{self.guild_id}/channels":
            channel = deepcopy(payload)
            channel["id"] = str(self.next_id)
            channel.setdefault("parent_id", None)
            channel.setdefault("permission_overwrites", [])
            self.next_id += 1
            self.channels.append(channel)
            return deepcopy(channel)
        if method == "PATCH" and path.startswith("channels/"):
            channel_id = int(path.split("/", maxsplit=1)[1])
            channel = next(
                channel for channel in self.channels if int(channel["id"]) == channel_id
            )
            channel.update(deepcopy(payload))
            return deepcopy(channel)
        if method == "PUT" and "/permissions/" in path:
            channel_part, target_part = path.split("/permissions/", maxsplit=1)
            channel_id = int(channel_part.split("/", maxsplit=1)[1])
            target_id = target_part
            channel = next(
                channel for channel in self.channels if int(channel["id"]) == channel_id
            )
            overwrites = channel.setdefault("permission_overwrites", [])
            existing = next(
                (
                    overwrite
                    for overwrite in overwrites
                    if str(overwrite["id"]) == target_id
                    and int(overwrite["type"]) == int(payload["type"])
                ),
                None,
            )
            replacement = {"id": target_id, **deepcopy(payload)}
            if existing is None:
                overwrites.append(replacement)
            else:
                overwrites[overwrites.index(existing)] = replacement
            return None
        raise AssertionError(f"Unexpected Discord API call: {method} {path}")


class DiscordProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        self.config["guildId"] = 100
        self.api = FakeDiscordApi()

    @staticmethod
    def overwrite_for(channel, target_id):
        return next(
            overwrite
            for overwrite in channel["permission_overwrites"]
            if int(overwrite["id"]) == target_id
        )

    def test_empty_server_gets_complete_safe_layout_and_saved_ids(self):
        config, report = provision_guild_resources(
            self.config,
            token="token",
            request_json=self.api,
        )

        self.assertEqual(config["client_id"], 200)
        self.assertEqual(
            config["managedResources"]["provisioningVersion"],
            AUTOMATIC_SETUP_VERSION,
        )
        self.assertTrue(config["welcome"])
        self.assertEqual(config["welcome_images"], 0)
        self.assertTrue(config["transcripts"])
        self.assertTrue(config["modmail"])
        self.assertTrue(config["voicecategory"])
        self.assertTrue(all(config["tickets"].values()))
        self.assertTrue(all(config["serverStatChannelIds"].values()))
        self.assertEqual(len(set(config["logChannelIds"].values())), 1)
        self.assertEqual(config["logs"], config["invitechannel"])
        self.assertEqual(config["logs"], config["logChannelIds"]["Message"])
        self.assertGreaterEqual(len(report.created), 16)

        modmail = next(
            channel
            for channel in self.api.channels
            if int(channel["id"]) == config["modmail"]
        )
        everyone_private = self.overwrite_for(modmail, 100)
        self.assertTrue(int(everyone_private["deny"]) & PERMISSION_BITS["view_channel"])
        bot_private = self.overwrite_for(modmail, 200)
        self.assertFalse(int(bot_private["allow"]) & PERMISSION_BITS["manage_roles"])

        welcome = next(
            channel
            for channel in self.api.channels
            if int(channel["id"]) == config["welcome"]
        )
        everyone_welcome = self.overwrite_for(welcome, 100)
        self.assertTrue(
            int(everyone_welcome["deny"]) & PERMISSION_BITS["send_messages"]
        )
        self.assertFalse(
            int(everyone_welcome["deny"]) & PERMISSION_BITS["view_channel"]
        )

        stat_channel_id = config["serverStatChannelIds"]["totalMembers"]
        stat_channel = next(
            channel
            for channel in self.api.channels
            if int(channel["id"]) == stat_channel_id
        )
        everyone_stats = self.overwrite_for(stat_channel, 100)
        self.assertTrue(int(everyone_stats["deny"]) & PERMISSION_BITS["connect"])
        bot_permissions = int(self.api.roles[1]["permissions"])
        for channel in self.api.channels:
            for overwrite in channel.get("permission_overwrites", []):
                requested = int(overwrite["allow"]) | int(overwrite["deny"])
                self.assertEqual(requested & ~bot_permissions, 0)

    def test_second_run_is_idempotent(self):
        config, first_report = provision_guild_resources(
            self.config,
            token="token",
            request_json=self.api,
        )
        first_channel_count = len(self.api.channels)
        second_config, second_report = provision_guild_resources(
            config,
            token="token",
            request_json=self.api,
        )

        self.assertTrue(first_report.created)
        self.assertEqual(len(self.api.channels), first_channel_count)
        self.assertEqual(second_report.created, [])
        self.assertEqual(second_report.updated, [])
        self.assertEqual(second_config, config)

    def test_integrations_share_one_automatic_alert_channel(self):
        self.config["ytChannel"] = "https://www.youtube.com/@projectl"
        self.config["twitchChannelName"] = "projectl"
        self.config["friendTwitch"] = [
            {"friendChannelId": 0, "friendChannel": "friend-one"},
            {"friendChannelId": 0, "friendChannel": "friend-two"},
        ]
        config, _report = provision_guild_resources(
            self.config,
            token="token",
            request_json=self.api,
        )

        alert_id = config["ytChannelId"]
        self.assertTrue(alert_id)
        self.assertEqual(config["twChannelId"], alert_id)
        self.assertEqual(
            {friend["friendChannelId"] for friend in config["friendTwitch"]},
            {alert_id},
        )
        alert_channels = [
            channel
            for channel in self.api.channels
            if channel.get("topic") == "project-l-managed:alerts"
        ]
        self.assertEqual(len(alert_channels), 1)

    def test_manage_channels_is_required_before_any_creation(self):
        api = FakeDiscordApi(allow_manage_channels=False)
        with self.assertRaisesRegex(DiscordProvisioningError, "Manage Channels"):
            validate_provisioning_access(
                self.config,
                token="token",
                request_json=api,
            )
        self.assertEqual(api.channels, [])

    def test_malformed_hidden_ids_are_replaced_instead_of_crashing(self):
        self.config["logs"] = "not-an-id"
        self.config["logChannelIds"] = ["invalid"]
        self.config["tickets"] = {
            "category_help": "bad",
            "category_suggestions": None,
            "category_report": False,
        }
        self.config["serverStatChannelIds"] = "invalid"
        self.config["managedResources"] = {
            "communityCategory": "bad",
            "staffCategory": [],
        }
        self.config["friendTwitch"] = [
            {"friendChannelId": "bad", "friendChannel": "friend"}
        ]

        config, report = provision_guild_resources(
            self.config,
            token="token",
            request_json=self.api,
        )

        self.assertTrue(report.created)
        self.assertTrue(all(config["tickets"].values()))
        self.assertTrue(all(config["serverStatChannelIds"].values()))
        self.assertTrue(all(config["logChannelIds"].values()))
        self.assertTrue(config["friendTwitch"][0]["friendChannelId"])

    def test_legacy_log_and_transcript_channels_are_made_private(self):
        public_logs = {
            "id": "901",
            "name": "old-logs",
            "type": 0,
            "parent_id": None,
            "permission_overwrites": [],
        }
        public_transcripts = {
            "id": "902",
            "name": "old-transcripts",
            "type": 0,
            "parent_id": None,
            "permission_overwrites": [
                {
                    "id": "999",
                    "type": 1,
                    "allow": str(PERMISSION_BITS["view_channel"] | (1 << 13)),
                    "deny": "0",
                }
            ],
        }
        self.api.channels.extend([public_logs, public_transcripts])
        self.config["logs"] = 901
        self.config["transcripts"] = 902

        config, _report = provision_guild_resources(
            self.config,
            token="token",
            request_json=self.api,
        )

        for channel_id in (config["logs"], config["transcripts"]):
            channel = next(
                channel
                for channel in self.api.channels
                if int(channel["id"]) == channel_id
            )
            everyone = self.overwrite_for(channel, 100)
            self.assertTrue(int(everyone["deny"]) & PERMISSION_BITS["view_channel"])
        transcript = next(
            channel for channel in self.api.channels if channel["id"] == "902"
        )
        custom = self.overwrite_for(transcript, 999)
        self.assertEqual(
            int(custom["allow"]), PERMISSION_BITS["view_channel"] | (1 << 13)
        )
        self.assertFalse(
            any(
                method == "PUT" and path.endswith("/permissions/999")
                for method, path, _payload in self.api.calls
            )
        )


if __name__ == "__main__":
    unittest.main()
