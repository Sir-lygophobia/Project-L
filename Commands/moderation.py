import json
import discord
from discord import app_commands
from discord.ext import commands
import time
import os
import asyncio
import datetime
import sqlite3
import traceback
from contextlib import closing
from logFunction import print_interaction_info


WARNINGS_DESCRIPTION_LIMIT = 3900
MAX_PURGE_MESSAGES = 1000
PURGE_CONFIRM_THRESHOLD = 100


def purge_requires_confirmation(amount: int) -> bool:
    return int(amount) >= PURGE_CONFIRM_THRESHOLD


class DestructiveConfirmationView(discord.ui.View):
    """Short-lived confirmation restricted to the administrator who invoked it."""

    def __init__(self, user_id: int):
        super().__init__(timeout=30)
        self.user_id = int(user_id)
        self.confirmed: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the administrator who started this action can confirm it.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button):
        self.confirmed = True
        self.stop()
        await interaction.response.edit_message(
            content="Confirmed. Working…", view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button):
        self.confirmed = False
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


async def confirm_destructive_action(
    interaction: discord.Interaction,
    prompt: str,
) -> bool:
    view = DestructiveConfirmationView(interaction.user.id)
    await interaction.response.send_message(prompt, view=view, ephemeral=True)
    timed_out = await view.wait()
    if timed_out:
        try:
            await interaction.edit_original_response(
                content="Confirmation expired; nothing was changed.",
                view=None,
            )
        except discord.HTTPException:
            pass
    return view.confirmed is True


def build_warning_pages(records, limit=WARNINGS_DESCRIPTION_LIMIT):
    """Format every warning without exceeding Discord's embed description limit."""
    if limit <= 0:
        raise ValueError("Warning page limit must be positive.")

    pages = []
    current_entries = []
    current_length = 0

    for index, (reason, warner) in enumerate(records, start=1):
        reason = reason or "No reason provided"
        entry = f"**{index}.** {reason} - <@{warner}>"

        while len(entry) > limit:
            if current_entries:
                pages.append("\n".join(current_entries))
                current_entries = []
                current_length = 0
            pages.append(entry[:limit])
            entry = entry[limit:]

        separator_length = 1 if current_entries else 0
        if current_entries and current_length + separator_length + len(entry) > limit:
            pages.append("\n".join(current_entries))
            current_entries = []
            current_length = 0
            separator_length = 0

        current_entries.append(entry)
        current_length += separator_length + len(entry)

    if current_entries:
        pages.append("\n".join(current_entries))

    return pages or [""]


def find_muted_role(guild):
    """Find the existing muted role without creating case-variant duplicates."""
    return next(
        (role for role in guild.roles if role.name.casefold() == "muted"),
        None,
    )


async def respond_to_interaction(interaction, *args, **kwargs):
    """Send through the initial response or follow-up, whichever is still valid."""
    if interaction.response.is_done():
        return await interaction.followup.send(*args, **kwargs)
    return await interaction.response.send_message(*args, **kwargs)


