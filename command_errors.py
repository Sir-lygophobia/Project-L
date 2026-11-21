"""Safe user-facing messages for unhandled Discord application-command errors."""

from __future__ import annotations

from discord import app_commands


def _permission_label(permission: str) -> str:
    return permission.replace("_", " ").title()


def app_command_error_message(error: app_commands.AppCommandError) -> str:
    """Return an actionable message without exposing internal exception details."""
    if isinstance(error, app_commands.CommandOnCooldown):
        return f"That command is on cooldown. Try again in {error.retry_after:.1f}s."
    if isinstance(error, app_commands.MissingPermissions):
        missing = ", ".join(
            _permission_label(item) for item in error.missing_permissions
        )
        return f"You need these permissions to use that command: {missing}."
    if isinstance(error, app_commands.BotMissingPermissions):
        missing = ", ".join(
            _permission_label(item) for item in error.missing_permissions
        )
        return f"I am missing permissions required for that command: {missing}."
    if isinstance(error, app_commands.CheckFailure):
        return "You cannot use that command here."
    return "That command could not be completed. The error was recorded for review."
