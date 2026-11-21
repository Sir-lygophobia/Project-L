"""Install optional bundled Project L server branding without overwriting it."""

from __future__ import annotations

import base64
import struct
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


RequestJson = Callable[..., Any]

ART_DIR = Path(__file__).resolve().parent / "Data" / "art"
EMOJI_DIR = ART_DIR / "emojis"
MAX_EMOJI_BYTES = 256 * 1024
MAX_EMOJI_DIMENSION = 512
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Stable names make setup idempotent. Setup creates a missing expression but
# deliberately never edits or deletes an existing server emoji with this name.
BUNDLED_EMOJIS = (
    ("projectl_verified", "projectl_verified.png"),
    ("projectl_ticket", "projectl_ticket.png"),
    ("projectl_voice", "projectl_voice.png"),
    ("projectl_giveaway", "projectl_giveaway.png"),
    ("projectl_coin", "projectl_coin.png"),
)


@dataclass
class EmojiInstallReport:
    """Best-effort results from installing the optional server emoji pack."""

    created: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _validate_png(path: Path) -> tuple[bytes | None, str | None]:
    """Return validated PNG bytes or a human-readable local-art warning."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, f"could not read {path.name}: {exc}"
    if not size:
        return None, f"{path.name} is empty"
    if size > MAX_EMOJI_BYTES:
        return None, (
            f"{path.name} is {size:,} bytes; Discord emoji files must be at most "
            f"{MAX_EMOJI_BYTES:,} bytes"
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"could not read {path.name}: {exc}"
    if not data.startswith(PNG_SIGNATURE):
        return None, f"{path.name} is not a PNG file"

    offset = len(PNG_SIGNATURE)
    saw_header = False
    saw_image_data = False
    saw_end = False
    width = height = 0
    while offset < len(data):
        if offset + 12 > len(data):
            return None, f"{path.name} has a truncated PNG chunk"
        chunk_length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_end = offset + 12 + chunk_length
        if chunk_end > len(data):
            return None, f"{path.name} has an invalid PNG chunk length"
        chunk_data = data[offset + 8 : offset + 8 + chunk_length]
        expected_crc = struct.unpack(">I", data[offset + 8 + chunk_length : chunk_end])[
            0
        ]
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            return None, f"{path.name} failed its PNG integrity check"

        if not saw_header:
            if chunk_type != b"IHDR" or chunk_length != 13:
                return None, f"{path.name} has an invalid PNG header"
            width, height = struct.unpack(">II", chunk_data[:8])
            saw_header = True
        elif chunk_type == b"IHDR":
            return None, f"{path.name} has more than one PNG header"
        if chunk_type == b"IDAT":
            saw_image_data = True
        if chunk_type == b"IEND":
            if chunk_length:
                return None, f"{path.name} has an invalid PNG ending"
            saw_end = True
            offset = chunk_end
            break
        offset = chunk_end

    if not (saw_header and saw_image_data and saw_end) or offset != len(data):
        return None, f"{path.name} is not a complete PNG file"
    if not (1 <= width <= MAX_EMOJI_DIMENSION and 1 <= height <= MAX_EMOJI_DIMENSION):
        return None, (
            f"{path.name} is {width}x{height}; bundled emojis must be no larger "
            f"than {MAX_EMOJI_DIMENSION}x{MAX_EMOJI_DIMENSION}"
        )
    return data, None


def install_bundled_emojis(
    guild_id: int,
    *,
    token: str,
    request_json: RequestJson,
    timeout: float = 15,
    emoji_dir: Path = EMOJI_DIR,
) -> EmojiInstallReport:
    """Create missing bundled emojis and leave all existing expressions intact."""
    report = EmojiInstallReport()
    valid_assets: list[tuple[str, bytes]] = []
    for name, filename in BUNDLED_EMOJIS:
        path = emoji_dir / filename
        if not path.is_file():
            continue
        data, warning = _validate_png(path)
        if warning:
            report.warnings.append(warning)
        elif data is not None:
            valid_assets.append((name, data))

    # The art pack is optional. Avoid even an API call on installations that do
    # not include emoji files or where every local file failed validation.
    if not valid_assets:
        return report

    try:
        current = request_json(
            "GET", f"guilds/{int(guild_id)}/emojis", token, timeout
        )
    except Exception as exc:  # best-effort optional branding boundary
        report.warnings.append(f"could not inspect server emojis: {exc}")
        return report
    if not isinstance(current, list):
        report.warnings.append("Discord returned invalid server emoji data")
        return report
    existing_names = {
        str(emoji.get("name", "")).casefold()
        for emoji in current
        if isinstance(emoji, Mapping)
    }

    for name, data in valid_assets:
        if name.casefold() in existing_names:
            report.existing.append(name)
            continue
        payload = {
            "name": name,
            "image": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
        }
        try:
            created = request_json(
                "POST",
                f"guilds/{int(guild_id)}/emojis",
                token,
                timeout,
                payload,
            )
        except Exception as exc:  # one optional emoji must not block setup
            report.warnings.append(f"could not create :{name}: {exc}")
            continue
        if not isinstance(created, Mapping) or not created.get("id"):
            report.warnings.append(
                f"Discord did not confirm creation of :{name}:"
            )
            continue
        report.created.append(name)
        existing_names.add(name.casefold())
    return report
