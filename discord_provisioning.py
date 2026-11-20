"""Create and reconcile the Discord resources managed by Project L setup."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config_loader import AUTOMATIC_SETUP_VERSION
from discord_validation import DISCORD_API, PERMISSION_BITS, USER_AGENT

RequestJson = Callable[..., Any]

MANAGED_TOPIC_PREFIX = "project-l-managed:"
MANAGED_RESOURCES_KEY = "managedResources"

TEXT_CHANNEL = 0
VOICE_CHANNEL = 2
CATEGORY_CHANNEL = 4


class DiscordProvisioningError(RuntimeError):
    """Raised when automatic Discord resource setup cannot finish safely."""


@dataclass
class DiscordProvisioningReport:
    """Human-readable details about one provisioning run."""

    created: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.created:
            parts.append(f"created {len(self.created)}")
        if self.reused:
            parts.append(f"reused {len(self.reused)}")
        if self.updated:
            parts.append(f"secured or updated {len(self.updated)}")
        return ", ".join(parts) if parts else "no changes needed"


@dataclass(frozen=True)
class _DiscordSnapshot:
    bot_user_id: int
    guild_id: int
    bot_permissions: int
    channels: list[dict[str, Any]]
    roles: list[dict[str, Any]]
    bot_member: Mapping[str, Any]


def _read_http_error(exc: HTTPError) -> dict[str, Any]:
    try:
        body = exc.read().decode("utf-8", errors="replace")
        value = json.loads(body)
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _request_json(
    method: str,
    path: str,
    token: str,
    timeout: float,
    payload: Mapping[str, Any] | None = None,
) -> Any:
    """Call Discord's REST API, including bounded rate-limit retries."""
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{DISCORD_API}/{path.lstrip('/')}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bot {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )

    for attempt in range(5):
        try:
            with urlopen(request, timeout=timeout) as response:
                response_body = response.read()
            return json.loads(response_body.decode("utf-8")) if response_body else None
        except HTTPError as exc:
            details = _read_http_error(exc)
            if exc.code == 429 and attempt < 4:
                try:
                    retry_after = float(details.get("retry_after", 1))
                except (TypeError, ValueError):
                    retry_after = 1
                time.sleep(max(0.25, min(retry_after, 15)))
                continue
            messages = {
                401: "Discord rejected the bot token.",
                403: (
                    "Discord denied automatic server setup. Invite the bot with "
                    "Manage Channels, View Channels, Send Messages, Embed Links, "
                    "Attach Files, Read Message History, and Manage Roles."
                ),
                404: "Discord could not find the configured server or resource.",
                429: "Discord kept rate-limiting setup. Wait a moment and try again.",
            }
            api_message = str(details.get("message", "")).strip()
            message = messages.get(
                exc.code, f"Discord returned HTTP {exc.code} during automatic setup."
            )
            if api_message and api_message.casefold() not in message.casefold():
                message = f"{message} Discord says: {api_message}"
            raise DiscordProvisioningError(message) from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise DiscordProvisioningError(
                f"Could not contact Discord during automatic setup: {exc}"
            ) from exc
    raise DiscordProvisioningError("Discord resource setup did not complete.")


