"""Configuration loading and validation for Project L."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "Data" / "config.json"
ROLES_PATH = BASE_DIR / "Data" / "roles.json"
AUTOMATIC_SETUP_VERSION = 1

ID_FIELDS = (
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

STRING_FIELDS = (
    "token",
    "ytChannel",
    "twitchChannelName",
    "twClientId",
    "twClientsecret",
)

MAPPING_FIELDS = ("tickets", "serverStatChannelIds", "logChannelIds")

MAPPING_KEYS = {
    "tickets": {"category_help", "category_suggestions", "category_report"},
    "serverStatChannelIds": {
        "totalMembers",
        "totalBots",
        "totalBoosts",
        "onlineMembers",
        "offlineMembers",
    },
    "logChannelIds": {"Message", "Ban_Unban", "Join_Leave", "Role", "Channel"},
}


class ConfigError(RuntimeError):
    """Raised when the bot configuration cannot be used safely."""


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or any(
        word in normalized
        for word in ("add ", "your ", " here", "placeholder", "example")
    )


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Load config JSON, apply the token environment override, and check types."""
    try:
        with path.open("r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except FileNotFoundError as exc:
        raise ConfigError(
            f"Missing {path}. Copy Data/config.example.json to Data/config.json first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}): "
            f"{exc.msg}."
        ) from exc

    if not isinstance(config, dict):
        raise ConfigError(f"{path} must contain one JSON object.")

    required_fields = set(ID_FIELDS + STRING_FIELDS + MAPPING_FIELDS) | {
        "kickMemberFunction",
        "friendTwitch",
    }
    missing = sorted(required_fields - config.keys())
    if missing:
        raise ConfigError(f"Missing configuration fields: {', '.join(missing)}.")

    for field in ID_FIELDS:
        value = config[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(
                f'Configuration field "{field}" must be a non-negative integer.'
            )

    for field in STRING_FIELDS:
        if not isinstance(config[field], str):
            raise ConfigError(f'Configuration field "{field}" must be a string.')

    if not isinstance(config["kickMemberFunction"], bool):
        raise ConfigError(
            'Configuration field "kickMemberFunction" must be true or false.'
        )

    if not isinstance(config["friendTwitch"], list):
        raise ConfigError('Configuration field "friendTwitch" must be a list.')

    for field in MAPPING_FIELDS:
        if not isinstance(config[field], dict):
            raise ConfigError(f'Configuration field "{field}" must be an object.')
        missing_nested = sorted(MAPPING_KEYS[field] - config[field].keys())
        if missing_nested:
            raise ConfigError(
                f'Configuration object "{field}" is missing: '
                f"{', '.join(missing_nested)}."
            )
        invalid_values = [
            key
            for key, value in config[field].items()
            if isinstance(value, bool) or not isinstance(value, int) or value < 0
        ]
        if invalid_values:
            raise ConfigError(
                f'Every value in "{field}" must be a non-negative integer; '
                f"check: {', '.join(invalid_values)}."
            )

    for index, friend in enumerate(config["friendTwitch"]):
        if not isinstance(friend, dict):
            raise ConfigError(f"friendTwitch item {index + 1} must be an object.")
        channel_id = friend.get("friendChannelId", 0)
        channel_name = friend.get("friendChannel", "")
        if (
            isinstance(channel_id, bool)
            or not isinstance(channel_id, int)
            or channel_id < 0
            or not isinstance(channel_name, str)
        ):
            raise ConfigError(
                f"friendTwitch item {index + 1} needs an integer friendChannelId "
                "and string friendChannel."
            )

    environment_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if environment_token:
        config["token"] = environment_token
    twitch_client_id = os.environ.get("TWITCH_CLIENT_ID", "").strip()
    if twitch_client_id:
        config["twClientId"] = twitch_client_id
    twitch_client_secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    if twitch_client_secret:
        config["twClientsecret"] = twitch_client_secret

    return config


def load_role_ids(path: Path = ROLES_PATH) -> list[int]:
    """Load and validate the reaction-role menu configuration."""
    try:
        with path.open("r", encoding="utf-8") as roles_file:
            role_ids = json.load(roles_file)
    except FileNotFoundError as exc:
        raise ConfigError(
            f"Missing {path}. Copy Data/roles.example.json to Data/roles.json first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}): "
            f"{exc.msg}."
        ) from exc

    if not isinstance(role_ids, list):
        raise ConfigError(f"{path} must contain a JSON list of Discord role IDs.")
    invalid = [
        index + 1
        for index, role_id in enumerate(role_ids)
        if isinstance(role_id, bool) or not isinstance(role_id, int) or role_id <= 0
    ]
    if invalid:
        raise ConfigError(
            f"Every entry in {path} must be a positive integer; check item(s): "
            f"{', '.join(map(str, invalid))}."
        )
    if len(set(role_ids)) != len(role_ids):
        raise ConfigError(f"{path} contains duplicate role IDs.")
    return role_ids


def missing_required_settings(config: dict[str, Any]) -> list[str]:
    """Return settings required to connect, without exposing their values."""
    missing: list[str] = []
    if _is_placeholder(config["token"]):
        missing.append("token (or the DISCORD_BOT_TOKEN environment variable)")
    if config["guildId"] <= 0:
        missing.append("guildId")
    return missing


def configuration_warnings(config: dict[str, Any]) -> list[str]:
    """Describe optional features that are currently disabled."""
    warnings: list[str] = []
    if not (config["ytChannel"] and config["ytChannelId"]):
        warnings.append("YouTube notifications are disabled")
    if not (
        config["twClientId"]
        and config["twClientsecret"]
        and (config["twChannelId"] or config["friendTwitch"])
    ):
        warnings.append("Twitch notifications are disabled")
    if not all(config["serverStatChannelIds"].values()):
        warnings.append("one or more server-stat channels are disabled")
    if not all(config["logChannelIds"].values()):
        warnings.append("one or more logging channels are disabled")
    return warnings
