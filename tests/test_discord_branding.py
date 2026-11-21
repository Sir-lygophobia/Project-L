import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from discord_branding import (
    BUNDLED_EMOJIS,
    MAX_EMOJI_BYTES,
    install_bundled_emojis,
)


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def valid_png(width: int = 1, height: int = 1) -> bytes:
    # One transparent RGBA scanline, sufficient for strict structure/CRC tests.
    raw = b"\x00" + (b"\x00\x00\x00\x00" * width)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0),
        )
        + png_chunk(b"IDAT", zlib.compress(raw * height))
        + png_chunk(b"IEND", b"")
    )


class FakeEmojiApi:
    def __init__(self, existing=(), failing=()):
        self.emojis = [
            {"id": str(index), "name": name}
            for index, name in enumerate(existing, start=1)
        ]
        self.failing = set(failing)
        self.calls = []

    def __call__(self, method, path, token, timeout, payload=None):
        self.calls.append((method, path, payload))
        if method == "GET":
            return list(self.emojis)
        if method == "POST":
            if payload["name"] in self.failing:
                raise RuntimeError("no emoji slots")
            emoji = {"id": str(len(self.emojis) + 1), "name": payload["name"]}
            self.emojis.append(emoji)
            return emoji
        raise AssertionError(f"unexpected request: {method} {path}")


class DiscordBrandingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.emoji_dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, filename, data=None):
        (self.emoji_dir / filename).write_bytes(data or valid_png())

    def test_missing_optional_directory_makes_no_api_call(self):
        api = FakeEmojiApi()
        report = install_bundled_emojis(
            100,
            token="token",
            request_json=api,
            emoji_dir=self.emoji_dir / "missing",
        )
        self.assertEqual(report.created, [])
        self.assertEqual(report.warnings, [])
        self.assertEqual(api.calls, [])

    def test_creates_only_missing_stable_names_and_is_idempotent(self):
        first_name, first_file = BUNDLED_EMOJIS[0]
        second_name, second_file = BUNDLED_EMOJIS[1]
        self.write(first_file)
        self.write(second_file)
        api = FakeEmojiApi(existing=[first_name])

        first = install_bundled_emojis(
            100,
            token="token",
            request_json=api,
            emoji_dir=self.emoji_dir,
        )
        second = install_bundled_emojis(
            100,
            token="token",
            request_json=api,
            emoji_dir=self.emoji_dir,
        )

        self.assertEqual(first.existing, [first_name])
        self.assertEqual(first.created, [second_name])
        self.assertEqual(second.created, [])
        self.assertEqual(second.existing, [first_name, second_name])
        posts = [call for call in api.calls if call[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0][2]["image"].startswith("data:image/png;base64,"))

    def test_invalid_and_oversized_pngs_are_warnings_not_uploads(self):
        _first_name, first_file = BUNDLED_EMOJIS[0]
        _second_name, second_file = BUNDLED_EMOJIS[1]
        self.write(first_file, b"not a png")
        self.write(second_file, b"\x89PNG\r\n\x1a\n" + b"x" * MAX_EMOJI_BYTES)
        api = FakeEmojiApi()

        report = install_bundled_emojis(
            100,
            token="token",
            request_json=api,
            emoji_dir=self.emoji_dir,
        )

        self.assertEqual(report.created, [])
        self.assertEqual(len(report.warnings), 2)
        self.assertEqual(api.calls, [])

    def test_one_rejected_emoji_does_not_block_the_rest(self):
        first_name, first_file = BUNDLED_EMOJIS[0]
        second_name, second_file = BUNDLED_EMOJIS[1]
        self.write(first_file)
        self.write(second_file)
        api = FakeEmojiApi(failing=[first_name])

        report = install_bundled_emojis(
            100,
            token="token",
            request_json=api,
            emoji_dir=self.emoji_dir,
        )

        self.assertEqual(report.created, [second_name])
        self.assertEqual(len(report.warnings), 1)
        self.assertIn(first_name, report.warnings[0])

    def test_corrupt_crc_is_rejected(self):
        _name, filename = BUNDLED_EMOJIS[0]
        data = bytearray(valid_png())
        data[-1] ^= 0xFF
        self.write(filename, bytes(data))
        api = FakeEmojiApi()

        report = install_bundled_emojis(
            100,
            token="token",
            request_json=api,
            emoji_dir=self.emoji_dir,
        )

        self.assertEqual(report.created, [])
        self.assertIn("integrity", report.warnings[0])


if __name__ == "__main__":
    unittest.main()
