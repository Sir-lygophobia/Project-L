import unittest

from discord import app_commands
from discord.app_commands import Cooldown

from command_errors import app_command_error_message


class CommandErrorMessageTests(unittest.TestCase):
    def test_user_missing_permissions_are_readable(self):
        error = app_commands.MissingPermissions(["manage_messages", "ban_members"])
        message = app_command_error_message(error)
        self.assertIn("Manage Messages", message)
        self.assertIn("Ban Members", message)

    def test_bot_missing_permissions_are_readable(self):
        error = app_commands.BotMissingPermissions(["attach_files"])
        self.assertIn("Attach Files", app_command_error_message(error))

    def test_cooldown_includes_retry_time(self):
        error = app_commands.CommandOnCooldown(Cooldown(1, 60), 12.34)
        self.assertIn("12.3s", app_command_error_message(error))

    def test_unknown_errors_do_not_leak_details(self):
        error = app_commands.AppCommandError("sensitive internal detail")
        message = app_command_error_message(error)
        self.assertNotIn("sensitive", message)


if __name__ == "__main__":
    unittest.main()
