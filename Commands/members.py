import discord
from discord import app_commands, SelectOption
from discord.ext import commands, tasks
from discord.ui import Button, View, Select
import asyncio
import sqlite3
import random
import traceback
from pathlib import Path
import re
from logFunction import print_interaction_info


STICKY_DB_PATH = Path(__file__).resolve().parents[1] / "sticky.db"
MODMAIL_TOPIC_PATTERN = re.compile(r"^modmail-user:(\d+)$")


def modmail_user_id_from_topic(topic):
    match = MODMAIL_TOPIC_PATTERN.fullmatch(str(topic or "").strip())
    return int(match.group(1)) if match else None


def find_modmail_channel(category, user_id):
    topic = f"modmail-user:{user_id}"
    return next(
        (channel for channel in category.text_channels if channel.topic == topic),
        None,
    )


class ModmailDeleteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                emoji="🗑️",
                style=discord.ButtonStyle.grey,
                custom_id="modmail_del",
            )
        )


conn = sqlite3.connect(STICKY_DB_PATH)
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS infos_salos (
        id_salon INTEGER PRIMARY KEY,
        contenu TEXT,
        message_limit INTEGER
    )
''')

conn.commit()
conn.close()

class MembersCommand(commands.Cog):
    def __init__(self, client):
        self.client = client
        self._last_modmail_warning = None
        self._modmail_locks = {}


    def connect(self):
        try:
            conn = sqlite3.connect(f"Data/users.db")
            curs = conn.cursor()
            return conn, curs
        except Exception as exc:
            print(exc)
            return None

    @staticmethod
    def _rebuild_button_view(message, message_id, *, excluded_label=None):
        """Recreate every button row without silently dropping later rows."""
        view = View(timeout=None)
        removed = False
        with sqlite3.connect("Data/user.db") as conn:
            for row_index, action_row in enumerate(message.components):
                for component in action_row.children:
                    if getattr(component, "type", None) != discord.ComponentType.button:
                        raise ValueError(
                            "This message contains a select menu, so its buttons cannot be "
                            "safely changed by this command."
                        )
                    if excluded_label is not None and component.label == excluded_label:
                        removed = True
                        continue
                    if component.style == discord.ButtonStyle.link:
                        view.add_item(
                            Button(
                                label=component.label,
                                style=component.style,
                                url=component.url,
                                emoji=component.emoji,
                                disabled=component.disabled,
                                row=row_index,
                            )
                        )
                        continue

                    role_record = conn.execute(
                        "SELECT roleid, custom_id FROM roles "
                        "WHERE messageid = ? AND (custom_id = ? OR content = ?) "
                        "ORDER BY CASE WHEN custom_id = ? THEN 0 ELSE 1 END LIMIT 1",
                        (
                            message_id,
                            component.custom_id,
                            component.label,
                            component.custom_id,
                        ),
                    ).fetchone()
                    if role_record is None:
                        raise ValueError(
                            f"The saved role mapping for button '{component.label}' is missing."
                        )
                    view.add_item(
                        RoleToggleButton(
                            component.label,
                            component.emoji,
                            role_record[0],
                            role_record[1],
                            row=row_index,
                            disabled=component.disabled,
                        )
                    )
        return view, removed


    # MODMAIL

    def _warn_modmail_once(self, warning):
        if warning != self._last_modmail_warning:
            print(f"[!] {warning}")
            self._last_modmail_warning = warning

    async def _resolve_modmail_category(self):
        category_id = getattr(self.client, "modmailid", 0)
        if not category_id:
            return None

        category = self.client.get_channel(category_id)
        if category is None:
            try:
                category = await self.client.fetch_channel(category_id)
            except discord.NotFound:
                self._warn_modmail_once(
                    f"Modmail category {category_id} no longer exists. Update it in the setup wizard."
                )
                return None
            except discord.Forbidden:
                self._warn_modmail_once(
                    f"The bot cannot access modmail category {category_id}. Check its permissions."
                )
                return None
            except discord.HTTPException as exc:
                self._warn_modmail_once(
                    f"Could not load modmail category {category_id}: {exc}"
                )
                return None

        if not isinstance(category, discord.CategoryChannel):
            self._warn_modmail_once(
                f"Modmail ID {category_id} must be a category, but it resolves to "
                f"{type(category).__name__}. Update it in the setup wizard."
            )
            return None

        configured_guild_id = getattr(self.client, "configured_guild_id", 0)
        if configured_guild_id and category.guild.id != configured_guild_id:
            self._warn_modmail_once(
                f"Modmail category {category_id} belongs to a different server."
            )
            return None

        self._last_modmail_warning = None
        return category

    @staticmethod
    async def _attachment_files(attachments, *, max_bytes=None):
        files = []
        failed = []
        for attachment in attachments:
            if max_bytes and getattr(attachment, "size", 0) > max_bytes:
                failed.append(f"{attachment.filename}: {attachment.url}")
                continue
            try:
                files.append(
                    await attachment.to_file(
                        use_cached=True,
                        spoiler=attachment.is_spoiler(),
                    )
                )
            except (discord.Forbidden, discord.HTTPException, OSError):
                failed.append(f"{attachment.filename}: {attachment.url}")
        return files, failed

    @staticmethod
    def _relay_embed(message, *, title, colour, failed_attachments=None):
        description = message.content or "(Attachment or sticker-only message)"
        if len(description) > 4096:
            description = description[:4095] + "…"
        embed = discord.Embed(
            title=title,
            description=description,
            colour=colour,
            timestamp=message.created_at,
        )
        embed.set_author(
            name=str(message.author),
            icon_url=message.author.display_avatar.url,
        )
        stickers = [sticker.name for sticker in getattr(message, "stickers", [])]
        if stickers:
            embed.add_field(
                name="Stickers",
                value=", ".join(stickers)[:1024],
                inline=False,
            )
        if failed_attachments:
            embed.add_field(
                name="Attachments sent as links",
                value="\n".join(failed_attachments)[:1024],
                inline=False,
            )
        return embed

    def _missing_modmail_permissions(self, category, *, require_attachments=False):
        bot_member = category.guild.me
        if bot_member is None:
            return ["bot membership"]
        permissions = category.permissions_for(bot_member)
        required = (
            ("view_channel", "View Channel"),
            ("manage_channels", "Manage Channels"),
            ("send_messages", "Send Messages"),
            ("embed_links", "Embed Links"),
        )
        missing = [
            label for attribute, label in required if not getattr(permissions, attribute)
        ]
        if require_attachments and not permissions.attach_files:
            missing.append("Attach Files")
        return missing

    async def _notify_modmail_sender(self, user, text):
        try:
            await user.send(text, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _handle_incoming_modmail(self, message):
        category = await self._resolve_modmail_category()
        if category is None:
            await self._notify_modmail_sender(
                message.author,
                "Modmail is not currently available. Please contact a server administrator.",
            )
            return

        if category.permissions_for(category.guild.default_role).view_channel:
            self._warn_modmail_once(
                f"Modmail category {category.id} is public. Deny View Channel for @everyone."
            )
            await self._notify_modmail_sender(
                message.author,
                "Modmail is temporarily unavailable because its staff category is not private.",
            )
            return

        missing_permissions = self._missing_modmail_permissions(
            category, require_attachments=bool(message.attachments)
        )
        if missing_permissions:
            self._warn_modmail_once(
                "Modmail is missing permissions: " + ", ".join(missing_permissions)
            )
            await self._notify_modmail_sender(
                message.author,
                "Modmail is temporarily unavailable because the bot lacks required permissions.",
            )
            return

        lock = self._modmail_locks.setdefault(message.author.id, asyncio.Lock())
        try:
            async with lock:
                channel = find_modmail_channel(category, message.author.id)
                created = channel is None
                if created:
                    channel = await category.guild.create_text_channel(
                        name=f"modmail-{message.author.id}",
                        category=category,
                        topic=f"modmail-user:{message.author.id}",
                        reason="Project L modmail conversation",
                    )
                elif modmail_user_id_from_topic(channel.topic) != message.author.id:
                    await channel.edit(
                        topic=f"modmail-user:{message.author.id}",
                        reason="Repair Project L modmail routing topic",
                    )

                files, failed = await self._attachment_files(
                    message.attachments,
                    max_bytes=getattr(category.guild, "filesize_limit", None),
                )
                embed = self._relay_embed(
                    message,
                    title="New Modmail" if created else "Modmail Reply",
                    colour=discord.Colour.blue() if created else discord.Colour.green(),
                    failed_attachments=failed,
                )
                try:
                    mirrored = await channel.send(
                        embed=embed,
                        files=files,
                        view=ModmailDeleteView() if created else None,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    if not files:
                        raise
                    fallback_links = [
                        f"{attachment.filename}: {attachment.url}"
                        for attachment in message.attachments
                    ]
                    fallback_embed = self._relay_embed(
                        message,
                        title="New Modmail" if created else "Modmail Reply",
                        colour=(
                            discord.Colour.blue()
                            if created
                            else discord.Colour.green()
                        ),
                        failed_attachments=fallback_links,
                    )
                    mirrored = await channel.send(
                        embed=fallback_embed,
                        view=ModmailDeleteView() if created else None,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                if created:
                    try:
                        await mirrored.pin(reason="Project L modmail conversation start")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
        except (discord.Forbidden, discord.HTTPException) as exc:
            self._warn_modmail_once(f"Could not deliver incoming modmail: {exc}")
            await self._notify_modmail_sender(
                message.author,
                "Your modmail message could not be delivered. Please try again later.",
            )
            return
        finally:
            if not lock.locked():
                self._modmail_locks.pop(message.author.id, None)
        await self._notify_modmail_sender(
            message.author,
            "Your message was delivered to the server staff.",
        )

    async def _handle_staff_modmail_reply(self, message):
        user_id = modmail_user_id_from_topic(getattr(message.channel, "topic", None))
        if user_id is None:
            return

        user = self.client.get_user(user_id)
        if user is None:
            try:
                user = await self.client.fetch_user(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                await message.channel.send(
                    "Could not find the modmail recipient.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

        files, failed = await self._attachment_files(
            message.attachments,
            max_bytes=getattr(message.guild, "filesize_limit", 10 * 1024 * 1024),
        )
        embed = self._relay_embed(
            message,
            title=f"Staff reply from {message.guild.name}",
            colour=discord.Colour.green(),
            failed_attachments=failed,
        )
        try:
            try:
                await user.send(
                    embed=embed,
                    files=files,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                if not files:
                    raise
                fallback_links = [
                    f"{attachment.filename}: {attachment.url}"
                    for attachment in message.attachments
                ]
                fallback_embed = self._relay_embed(
                    message,
                    title=f"Staff reply from {message.guild.name}",
                    colour=discord.Colour.green(),
                    failed_attachments=fallback_links,
                )
                await user.send(
                    embed=fallback_embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except (discord.Forbidden, discord.HTTPException):
            await message.channel.send(
                "Could not DM this user. They may have disabled or blocked direct messages.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        try:
            await message.add_reaction("✅")
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            message.author == self.client.user
            or getattr(message.author, "bot", False)
            or getattr(message, "webhook_id", None) is not None
        ):
            return
        try:
            if message.guild is None:
                await self._handle_incoming_modmail(message)
                return

            if getattr(message.channel, "category_id", None) == self.client.modmailid:
                await self._handle_staff_modmail_reply(message)
        except Exception:
            print("[!] Unexpected modmail error:\n" + traceback.format_exc())



    class SelectD(discord.ui.Select):
        def __init__(self):
            options = [
                discord.SelectOption(label="Economy commands", description="Shows all the economy commands"),
                discord.SelectOption(label="Invites commands", description="Shows all the economy commands"),
                discord.SelectOption(label="Fun commands", description="Shows all the economy commands"),
                discord.SelectOption(label="Giveaways commands", description="Shows all the economy commands"),
                discord.SelectOption(label="Moderation commands", description="Shows all the economy commands"),
                discord.SelectOption(label="Admin commands", description="Shows all the economy commands"),
                discord.SelectOption(label="Giveaways commands", description="Shows all the economy commands")
            ]
            super().__init__(placeholder="Which help section do you want ?", min_values=1, max_values=1, options=options)

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.send_message("You selected: " + self.values[0], ephemeral=True)


    @app_commands.command(name="say", description="Say something as the bot")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def say(self, interaction: discord.Interaction, channel: discord.TextChannel, *, title: str = None, message: str = None, link: str = None, img: discord.Attachment = None):
        print_interaction_info(interaction)
        await interaction.response.defer(ephemeral=True)
        title_text = f"👑 {title} 👑" if title else "Default title"
        message_text = f"❤️ {message} ❤️" if message else None
        embed = discord.Embed(
            title=title_text,
            description=message_text,
            colour=self.client.colour,
        )
        view = View()
        if link:
            view.add_item(
                Button(label="Click Here", style=discord.ButtonStyle.link, url=link)
            )

        image_file = None
        try:
            if img:
                safe_name = Path(img.filename or "embedded-image").name
                safe_name = safe_name or "embedded-image"
                image_file = await img.to_file(
                    filename=f"embed-{safe_name}", use_cached=True
                )
                embed.set_image(url=f"attachment://{image_file.filename}")

            send_options = {"embed": embed, "view": view}
            if image_file is not None:
                send_options["file"] = image_file
            await channel.send(**send_options)
        except (
            OSError,
            discord.Forbidden,
            discord.NotFound,
            discord.HTTPException,
        ) as exc:
            print(f"[!] /say could not send to channel {channel.id}: {exc}")
            await interaction.followup.send(
                "I could not send there. Check that I can view the channel, send "
                "messages, embed links, and attach files if an image was supplied.",
                ephemeral=True,
            )
            return
        finally:
            if image_file is not None:
                image_file.close()

        await interaction.followup.send("Sent!", ephemeral=True)


    dico = {}

    @app_commands.command(name="addbutton", description="Add a button to a message")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def addbutton(self, interaction: discord.Interaction, messageid: str, content: str, emoji: str, role: discord.Role=None,link:str=None):
        print_interaction_info(interaction)
        if link is None and role is None:
            return await interaction.response.send_message("Select a role/link.", ephemeral=True)
        if not content or len(content) > 80:
            return await interaction.response.send_message(
                "Button labels must contain 1 to 80 characters.", ephemeral=True
            )
        if role is not None:
            bot_member = interaction.guild.me
            invoker_is_owner = interaction.user.id == interaction.guild.owner_id
            if (
                role.is_default()
                or role.managed
                or bot_member is None
                or role >= bot_member.top_role
                or (not invoker_is_owner and role >= interaction.user.top_role)
            ):
                return await interaction.response.send_message(
                    "That role cannot be managed by both you and the bot.",
                    ephemeral=True,
                )

        await interaction.response.defer(ephemeral=True)
        try:
            messageid = int(messageid)
            message = await interaction.channel.fetch_message(messageid)
            view, _ = self._rebuild_button_view(message, messageid)
            if link is None:
                button_custom_id = f"role_button:{messageid}:{role.id}"
                view.add_item(
                    RoleToggleButton(content, emoji, role.id, button_custom_id)
                )
            else:
                view.add_item(
                    Button(label=content, style=discord.ButtonStyle.link, url=link)
                )

            if link is None:
                with sqlite3.connect("Data/user.db") as conn:
                    conn.execute(
                        "INSERT INTO roles "
                        "(channelid, messageid, roleid, emoji, content, custom_id) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            interaction.channel.id,
                            messageid,
                            role.id,
                            emoji,
                            content,
                            button_custom_id,
                        ),
                    )
                    await message.edit(view=view)
            else:
                await message.edit(view=view)
            await interaction.followup.send("Button added!", ephemeral=True)
        except (TypeError, ValueError, sqlite3.Error, discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            print(f"[!] /addbutton failed: {exc}")
            await interaction.followup.send(
                f"Could not add the button: {exc}", ephemeral=True
            )


    @app_commands.command(name="removebuttons", description="Remove buttons from a message")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def removebuttons(self, interaction: discord.Interaction, messageid: str, content: str):
        print_interaction_info(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            messageid = int(messageid)
            message = await interaction.channel.fetch_message(messageid)
            new_view, removed = self._rebuild_button_view(
                message, messageid, excluded_label=content
            )
            if not removed:
                await interaction.followup.send(
                    "No button with that label was found.", ephemeral=True
                )
                return
            await message.edit(view=new_view)
            with sqlite3.connect("Data/user.db") as conn:
                conn.execute(
                    "DELETE FROM roles WHERE messageid = ? AND content = ?",
                    (messageid, content),
                )
            await interaction.followup.send("Button removed!", ephemeral=True)
        except (TypeError, ValueError, sqlite3.Error, discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            print(f"[!] /removebuttons failed: {exc}")
            await interaction.followup.send(
                f"Could not remove the button: {exc}", ephemeral=True
            )

    @app_commands.command(name="help", description="Displays all commands")
    async def help(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        select = Select(
            placeholder="Which help section do you want ?",
            options=[
                SelectOption(label="📈 Economy commands", description="Shows all the economy commands", value="1"),
                SelectOption(label="📢 Invites commands", description="Shows all the economy commands", value="2"),
                SelectOption(label="🎉 Fun commands", description="Shows all the economy commands", value="3"),
                SelectOption(label="🎁 Giveaways commands", description="Shows all the economy commands", value="4"),
                SelectOption(label="🔨 Moderation commands", description="Shows all the economy commands", value="5"),
                SelectOption(label="🔒 Admin commands", description="Shows all the economy commands", value="6"),
            ])
        async def callback(interaction):
            if select.values[0] == "1":
                await interaction.response.edit_message(embed=eco_embed, view=None)
            elif select.values[0] == "2":
                await interaction.response.edit_message(embed=invite_embed, view=None)
            elif select.values[0] == "3":
                await interaction.response.edit_message(embed=fun_embed, view=None)
            elif select.values[0] == "4":
                await interaction.response.edit_message(embed=give_embed, view=None)
            elif select.values[0] == "5":
                await interaction.response.edit_message(embed=mod_embed, view=None)
            elif select.values[0] == "6":
                await interaction.response.edit_message(embed=admin_embed, view=None)

        select.callback = callback

        embed = discord.Embed(title="Help", colour=self.client.colour, description="Select a category to see the commands")

        eco_embed = discord.Embed(title="Economy", colour=self.client.colour, description="Here are all the economy commands").add_field(name="Balance", value="`/balance`", inline=False).add_field(name="Leaderboard", value="`/leaderboard`", inline=False).add_field(name="Daily", value="`/daily`", inline=False).add_field(name="Work", value="`/work`", inline=False).add_field(name="Rob", value="`/rob`", inline=False).add_field(name="Bet", value="`/bet`", inline=False).add_field(name="Crash", value="`/crash`", inline=False).add_field(name="Dice", value="`/dice`", inline=False).add_field(name="Send", value="`/send`", inline=False).add_field(name="Addmoney", value="`/addmoney`", inline=False).add_field(name="Removemoney", value="`/removemoney`", inline=False)
        invite_embed = discord.Embed(title="Invites", colour=self.client.colour, description="Here are all the invites commands").add_field(name="Invites", value="`/invites`", inline=False)
        fun_embed = discord.Embed(title="Fun", colour=self.client.colour, description="Here are all the fun commands").add_field(name="Coinflip", value="`/coinflip`", inline=False).add_field(name="RPS", value="`/rps`", inline=False).add_field(name="Play", value="`/play`", inline=False).add_field(name="Stop", value="`/stop`", inline=False).add_field(name="Join", value="`/join`", inline=False)
        give_embed = discord.Embed(title="Giveaways", colour=self.client.colour, description="Here are all the giveaways commands").add_field(name="Giveaway", value="`/giveaway`", inline=False).add_field(name="End", value="`/end`", inline=False).add_field(name="Reroll", value="`/reroll`", inline=False)
        mod_embed = discord.Embed(title="Moderation", colour=self.client.colour, description="Here are all the moderation commands").add_field(name="Ban", value="`/ban`", inline=False).add_field(name="Kick", value="`/kick`", inline=False).add_field(name="Unban", value="`/unban`", inline=False).add_field(name="Mute", value="`/mute`", inline=False).add_field(name="Unmute", value="`/unmute`", inline=False).add_field(name="Nuke", value="`/nuke`", inline=False).add_field(name="Purge", value="`/purge`", inline=False).add_field(name="Lock", value="`/lock`", inline=False).add_field(name="Unlock", value="`/unlock`", inline=False).add_field(name="Warn", value="`/warn`", inline=False).add_field(name="Warnings", value="`/warnings`", inline=False).add_field(name="Sticky", value="`/sticky`", inline=False).add_field(name="Unsticky", value="`/unsticky`", inline=False)
        admin_embed = discord.Embed(title="Admin", colour=self.client.colour, description="Here are all the admin commands").add_field(name="Steal", value="`/steal`", inline=False).add_field(name="Ticketpanel", value="`/ticketpanel`", inline=False).add_field(name="Verifypanel", value="`/verifypanel`", inline=False).add_field(name="Reactionroles", value="`/reactionroles`", inline=False).add_field(name="Voicepanel", value="`/voicepanel`", inline=False).add_field(name="Stats", value="`/stats`", inline=False)

        async def callback_1(interaction):
            await interaction.response.edit_message(embed=eco_embed, view=view1)
        async def callback_2(interaction):
            await interaction.response.edit_message(embed=embed, view=view)
        async def callback_3(interaction):
            await interaction.response.edit_message(embed=invite_embed, view=view2)
        async def callback_4(interaction):
            await interaction.response.edit_message(embed=eco_embed, view=view1)
        async def callback_5(interaction):
            await interaction.response.edit_message(embed=fun_embed, view=view3)
        async def callback_6(interaction):
            await interaction.response.edit_message(embed=invite_embed, view=view2)
        async def callback_7(interaction):
            await interaction.response.edit_message(embed=give_embed, view=view4)
        async def callback_8(interaction):
            await interaction.response.edit_message(embed=fun_embed, view=view3)
        async def callback_9(interaction):
            await interaction.response.edit_message(embed=mod_embed, view=view5)
        async def callback_10(interaction):
            await interaction.response.edit_message(embed=give_embed, view=view4)
        async def callback_11(interaction):
            await interaction.response.edit_message(embed=admin_embed, view=view6)
        async def callback_12(interaction):
            await interaction.response.edit_message(embed=mod_embed, view=view5)


        view = View()
        next = Button(label="Next", style=discord.ButtonStyle.green)
        view.add_item(next)
        next.callback = callback_1

        view1 = View()
        previous_1 = Button(label="Previous", style=discord.ButtonStyle.green)
        view1.add_item(previous_1)
        previous_1.callback = callback_2
        next_1 = Button(label="Next", style=discord.ButtonStyle.green)
        view1.add_item(next_1)
        next_1.callback = callback_3

        view2 = View()
        previous_2 = Button(label="Previous", style=discord.ButtonStyle.green)
        view2.add_item(previous_2)
        previous_2.callback = callback_4
        next_2 = Button(label="Next", style=discord.ButtonStyle.green)
        view2.add_item(next_2)
        next_2.callback = callback_5

        view3 = View()
        previous_3 = Button(label="Previous", style=discord.ButtonStyle.green)
        view3.add_item(previous_3)
        previous_3.callback = callback_6
        next_3 = Button(label="Next", style=discord.ButtonStyle.green)
        view3.add_item(next_3)
        next_3.callback = callback_7

        view4 = View()
        previous_4 = Button(label="Previous", style=discord.ButtonStyle.green)
        view4.add_item(previous_4)
        previous_4.callback = callback_8
        next_4 = Button(label="Next", style=discord.ButtonStyle.green)
        view4.add_item(next_4)
        next_4.callback = callback_9

        view5 = View()
        previous_5 = Button(label="Previous", style=discord.ButtonStyle.green)
        view5.add_item(previous_5)
        previous_5.callback = callback_10
        next_5 = Button(label="Next", style=discord.ButtonStyle.green)
        view5.add_item(next_5)
        next_5.callback = callback_11

        view6 = View()
        previous_6 = Button(label="Previous", style=discord.ButtonStyle.green)
        view6.add_item(previous_6)
        previous_6.callback = callback_12

        select_view = View()
        select_view.add_item(select)






        await interaction.response.send_message(embed=embed, view=select_view)
        return


    @app_commands.command(name="unsticky", description="Remove a stickied message (Admin)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def unsticky(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        if interaction.channel.id not in self.client.sticky:
            await interaction.response.send_message(f"🗂️ There is no stickied message here.")
            return
        else:
            conn = sqlite3.connect('sticky.db')
            cursor = conn.cursor()
            cursor.execute('SELECT * from infos_salos')
            rows = cursor.fetchall()
            cursor.execute('SELECT * FROM infos_salos WHERE id_salon = ?', (interaction.channel.id,))
            existing_entry = cursor.fetchone()
            if existing_entry is None:
                conn.close()
                self.client.sticky.pop(interaction.channel.id, None)
                await interaction.response.send_message(
                    "No stored sticky message was found.", ephemeral=True
                )
                return
            if existing_entry[1] == "":
                conn.close()
                self.client.sticky.pop(interaction.channel.id, None)
                await interaction.response.send_message(
                    "The sticky message was already disabled.", ephemeral=True
                )
                return
            if existing_entry[1] != "":
            # Mettre à jour l'entrée existante
                cursor.execute('UPDATE infos_salos SET contenu = "" , message_limit = "" WHERE id_salon = ?', (interaction.channel.id,))
                cursor.execute('SELECT * from infos_salos')
                rows = cursor.fetchall()
                # print(rows)
                conn.commit()
                conn.close()
                del self.client.sticky[interaction.channel.id]
                await interaction.response.send_message(f"🗂️ Unstickied the message.")
                return


    @app_commands.command(name="sticky", description="Sticky a message (Admin)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def sticky(self, interaction: discord.Interaction,messagelimit: app_commands.Range[int, 1, 100], member: discord.Member = None,):
        print_interaction_info(interaction)
        member = interaction.user if member is None else member
        lastmsg = None
        async for msg in interaction.channel.history(limit=None, oldest_first=False):
            if msg.author == member:
                lastmsg = msg
                break


        if not lastmsg:
            await interaction.response.send_message("No message to stick.", ephemeral=True)
            return

        sticky_object = {
            'content': lastmsg.content,
            'messageLimit': messagelimit
        }



        conn = sqlite3.connect('sticky.db')
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM infos_salos WHERE id_salon = ?', (interaction.channel.id,))
        existing_entry = cursor.fetchone()
        # print(existing_entry)
        if existing_entry:
        # Mettre à jour l'entrée existante
            cursor.execute('UPDATE infos_salos SET contenu = ?, message_limit = ? WHERE id_salon = ?',(sticky_object['content'], sticky_object['messageLimit'], interaction.channel.id))
            # cursor.execute('UPDATE infos_salons SET contenu = ? WHERE id_salon = ?', (lastmsg.content, interaction.channel.id))
        else:
            # Ajouter une nouvelle entrée
            cursor.execute('INSERT INTO infos_salos (id_salon, contenu, message_limit) VALUES (?, ?, ?)',(interaction.channel.id, sticky_object['content'], sticky_object['messageLimit']))

            # cursor.execute('INSERT INTO infos_salons (id_salon, contenu) VALUES (?, ?)', (interaction.channel.id, lastmsg.content))
        conn.commit()
        conn.close()


        @tasks.loop(seconds=12)
        async def stickyloop(channel: discord.TextChannel, sticky_object: dict):
            message, message_limit = sticky_object['content'], sticky_object['messageLimit']
            # print(message)
            # print(message_limit)
            if channel.id not in self.client.sticky:
                stickyloop.stop()
                return

            elif self.client.sticky[channel.id]['content'] != message:
                print('stopping loop')
                stickyloop.stop()
                return

            msgz = []
            async for msg in channel.history(limit=message_limit, oldest_first=False):
                msgz.append(msg.content)

            if message not in msgz:
                await channel.send(f"{message}")


        self.client.sticky[interaction.channel.id] = sticky_object
        stickyloop.start(interaction.channel, sticky_object)
        conn = sqlite3.connect('sticky.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM infos_salos")
        rows = cursor.fetchall()
        # print('sending rows from db')
        # print(rows)
        conn.close()

        await interaction.response.send_message(f"🗂️ Stickied.")
        return






    @app_commands.command(name="coinflip", description="Play coinflip with the bot")
    #@app_commands.choices(choice=[
        #app_commands.Choice(name="Heads", value="Heads"),
       # app_commands.Choice(name="Tails", value="Tails"),
    #])
    async def coinflip(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        if interaction.user.avatar is None:
            userpfp = interaction.user.default_avatar
        else:
            userpfp = interaction.user.avatar

        choices = ["heads", "tails"]
        choice = random.choice(choices)

        emb = discord.Embed(colour=discord.Colour.blue())
        emb.set_author(name=f"{choice.capitalize()}!", icon_url=userpfp)
        if choice == "heads":
            emb.set_image(url="https://i.ibb.co/rHtXXz0/heads-coinflip-1.gif")
        else:
            emb.set_image(url="https://i.ibb.co/DkvgBCt/coins-tails.gif")
        await interaction.response.send_message(embed=emb)
        return



    @app_commands.command(name="rps", description="Play rock/paper/scissors with the bot")
    @app_commands.choices(choice=[
        app_commands.Choice(name="Rock", value="Rock"),
        app_commands.Choice(name="Paper", value="Paper"),
        app_commands.Choice(name="Scissors", value="Scissors")
    ])
    async def rps(self, interaction: discord.Interaction, choice: app_commands.Choice[str]):
        print_interaction_info(interaction)
        if interaction.user.avatar is None:
            userpfp = interaction.user.default_avatar
        else:
            userpfp = interaction.user.avatar

        # AUTHOR URL?

        choices = ["rock", "paper", "scissors"]
        choice = choice.value.lower()
        botchoice = random.choice(choices)
        yourem = choice.replace("rock", "🪨").replace("paper", "📜").replace("scissors", "✂️")
        botem = botchoice.replace("rock", "🪨").replace("paper", "📜").replace("scissors", "✂️")
        if choice == botchoice:
            emb = discord.Embed(title="Draw!", description=f"{interaction.user.mention} {yourem}\n{self.client.user.mention} {botem}", colour=discord.Colour.grey())
            #emb.set_image(url="https://giphy.com/embed/3oEjHVNeiP3utYQyoE")
            await interaction.response.send_message(embed=emb)
            return
        elif choice == "rock":
            if botchoice == "paper":
                emb = discord.Embed(title="You lost!", description=f"{interaction.user.mention} {yourem}\n{self.client.user.mention} {botem}", colour=discord.Colour.red())
                #emb.set_image(url="https://giphy.com/embed/3oEjHVNeiP3utYQyoE")
                await interaction.response.send_message(embed=emb)
                return
            elif botchoice == "scissors":
                emb = discord.Embed(title="You win!", description=f"{interaction.user.mention} {yourem}\n{self.client.user.mention} {botem}", colour=self.client.colour)
                #emb.set_image(url="https://giphy.com/embed/3oEjHVNeiP3utYQyoE")
                await interaction.response.send_message(embed=emb)
                return
        elif choice == "paper":
            if botchoice == "scissors":
                emb = discord.Embed(title="You lost!", description=f"{interaction.user.mention} {yourem}\n{self.client.user.mention} {botem}", colour=discord.Colour.red())
                #emb.set_image(url="https://giphy.com/embed/3oEjHVNeiP3utYQyoE")
                await interaction.response.send_message(embed=emb)
                return
            elif botchoice == "rock":
                emb = discord.Embed(title="You win!", description=f"{interaction.user.mention} {yourem}\n{self.client.user.mention} {botem}", colour=self.client.colour)
                #emb.set_image(url="https://giphy.com/embed/3oEjHVNeiP3utYQyoE")
                await interaction.response.send_message(embed=emb)
                return
        elif choice == "scissors":
            if botchoice == "rock":
                emb = discord.Embed(title="You lost!", description=f"{interaction.user.mention} {yourem}\n{self.client.user.mention} {botem}", colour=discord.Colour.red())
                #emb.set_image(url="https://giphy.com/embed/3oEjHVNeiP3utYQyoE")
                await interaction.response.send_message(embed=emb)
                return
            elif botchoice == "paper":
                emb = discord.Embed(title="You win!", description=f"{interaction.user.mention} {yourem}\n{self.client.user.mention} {botem}", colour=self.client.colour)
                #emb.set_image(url="https://giphy.com/embed/3oEjHVNeiP3utYQyoE")
                await interaction.response.send_message(embed=emb)
                return



class RoleToggleButton(Button):
    def __init__(
        self,
        content: str,
        emoji,
        role_id: int,
        custom_id: str = None,
        *,
        row: int = None,
        disabled: bool = False,
    ):
        self.role_id = int(role_id)
        super().__init__(
            label=content,
            style=discord.ButtonStyle.green,
            custom_id=custom_id or f"role_button:{self.role_id}",
            emoji=emoji,
            row=row,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild_id != getattr(
            interaction.client, "configured_guild_id", None
        ):
            await interaction.response.send_message(
                "This bot is not configured for this server.", ephemeral=True
            )
            return
        role = interaction.guild.get_role(self.role_id)
        bot_member = interaction.guild.me
        if (
            role is None
            or role.managed
            or bot_member is None
            or role >= bot_member.top_role
        ):
            await interaction.response.send_message(
                "This role is unavailable or cannot be managed by the bot.",
                ephemeral=True,
            )
            return
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role, reason="Self-service role button")
            await interaction.response.send_message("Role removed!", ephemeral=True)
        else:
            await interaction.user.add_roles(role, reason="Self-service role button")
            await interaction.response.send_message("Role added!", ephemeral=True)


class mAddBtn(View):
    def __init__(self, content: str=None, emoji: str=None, role: discord.Role=None, custom_id: str=None):
        try:
            super().__init__(timeout=None)
            self.add_item(RoleToggleButton(content, emoji, role.id, custom_id))
        except Exception as e:
            print(e)








async def setup(client):
    await client.add_cog(MembersCommand(client))
