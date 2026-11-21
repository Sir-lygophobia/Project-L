import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from Commands.moderation import (
    MAX_PURGE_MESSAGES,
    PURGE_CONFIRM_THRESHOLD,
    WARNINGS_DESCRIPTION_LIMIT,
    ModsCommand,
    build_warning_pages,
    find_muted_role,
    purge_requires_confirmation,
    respond_to_interaction,
)


class FakeRole:
    def __init__(self, name="muted", *, assignable=True):
        self.name = name
        self._assignable = assignable

    def is_assignable(self):
        return self._assignable


def make_interaction(*, user_id=1, guild=None, response_done=False):
    response = SimpleNamespace(
        defer=AsyncMock(),
        edit_message=AsyncMock(),
        send_message=AsyncMock(),
        is_done=Mock(return_value=response_done),
    )
    return SimpleNamespace(
        id=987654,
        user=SimpleNamespace(id=user_id, mention=f"<@{user_id}>"),
        guild=guild,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )


class ModerationHelperTests(unittest.TestCase):
    def test_large_purges_require_confirmation_and_have_a_safe_cap(self):
        self.assertFalse(purge_requires_confirmation(PURGE_CONFIRM_THRESHOLD - 1))
        self.assertTrue(purge_requires_confirmation(PURGE_CONFIRM_THRESHOLD))
        self.assertEqual(MAX_PURGE_MESSAGES, 1000)

    def test_warning_pages_stay_under_discord_description_limit(self):
        records = [("x" * 250, index) for index in range(1, 80)]

        pages = build_warning_pages(records)

        self.assertGreater(len(pages), 1)
        self.assertTrue(pages)
        self.assertTrue(all(len(page) <= WARNINGS_DESCRIPTION_LIMIT for page in pages))
        for index in range(1, 80):
            self.assertIn(f"**{index}.**", "".join(pages))

    def test_warning_pages_split_legacy_oversized_reason(self):
        reason = "z" * (WARNINGS_DESCRIPTION_LIMIT + 500)

        pages = build_warning_pages([(reason, 12)])

        self.assertEqual(len(pages), 2)
        self.assertTrue(all(len(page) <= WARNINGS_DESCRIPTION_LIMIT for page in pages))
        self.assertEqual("".join(pages), f"**1.** {reason} - <@12>")

    def test_muted_role_lookup_is_case_insensitive(self):
        muted = FakeRole("Muted")
        guild = SimpleNamespace(roles=[FakeRole("member"), muted])

        self.assertIs(find_muted_role(guild), muted)


class ModerationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = ModsCommand(SimpleNamespace())

    async def test_response_helper_uses_initial_response_then_followup(self):
        interaction = make_interaction(response_done=False)

        await respond_to_interaction(interaction, "initial", ephemeral=True)

        interaction.response.send_message.assert_awaited_once_with(
            "initial", ephemeral=True
        )
        interaction.followup.send.assert_not_awaited()

        deferred = make_interaction(response_done=True)
        await respond_to_interaction(deferred, "follow-up", ephemeral=True)
        deferred.followup.send.assert_awaited_once_with("follow-up", ephemeral=True)

    async def test_warnings_database_failure_finishes_deferred_interaction(self):
        guild = SimpleNamespace()
        interaction = make_interaction(guild=guild)
        member = SimpleNamespace(
            id=2,
            avatar=None,
            default_avatar="https://example.test/avatar.png",
        )

        with (
            patch("Commands.moderation.print_interaction_info"),
            patch(
                "Commands.moderation.sqlite3.connect",
                side_effect=sqlite3.OperationalError("database unavailable"),
            ),
            patch("builtins.print"),
        ):
            await ModsCommand.warnings.callback(self.cog, interaction, member)

        interaction.response.defer.assert_awaited_once_with()
        interaction.followup.send.assert_awaited_once_with(
            "Could not load warnings right now. Please try again.",
            ephemeral=True,
        )

    async def test_warnings_paginate_and_navigation_acknowledges(self):
        records = [("x" * 250, index) for index in range(40)]
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = records
        interaction = make_interaction(guild=SimpleNamespace())
        member = SimpleNamespace(
            id=2,
            avatar=None,
            default_avatar="https://example.test/avatar.png",
        )

        with (
            patch("Commands.moderation.print_interaction_info"),
            patch("Commands.moderation.sqlite3.connect", return_value=connection),
        ):
            await ModsCommand.warnings.callback(self.cog, interaction, member)

        interaction.response.defer.assert_awaited_once_with()
        sent = interaction.followup.send.await_args.kwargs
        self.assertLessEqual(len(sent["embed"].description), WARNINGS_DESCRIPTION_LIMIT)
        view = sent["view"]
        self.assertTrue(view.previous.disabled)
        self.assertFalse(view.next.disabled)

        component_interaction = make_interaction(user_id=1)
        await view.next.callback(component_interaction)
        self.assertEqual(view.page_index, 1)
        component_interaction.response.edit_message.assert_awaited_once()

        unauthorized = make_interaction(user_id=99)
        await view.clearwarnings.callback(unauthorized)
        unauthorized.response.send_message.assert_awaited_once()

    async def test_unmute_does_not_create_a_missing_role(self):
        guild = SimpleNamespace(roles=[], create_role=AsyncMock())
        interaction = make_interaction(guild=guild)
        member = SimpleNamespace(
            id=2,
            mention="<@2>",
            roles=[],
            remove_roles=AsyncMock(),
        )

        with patch("Commands.moderation.print_interaction_info"):
            await ModsCommand.unmute.callback(self.cog, interaction, member)

        interaction.response.defer.assert_awaited_once_with()
        guild.create_role.assert_not_awaited()
        member.remove_roles.assert_not_awaited()
        interaction.followup.send.assert_awaited_once_with("<@2> is not muted.")

    async def test_unmute_removes_all_assignable_muted_role_variants(self):
        role = FakeRole("MUTED")
        duplicate = FakeRole("muted")
        guild = SimpleNamespace(roles=[role, duplicate])
        interaction = make_interaction(guild=guild)
        member = SimpleNamespace(
            id=2,
            mention="<@2>",
            roles=[role, duplicate],
            remove_roles=AsyncMock(),
        )

        with patch("Commands.moderation.print_interaction_info"):
            await ModsCommand.unmute.callback(self.cog, interaction, member)

        removed_roles = member.remove_roles.await_args.args
        self.assertEqual(removed_roles, (role, duplicate))
        interaction.followup.send.assert_awaited_once_with("<@2> has been unmuted.")

    async def test_mute_configures_role_before_assigning_it(self):
        role = FakeRole()
        channel = SimpleNamespace(set_permissions=AsyncMock())
        guild = SimpleNamespace(
            roles=[],
            text_channels=[channel],
            create_role=AsyncMock(return_value=role),
        )
        interaction = make_interaction(guild=guild)
        member = SimpleNamespace(
            id=2,
            mention="<@2>",
            roles=[],
            add_roles=AsyncMock(),
        )

        with patch("Commands.moderation.print_interaction_info"):
            await ModsCommand.mute.callback(self.cog, interaction, member)

        guild.create_role.assert_awaited_once()
        channel.set_permissions.assert_awaited_once()
        permissions = channel.set_permissions.await_args.kwargs
        self.assertIs(permissions["send_messages"], False)
        self.assertIs(permissions["send_messages_in_threads"], False)
        self.assertIs(permissions["add_reactions"], False)
        member.add_roles.assert_awaited_once()
        interaction.followup.send.assert_awaited_once_with("<@2> has been muted.")

    async def test_mute_reports_partial_channel_permission_failure(self):
        role = FakeRole()
        working = SimpleNamespace(set_permissions=AsyncMock())
        blocked = SimpleNamespace(
            set_permissions=AsyncMock(side_effect=RuntimeError("blocked"))
        )
        guild = SimpleNamespace(
            roles=[role],
            text_channels=[working, blocked],
            create_role=AsyncMock(),
        )
        interaction = make_interaction(guild=guild)
        member = SimpleNamespace(
            id=2,
            mention="<@2>",
            roles=[],
            add_roles=AsyncMock(),
        )

        with (
            patch("Commands.moderation.print_interaction_info"),
            patch("builtins.print"),
        ):
            await ModsCommand.mute.callback(self.cog, interaction, member)

        member.add_roles.assert_awaited_once()
        message = interaction.followup.send.await_args.args[0]
        self.assertIn("could not update permissions in 1 channel", message)
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])


if __name__ == "__main__":
    unittest.main()
