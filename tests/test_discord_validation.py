import json
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from config_loader import ConfigError
from configure_gui import (
    ProjectLSetupWizard,
    build_bot_settings_url,
    format_validation_warnings,
    required_gateway_intents_text,
)
from discord_provisioning import DiscordProvisioningReport
from discord_validation import (
    PERMISSION_BITS,
    required_bot_permissions,
    validate_discord_snapshot,
    validate_discord_snapshot_report,
    validate_live_discord_report,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = PROJECT_ROOT / "Data" / "config.example.json"
ENABLED_PRIVILEGED_INTENT_FLAGS = (1 << 13) | (1 << 15) | (1 << 19)


class DiscordSnapshotValidationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        self.config["guildId"] = 100
        self.config["client_id"] = 200

    def validate(self, channels, roles=None, role_ids=None, bot_id=200):
        return validate_discord_snapshot(
            self.config,
            [] if role_ids is None else role_ids,
            bot_user={"id": str(bot_id)},
            channels=channels,
            roles=[] if roles is None else roles,
        )

    def test_valid_categories_text_channels_voice_channels_and_roles(self):
        self.config["modmail"] = 301
        self.config["transcripts"] = 302
        self.config["serverStatChannelIds"]["totalMembers"] = 303
        self.config["verified"] = 401
        errors = self.validate(
            [
                {"id": "301", "type": 4, "parent_id": None},
                {"id": "302", "type": 0, "parent_id": None},
                {"id": "303", "type": 2, "parent_id": None},
            ],
            roles=[{"id": "401"}, {"id": "402"}],
            role_ids=[402],
        )
        self.assertEqual(errors, [])

    def test_wrong_channel_type_and_missing_channel_are_actionable(self):
        self.config["modmail"] = 301
        self.config["tickets"]["category_help"] = 999
        errors = self.validate([{"id": "301", "type": 0, "parent_id": None}])
        self.assertTrue(
            any(
                "Modmail Category points to a text channel" in error for error in errors
            )
        )
        self.assertTrue(
            any("Help Ticket Category was not found" in error for error in errors)
        )

    def test_client_and_role_mismatches_are_reported(self):
        self.config["verified"] = 401
        errors = self.validate([], roles=[], bot_id=999)
        self.assertIn("Application ID does not match the configured bot token.", errors)
        self.assertIn("Verified Role was not found in the configured server.", errors)

    def test_disabled_privileged_intents_are_blocking_and_actionable(self):
        report = validate_discord_snapshot_report(
            self.config,
            [],
            bot_user={"id": "200"},
            application={"id": "200", "flags_new": str(1 << 15)},
            channels=[],
            roles=[],
        )
        self.assertEqual(len(report.errors), 1)
        self.assertIn("Presence Intent", report.errors[0])
        self.assertNotIn("Server Members Intent", report.errors[0])
        self.assertIn("Message Content Intent", report.errors[0])
        self.assertIn("/applications/200/bot", report.errors[0])

    def test_full_category_is_rejected(self):
        self.config["modmail"] = 301
        channels = [{"id": "301", "type": 4, "parent_id": None}]
        channels.extend(
            {"id": str(500 + index), "type": 0, "parent_id": "301"}
            for index in range(50)
        )
        errors = self.validate(channels)
        self.assertIn("Modmail Category is full (50 channels).", errors)

    def test_bot_permissions_and_role_hierarchy_are_blocking_errors(self):
        self.config["welcome"] = 301
        self.config["verified"] = 401
        roles = [
            {
                "id": "100",
                "name": "@everyone",
                "permissions": str(PERMISSION_BITS["view_channel"]),
                "position": 0,
            },
            {
                "id": "201",
                "name": "Project L",
                "permissions": str(PERMISSION_BITS["view_channel"]),
                "position": 5,
                "tags": {"bot_id": "200"},
            },
            {
                "id": "401",
                "name": "Verified",
                "permissions": "0",
                "position": 6,
                "managed": False,
            },
            {
                "id": "402",
                "name": "Managed Reaction",
                "permissions": "0",
                "position": 1,
                "managed": True,
            },
        ]
        report = validate_discord_snapshot_report(
            self.config,
            [402],
            bot_user={"id": "200"},
            bot_member={"roles": ["201"]},
            channels=[
                {"id": "301", "type": 0, "parent_id": None, "permission_overwrites": []}
            ],
            roles=roles,
        )
        error_text = "\n".join(report.errors)
        self.assertIn(
            "Welcome Channel is missing bot permissions: Send Messages, Embed Links",
            error_text,
        )
        self.assertIn("Verified Role is not below the bot's highest role", error_text)
        self.assertIn("Reaction Role 1 is managed by Discord", error_text)
        self.assertIn("Manage Roles", error_text)

    def test_channel_overwrite_can_remove_a_required_bot_permission(self):
        self.config["welcome"] = 301
        base_permissions = required_bot_permissions()
        report = validate_discord_snapshot_report(
            self.config,
            [],
            bot_user={"id": "200"},
            bot_member={"roles": ["201"]},
            channels=[
                {
                    "id": "301",
                    "type": 0,
                    "parent_id": None,
                    "permission_overwrites": [
                        {
                            "id": "201",
                            "type": 0,
                            "allow": "0",
                            "deny": str(PERMISSION_BITS["send_messages"]),
                        }
                    ],
                }
            ],
            roles=[
                {"id": "100", "name": "@everyone", "permissions": "0", "position": 0},
                {
                    "id": "201",
                    "name": "Project L",
                    "permissions": str(base_permissions),
                    "position": 1,
                    "tags": {"bot_id": "200"},
                },
            ],
        )
        self.assertEqual(
            report.errors,
            ["Welcome Channel is missing bot permissions: Send Messages."],
        )

    def test_security_findings_are_nonblocking_warnings(self):
        self.config["tickets"]["category_help"] = 301
        self.config["transcripts"] = 302
        self.config["streamRole"] = 401
        roles = [
            {
                "id": "100",
                "name": "@everyone",
                "permissions": str(PERMISSION_BITS["view_channel"]),
                "position": 0,
            },
            {
                "id": "201",
                "name": "Project L",
                "permissions": str(PERMISSION_BITS["administrator"]),
                "position": 10,
                "tags": {"bot_id": "200"},
            },
            {
                "id": "401",
                "name": "Streamer",
                "permissions": "0",
                "position": 2,
                "mentionable": False,
            },
        ]
        channels = [
            {
                "id": "301",
                "name": "tickets",
                "type": 4,
                "parent_id": None,
                "permission_overwrites": [],
            },
            {
                "id": "302",
                "name": "transcripts",
                "type": 0,
                "parent_id": None,
                "permission_overwrites": [],
            },
            {
                "id": "303",
                "name": "ticket-help-123",
                "topic": "ticket-owner:123",
                "type": 0,
                "parent_id": "301",
                "permission_overwrites": [],
            },
        ]
        report = validate_discord_snapshot_report(
            self.config,
            [],
            bot_user={"id": "200"},
            bot_member={"roles": ["201"]},
            channels=channels,
            roles=roles,
        )
        self.assertEqual(report.errors, [])
        warning_text = "\n".join(report.warnings)
        self.assertIn("Administrator permission", warning_text)
        self.assertIn("Help Ticket Category is visible to nonstaff roles", warning_text)
        self.assertIn("Transcript Channel is visible to nonstaff roles", warning_text)
        self.assertNotIn("Streamer Role is not mentionable", warning_text)
        self.assertIn("1 recognizable ticket channel(s)", warning_text)

    def test_live_report_fetches_the_bot_guild_member(self):
        responses = {
            "users/@me": {"id": "200"},
            "oauth2/applications/@me": {
                "id": "200",
                "flags": ENABLED_PRIVILEGED_INTENT_FLAGS,
            },
            "guilds/100/channels": [],
            "guilds/100/roles": [
                {
                    "id": "100",
                    "name": "@everyone",
                    "permissions": str(required_bot_permissions()),
                    "position": 0,
                }
            ],
            "guilds/100/members/200": {"roles": []},
        }
        with patch(
            "discord_validation._get_json",
            side_effect=lambda path, _token, _timeout: responses[path],
        ) as get_json:
            report = validate_live_discord_report(
                self.config, [], token="token", timeout=1
            )
        self.assertEqual(report.errors, [])
        self.assertIn(
            call("guilds/100/members/200", "token", 1), get_json.call_args_list
        )

    def test_gui_warnings_are_readable_and_do_not_block_saving(self):
        self.assertEqual(
            format_validation_warnings(["First warning", "Second warning"]),
            "• First warning\n• Second warning",
        )
        wizard = object.__new__(ProjectLSetupWizard)
        wizard.root = Mock()
        wizard.messagebox = Mock()
        wizard.saved = False
        wizard.set_status = Mock()
        wizard.validated_live_values = Mock(
            return_value=(
                {"token": ""},
                [401],
                ["Review this finding."],
                DiscordProvisioningReport(reused=["welcome channel"]),
            )
        )
        with patch("configure_gui.save_configuration") as save_configuration:
            wizard.save()
        save_configuration.assert_called_once_with({"token": ""}, [401])
        self.assertTrue(wizard.saved)
        wizard.messagebox.showwarning.assert_called_once()
        wizard.messagebox.showinfo.assert_not_called()
        wizard.root.destroy.assert_called_once()
        wizard.validated_live_values.assert_called_once_with(provision=True)

    def test_gui_invite_button_uses_entered_bot_and_server(self):
        wizard = object.__new__(ProjectLSetupWizard)
        wizard.variables = {
            "token": Mock(get=Mock(return_value="bot-token")),
            "guildId": Mock(get=Mock(return_value="123456")),
        }
        wizard.root = Mock()
        wizard.messagebox = Mock()
        wizard.set_status = Mock()
        with (
            patch("configure_gui.discover_bot_user_id", return_value=654321),
            patch(
                "configure_gui.build_bot_invite_url",
                return_value="https://discord.example/invite",
            ) as build_invite,
            patch("configure_gui.webbrowser.open", return_value=True) as open_browser,
        ):
            wizard.open_bot_invite()

        build_invite.assert_called_once_with(654321, 123456)
        open_browser.assert_called_once_with("https://discord.example/invite")
        wizard.messagebox.showerror.assert_not_called()

    def test_gui_bot_settings_button_resolves_exact_application(self):
        wizard = object.__new__(ProjectLSetupWizard)
        wizard.variables = {"token": Mock(get=Mock(return_value="bot-token"))}
        wizard.root = Mock()
        wizard.messagebox = Mock()
        wizard.set_status = Mock()
        with (
            patch("configure_gui.discover_bot_user_id", return_value=654321),
            patch("configure_gui.webbrowser.open", return_value=True) as open_browser,
        ):
            wizard.open_bot_settings()

        self.assertEqual(
            build_bot_settings_url(654321),
            "https://discord.com/developers/applications/654321/bot",
        )
        open_browser.assert_called_once_with(build_bot_settings_url(654321))
        wizard.messagebox.showerror.assert_not_called()

    def test_gui_server_branding_button_opens_configured_server(self):
        wizard = object.__new__(ProjectLSetupWizard)
        wizard.variables = {"guildId": Mock(get=Mock(return_value="123456789"))}
        wizard.root = Mock()
        wizard.messagebox = Mock()
        wizard.set_status = Mock()
        with patch("configure_gui.webbrowser.open", return_value=True) as open_browser:
            wizard.open_server_in_discord()

        open_browser.assert_called_once_with(
            "https://discord.com/channels/123456789"
        )
        wizard.messagebox.showerror.assert_not_called()

    def test_gui_requires_privileged_intent_confirmation(self):
        wizard = object.__new__(ProjectLSetupWizard)
        wizard.gateway_intents_confirmed = Mock(get=Mock(return_value=False))
        with self.assertRaisesRegex(
            ConfigError,
            "Presence Intent.*Server Members Intent.*Message Content Intent",
        ):
            wizard.validated_values()
        self.assertEqual(
            required_gateway_intents_text(),
            "Presence Intent, Server Members Intent, Message Content Intent",
        )

    def test_gui_validate_is_read_only_and_does_not_provision(self):
        wizard = object.__new__(ProjectLSetupWizard)
        wizard.root = Mock()
        wizard.set_status = Mock()
        wizard.validated_values = Mock(
            return_value=({"token": "token", "guildId": 100}, [])
        )
        with (
            patch("configure_gui.validate_provisioning_access", return_value=200),
            patch(
                "configure_gui.configuration_without_managed_channels",
                return_value={"token": "token", "guildId": 100, "client_id": 200},
            ),
            patch(
                "configure_gui.validate_live_discord_report",
                return_value=Mock(errors=[], warnings=[]),
            ),
            patch("configure_gui.provision_guild_resources") as provision,
        ):
            config, roles, warnings, report = wizard.validated_live_values()

        provision.assert_not_called()
        self.assertEqual(config["client_id"], 200)
        self.assertEqual(roles, [])
        self.assertEqual(warnings, [])
        self.assertIsNone(report)


if __name__ == "__main__":
    unittest.main()