def _permission_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _fetch_snapshot(
    config: Mapping[str, Any],
    token: str,
    timeout: float,
    request_json: RequestJson,
) -> _DiscordSnapshot:
    guild_id = int(config.get("guildId", 0) or 0)
    if not str(token or "").strip() or not guild_id:
        raise DiscordProvisioningError(
            "A bot token and Discord server ID are required for automatic setup."
        )

    bot_user = request_json("GET", "users/@me", token, timeout)
    channels = request_json("GET", f"guilds/{guild_id}/channels", token, timeout)
    roles = request_json("GET", f"guilds/{guild_id}/roles", token, timeout)
    if not isinstance(bot_user, Mapping) or not bot_user.get("id"):
        raise DiscordProvisioningError(
            "Discord returned an invalid bot account response."
        )
    bot_user_id = int(bot_user["id"])
    bot_member = request_json(
        "GET", f"guilds/{guild_id}/members/{bot_user_id}", token, timeout
    )
    if (
        not isinstance(channels, list)
        or not isinstance(roles, list)
        or not isinstance(bot_member, Mapping)
    ):
        raise DiscordProvisioningError("Discord returned invalid server setup data.")

    role_index = {int(role["id"]): role for role in roles if role.get("id")}
    bot_role_ids = {guild_id}
    bot_role_ids.update(int(role_id) for role_id in bot_member.get("roles", []))
    permissions = 0
    for role_id in bot_role_ids:
        permissions |= _permission_value(role_index.get(role_id, {}).get("permissions"))
    administrator = bool(permissions & PERMISSION_BITS["administrator"])
    required_permissions = (
        "manage_channels",
        "view_channel",
        "send_messages",
        "embed_links",
        "attach_files",
        "read_message_history",
        "manage_roles",
        "connect",
        "speak",
    )
    missing_permissions = [
        name
        for name in required_permissions
        if not administrator and not permissions & PERMISSION_BITS[name]
    ]
    if missing_permissions:
        labels = {
            "manage_channels": "Manage Channels",
            "view_channel": "View Channels",
            "send_messages": "Send Messages",
            "embed_links": "Embed Links",
            "attach_files": "Attach Files",
            "read_message_history": "Read Message History",
            "manage_roles": "Manage Roles",
            "connect": "Connect",
            "speak": "Speak",
        }
        raise DiscordProvisioningError(
            "The bot is missing permissions required for automatic setup: "
            + ", ".join(labels[name] for name in missing_permissions)
            + ". Update the bot role or reinvite it, then try again."
        )

    return _DiscordSnapshot(
        bot_user_id=bot_user_id,
        guild_id=guild_id,
        bot_permissions=permissions,
        channels=[dict(channel) for channel in channels],
        roles=[dict(role) for role in roles],
        bot_member=bot_member,
    )


def validate_provisioning_access(
    config: Mapping[str, Any],
    *,
    token: str,
    timeout: float = 15,
    request_json: RequestJson = _request_json,
) -> int:
    """Validate the token, guild membership, and Manage Channels permission."""
    return _fetch_snapshot(config, token, timeout, request_json).bot_user_id


def _overwrite(
    target_id: int,
    overwrite_type: int,
    *,
    allow: int = 0,
    deny: int = 0,
) -> dict[str, Any]:
    return {
        "id": str(target_id),
        "type": overwrite_type,
        "allow": str(allow),
        "deny": str(deny),
    }


def _staff_role_ids(snapshot: _DiscordSnapshot) -> list[int]:
    result = []
    for role in snapshot.roles:
        role_id = int(role.get("id", 0) or 0)
        permissions = _permission_value(role.get("permissions"))
        if role_id == snapshot.guild_id:
            continue
        if permissions & (
            PERMISSION_BITS["administrator"] | PERMISSION_BITS["manage_channels"]
        ):
            result.append(role_id)
    return result


def _private_overwrites(snapshot: _DiscordSnapshot) -> list[dict[str, Any]]:
    view_and_write = sum(
        PERMISSION_BITS[name]
        for name in (
            "manage_channels",
            "view_channel",
            "send_messages",
            "embed_links",
            "attach_files",
            "read_message_history",
        )
    )
    overwrites = [
        _overwrite(
            snapshot.guild_id,
            0,
            deny=PERMISSION_BITS["view_channel"],
        )
    ]
    overwrites.extend(
        _overwrite(role_id, 0, allow=view_and_write)
        for role_id in _staff_role_ids(snapshot)
    )
    overwrites.append(_overwrite(snapshot.bot_user_id, 1, allow=view_and_write))
    return overwrites


def _public_readonly_overwrites(
    snapshot: _DiscordSnapshot,
) -> list[dict[str, Any]]:
    public_deny = PERMISSION_BITS["send_messages"]
    writer_permissions = sum(
        PERMISSION_BITS[name]
        for name in (
            "view_channel",
            "send_messages",
            "embed_links",
            "attach_files",
            "read_message_history",
        )
    )
    overwrites = [_overwrite(snapshot.guild_id, 0, deny=public_deny)]
    overwrites.extend(
        _overwrite(role_id, 0, allow=writer_permissions)
        for role_id in _staff_role_ids(snapshot)
    )
    overwrites.append(_overwrite(snapshot.bot_user_id, 1, allow=writer_permissions))
    return overwrites