class ModsCommand(commands.Cog):
    def __init__(self, client):
        self.client = client

    def connect(self):
        try:
            conn = sqlite3.connect(f"Data/users.db")
            curs = conn.cursor()
            return conn, curs
        except Exception as exc:
            print(exc)
            return None

    @app_commands.command(name="steal", description="Steal an emoji")
    @app_commands.checks.has_permissions(manage_expressions=True)
    async def steal(self, interaction: discord.Interaction):
        print_interaction_info(interaction)

        await interaction.response.send_message(
            f"{interaction.user.mention} React to this message with an emoji to steal"
        )  # , ephemeral=True, delete_after=0.5)
        return

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        try:
            channel: discord.TextChannel = self.client.get_channel(payload.channel_id)
            msg: discord.Message = await channel.fetch_message(payload.message_id)
            if msg.author.id == self.client.user.id:
                if msg.content.startswith(payload.member.mention):
                    emoji = payload.emoji
                    em = await channel.guild.create_custom_emoji(
                        name=emoji.name, image=await emoji.read()
                    )
                    await msg.edit(content=f"Stole emoji {str(em)}")
        except Exception:
            print(traceback.format_exc())

    @steal.error
    async def catch(self, interaction, error):
        print(error)
        raise (error)

    @app_commands.command(
        name="warnings", description="See all warnings of a member (Admin)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        print_interaction_info(interaction)
        await interaction.response.defer()

        if member.avatar is None:
            userpfp = member.default_avatar
        else:
            userpfp = member.avatar

        userid = member.id
        try:
            with closing(sqlite3.connect("Data/users.db")) as conn:
                records = conn.execute(
                    "SELECT reason, warner FROM warnings WHERE userid = ? ORDER BY id",
                    (userid,),
                ).fetchall()
        except sqlite3.Error as exc:
            print(exc)
            await interaction.followup.send(
                "Could not load warnings right now. Please try again.",
                ephemeral=True,
            )
            return

        if len(records) == 0:
            emb = discord.Embed(title=f"Warnings (0)", colour=discord.Colour.green())
            emb.set_author(name=member, icon_url=userpfp)
            await interaction.followup.send(embed=emb)
            return

        pages = build_warning_pages(records)
        page_count = len(pages)
        requester_id = interaction.user.id

        def warning_embed(page_index):
            title = f"Warnings ({len(records)})"
            if page_count > 1:
                title += f" - Page {page_index + 1}/{page_count}"
            embed = discord.Embed(
                title=title,
                description=pages[page_index],
                colour=discord.Colour.red(),
            )
            embed.set_author(name=member, icon_url=userpfp)
            return embed

        class ChoiceView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=None)
                self.page_index = 0
                self.update_buttons()

            def update_buttons(self):
                self.previous.disabled = self.page_index == 0
                self.next.disabled = self.page_index == page_count - 1

            async def authorized(self, inter):
                if requester_id == inter.user.id:
                    return True
                await respond_to_interaction(
                    inter,
                    "Only the moderator who requested this list can use these controls.",
                    ephemeral=True,
                )
                return False

            @discord.ui.button(
                emoji="◀️",
                label="Previous",
                style=discord.ButtonStyle.secondary,
                custom_id=f"warnings_previous_{interaction.id}",
            )
            async def previous(
                self, inter: discord.Interaction, button: discord.Button
            ):
                if not await self.authorized(inter):
                    return
                if self.page_index > 0:
                    self.page_index -= 1
                self.update_buttons()
                await inter.response.edit_message(
                    embed=warning_embed(self.page_index), view=self
                )

            @discord.ui.button(
                emoji="▶️",
                label="Next",
                style=discord.ButtonStyle.secondary,
                custom_id=f"warnings_next_{interaction.id}",
            )
            async def next(self, inter: discord.Interaction, button: discord.Button):
                if not await self.authorized(inter):
                    return
                if self.page_index < page_count - 1:
                    self.page_index += 1
                self.update_buttons()
                await inter.response.edit_message(
                    embed=warning_embed(self.page_index), view=self
                )

            @discord.ui.button(
                emoji="🗑️",
                label="Clear",
                style=discord.ButtonStyle.danger,
                custom_id=f"clear_{userid}_{interaction.id}",
            )
            async def clearwarnings(
                self, inter: discord.Interaction, button: discord.Button
            ):
                if not await self.authorized(inter):
                    return

                try:
                    with closing(sqlite3.connect("Data/users.db")) as conn:
                        conn.execute("DELETE FROM warnings WHERE userid = ?", (userid,))
                        conn.commit()
                except sqlite3.Error as exc:
                    print(exc)
                    await respond_to_interaction(
                        inter,
                        "Could not clear warnings right now. Please try again.",
                        ephemeral=True,
                    )
                    return

                for child in self.children:
                    child.disabled = True
                embed = discord.Embed(
                    title="Warnings (0)", colour=discord.Colour.green()
                )
                embed.set_author(name=member, icon_url=userpfp)
                await inter.response.edit_message(embed=embed, view=self)
                self.stop()

        await interaction.followup.send(embed=warning_embed(0), view=ChoiceView())
        return

    @warnings.error
    async def catch(self, interaction, error):
        print(error)
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need Administrator permission to view warnings."
        else:
            message = "Could not load warnings right now. Please try again."
        await respond_to_interaction(
            interaction,
            message,
            ephemeral=True,
        )

    @app_commands.command(name="warn", description="Warn a member (Admin)")
    @app_commands.checks.has_permissions(administrator=True)
    async def warn(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: app_commands.Range[str, 1, 250] = None,
    ):
        print_interaction_info(interaction)

        if member.avatar is None:
            userpfp = member.default_avatar
        else:
            userpfp = member.avatar

        userid = member.id
        if reason is None:
            reason = "No reason provided"

        try:
            with closing(sqlite3.connect("Data/users.db")) as conn:
                cursor = conn.execute(
                    "INSERT INTO warnings(userid, reason, warner) VALUES(?, ?, ?)",
                    (userid, reason, interaction.user.id),
                )
                warning_id = cursor.lastrowid
                conn.commit()
        except sqlite3.Error as exc:
            print(exc)
            await interaction.response.send_message(
                "Could not save that warning. Please try again.", ephemeral=True
            )
            return

        class ChoiceView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=None)

            @discord.ui.button(
                emoji="🗑️",
                label="Undo",
                custom_id=f"undo_{warning_id}_{interaction.id}",
            )
            async def undowarning(
                self, inter: discord.Interaction, button: discord.Button
            ):
                if interaction.user.id != inter.user.id:
                    await respond_to_interaction(
                        inter,
                        "Only the moderator who created this warning can undo it.",
                        ephemeral=True,
                    )
                    return

                await inter.response.defer()
                try:
                    with closing(sqlite3.connect("Data/users.db")) as conn:
                        conn.execute("DELETE FROM warnings WHERE id = ?", (warning_id,))
                        conn.commit()
                    await inter.message.delete()
                    return
                except sqlite3.Error as exc:
                    print(exc)
                    await inter.followup.send(
                        "Could not undo that warning. Please try again.",
                        ephemeral=True,
                    )

        emb = discord.Embed(
            colour=discord.Colour.red(), timestamp=datetime.datetime.now()
        )
        emb.set_author(name=f"Member Warned", icon_url=userpfp)
        emb.add_field(name="Who", value=member.mention, inline=False)
        emb.add_field(name="By", value=interaction.user.mention, inline=False)
        emb.add_field(name="Reason", value=reason, inline=False)
        await interaction.response.send_message(embed=emb, view=ChoiceView())

        try:
            emb = discord.Embed(
                description=f"You have been warned in **{interaction.guild.name}**",
                colour=discord.Colour.red(),
            )
            emb.set_author(name=member, icon_url=userpfp)
            emb.add_field(name="Reason", value=reason, inline=False)
            await member.send(embed=emb)
        except:
            pass

        return

    @warn.error
    async def catch(self, interaction, error):
        print(error)
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need Administrator permission to warn members."
        else:
            message = "Could not save that warning. Please try again."
        await respond_to_interaction(
            interaction,
            message,
            ephemeral=True,
        )

    @app_commands.command(name="stats", description="Displays server stats")
    async def stats(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        await interaction.response.defer()
        online = 0
        offline = 0
        bots = 0
        for member in interaction.guild.members:
            if member.bot:
                bots += 1
            elif str(member.status) == "offline":
                offline += 1
            else:
                online += 1

        boosts = interaction.guild.premium_subscription_count
        owner = interaction.guild.owner.mention
        timer = int(str(interaction.guild.created_at.timestamp()).split(".")[0])

        emb = discord.Embed(
            title=interaction.guild.name,
            colour=self.client.colour,
            timestamp=datetime.datetime.now(),
        )
        emb.add_field(name="Online", value=online, inline=True)
        emb.add_field(name="Offline", value=offline, inline=True)
        emb.add_field(name="Bots", value=bots, inline=True)
        emb.add_field(name="Boosts", value=boosts, inline=True)
        emb.add_field(name="Created At", value=f"<t:{timer}>", inline=False)
        emb.add_field(name="Owner", value=owner, inline=False)
        await interaction.followup.send(embed=emb)
        return

    @stats.error
    async def catch(self, interaction, error):
        print(error)
        await respond_to_interaction(
            interaction,
            "Could not load the server stats right now. Please try again.",
            ephemeral=True,
        )

    @app_commands.command(name="lock", description="Lock a channel")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        await interaction.response.send_message(f"Locked {interaction.channel.mention}")
        overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await interaction.channel.set_permissions(
            interaction.guild.default_role, overwrite=overwrite
        )
        return

    @app_commands.command(name="unlock", description="Unlock a channel")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        await interaction.response.send_message(
            f"Unlocked {interaction.channel.mention}"
        )
        overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = True
        await interaction.channel.set_permissions(
            interaction.guild.default_role, overwrite=overwrite
        )
        return

    @app_commands.command(name="purge", description="Purge messages")
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True)
    @app_commands.checks.cooldown(
        1, 30.0, key=lambda interaction: (interaction.guild_id, interaction.user.id)
    )
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, MAX_PURGE_MESSAGES],
    ):
        print_interaction_info(interaction)
        if purge_requires_confirmation(amount):
            confirmed = await confirm_destructive_action(
                interaction,
                f"Delete up to **{amount}** messages from {interaction.channel.mention}?",
            )
            if not confirmed:
                return
        else:
            await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(
            f"Deleted **{len(deleted)}** message(s).",
            ephemeral=True,
        )
        return

    @app_commands.command(name="nuke", description="Nuke")
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    @app_commands.checks.cooldown(
        1, 60.0, key=lambda interaction: (interaction.guild_id, interaction.user.id)
    )
    async def nuke(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        confirmed = await confirm_destructive_action(
            interaction,
            f"Permanently recreate {interaction.channel.mention} and delete its message history?",
        )
        if not confirmed:
            return
        chanpos = interaction.channel.position
        newchan = await interaction.channel.clone()
        await interaction.channel.delete()
        await newchan.edit(position=chanpos, sync_permissions=True)
        await newchan.send(
            f"Channel nuked by {interaction.user.mention}", delete_after=5
        )
        return

    @app_commands.command(name="timeout", description="Timeout a member")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        minutes: app_commands.Range[int, 1, 40320],
    ):
        print_interaction_info(interaction)
        then = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(
            minutes=minutes
        )

        await member.timeout(then)
        await interaction.response.send_message(
            f"{member.mention} has been timed out for **{minutes}** minutes."
        )
        return

    @app_commands.command(name="untimeout", description="Remove a timeout of a member")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member):
        print_interaction_info(interaction)
        then = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(
            minutes=1
        )
        await member.timeout(then)
        await interaction.response.send_message(
            f"{member.mention} is not timed out anymore."
        )
        return

    @app_commands.command(name="unmute", description="Unmute a member")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def unmute(self, interaction: discord.Interaction, member: discord.Member):
        print_interaction_info(interaction)
        if member.id == interaction.user.id:
            await interaction.response.send_message(
                "You can't unmute yourself", ephemeral=True
            )
            return

        await interaction.response.defer()
        roles = [role for role in member.roles if role.name.casefold() == "muted"]
        if not roles:
            await interaction.followup.send(f"{member.mention} is not muted.")
            return

        if any(not role.is_assignable() for role in roles):
            await interaction.followup.send(
                "I cannot manage a muted role. Move my highest role above it "
                "and make sure I have Manage Roles.",
                ephemeral=True,
            )
            return

        try:
            await member.remove_roles(*roles, reason=f"Unmuted by {interaction.user}")
        except Exception as exc:
            print(exc)
            await interaction.followup.send(
                f"Could not unmute {member.mention}. Check my role position and "
                "Manage Roles permission.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(f"{member.mention} has been unmuted.")
        return

    @unmute.error
    async def catch(self, interaction, error):
        print(error)
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need Manage Roles permission to unmute members."
        else:
            message = (
                "Could not complete that unmute. Check my role position and "
                "permissions."
            )
        await respond_to_interaction(
            interaction,
            message,
            ephemeral=True,
        )

    @app_commands.command(name="mute", description="Mute a member")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def mute(self, interaction: discord.Interaction, member: discord.Member):
        print_interaction_info(interaction)
        if member.id == interaction.user.id:
            await interaction.response.send_message(
                "You can't mute yourself", ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            role = next(
                (
                    existing_role
                    for existing_role in member.roles
                    if existing_role.name.casefold() == "muted"
                ),
                None,
            ) or find_muted_role(interaction.guild)
            if role is None:
                role = await interaction.guild.create_role(
                    name="muted",
                    permissions=discord.Permissions.none(),
                    reason=f"Mute requested by {interaction.user}",
                )

            if not role.is_assignable():
                await interaction.followup.send(
                    "I cannot manage the muted role. Move my highest role above it "
                    "and make sure I have Manage Roles.",
                    ephemeral=True,
                )
                return

            failed_channels = []
            for channel in interaction.guild.text_channels:
                try:
                    await channel.set_permissions(
                        role,
                        send_messages=False,
                        add_reactions=False,
                        send_messages_in_threads=False,
                        create_public_threads=False,
                        create_private_threads=False,
                        reason=f"Mute role configured by {interaction.user}",
                    )
                except Exception as exc:
                    print(exc)
                    failed_channels.append(channel)

            await member.add_roles(role, reason=f"Muted by {interaction.user}")
        except Exception as exc:
            print(exc)
            await interaction.followup.send(
                f"Could not mute {member.mention}. Check my role position and "
                "Manage Roles permission.",
                ephemeral=True,
            )
            return

        if failed_channels:
            await interaction.followup.send(
                f"{member.mention} received the muted role, but I could not update "
                f"permissions in {len(failed_channels)} channel(s).",
                ephemeral=True,
            )
            return

        await interaction.followup.send(f"{member.mention} has been muted.")
        return

    @mute.error
    async def catch(self, interaction, error):
        print(error)
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need Manage Roles permission to mute members."
        else:
            message = (
                "Could not complete that mute. Check my role position and permissions."
            )
        await respond_to_interaction(
            interaction,
            message,
            ephemeral=True,
        )

    @app_commands.command(name="ban", description="Ban a member")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = None,
    ):
        print_interaction_info(interaction)
        if member.id == interaction.user.id:
            await interaction.response.send_message(
                "You can't ban yourself", ephemeral=True
            )
            return
        await interaction.guild.ban(member, reason=reason)
        await interaction.response.send_message(f"{member.mention} has been banned.")
        return

    @app_commands.command(name="unban", description="Unban a member")
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, member: discord.User):
        print_interaction_info(interaction)
        if member.id == interaction.user.id:
            await interaction.response.send_message(
                "You can't unban yourself", ephemeral=True
            )
            return

        await interaction.guild.unban(member)
        await interaction.response.send_message(f"{member.mention} has been unbanned.")
        return

    @unban.error
    async def catch(self, interaction, error):
        print(error)
        raise (error)

    @app_commands.command(name="kick", description="Kick a member")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = None,
    ):
        print_interaction_info(interaction)
        if member.id == interaction.user.id:
            await interaction.response.send_message(
                "You can't kick yourself", ephemeral=True
            )
            return
        await interaction.guild.kick(member, reason=reason)
        await interaction.response.send_message(f"{member.mention} has been kicked.")
        return

    @untimeout.error
    async def catch(self, interaction, error):
        await interaction.response.send_message(
            f"Error, I probably don't have permissions to do that."
        )
        return

    @timeout.error
    async def catch(self, interaction, error):
        await interaction.response.send_message(
            f"Error, I probably don't have permissions to do that."
        )
        return

    @unban.error
    async def catch(self, interaction, error):
        await interaction.response.send_message(
            f"Error, I probably don't have permissions to do that."
        )
        return

    @ban.error
    async def catch(self, interaction, error):
        await interaction.response.send_message(
            f"Error, I probably don't have permissions to do that."
        )
        return

    @kick.error
    async def catch(self, interaction, error):
        await interaction.response.send_message(
            f"Error, I probably don't have permissions to do that."
        )
        return


async def setup(client):
    await client.add_cog(ModsCommand(client))
