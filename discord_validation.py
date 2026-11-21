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
    "kick_members": 1 << 1,
    "ban_members": 1 << 2,
    "administrator": 1 << 3,
    "manage_channels": 1 << 4,
    "manage_guild": 1 << 5,
    "add_reactions": 1 << 6,
    "view_audit_log": 1 << 7,
    "view_channel": 1 << 10,
    "send_messages": 1 << 11,
    "manage_messages": 1 << 13,
    "embed_links": 1 << 14,
    "attach_files": 1 << 15,
    "read_message_history": 1 << 16,
    "mention_everyone": 1 << 17,
    "connect": 1 << 20,
    "speak": 1 << 21,
    "manage_roles": 1 << 28,
    "manage_guild_expressions": 1 << 30,
    "send_messages_in_threads": 1 << 38,
    "moderate_members": 1 << 40,
    "create_guild_expressions": 1 << 43,
}
PERMISSION_LABELS = {
    "kick_members": "Kick Members",
    "ban_members": "Ban Members",
    "manage_channels": "Manage Channels",
    "manage_guild": "Manage Server",
    "add_reactions": "Add Reactions",
    "view_audit_log": "View Audit Log",
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "manage_messages": "Manage Messages",
    "embed_links": "Embed Links",
    "attach_files": "Attach Files",
    "read_message_history": "Read Message History",
    "mention_everyone": "Mention @everyone, @here, and All Roles",
    "connect": "Connect",
    "speak": "Speak",
    "manage_roles": "Manage Roles",
    "send_messages_in_threads": "Send Messages in Threads",
    "moderate_members": "Timeout Members",
    "create_guild_expressions": "Create Expressions",
}

# Guild permissions used by Project L's always-loaded commands and background
# tasks. This is shared by live validation and the generated OAuth invite so
# setup cannot report success with a permission the running bot still lacks.
REQUIRED_BOT_PERMISSION_SPECS = (
    ("kick_members", PERMISSION_LABELS["kick_members"], "moderation"),
    ("ban_members", PERMISSION_LABELS["ban_members"], "moderation"),
    ("manage_channels", PERMISSION_LABELS["manage_channels"], "core"),
    ("manage_guild", PERMISSION_LABELS["manage_guild"], "invites"),
    ("add_reactions", PERMISSION_LABELS["add_reactions"], "giveaways"),
    ("view_audit_log", PERMISSION_LABELS["view_audit_log"], "logging"),
    ("view_channel", "View Channels", "core"),
    ("send_messages", PERMISSION_LABELS["send_messages"], "core"),
    ("send_messages_in_threads", PERMISSION_LABELS["send_messages_in_threads"], "core"),
    ("manage_messages", PERMISSION_LABELS["manage_messages"], "moderation"),
    ("embed_links", PERMISSION_LABELS["embed_links"], "core"),
    ("attach_files", PERMISSION_LABELS["attach_files"], "core"),
    ("read_message_history", PERMISSION_LABELS["read_message_history"], "core"),
    ("mention_everyone", PERMISSION_LABELS["mention_everyone"], "alerts"),
    ("connect", PERMISSION_LABELS["connect"], "voice"),
    ("speak", PERMISSION_LABELS["speak"], "voice"),
    ("manage_roles", PERMISSION_LABELS["manage_roles"], "roles"),
    (
        "create_guild_expressions",
        PERMISSION_LABELS["create_guild_expressions"],
        "moderation",
    ),
    ("moderate_members", PERMISSION_LABELS["moderate_members"], "moderation"),
)

# Discord exposes one flag for approved large applications and one for
# self-service/limited applications. Either flag means the intent is enabled.
PRIVILEGED_INTENT_SPECS = (
    ("Presence Intent", (1 << 12) | (1 << 13)),
    ("Server Members Intent", (1 << 14) | (1 << 15)),
    ("Message Content Intent", (1 << 18) | (1 << 19)),
)


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
        (
            "Community Category",
            _nested(config, "managedResources", "communityCategory"),
            {4},
        ),
        (
            "Staff Category",
            _nested(config, "managedResources", "staffCategory"),
            {4},
        ),
        (
            "Server Statistics Category",
            _nested(config, "managedResources", "statsCategory"),
            {4},
        ),
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
        (
            "Setup Panels Channel",
            _nested(config, "managedResources", "panelsChannel"),
            {0, 5},
        ),
        (
            "Alerts Channel",
            _nested(config, "managedResources", "alertsChannel"),
            {0, 5},
        ),
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