def _stats_overwrites(snapshot: _DiscordSnapshot) -> list[dict[str, Any]]:
    deny_voice = PERMISSION_BITS["connect"] | PERMISSION_BITS["speak"]
    bot_allow = PERMISSION_BITS["view_channel"] | PERMISSION_BITS["manage_channels"]
    return [
        _overwrite(snapshot.guild_id, 0, deny=deny_voice),
        _overwrite(snapshot.bot_user_id, 1, allow=bot_allow),
    ]


CHANNEL_OVERWRITE_MASK = sum(
    PERMISSION_BITS[name]
    for name in (
        "manage_channels",
        "view_channel",
        "send_messages",
        "embed_links",
        "attach_files",
        "read_message_history",
        "connect",
        "speak",
    )
)


def _ensure_required_overwrites(
    *,
    snapshot: _DiscordSnapshot,
    channel: dict[str, Any],
    required: list[dict[str, Any]],
    request_json: RequestJson,
    token: str,
    timeout: float,
) -> bool:
    """Update only Project L's required targets, preserving unrelated access."""
    current = channel.get("permission_overwrites")
    if not isinstance(current, list):
        current = []
        channel["permission_overwrites"] = current

    changed = False
    for required_overwrite in required:
        target_id = str(required_overwrite["id"])
        overwrite_type = int(required_overwrite.get("type", 0) or 0)
        existing = next(
            (
                overwrite
                for overwrite in current
                if str(overwrite.get("id", "")) == target_id
                and int(overwrite.get("type", 0) or 0) == overwrite_type
            ),
            None,
        )
        existing_allow = _permission_value(
            existing.get("allow") if isinstance(existing, Mapping) else 0
        )
        existing_deny = _permission_value(
            existing.get("deny") if isinstance(existing, Mapping) else 0
        )
        required_allow = _permission_value(required_overwrite.get("allow"))
        required_deny = _permission_value(required_overwrite.get("deny"))
        satisfied = (
            existing_allow & required_allow == required_allow
            and existing_deny & required_deny == required_deny
            and not existing_deny & required_allow
            and not existing_allow & required_deny
        )
        if satisfied:
            continue

        # Discord rejects overwrite permissions the bot cannot manage. Preserve
        # only known channel-scoped bits, then enforce the required allow/deny.
        manageable = CHANNEL_OVERWRITE_MASK & (
            CHANNEL_OVERWRITE_MASK
            if snapshot.bot_permissions & PERMISSION_BITS["administrator"]
            else snapshot.bot_permissions
        )
        allow = ((existing_allow & manageable) | required_allow) & ~required_deny
        deny = ((existing_deny & manageable) | required_deny) & ~required_allow
        payload = {
            "type": overwrite_type,
            "allow": str(allow),
            "deny": str(deny),
        }
        request_json(
            "PUT",
            f"channels/{_channel_id(channel)}/permissions/{target_id}",
            token,
            timeout,
            payload,
        )
        replacement = {"id": target_id, **payload}
        if existing is None:
            current.append(replacement)
        else:
            current[current.index(existing)] = replacement
        changed = True
    return changed


def _channel_id(channel: Mapping[str, Any]) -> int:
    return int(channel.get("id", 0) or 0)


def _is_type(channel: Mapping[str, Any], channel_type: int) -> bool:
    return int(channel.get("type", -1)) == channel_type


def _find_resource(
    channels: list[dict[str, Any]],
    *,
    channel_type: int,
    preferred_ids: list[int],
    name: str,
    marker: str | None,
    parent_id: int | None,
    name_prefixes: tuple[str, ...] = (),
) -> tuple[dict[str, Any] | None, bool]:
    for preferred_id in preferred_ids:
        if not preferred_id:
            continue
        channel = next(
            (
                candidate
                for candidate in channels
                if _channel_id(candidate) == preferred_id
                and _is_type(candidate, channel_type)
            ),
            None,
        )
        if channel is not None:
            channel_name = str(channel.get("name", ""))
            owned = (
                (marker is not None and channel.get("topic") == marker)
                or channel_name.casefold() == name.casefold()
                or any(channel_name.startswith(prefix) for prefix in name_prefixes)
            )
            return channel, owned

    if marker is not None:
        channel = next(
            (
                candidate
                for candidate in channels
                if _is_type(candidate, channel_type)
                and candidate.get("topic") == marker
            ),
            None,
        )
        if channel is not None:
            return channel, True

    candidates = []
    for candidate in channels:
        if not _is_type(candidate, channel_type):
            continue
        if (
            parent_id is not None
            and int(candidate.get("parent_id", 0) or 0) != parent_id
        ):
            continue
        candidate_name = str(candidate.get("name", ""))
        if candidate_name.casefold() == name.casefold() or any(
            candidate_name.startswith(prefix) for prefix in name_prefixes
        ):
            candidates.append(candidate)
    return (candidates[0], True) if candidates else (None, False)


