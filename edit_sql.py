"""Idempotently initialize the sticky-message table.

This utility is kept for compatibility with older Project L instructions. It
never drops an existing table or deletes sticky-message data.
"""

import sqlite3
from pathlib import Path


DATABASE_PATH = Path(__file__).resolve().parent / "sticky.db"


def main():
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS infos_salos ("
            "id_salon INTEGER PRIMARY KEY, contenu TEXT, message_limit INTEGER)"
        )
    print(f"Sticky-message database ready: {DATABASE_PATH}")


if __name__ == "__main__":
    main()
