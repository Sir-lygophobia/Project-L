"""Small, testable helpers shared by automatic and manual giveaways."""

from __future__ import annotations

import re


_WINNER_COUNT = re.compile(r"\*\*Winners:\*\*\s*(\d+)")


def parse_winner_count(description: str | None) -> int:
    """Return the configured winner count from a Project L giveaway embed."""
    match = _WINNER_COUNT.search(description or "")
    if match is None:
        raise ValueError("Giveaway embed does not contain a valid winner count.")
    winners = int(match.group(1))
    if winners < 1:
        raise ValueError("Giveaway winner count must be at least one.")
    return winners


def ended_description(description: str | None) -> str:
    """Return a giveaway description with its entry prompt replaced by an end marker."""
    prefix = (description or "").split("React with", 1)[0].rstrip()
    return f"{prefix}\n\nGiveaway ended." if prefix else "Giveaway ended."


def chunk_display_values(values: list[str], limit: int = 1024) -> list[str]:
    """Join display values into chunks that fit a Discord embed field."""
    if limit < 1:
        raise ValueError("limit must be positive")

    chunks: list[str] = []
    current = ""
    for value in values:
        clean_value = str(value)
        if len(clean_value) > limit:
            clean_value = clean_value[: max(1, limit - 1)] + "…"
        candidate = f"{current}, {clean_value}" if current else clean_value
        if current and len(candidate) > limit:
            chunks.append(current)
            current = clean_value
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