def _ensure_resource(
    *,
    snapshot: _DiscordSnapshot,
    request_json: RequestJson,
    token: str,
    timeout: float,
    report: DiscordProvisioningReport,
    key: str,
    label: str,
    name: str,
    channel_type: int,
    preferred_ids: list[int] | None = None,
    parent_id: int | None = None,
    overwrites: list[dict[str, Any]] | None = None,
    topic: str | None = None,
    name_prefixes: tuple[str, ...] = (),
    secure_existing: bool = False,
) -> dict[str, Any]:
    marker = (
        f"{MANAGED_TOPIC_PREFIX}{key}" if topic is None and channel_type == 0 else topic
    )
    channel, owned = _find_resource(
        snapshot.channels,
        channel_type=channel_type,
        preferred_ids=preferred_ids or [],
        name=name,
        marker=marker,
        parent_id=parent_id,
        name_prefixes=name_prefixes,
    )

    if channel is None:
        payload: dict[str, Any] = {"name": name, "type": channel_type}
        if parent_id is not None:
            payload["parent_id"] = str(parent_id)
        if overwrites is not None:
            payload["permission_overwrites"] = overwrites
        if marker is not None:
            payload["topic"] = marker
        created = request_json(
            "POST",
            f"guilds/{snapshot.guild_id}/channels",
            token,
            timeout,
            payload,
        )
        if not isinstance(created, Mapping) or not created.get("id"):
            raise DiscordProvisioningError(
                f"Discord did not return the new {label} resource."
            )
        channel = dict(created)
        snapshot.channels.append(channel)
        report.created.append(label)
        return channel

    updates: dict[str, Any] = {}
    if owned:
        if channel_type != VOICE_CHANNEL and str(channel.get("name", "")) != name:
            updates["name"] = name
        if parent_id is not None and int(channel.get("parent_id", 0) or 0) != parent_id:
            updates["parent_id"] = str(parent_id)
        if marker is not None and channel.get("topic") != marker:
            updates["topic"] = marker
    metadata_updated = False
    if updates:
        updated = request_json(
            "PATCH",
            f"channels/{_channel_id(channel)}",
            token,
            timeout,
            updates,
        )
        if isinstance(updated, Mapping):
            channel.update(updated)
        else:
            channel.update(updates)
        metadata_updated = True

    permissions_updated = False
    if overwrites is not None and (owned or secure_existing):
        permissions_updated = _ensure_required_overwrites(
            snapshot=snapshot,
            channel=channel,
            required=overwrites,
            request_json=request_json,
            token=token,
            timeout=timeout,
        )

    if metadata_updated or permissions_updated:
        report.updated.append(label)
    else:
        report.reused.append(label)
    return channel


def _managed_resources(config: dict[str, Any]) -> dict[str, Any]:
    current = config.get(MANAGED_RESOURCES_KEY)
    if not isinstance(current, dict):
        current = {}
        config[MANAGED_RESOURCES_KEY] = current
    return current


def _mapping(config: dict[str, Any], key: str) -> dict[str, Any]:
    current = config.get(key)
    if not isinstance(current, dict):
        current = {}
        config[key] = current
    return current


