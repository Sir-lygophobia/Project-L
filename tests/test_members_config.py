import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from Commands.members import (
    MembersCommand,
    find_modmail_channel,
    modmail_user_id_from_topic,
)


class FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id


class FakeCategory:
    def __init__(self, guild_id):
        self.guild = FakeGuild(guild_id)
        self.text_channels = []


class FakeTextChannel:
    pass


class ModmailCategoryTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, cached_channel, *, category_id=123, guild_id=456):
        client = Mock()
        client.modmailid = category_id
        client.configured_guild_id = guild_id
        client.get_channel.return_value = cached_channel
        client.fetch_channel = AsyncMock()
        return client

    async def test_disabled_modmail_does_not_resolve_a_channel(self):
        client = self.make_client(None, category_id=0)
        cog = MembersCommand(client)

        self.assertIsNone(await cog._resolve_modmail_category())
        client.get_channel.assert_not_called()
        client.fetch_channel.assert_not_awaited()

    async def test_text_channel_is_rejected_without_raising_or_fetching(self):
        client = self.make_client(FakeTextChannel())
        cog = MembersCommand(client)

        with (
            patch("Commands.members.discord.CategoryChannel", FakeCategory),
            patch("builtins.print") as printer,
        ):
            self.assertIsNone(await cog._resolve_modmail_category())
            self.assertIsNone(await cog._resolve_modmail_category())

        client.fetch_channel.assert_not_awaited()
        self.assertEqual(printer.call_count, 1)
        self.assertIn("must be a category", printer.call_args.args[0])

    async def test_uncached_valid_category_is_fetched(self):
        category = FakeCategory(456)
        client = self.make_client(None)
        client.fetch_channel.return_value = category
        cog = MembersCommand(client)

        with patch("Commands.members.discord.CategoryChannel", FakeCategory):
            resolved = await cog._resolve_modmail_category()

        self.assertIs(resolved, category)
        client.fetch_channel.assert_awaited_once_with(123)

    async def test_category_from_another_server_is_rejected(self):
        client = self.make_client(FakeCategory(999))
        cog = MembersCommand(client)

        with (
            patch("Commands.members.discord.CategoryChannel", FakeCategory),
            patch("builtins.print") as printer,
        ):
            self.assertIsNone(await cog._resolve_modmail_category())

        self.assertIn("different server", printer.call_args.args[0])

    def test_modmail_topic_parsing_is_exact(self):
        self.assertEqual(modmail_user_id_from_topic("modmail-user:123"), 123)
        for topic in ("", "prefix-modmail-user:123", "modmail-user:123-extra"):
            with self.subTest(topic=topic):
                self.assertIsNone(modmail_user_id_from_topic(topic))

    def test_modmail_channel_matching_never_uses_username_substrings(self):
        correct = SimpleNamespace(topic="modmail-user:123", name="modmail-123")
        unrelated = SimpleNamespace(topic=None, name="joanna")
        category = SimpleNamespace(text_channels=[unrelated, correct])
        self.assertIs(find_modmail_channel(category, 123), correct)
        self.assertIsNone(find_modmail_channel(category, 999))

    def test_attachment_only_and_long_messages_build_valid_embeds(self):
        author = SimpleNamespace(
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
            __str__=lambda self: "member",
        )
        attachment_only = SimpleNamespace(
            content="",
            author=author,
            created_at=datetime.now(UTC),
            stickers=[],
        )
        long_message = SimpleNamespace(
            content="x" * 2000,
            author=author,
            created_at=datetime.now(UTC),
            stickers=[],
        )

        empty_embed = MembersCommand._relay_embed(
            attachment_only, title="Modmail", colour=discord.Colour.blue()
        )
        long_embed = MembersCommand._relay_embed(
            long_message, title="Modmail", colour=discord.Colour.blue()
        )
        self.assertTrue(empty_embed.description)
        self.assertEqual(len(long_embed.description), 2000)

    def test_attach_files_is_required_only_when_a_message_has_attachments(self):
        permissions = SimpleNamespace(
            view_channel=True,
            manage_channels=True,
            send_messages=True,
            embed_links=True,
            attach_files=False,
        )
        category = SimpleNamespace(
            guild=SimpleNamespace(me=object()),
            permissions_for=lambda _member: permissions,
        )
        cog = MembersCommand(SimpleNamespace())

        self.assertEqual(cog._missing_modmail_permissions(category), [])
        self.assertEqual(
            cog._missing_modmail_permissions(category, require_attachments=True),
            ["Attach Files"],
        )


class SayCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_say_without_image_omits_the_file_argument(self):
        client = SimpleNamespace(colour=discord.Colour.green())
        cog = MembersCommand(client)
        channel = SimpleNamespace(id=22, send=AsyncMock())
        interaction = SimpleNamespace(
            user="tester",
            data={"name": "say"},
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await MembersCommand.say.callback(cog, interaction, channel)

        channel.send.assert_awaited_once()
        self.assertNotIn("file", channel.send.await_args.kwargs)
        interaction.followup.send.assert_awaited_once_with("Sent!", ephemeral=True)

    async def test_say_reuploads_image_as_a_durable_message_attachment(self):
        client = SimpleNamespace(colour=discord.Colour.green())
        cog = MembersCommand(client)
        channel = SimpleNamespace(id=22, send=AsyncMock())
        image_file = SimpleNamespace(filename="embed-panel.png", close=Mock())
        attachment = SimpleNamespace(
            filename="folder/panel.png",
            to_file=AsyncMock(return_value=image_file),
        )
        interaction = SimpleNamespace(
            user="tester",
            data={"name": "say"},
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await MembersCommand.say.callback(
            cog, interaction, channel, img=attachment
        )

        attachment.to_file.assert_awaited_once_with(
            filename="embed-panel.png", use_cached=True
        )
        sent = channel.send.await_args.kwargs
        self.assertIs(sent["file"], image_file)
        self.assertEqual(sent["embed"].image.url, "attachment://embed-panel.png")
        image_file.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
