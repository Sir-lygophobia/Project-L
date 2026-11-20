import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from Commands.panels import (
    is_self_role_panel_message,
    normalized_panel_title,
    repair_self_role_panel_images,
)


class PanelHelperTests(unittest.TestCase):
    def test_decorated_self_role_titles_are_normalized(self):
        self.assertEqual(normalized_panel_title(" ** Game Roles** "), "game roles")

    def test_panel_match_requires_known_title_components_and_bot_author(self):
        message = SimpleNamespace(
            author=SimpleNamespace(id=10),
            embeds=[discord.Embed(title="**Color Roles**")],
            components=[object()],
        )
        self.assertTrue(is_self_role_panel_message(message, 10))
        self.assertFalse(is_self_role_panel_message(message, 11))
        message.components = []
        self.assertFalse(is_self_role_panel_message(message, 10))


class PanelRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_repair_uploads_a_same_message_attachment(self):
        edits = []

        async def edit(**kwargs):
            edits.append(kwargs)

        message = SimpleNamespace(
            id=123,
            author=SimpleNamespace(id=10),
            embeds=[discord.Embed(title="Gender Roles")],
            components=[object()],
            attachments=[],
            edit=edit,
        )

        class Channel:
            async def history(self, **_kwargs):
                yield message

        repaired, failures = await repair_self_role_panel_images(Channel(), 10)

        self.assertEqual(repaired, 1)
        self.assertEqual(failures, [])
        self.assertEqual(
            edits[0]["embed"].image.url,
            "attachment://self_roles-123.gif",
        )
        self.assertEqual(edits[0]["attachments"][0].filename, "self_roles-123.gif")

    async def test_repair_preserves_unrelated_attachments_and_components(self):
        unrelated = SimpleNamespace(filename="rules.pdf")
        components = [object(), object()]
        message = SimpleNamespace(
            id=456,
            author=SimpleNamespace(id=10),
            embeds=[discord.Embed(title="Platform Roles")],
            components=components,
            attachments=[unrelated],
            edit=AsyncMock(),
        )

        class Channel:
            async def history(self, **_kwargs):
                yield message

        repaired, failures = await repair_self_role_panel_images(Channel(), 10)

        self.assertEqual((repaired, failures), (1, []))
        sent = message.edit.await_args.kwargs
        self.assertIs(sent["attachments"][0], unrelated)
        self.assertEqual(sent["attachments"][1].filename, "self_roles-456.gif")
        self.assertIs(message.components, components)
        self.assertNotIn("view", sent)

    async def test_repair_is_idempotent_when_attachment_is_current(self):
        embed = discord.Embed(title="Server Notifications")
        embed.set_image(url="attachment://self_roles-789.gif")
        message = SimpleNamespace(
            id=789,
            author=SimpleNamespace(id=10),
            embeds=[embed],
            components=[object()],
            attachments=[SimpleNamespace(filename="self_roles-789.gif")],
            edit=AsyncMock(),
        )

        class Channel:
            async def history(self, **_kwargs):
                yield message

        repaired, failures = await repair_self_role_panel_images(Channel(), 10)

        self.assertEqual((repaired, failures), (0, []))
        message.edit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
