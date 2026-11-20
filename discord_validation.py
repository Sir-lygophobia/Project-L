"""Live, read-only validation of Discord resources selected in setup."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DISCORD_API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://discord.com/developers/docs, 1.5)"
TICKET_TOPIC_PATTERN = re.compile(r"^ticket-owner:\d+$")
CHANNEL_TYPE_NAMES = {
    0: "text channel",
    2: "voice channel",
    4: "category",
    5: "announcement channel",
    13: "stage channel",
    15: "forum channel",
    16: "media channel",
}
PERMISSION_BITS = {
    "administrator": 1 << 3,
    "manage_channels": 1 << 4,
    "view_channel": 1 << 10,
    "send_messages": 1 << 11,
    "embed_links": 1 << 14,
    "attach_files": 1 << 15,
    "read_message_history": 1 << 16,
    "connect": 1 << 20,
    "speak": 1 << 21,
    "manage_roles": 1 << 28,
}
PERMISSION_LABELS = {
    "manage_channels": "Manage Channels",
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "attach_files": "Attach Files",
    "connect": "Connect",
    "speak": "Speak",
    "manage_roles": "Manage Roles",
}


@dataclass(frozen=True)
class DiscordValidationResult:
    errors: list[str]
    warnings: list[str]


class DiscordValidationError(RuntimeError):
    """Raised when Discord cannot be contacted for setup validation."""


def _nested(config: Mapping[str, Any], parent: str, child: str) -> Any:
    value = config.get(parent, {})
    return value.get(child, 0) if isinstance(value, Mapping) else 0


def _channel_specs(config: Mapping[str, Any]):
    category_specs = [
        ("Modmail Category", config.get("modmail", 0), {4}),
        ("Private Voice Category", config.get("voicecategory", 0), {4}),
        (
            "Help Ticket Category",
            _nested(config, "tickets", "category_help"),
            {4},
        ),
        (
            "Suggestion Ticket Category",
            _nested(config, "tickets", "category_suggestions"),
            {4},
        ),
        (
            "Report Ticket Category",
            _nested(config, "tickets", "category_report"),
            {4},
        ),
    ]
    text_specs = [
        ("Welcome Channel", config.get("welcome", 0), {0, 5}),
        ("Welcome Image Channel", config.get("welcome_images", 0), {0, 5}),
        ("Invite Log Channel", config.get("invitechannel", 0), {0, 5}),
        ("YouTube Output Channel", config.get("ytChannelId", 0), {0, 5}),
        ("Twitch Output Channel", config.get("twChannelId", 0), {0, 5}),
        ("Transcript Channel", config.get("transcripts", 0), {0, 5}),
        ("Legacy Logs Channel", config.get("logs", 0), {0, 5}),
    ]
    for child in ("Message", "Ban_Unban", "Join_Leave", "Role", "Channel"):
        text_specs.append(
            (
                f"{child.replace('_', ' ')} Log Channel",
                _nested(config, "logChannelIds", child),
                {0, 5},
            )
        )
    for index, friend in enumerate(config.get("friendTwitch", []), start=1):
        if isinstance(friend, Mapping):
            text_specs.append(
                (
                    f"Twitch Friend {index} Output Channel",
                    friend.get("friendChannelId", 0),
                    {0, 5},
                )
            )

    voice_specs = []
    for child in (
        "totalMembers",
        "totalBots",
        "totalBoosts",
        "onlineMembers",
        "offlineMembers",
    ):
        voice_specs.append(
            (
                f"{child} Statistics Channel",
                _nested(config, "serverStatChannelIds", child),
                {0, 2, 5},
            )
        )
    return category_specs + text_specs + voice_specs


def _permission_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _bot_guild_permissions(
    config: Mapping[str, Any],
    bot_member: Mapping[str, Any],
    role_index: Mapping[int, Mapping[str, Any]],
) -> tuple[int, set[int], int]:
    guild_id = int(config.get("guildId", 0) or 0)
    role_ids = {int(role_id) for role_id in bot_member.get("roles", [])}
    if guild_id:
        role_ids.add(guild_id)
    permissions = 0
    highest_position = 0
    for role_id in role_ids:
        role = role_index.get(role_id)
        if role is None:
            continue
        permissions |= _permission_value(role.get("permissions"))
        highest_position = max(highest_position, int(role.get("position", 0) or 0))
    return permissions, role_ids, highest_position


def _apply_overwrite(permissions: int, overwrite: Mapping[str, Any]) -> int:
    permissions &= ~_permission_value(overwrite.get("deny"))
    permissions |= _permission_value(overwrite.get("allow"))
    return permissions


def _channel_permissions(
    base_permissions: int,
    channel: Mapping[str, Any],
    *,
    guild_id: int,
    bot_user_id: int,
    bot_role_ids: set[int],
) -> int:
    if base_permissions & PERMISSION_BITS["administrator"]:
        return (1 << 53) - 1
    overwrites = channel.get("permission_overwrites") or []
    permissions = base_permissions
    everyone = next(
        (
            overwrite
            for overwrite in overwrites
            if int(overwrite.get("id", 0) or 0) == guild_id
            and int(overwrite.get("type", 0) or 0) == 0
        ),
        None,
    )
    if everyone is not None:
        permissions = _apply_overwrite(permissions, everyone)

    role_allow = 0
    role_deny = 0
    for overwrite in overwrites:
        if (
            int(overwrite.get("type", 0) or 0) == 0
            and int(overwrite.get("id", 0) or 0) in bot_role_ids
        ):
            role_allow |= _permission_value(overwrite.get("allow"))
            role_deny |= _permission_value(overwrite.get("deny"))
    permissions &= ~role_deny
    permissions |= role_allow

    member = next(
        (
            overwrite
            for overwrite in overwrites
            if int(overwrite.get("id", 0) or 0) == bot_user_id
            and int(overwrite.get("type", 0) or 0) == 1
        ),
        None,
    )
    return _apply_overwrite(permissions, member) if member is not None else permissions


def _required_channel_permissions(label: str, channel_type: int) -> tuple[str, ...]:
    if "Statistics Channel" in label:
        return ("view_channel", "manage_channels")
    if channel_type == 4:
        return ("view_channel", "manage_channels", "manage_roles")
    if channel_type in {2, 13}:
        return ("view_channel", "connect", "speak")
    required = ["view_channel", "send_messages", "embed_links"]
    if label in {"Welcome Image Channel", "Transcript Channel"}:
        required.append("attach_files")
    return tuple(required)


def _everyone_can_view(
    config: Mapping[str, Any],
    channel: Mapping[str, Any],
    role_index: Mapping[int, Mapping[str, Any]],
) -> bool:
    guild_id = int(config.get("guildId", 0) or 0)
    everyone = role_index.get(guild_id, {})
    permissions = _permission_value(everyone.get("permissions"))
    for overwrite in channel.get("permission_overwrites") or []:
        if (
            int(overwrite.get("id", 0) or 0) == guild_id
            and int(overwrite.get("type", 0) or 0) == 0
        ):
            permissions = _apply_overwrite(permissions, overwrite)
            break
    return bool(permissions & PERMISSION_BITS["view_channel"])


def validate_discord_snapshot(
    config: Mapping[str, Any],
    role_ids: list[int],
    *,
    bot_user: Mapping[str, Any],
    channels: list[Mapping[str, Any]],
    roles: list[Mapping[str, Any]],
) -> list[str]:
    """Return actionable errors for a previously fetched Discord snapshot."""
    return validate_discord_snapshot_report(
        config,
        role_ids,
        bot_user=bot_user,
        channels=channels,
        roles=roles,
    ).errors


def validate_discord_snapshot_report(
    config: Mapping[str, Any],
    role_ids: list[int],
    *,
    bot_user: Mapping[str, Any],
    channels: list[Mapping[str, Any]],
    roles: list[Mapping[str, Any]],
    bot_member: Mapping[str, Any] | None = None,
) -> DiscordValidationResult:
    """Return blocking errors and non-blocking security findings."""
    errors: list[str] = []
    warnings: list[str] = []
    channel_index = {int(channel["id"]): channel for channel in channels}
    role_index = {int(role["id"]): role for role in roles}

    configured_client_id = int(config.get("client_id", 0) or 0)
    actual_client_id = int(bot_user.get("id", 0) or 0)
    if configured_client_id and configured_client_id != actual_client_id:
        errors.append("Application ID does not match the configured bot token.")

    validated_channels: list[tuple[str, Mapping[str, Any]]] = []
    for label, raw_id, expected_types in _channel_specs(config):
        resource_id = int(raw_id or 0)
        if not resource_id:
            continue
        channel = channel_index.get(resource_id)
        if channel is None:
            errors.append(f"{label} was not found in the configured server.")
            continue
        actual_type = int(channel.get("type", -1))
        if actual_type not in expected_types:
            expected = " or ".join(
                CHANNEL_TYPE_NAMES.get(value, str(value))
                for value in sorted(expected_types)
            )
            actual = CHANNEL_TYPE_NAMES.get(actual_type, f"channel type {actual_type}")
            errors.append(f"{label} points to a {actual}; expected {expected}.")
            continue
        validated_channels.append((label, channel))
        if actual_type == 4:
            child_count = sum(
                1
                for candidate in channels
                if str(candidate.get("parent_id") or "") == str(resource_id)
            )
            if child_count >= 50:
                errors.append(f"{label} is full (50 channels).")

    configured_roles = [
        ("Verified Role", int(config.get("verified", 0) or 0)),
        ("Streamer Role", int(config.get("streamRole", 0) or 0)),
    ]
    configured_roles.extend(
        (f"Reaction Role {index}", role_id)
        for index, role_id in enumerate(role_ids, start=1)
    )
    configured_roles = [(label, int(role_id or 0)) for label, role_id in configured_roles]
    for label, role_id in configured_roles:
        if role_id and role_id not in role_index:
            errors.append(f"{label} was not found in the configured server.")

    if bot_member is not None:
        base_permissions, bot_role_ids, highest_bot_position = _bot_guild_permissions(
            config, bot_member, role_index
        )
        administrator = bool(base_permissions & PERMISSION_BITS["administrator"])
        if administrator:
            warnings.append(
                "The bot has Administrator permission; grant only the permissions it needs."
            )

        guild_id = int(config.get("guildId", 0) or 0)
        bot_user_id = int(bot_user.get("id", 0) or 0)
        for label, channel in validated_channels:
            actual_permissions = _channel_permissions(
                base_permissions,
                channel,
                guild_id=guild_id,
                bot_user_id=bot_user_id,
                bot_role_ids=bot_role_ids,
            )
            missing = [
                PERMISSION_LABELS[name]
                for name in _required_channel_permissions(
                    label, int(channel.get("type", -1))
                )
                if not actual_permissions & PERMISSION_BITS[name]
            ]
            if missing:
                errors.append(
                    f"{label} is missing bot permissions: {', '.join(missing)}."
                )

        assignable_roles = [
            (label, role_id)
            for label, role_id in configured_roles
            if role_id and label != "Streamer Role"
        ]
        if assignable_roles and not (
            administrator or base_permissions & PERMISSION_BITS["manage_roles"]
        ):
            errors.append("The bot needs Manage Roles permission for configured roles.")
        for label, role_id in assignable_roles:
            role = role_index.get(role_id)
            if role is None:
                continue
            if role.get("managed"):
                errors.append(f"{label} is managed by Discord and cannot be assigned.")
            if int(role.get("position", 0) or 0) >= highest_bot_position:
                errors.append(f"{label} is not below the bot's highest role.")

        stream_role_id = int(config.get("streamRole", 0) or 0)
        stream_role = role_index.get(stream_role_id)
        if stream_role_id and stream_role is not None and not stream_role.get(
            "mentionable", False
        ):
            warnings.append(
                "Streamer Role is not mentionable, so Twitch alerts cannot mention it."
            )

        private_labels = {
            "Modmail Category",
            "Help Ticket Category",
            "Suggestion Ticket Category",
            "Report Ticket Category",
            "Transcript Channel",
        }
        for label, channel in validated_channels:
            if label in private_labels and _everyone_can_view(config, channel, role_index):
                warnings.append(f"{label} is visible to nonstaff roles.")

        ticket_category_ids = {
            int(value or 0)
            for value in config.get("tickets", {}).values()
            if int(value or 0)
        }
        recognizable_tickets = sum(
            1
            for channel in channels
            if int(channel.get("parent_id", 0) or 0) in ticket_category_ids
            and TICKET_TOPIC_PATTERN.fullmatch(str(channel.get("topic", "") or ""))
        )
        if recognizable_tickets:
            warnings.append(
                f"Found {recognizable_tickets} recognizable ticket channel(s); "
                "their permissions will be reconciled when the bot starts."
            )

    return DiscordValidationResult(errors=errors, warnings=warnings)


def _get_json(path: str, token: str, timeout: float) -> Any:
    request = Request(
        f"{DISCORD_API}/{path.lstrip('/')}",
        headers={
            "Authorization": f"Bot {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        messages = {
            401: "Discord rejected the bot token.",
            403: "The bot cannot inspect the configured server. Confirm it was invited.",
            404: "The configured Discord server was not found.",
        }
        raise DiscordValidationError(
            messages.get(exc.code, f"Discord returned HTTP {exc.code} during validation.")
        ) from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise DiscordValidationError(f"Could not validate with Discord: {exc}") from exc


def validate_live_discord_report(
    config: Mapping[str, Any],
    role_ids: list[int],
    *,
    token: str,
    timeout: float = 15,
) -> DiscordValidationResult:
    """Fetch a read-only Discord snapshot and return errors plus warnings."""
    clean_token = str(token or "").strip()
    guild_id = int(config.get("guildId", 0) or 0)
    if not clean_token or not guild_id:
        raise DiscordValidationError("A bot token and server ID are required.")
    bot_user = _get_json("users/@me", clean_token, timeout)
    channels = _get_json(f"guilds/{guild_id}/channels", clean_token, timeout)
    roles = _get_json(f"guilds/{guild_id}/roles", clean_token, timeout)
    if not isinstance(bot_user, Mapping) or not bot_user.get("id"):
        raise DiscordValidationError("Discord returned an unexpected validation response.")
    bot_member = _get_json(
        f"guilds/{guild_id}/members/{bot_user['id']}", clean_token, timeout
    )
    if (
        not isinstance(channels, list)
        or not isinstance(roles, list)
        or not isinstance(bot_member, Mapping)
    ):
        raise DiscordValidationError("Discord returned an unexpected validation response.")
    return validate_discord_snapshot_report(
        config,
        role_ids,
        bot_user=bot_user,
        channels=channels,
        roles=roles,
        bot_member=bot_member,
    )


def validate_live_discord_config(
    config: Mapping[str, Any],
    role_ids: list[int],
    *,
    token: str,
    timeout: float = 15,
) -> list[str]:
    """Return blocking errors, preserving the original validation API."""
    return validate_live_discord_report(
        config, role_ids, token=token, timeout=timeout
    ).errors
