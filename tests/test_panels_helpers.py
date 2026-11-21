import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from Commands.panels import (
    ensure_automatic_panels,
    is_self_role_panel_message,
    normalized_panel_title,
    panel_kind_for_message,
    reaction_role_options,
    repair_managed_panel_art,
    repair_self_role_panel_images,
    send_panel_with_local_art,
    ticket_embed,
    verification_embed,
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

    def test_managed_panels_are_identified_by_stable_components(self):
        row = SimpleNamespace(
            children=[
                SimpleNamespace(custom_id="ticket_help"),
                SimpleNamespace(custom_id="ticket_suggestions"),
                SimpleNamespace(custom_id="ticket_report"),
            ]
        )
        message = SimpleNamespace(author=SimpleNamespace(id=10), components=[row])
        self.assertEqual(panel_kind_for_message(message, 10), "tickets")
        self.assertIsNone(panel_kind_for_message(message, 11))

    def test_standard_panel_embeds_have_no_external_image_dependencies(self):
        guild = SimpleNamespace(name="Example Server")
        for embed in (
            verification_embed(guild, discord.Colour.green()),
            ticket_embed(discord.Colour.green()),
        ):
            self.assertFalse(embed.image.url)
            self.assertNotIn("Lygo", str(embed.to_dict()))

    def test_default_everyone_role_is_never_offered_for_self_assignment(self):
        everyone = SimpleNamespace(
            id=7,
            name="@everyone",
            managed=False,
            is_default=lambda: True,
        )
        guild = SimpleNamespace(
            id=7,
            me=None,
            get_role=lambda _role_id: everyone,
        )
        self.assertEqual(reaction_role_options(guild, [7]), [])


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


class AutomaticPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_text_only_panel_is_upgraded_in_place(self):
        message = SimpleNamespace(
            embeds=[discord.Embed(title="Verify")],
            attachments=[],
            edit=AsyncMock(),
        )
        with TemporaryDirectory() as temp_dir:
            art_path = Path(temp_dir) / "verification.png"
            art_path.write_bytes(b"test image contents")
            with patch.dict(
                "Commands.panels.PANEL_ART_IMAGES",
                {"verification": art_path},
                clear=False,
            ):
                updated = await repair_managed_panel_art(message, "verification")

        self.assertTrue(updated)
        message.edit.assert_awaited_once()
        edited = message.edit.await_args.kwargs
        self.assertEqual(
            edited["embed"].image.url,
            "attachment://project_l_verification.png",
        )
        self.assertEqual(
            edited["attachments"][-1].filename,
            "project_l_verification.png",
        )

    async def test_local_panel_art_is_uploaded_as_a_message_attachment(self):
        sent = {}

        class Channel:
            async def send(self, **kwargs):
                sent.update(kwargs)
                return SimpleNamespace()

        with TemporaryDirectory() as temp_dir:
            art_path = Path(temp_dir) / "verification.png"
            art_path.write_bytes(b"test image contents")
            with patch.dict(
                "Commands.panels.PANEL_ART_IMAGES",
                {"verification": art_path},
                clear=False,
            ):
                await send_panel_with_local_art(
                    "verification",
                    Channel(),
                    embed=discord.Embed(title="Verify"),
                    view=object(),
                )

        self.assertEqual(
            sent["embed"].image.url,
            "attachment://project_l_verification.png",
        )
        self.assertEqual(sent["file"].filename, "project_l_verification.png")

    async def test_missing_local_panel_art_uses_text_only_embed(self):
        sent = {}

        class Channel:
            async def send(self, **kwargs):
                sent.update(kwargs)
                return SimpleNamespace()

        with patch.dict(
            "Commands.panels.PANEL_ART_IMAGES",
            {"tickets": Path("does-not-exist.png")},
            clear=False,
        ):
            await send_panel_with_local_art(
                "tickets",
                Channel(),
                embed=discord.Embed(title="Tickets"),
                view=object(),
            )

        self.assertNotIn("file", sent)
        self.assertFalse(sent["embed"].image.url)

    async def test_panel_installation_is_idempotent(self):
        bot_user = SimpleNamespace(id=42)

        class FakeChannel:
            def __init__(self):
                self.messages = []

            def history(self, **_kwargs):
                async def iterate():
                    for item in reversed(self.messages):
                        yield item

                return iterate()

            async def send(self, *, embed, view, **_kwargs):
                image_file = _kwargs.get("file")
                message = SimpleNamespace(
                    author=bot_user,
                    embeds=[embed],
                    components=[SimpleNamespace(children=view.children)],
                    attachments=(
                        [SimpleNamespace(filename=image_file.filename)]
                        if image_file is not None
                        else []
                    ),
                )
                self.messages.append(message)
                return message

        channel = FakeChannel()
        guild = SimpleNamespace(
            name="Test Server",
            me=None,
            get_channel=lambda channel_id: channel if channel_id == 99 else None,
        )
        client = SimpleNamespace(
            user=bot_user,
            colour=discord.Colour.green(),
            get_guild=lambda guild_id: guild if guild_id == 7 else None,
            get_channel=lambda _channel_id: None,
        )
        config = {
            "guildId": 7,
            "managedResources": {"panelsChannel": 99},
        }
        with (
            patch("Commands.panels.load_config", return_value=config),
            patch("Commands.panels.load_role_ids", return_value=[]),
        ):
            first = await ensure_automatic_panels(client)
            second = await ensure_automatic_panels(client)

        self.assertEqual(first.created, ["verification", "private_voice", "tickets"])
        self.assertEqual(first.failed, [])
        self.assertEqual(second.created, [])
        self.assertEqual(
            second.existing,
            ["verification", "private_voice", "tickets"],
        )
        self.assertEqual(len(channel.messages), 3)


if __name__ == "__main__":
    unittest.main()