def required_bot_permissions() -> int:
    """Return the exact guild permission bitset requested during installation."""
    return sum(
        PERMISSION_BITS[name]
        for name, _label, _feature in REQUIRED_BOT_PERMISSION_SPECS
    )


def missing_privileged_intents(application: Mapping[str, Any]) -> list[str]:
    """Return required privileged gateway intents disabled for an application."""
    flags = _permission_value(application.get("flags_new", application.get("flags", 0)))
    return [
        label
        for label, accepted_flags in PRIVILEGED_INTENT_SPECS
        if not flags & accepted_flags
    ]


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
        return ("view_channel", "manage_channels")
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
    application: Mapping[str, Any] | None = None,
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

    if application is not None:
        missing_intents = missing_privileged_intents(application)
        if missing_intents:
            portal_url = (
                f"https://discord.com/developers/applications/{actual_client_id}/bot"
                if actual_client_id
                else "the Discord Developer Portal Bot page"
            )
            errors.append(
                "Enable Project L's required Privileged Gateway Intents: "
                f"{', '.join(missing_intents)}. Open {portal_url}, enable each "
                "switch, save, and validate again."
            )

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
    configured_roles = [
        (label, int(role_id or 0)) for label, role_id in configured_roles
    ]
    for label, role_id in configured_roles:
        if role_id and role_id not in role_index:
            errors.append(f"{label} was not found in the configured server.")

    if bot_member is not None:
        base_permissions, bot_role_ids, highest_bot_position = _bot_guild_permissions(
            config, bot_member, role_index
        )
        administrator = bool(base_permissions & PERMISSION_BITS["administrator"])
        missing_base_permissions = [
            (name, label)
            for name, label, _feature in REQUIRED_BOT_PERMISSION_SPECS
            if not administrator and not base_permissions & PERMISSION_BITS[name]
        ]
        if administrator:
            warnings.append(
                "The bot has Administrator permission; grant only the permissions it needs."
            )
        elif missing_base_permissions:
            errors.append(
                "The bot is missing required server permissions: "
                + ", ".join(label for _name, label in missing_base_permissions)
                + ". Reopen Invite Bot to Server in setup and approve every "
                "requested permission."
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
        missing_base_permission_names = {
            name for name, _label in missing_base_permissions
        }
        if (
            assignable_roles
            and "manage_roles" not in missing_base_permission_names
            and not (
                administrator or base_permissions & PERMISSION_BITS["manage_roles"]
            )
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
        if (
            stream_role_id
            and stream_role is not None
            and not stream_role.get("mentionable", False)
            and not (
                administrator or base_permissions & PERMISSION_BITS["mention_everyone"]
            )
        ):
            warnings.append(
                "Streamer Role is not mentionable, so Twitch alerts cannot mention it."
            )

        private_labels = {
            "Staff Category",
            "Modmail Category",
            "Help Ticket Category",
            "Suggestion Ticket Category",
            "Report Ticket Category",
            "Transcript Channel",
        }
        for label, channel in validated_channels:
            if label in private_labels and _everyone_can_view(
                config, channel, role_index
            ):
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
            messages.get(
                exc.code, f"Discord returned HTTP {exc.code} during validation."
            )
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
    application = _get_json("oauth2/applications/@me", clean_token, timeout)
    channels = _get_json(f"guilds/{guild_id}/channels", clean_token, timeout)
    roles = _get_json(f"guilds/{guild_id}/roles", clean_token, timeout)
    if not isinstance(bot_user, Mapping) or not bot_user.get("id"):
        raise DiscordValidationError(
            "Discord returned an unexpected validation response."
        )
    bot_member = _get_json(
        f"guilds/{guild_id}/members/{bot_user['id']}", clean_token, timeout
    )
    if (
        not isinstance(channels, list)
        or not isinstance(roles, list)
        or not isinstance(bot_member, Mapping)
        or not isinstance(application, Mapping)
    ):
        raise DiscordValidationError(
            "Discord returned an unexpected validation response."
        )
    return validate_discord_snapshot_report(
        config,
        role_ids,
        bot_user=bot_user,
        channels=channels,
        roles=roles,
        bot_member=bot_member,
        application=application,
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