def configuration_without_managed_channels(
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a validation copy that checks roles without requiring resources yet."""
    clean = deepcopy(dict(config))
    for key in (
        "welcome",
        "welcome_images",
        "invitechannel",
        "ytChannelId",
        "twChannelId",
        "transcripts",
        "logs",
        "modmail",
        "voicecategory",
    ):
        clean[key] = 0
    for key in ("tickets", "serverStatChannelIds", "logChannelIds"):
        if isinstance(clean.get(key), dict):
            for child in clean[key]:
                clean[key][child] = 0
    for friend in clean.get("friendTwitch", []):
        if isinstance(friend, dict):
            friend["friendChannelId"] = 0
    return clean


def provision_guild_resources(
    config: Mapping[str, Any],
    *,
    token: str,
    timeout: float = 15,
    request_json: RequestJson = _request_json,
) -> tuple[dict[str, Any], DiscordProvisioningReport]:
    """Create/reuse Project L's channels, secure them, and return updated IDs."""
    updated_config = deepcopy(dict(config))
    snapshot = _fetch_snapshot(updated_config, token, timeout, request_json)
    report = DiscordProvisioningReport()
    managed = _managed_resources(updated_config)
    updated_config["client_id"] = snapshot.bot_user_id

    private_overwrites = _private_overwrites(snapshot)
    public_overwrites = _public_readonly_overwrites(snapshot)
    stats_overwrites = _stats_overwrites(snapshot)

    community = _ensure_resource(
        snapshot=snapshot,
        request_json=request_json,
        token=token,
        timeout=timeout,
        report=report,
        key="community-category",
        label="community category",
        name="PROJECT L • COMMUNITY",
        channel_type=CATEGORY_CHANNEL,
        preferred_ids=[_permission_value(managed.get("communityCategory"))],
    )
    managed["communityCategory"] = _channel_id(community)

    staff = _ensure_resource(
        snapshot=snapshot,
        request_json=request_json,
        token=token,
        timeout=timeout,
        report=report,
        key="staff-category",
        label="staff category",
        name="PROJECT L • STAFF",
        channel_type=CATEGORY_CHANNEL,
        preferred_ids=[_permission_value(managed.get("staffCategory"))],
        overwrites=private_overwrites,
        secure_existing=True,
    )
    managed["staffCategory"] = _channel_id(staff)

    welcome = _ensure_resource(
        snapshot=snapshot,
        request_json=request_json,
        token=token,
        timeout=timeout,
        report=report,
        key="welcome",
        label="welcome channel",
        name="welcome",
        channel_type=TEXT_CHANNEL,
        preferred_ids=[_permission_value(updated_config.get("welcome"))],
        parent_id=_channel_id(community),
        overwrites=public_overwrites,
    )
    updated_config["welcome"] = _channel_id(welcome)
    # Welcome images are attached directly to the public welcome message now.
    updated_config["welcome_images"] = 0

    log_channels = _mapping(updated_config, "logChannelIds")
    logs = _ensure_resource(
        snapshot=snapshot,
        request_json=request_json,
        token=token,
        timeout=timeout,
        report=report,
        key="logs",
        label="staff log channel",
        name="project-l-logs",
        channel_type=TEXT_CHANNEL,
        preferred_ids=[
            _permission_value(updated_config.get("logs")),
            _permission_value(updated_config.get("invitechannel")),
            *[_permission_value(value) for value in log_channels.values()],
        ],
        parent_id=_channel_id(staff),
        overwrites=private_overwrites,
        secure_existing=True,
    )
    logs_id = _channel_id(logs)
    updated_config["logs"] = logs_id
    updated_config["invitechannel"] = logs_id
    for key in ("Message", "Ban_Unban", "Join_Leave", "Role", "Channel"):
        log_channels[key] = logs_id

    transcripts = _ensure_resource(
        snapshot=snapshot,
        request_json=request_json,
        token=token,
        timeout=timeout,
        report=report,
        key="transcripts",
        label="ticket transcript channel",
        name="ticket-transcripts",
        channel_type=TEXT_CHANNEL,
        preferred_ids=[_permission_value(updated_config.get("transcripts"))],
        parent_id=_channel_id(staff),
        overwrites=private_overwrites,
        secure_existing=True,
    )
    updated_config["transcripts"] = _channel_id(transcripts)

    category_specs = (
        (
            "modmail",
            "modmail category",
            "PROJECT L • MODMAIL",
            ("modmail",),
        ),
        (
            "help-tickets",
            "help ticket category",
            "PROJECT L • HELP TICKETS",
            ("tickets", "category_help"),
        ),
        (
            "suggestion-tickets",
            "suggestion ticket category",
            "PROJECT L • SUGGESTION TICKETS",
            ("tickets", "category_suggestions"),
        ),
        (
            "staff-reports",
            "staff report category",
            "PROJECT L • STAFF REPORTS",
            ("tickets", "category_report"),
        ),
        (
            "private-voice",
            "private voice category",
            "PROJECT L • PRIVATE VOICE",
            ("voicecategory",),
        ),
    )
    ticket_mapping = _mapping(updated_config, "tickets")
    for key, label, name, path in category_specs:
        if len(path) == 1:
            preferred = _permission_value(updated_config.get(path[0]))
        else:
            preferred = _permission_value(ticket_mapping.get(path[1]))
        category = _ensure_resource(
            snapshot=snapshot,
            request_json=request_json,
            token=token,
            timeout=timeout,
            report=report,
            key=key,
            label=label,
            name=name,
            channel_type=CATEGORY_CHANNEL,
            preferred_ids=[preferred],
            overwrites=private_overwrites,
            secure_existing=True,
        )
        if len(path) == 1:
            updated_config[path[0]] = _channel_id(category)
        else:
            ticket_mapping[path[1]] = _channel_id(category)

    stats_category = _ensure_resource(
        snapshot=snapshot,
        request_json=request_json,
        token=token,
        timeout=timeout,
        report=report,
        key="stats-category",
        label="server statistics category",
        name="PROJECT L • SERVER STATS",
        channel_type=CATEGORY_CHANNEL,
        preferred_ids=[_permission_value(managed.get("statsCategory"))],
        overwrites=stats_overwrites,
        secure_existing=True,
    )
    managed["statsCategory"] = _channel_id(stats_category)
    stats = _mapping(updated_config, "serverStatChannelIds")
    stat_specs = (
        ("totalMembers", "👥〢Members 0", ("👥〢Members",)),
        ("totalBots", "🤖〢Bots 0", ("🤖〢Bots",)),
        ("totalBoosts", "🚀〢Boosts 0", ("🚀〢",)),
        ("onlineMembers", "🟢〢Online 0", ("🟢〢Online",)),
        ("offlineMembers", "🔴〢Offline 0", ("🔴〢Offline",)),
    )
    for key, name, prefixes in stat_specs:
        stat_channel = _ensure_resource(
            snapshot=snapshot,
            request_json=request_json,
            token=token,
            timeout=timeout,
            report=report,
            key=f"stats-{key}",
            label=f"{key} statistics channel",
            name=name,
            channel_type=VOICE_CHANNEL,
            preferred_ids=[_permission_value(stats.get(key))],
            parent_id=_channel_id(stats_category),
            overwrites=stats_overwrites,
            name_prefixes=prefixes,
        )
        stats[key] = _channel_id(stat_channel)

    youtube_enabled = bool(str(updated_config.get("ytChannel", "")).strip())
    twitch_enabled = bool(
        str(updated_config.get("twitchChannelName", "")).strip()
        or updated_config.get("friendTwitch")
    )
    if youtube_enabled or twitch_enabled:
        friend_ids = [
            _permission_value(friend.get("friendChannelId"))
            for friend in updated_config.get("friendTwitch", [])
            if isinstance(friend, Mapping)
        ]
        alerts = _ensure_resource(
            snapshot=snapshot,
            request_json=request_json,
            token=token,
            timeout=timeout,
            report=report,
            key="alerts",
            label="social notification channel",
            name="project-l-alerts",
            channel_type=TEXT_CHANNEL,
            preferred_ids=[
                _permission_value(managed.get("alertsChannel")),
                _permission_value(updated_config.get("ytChannelId")),
                _permission_value(updated_config.get("twChannelId")),
                *friend_ids,
            ],
            parent_id=_channel_id(community),
            overwrites=public_overwrites,
        )
        alert_id = _channel_id(alerts)
        managed["alertsChannel"] = alert_id
        updated_config["ytChannelId"] = alert_id if youtube_enabled else 0
        updated_config["twChannelId"] = (
            alert_id if str(updated_config.get("twitchChannelName", "")).strip() else 0
        )
        for friend in updated_config.get("friendTwitch", []):
            if isinstance(friend, dict):
                friend["friendChannelId"] = alert_id
    else:
        updated_config["ytChannelId"] = 0
        updated_config["twChannelId"] = 0

    managed["provisioningVersion"] = AUTOMATIC_SETUP_VERSION
    return updated_config, report
