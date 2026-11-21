"""Pure configuration editing helpers used by the graphical setup wizard."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from config_loader import (
    AUTOMATIC_SETUP_VERSION,
    CONFIG_PATH,
    ROLES_PATH,
    ConfigError,
    load_config,
    load_role_ids,
    missing_required_settings,
)
from youtube_notifications import YouTubeSourceError, normalize_youtube_source

EXAMPLE_CONFIG_PATH = CONFIG_PATH.with_name("config.example.json")

NESTED_ID_FIELDS = {
    "tickets.category_help": ("tickets", "category_help"),
    "tickets.category_suggestions": ("tickets", "category_suggestions"),
    "tickets.category_report": ("tickets", "category_report"),
    "serverStatChannelIds.totalMembers": ("serverStatChannelIds", "totalMembers"),
    "serverStatChannelIds.totalBots": ("serverStatChannelIds", "totalBots"),
    "serverStatChannelIds.totalBoosts": ("serverStatChannelIds", "totalBoosts"),
    "serverStatChannelIds.onlineMembers": ("serverStatChannelIds", "onlineMembers"),
    "serverStatChannelIds.offlineMembers": ("serverStatChannelIds", "offlineMembers"),
    "logChannelIds.Message": ("logChannelIds", "Message"),
    "logChannelIds.Ban_Unban": ("logChannelIds", "Ban_Unban"),
    "logChannelIds.Join_Leave": ("logChannelIds", "Join_Leave"),
    "logChannelIds.Role": ("logChannelIds", "Role"),
    "logChannelIds.Channel": ("logChannelIds", "Channel"),
}

MANAGED_CHANNEL_ID_FIELDS = {
    "welcome",
    "welcome_images",
    "invitechannel",
    "ytChannelId",
    "twChannelId",
    "transcripts",
    "logs",
    "modmail",
    "voicecategory",
}


def _merge_defaults(current: Any, defaults: Any) -> Any:
    if not isinstance(current, dict) or not isinstance(defaults, dict):
        return deepcopy(current)
    merged = deepcopy(current)
    for key, default_value in defaults.items():
        if key not in merged:
            merged[key] = deepcopy(default_value)
        elif isinstance(default_value, dict):
            merged[key] = _merge_defaults(merged[key], default_value)
    return merged


def load_editable_config(
    config_path: Path = CONFIG_PATH,
    example_path: Path = EXAMPLE_CONFIG_PATH,
) -> dict[str, Any]:
    """Load raw editable values without applying secret environment overrides."""
    try:
        defaults = json.loads(example_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"Could not read configuration template {example_path}: {exc}"
        ) from exc

    if not config_path.exists():
        return defaults
    try:
        current = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"Could not read existing configuration {config_path}: {exc}"
        ) from exc
    if not isinstance(current, dict):
        raise ConfigError(f"{config_path} must contain one JSON object.")
    return _merge_defaults(current, defaults)


def parse_discord_id(value: object, label: str, *, required: bool = False) -> int:
    text = str(value or "").strip()
    if not text or text == "0":
        if required:
            raise ConfigError(f"{label} is required.")
        return 0

    mention = re.fullmatch(r"<(?:#|@!?|@&)([0-9]+)>", text)
    if mention:
        text = mention.group(1)
    if not re.fullmatch(r"[0-9]+", text) or int(text) <= 0:
        raise ConfigError(
            f"{label} must be a positive numeric Discord ID. You can also paste a Discord mention."
        )
    return int(text)


def parse_role_ids_text(value: str) -> list[int]:
    tokens = [token for token in re.split(r"[\s,]+", value.strip()) if token]
    role_ids = [
        parse_discord_id(token, f"Reaction role item {index + 1}", required=True)
        for index, token in enumerate(tokens)
    ]
    if len(set(role_ids)) != len(role_ids):
        raise ConfigError("Reaction role IDs must not contain duplicates.")
    if len(role_ids) > 25:
        raise ConfigError("Discord menus support at most 25 reaction roles.")
    return role_ids


def format_role_ids(role_ids: list[int]) -> str:
    return "\n".join(str(role_id) for role_id in role_ids)


def parse_twitch_friends_text(value: str) -> list[dict[str, Any]]:
    """Parse friend stream usernames, while accepting the legacy ID/name format."""
    friends: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",", maxsplit=1)]
        if len(parts) == 1:
            channel_id = 0
            twitch_name = parts[0].lstrip("@").strip()
        else:
            channel_id = parse_discord_id(
                parts[0], f"Twitch friend line {line_number} channel", required=True
            )
            twitch_name = parts[1].lstrip("@").strip()
        if not twitch_name:
            raise ConfigError(
                f"Twitch friend line {line_number} must contain a Twitch username."
            )
        normalized_name = twitch_name.casefold()
        if normalized_name in seen_names:
            raise ConfigError(
                f"Twitch friend line {line_number} repeats Twitch username {twitch_name}."
            )
        seen_names.add(normalized_name)
        friends.append(
            {
                "friendChannelId": channel_id,
                "friendChannel": twitch_name,
            }
        )
    return friends


def format_twitch_friends(friends: list[dict[str, Any]]) -> str:
    return "\n".join(
        str(friend.get("friendChannel", ""))
        for friend in friends
        if isinstance(friend, Mapping) and friend.get("friendChannel")
    )


def _mapping_value(config: Mapping[str, Any], path: tuple[str, str]) -> Any:
    parent = config.get(path[0], {})
    return parent.get(path[1], 0) if isinstance(parent, Mapping) else 0


def _safe_existing_id(value: object) -> int:
    """Treat malformed hidden legacy IDs as missing so setup can replace them."""
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def flatten_config(config: Mapping[str, Any], role_ids: list[int]) -> dict[str, Any]:
    """Convert nested JSON data into values consumed by the GUI controls."""
    values: dict[str, Any] = {
        key: config.get(key, "")
        for key in (
            "token",
            "guildId",
            "client_id",
            "welcome",
            "welcome_images",
            "invitechannel",
            "ytChannelId",
            "twChannelId",
            "verified",
            "transcripts",
            "logs",
            "modmail",
            "voicecategory",
            "streamRole",
            "ytChannel",
            "twitchChannelName",
            "twClientId",
            "twClientsecret",
        )
    }
    values["kickMemberFunction"] = bool(config.get("kickMemberFunction", False))
    values["logMessageContent"] = bool(config.get("logMessageContent", False))
    for flat_key, path in NESTED_ID_FIELDS.items():
        values[flat_key] = _mapping_value(config, path)
    friends = config.get("friendTwitch", [])
    values["friendTwitch"] = format_twitch_friends(
        friends if isinstance(friends, list) else []
    )
    values["roleIds"] = format_role_ids(role_ids)
    return values


def build_configuration(
    values: Mapping[str, Any],
    base_config: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    auto_managed_channels: bool = False,
) -> tuple[dict[str, Any], list[int]]:
    """Validate GUI values and return JSON-ready configuration objects."""
    environment = os.environ if environ is None else environ
    config = deepcopy(dict(base_config))
    errors: list[str] = []

    for parent_name, _child_name in NESTED_ID_FIELDS.values():
        if not isinstance(config.get(parent_name), dict):
            config[parent_name] = {}

    config["token"] = str(values.get("token", "")).strip()
    for field in ("ytChannel", "twitchChannelName", "twClientId", "twClientsecret"):
        config[field] = str(values.get(field, "")).strip()
    config["twitchChannelName"] = config["twitchChannelName"].lstrip("@").strip()

    id_fields = (
        "guildId",
        "client_id",
        "welcome",
        "welcome_images",
        "invitechannel",
        "ytChannelId",
        "twChannelId",
        "verified",
        "transcripts",
        "logs",
        "modmail",
        "voicecategory",
        "streamRole",
    )
    if auto_managed_channels:
        id_fields = tuple(
            field for field in id_fields if field not in MANAGED_CHANNEL_ID_FIELDS
        )
    for field in id_fields:
        try:
            config[field] = parse_discord_id(
                values.get(field, ""),
                "Discord server ID" if field == "guildId" else field,
                required=field == "guildId",
            )
        except ConfigError as exc:
            errors.append(str(exc))

    if not auto_managed_channels:
        for flat_key, path in NESTED_ID_FIELDS.items():
            try:
                parsed = parse_discord_id(values.get(flat_key, ""), flat_key)
                config.setdefault(path[0], {})[path[1]] = parsed
            except ConfigError as exc:
                errors.append(str(exc))

    config["kickMemberFunction"] = bool(values.get("kickMemberFunction", False))
    config["logMessageContent"] = bool(values.get("logMessageContent", False))

    try:
        config["friendTwitch"] = parse_twitch_friends_text(
            str(values.get("friendTwitch", ""))
        )
        if auto_managed_channels:
            existing_friends = base_config.get("friendTwitch", [])
            existing_ids = {
                str(friend.get("friendChannel", "")).casefold(): _safe_existing_id(
                    friend.get("friendChannelId")
                )
                for friend in existing_friends
                if isinstance(friend, Mapping)
            }
            for friend in config["friendTwitch"]:
                if not friend["friendChannelId"]:
                    friend["friendChannelId"] = existing_ids.get(
                        friend["friendChannel"].casefold(), 0
                    )
    except ConfigError as exc:
        errors.append(str(exc))
        config["friendTwitch"] = []

    try:
        role_ids = parse_role_ids_text(str(values.get("roleIds", "")))
    except ConfigError as exc:
        errors.append(str(exc))
        role_ids = []

    effective_config = deepcopy(config)
    if environment.get("DISCORD_BOT_TOKEN", "").strip():
        effective_config["token"] = environment["DISCORD_BOT_TOKEN"].strip()
    missing = missing_required_settings(effective_config)
    if missing:
        errors.extend(f"Required setting missing: {setting}." for setting in missing)

    youtube_name = config["ytChannel"]
    youtube_output = config.get("ytChannelId", 0)
    if not auto_managed_channels and bool(youtube_name) != bool(youtube_output):
        errors.append(
            "YouTube notifications need both a YouTube channel and a Discord output channel ID."
        )
    elif youtube_name:
        try:
            config["ytChannel"] = normalize_youtube_source(youtube_name)
        except YouTubeSourceError as exc:
            errors.append(str(exc))

    primary_twitch_name = config["twitchChannelName"]
    primary_twitch_output = config.get("twChannelId", 0)
    if not auto_managed_channels and bool(primary_twitch_name) != bool(
        primary_twitch_output
    ):
        errors.append(
            "Primary Twitch notifications need both a Twitch username and a Discord output channel ID."
        )
    friend_names = {
        str(friend.get("friendChannel", "")).casefold()
        for friend in config["friendTwitch"]
    }
    if primary_twitch_name and primary_twitch_name.casefold() in friend_names:
        errors.append(
            "The primary Twitch username must not also appear in Friend Streams."
        )

    twitch_is_used = bool(
        (primary_twitch_name and (primary_twitch_output or auto_managed_channels))
        or config["friendTwitch"]
    )
    effective_twitch_client = (
        config["twClientId"] or environment.get("TWITCH_CLIENT_ID", "").strip()
    )
    effective_twitch_secret = (
        config["twClientsecret"] or environment.get("TWITCH_CLIENT_SECRET", "").strip()
    )
    if twitch_is_used and not (effective_twitch_client and effective_twitch_secret):
        errors.append(
            "Twitch notifications require a client ID and client secret, either here or in environment variables."
        )

    if config["kickMemberFunction"] and not config.get("verified"):
        errors.append("The unverified-member cleanup requires a Verified Role ID.")
    if (
        not auto_managed_channels
        and config.get("welcome_images")
        and not config.get("welcome")
    ):
        errors.append("A Welcome Image Channel ID also requires a Welcome Channel ID.")

    if errors:
        raise ConfigError("\n".join(f"• {error}" for error in errors))
    return config, role_ids


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, indent=2, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def save_configuration(
    config: Mapping[str, Any],
    role_ids: list[int],
    *,
    config_path: Path = CONFIG_PATH,
    roles_path: Path = ROLES_PATH,
) -> None:
    """Persist validated configuration using replace-on-success writes."""
    _write_json_atomic(config_path, dict(config))
    _write_json_atomic(roles_path, role_ids)


def configuration_status(
    config_path: Path = CONFIG_PATH,
    roles_path: Path = ROLES_PATH,
) -> tuple[bool, list[str]]:
    """Return whether configuration can start and safe user-facing reasons."""
    reasons: list[str] = []
    try:
        config = load_config(config_path)
        reasons.extend(missing_required_settings(config))
        managed = config.get("managedResources", {})
        try:
            provisioning_version = int(
                managed.get("provisioningVersion", 0)
                if isinstance(managed, Mapping)
                else 0
            )
        except (TypeError, ValueError):
            provisioning_version = 0
        required_resource_ids = [
            config.get("welcome"),
            config.get("invitechannel"),
            config.get("verified"),
            config.get("transcripts"),
            config.get("logs"),
            config.get("modmail"),
            config.get("voicecategory"),
            config.get("streamRole"),
            *(config.get("tickets") or {}).values(),
            *(config.get("serverStatChannelIds") or {}).values(),
            *(config.get("logChannelIds") or {}).values(),
        ]
        if isinstance(managed, Mapping):
            required_resource_ids.extend(
                managed.get(key)
                for key in (
                    "communityCategory",
                    "staffCategory",
                    "statsCategory",
                    "panelsChannel",
                )
            )
        else:
            required_resource_ids.append(0)
        resources_incomplete = any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in required_resource_ids
        )
        if provisioning_version < AUTOMATIC_SETUP_VERSION or resources_incomplete:
            reasons.append(
                "automatic Discord roles, channels, categories, and panels setup "
                "has not been completed"
            )
    except ConfigError as exc:
        reasons.append(str(exc))
    try:
        load_role_ids(roles_path)
    except ConfigError as exc:
        reasons.append(str(exc))
    return not reasons, reasons
