import discord
from discord.ext import commands
from pathlib import Path
import asyncio
import sqlite3
import re
from config_loader import load_config


TICKET_TOPIC_PATTERN = re.compile(r"^ticket-owner:(\d+)$")


def ticket_owner_id(channel):
    match = TICKET_TOPIC_PATTERN.fullmatch(str(getattr(channel, "topic", "") or ""))
    return int(match.group(1)) if match else None


def ticket_staff_overwrites(category, existing_overwrites=None):
    """Build private overwrites that retain access for staff and the bot."""
    source_overwrites = (
        category.overwrites if existing_overwrites is None else existing_overwrites
    )
    overwrites = dict(source_overwrites)

    everyone = overwrites.get(category.guild.default_role, discord.PermissionOverwrite())
    everyone.view_channel = False
    overwrites[category.guild.default_role] = everyone

    for role in category.guild.roles:
        if role.is_default():
            continue
        if role.permissions.administrator or role.permissions.manage_channels:
            staff_permissions = overwrites.get(role, discord.PermissionOverwrite())
            staff_permissions.update(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )
            overwrites[role] = staff_permissions

    if category.guild.me is not None:
        bot_permissions = overwrites.get(
            category.guild.me, discord.PermissionOverwrite()
        )
        bot_permissions.update(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            attach_files=True,
            embed_links=True,
        )
        overwrites[category.guild.me] = bot_permissions
    return overwrites


def ticket_permission_overwrites(category, owner, existing_overwrites=None):
    """Build private, atomic overwrites for a ticket owner and staff."""
    overwrites = ticket_staff_overwrites(category, existing_overwrites)
    owner_permissions = overwrites.get(owner, discord.PermissionOverwrite())
    owner_permissions.update(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
    )
    overwrites[owner] = owner_permissions
    return overwrites


class OnInteractionEvent(commands.Cog):
    def __init__(self, client):
        self.client = client
        self._ticket_permissions_repaired = False


    def connect(self):
        try:
            conn = sqlite3.connect(f"Data/users.db")
            curs = conn.cursor()
            return conn, curs
        except Exception as exc:
            print(exc)
            return None

    @commands.Cog.listener()
    async def on_ready(self):
        if self._ticket_permissions_repaired:
            return
        config = load_config()
        guild = self.client.get_guild(config["guildId"])
        if guild is None:
            return

        all_succeeded = True
        categories_secured = 0
        repaired = 0
        quarantined = 0
        for category_id in config["tickets"].values():
            category = guild.get_channel(category_id)
            if not isinstance(category, discord.CategoryChannel):
                all_succeeded = False
                continue
            try:
                await category.edit(
                    overwrites=ticket_staff_overwrites(category),
                    reason="Secure Project L ticket category",
                )
                categories_secured += 1
            except (discord.Forbidden, discord.HTTPException):
                all_succeeded = False
                print(f"[!] Could not secure ticket category {category.id}.")

            for channel in category.text_channels:
                owner_id = ticket_owner_id(channel)
                if owner_id is None:
                    overwrites = ticket_staff_overwrites(category)
                else:
                    owner = guild.get_member(owner_id) or discord.Object(id=owner_id)
                    overwrites = ticket_permission_overwrites(
                        category,
                        owner,
                        existing_overwrites=channel.overwrites,
                    )
                try:
                    await channel.edit(
                        overwrites=overwrites,
                        reason="Secure existing Project L ticket permissions",
                    )
                    if owner_id is None:
                        quarantined += 1
                    else:
                        repaired += 1
                except (discord.Forbidden, discord.HTTPException):
                    all_succeeded = False
                    print(f"[!] Could not secure existing ticket channel {channel.id}.")
        self._ticket_permissions_repaired = all_succeeded
        print(
            f"[OK] Secured {categories_secured} ticket category(s), "
            f"{repaired} owned ticket(s), and {quarantined} legacy ticket(s)."
        )

    @staticmethod
    def is_staff(interaction: discord.Interaction):
        permissions = interaction.user.guild_permissions
        return permissions.administrator or permissions.manage_channels

    @classmethod
    def can_access_ticket(cls, interaction: discord.Interaction):
        if cls.is_staff(interaction):
            return True
        return ticket_owner_id(interaction.channel) == interaction.user.id



    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        config = load_config()
        transcriptsid = config["transcripts"]
        verifiedrole = config["verified"]
        voicecategory = config["voicecategory"]
        streammerrole = config["streamRole"]


        if not interaction.data or "custom_id" not in interaction.data:
            return
        elif interaction.guild is None:
            return
        elif interaction.guild_id != config["guildId"]:
            return

        if interaction.user.avatar is None:
            userpfp = interaction.user.default_avatar
        else:
            userpfp = interaction.user.avatar

        class CloseView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=None)
                self.add_item(discord.ui.Button(emoji="📄", label=f"Transcript", style=discord.ButtonStyle.grey, custom_id=f"transcript"))
                self.add_item(discord.ui.Button(emoji="🔒", label=f"Close", style=discord.ButtonStyle.grey, custom_id=f"close"))

        class CloseView2(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=None)
                self.add_item(discord.ui.Button(emoji="🗑️", label=f"Delete", style=discord.ButtonStyle.grey, custom_id=f"delete"))
                self.add_item(discord.ui.Button(emoji="🔓", label=f"Reopen", style=discord.ButtonStyle.grey, custom_id=f"reopen"))

        if interaction.data['custom_id'] == "voicechannel":
            await interaction.response.defer(thinking=False, ephemeral=True)
            member = interaction.guild.get_member(interaction.user.id)
            role = interaction.guild.get_role(streammerrole)
            if role not in member.roles:
                await interaction.followup.send(f"You don't have the streamer role!", ephemeral=True)
                return

            category = interaction.guild.get_channel(voicecategory)
            if not isinstance(category, discord.CategoryChannel):
                await interaction.followup.send(
                    "The configured private-voice destination is not a category. "
                    "Ask an administrator to update it in the setup wizard.",
                    ephemeral=True,
                )
                return


            result = self.connect()
            if result is None:
                return

            userid = interaction.user.id

            conn, curs = result
            query = f"""SELECT voice FROM users WHERE userid={userid}"""
            curs.execute(query)
            records = curs.fetchall()
            if len(records) > 0:
                channelid = records[0][0]
                chan = self.client.get_channel(channelid)
                if chan is not None:
                    conn.close()
                    await interaction.followup.send(f"You already have a private voice channel: {chan.mention}", ephemeral=True)
                    return

            channel = await interaction.guild.create_voice_channel(name=interaction.user.name, category=category)
            await channel.set_permissions(member, administrator=True, manage_channels=True, connect=True, mute_members=True)
            curs.execute(
                "INSERT OR IGNORE INTO users(userid, balance, invited, left) "
                "VALUES(?, 0, 0, 0)",
                (userid,),
            )
            curs.execute(
                "UPDATE users SET voice = ? WHERE userid = ?",
                (channel.id, userid),
            )
            conn.commit()
            conn.close()
            await interaction.followup.send(f"Your private voice channel: {channel.mention}", ephemeral=True)
            return


        elif interaction.data['custom_id'] == "modmail_del":
            if not self.is_staff(interaction):
                await interaction.response.send_message(
                    "Only staff can delete modmail channels.", ephemeral=True
                )
                return
            valid_modmail_channel = (
                getattr(interaction.channel, "category_id", None) == config["modmail"]
                and re.fullmatch(
                    r"modmail-user:\d+",
                    str(getattr(interaction.channel, "topic", "") or ""),
                )
            )
            if not valid_modmail_channel:
                await interaction.response.send_message(
                    "This control is not attached to a valid modmail channel.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message("Deleting modmail channel in 3 seconds")
            await asyncio.sleep(3)
            try:
                await interaction.channel.delete(
                    reason=f"Modmail deleted by {interaction.user}"
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                print(f"Could not delete modmail channel {interaction.channel.id}: {exc}")
                await interaction.followup.send(
                    "The modmail channel could not be deleted. Check my permissions.",
                    ephemeral=True,
                )
            return



        elif interaction.data['custom_id'] == "roles":
            try:
                roleid = int(interaction.data['values'][0])
                role = interaction.guild.get_role(roleid)
            except Exception as exc:
                print(exc)
                await interaction.response.send_message("Role does not exist.", ephemeral=True)
                return
            if role is None:
                await interaction.response.send_message("Role does not exist.", ephemeral=True)
                return


            member = interaction.guild.get_member(interaction.user.id)
            if role not in member.roles:
                try:
                    await member.add_roles(role)
                    await interaction.response.send_message(f"You received {role.mention}", ephemeral=True)
                except Exception as exc:
                    await interaction.response.send_message("Could not give this role.", ephemeral=True)
                    return
            else:
                try:
                    await member.remove_roles(role)
                    await interaction.response.send_message(f"You no longer have {role.mention}", ephemeral=True)
                except Exception as exc:
                    await interaction.response.send_message("Could not remove this role.", ephemeral=True)
                    return

        elif interaction.data['custom_id'] == "verify":
            role = interaction.guild.get_role(verifiedrole)
            if role is None:
                await interaction.response.send_message(f"Something went wrong.", ephemeral=True)
                return

            member = interaction.guild.get_member(interaction.user.id)
            if role in member.roles:
                await interaction.response.send_message(f"You are already verified!", ephemeral=True)
                return
            else:
                try:
                    await member.add_roles(role)
                    await interaction.response.send_message(f"Verified!", ephemeral=True)
                    return
                except Exception as exc:
                    if interaction.response.is_done():
                        await interaction.followup.send("Unable to verify.", ephemeral=True)
                    else:
                        await interaction.response.send_message("Unable to verify.", ephemeral=True)
                    return

        elif interaction.data['custom_id'] in {
            "ticket_help",
            "ticket_suggestions",
            "ticket_report",
        }:
            tickettype = interaction.data['custom_id'].split("_")[1]
            categoryid = config["tickets"][f"category_{tickettype}"]
            await interaction.response.defer(ephemeral=True, thinking=True)
            ticketchannel = None
            try:
                ticketcat = interaction.guild.get_channel(categoryid)
                if ticketcat is None:
                    ticketcat = await interaction.guild.fetch_channel(categoryid)
                if not isinstance(ticketcat, discord.CategoryChannel):
                    await interaction.followup.send(
                        "The configured ticket destination is not a category. "
                        "Ask an administrator to update it in the setup wizard.",
                        ephemeral=True,
                    )
                    return
                if len(ticketcat.channels) >= 50:
                    await interaction.followup.send(
                        "This ticket category is full. Ask an administrator to archive old tickets.",
                        ephemeral=True,
                    )
                    return

                bot_member = interaction.guild.me
                if bot_member is None:
                    await interaction.followup.send(
                        "The bot could not resolve its server membership.", ephemeral=True
                    )
                    return
                permissions = ticketcat.permissions_for(bot_member)
                required_permissions = (
                    ("manage_channels", "Manage Channels"),
                    ("manage_roles", "Manage Roles"),
                    ("view_channel", "View Channel"),
                    ("send_messages", "Send Messages"),
                    ("embed_links", "Embed Links"),
                )
                missing_permissions = [
                    label
                    for attribute, label in required_permissions
                    if not getattr(permissions, attribute)
                ]
                if missing_permissions:
                    await interaction.followup.send(
                        "The bot is missing ticket permissions: "
                        + ", ".join(missing_permissions),
                        ephemeral=True,
                    )
                    return

                owner_topic = f"ticket-owner:{interaction.user.id}"
                existing_ticket = next(
                    (
                        channel
                        for channel in ticketcat.text_channels
                        if channel.topic == owner_topic
                    ),
                    None,
                )
                if existing_ticket is not None:
                    await interaction.followup.send(
                        f"You already have an open {tickettype} ticket: "
                        f"{existing_ticket.mention}",
                        ephemeral=True,
                    )
                    return

                ticketchannel = await interaction.guild.create_text_channel(
                    name=f"ticket-{tickettype}-{interaction.user.id}",
                    category=ticketcat,
                    topic=owner_topic,
                    overwrites=ticket_permission_overwrites(
                        ticketcat, interaction.user
                    ),
                    reason=f"{tickettype.capitalize()} ticket opened by {interaction.user}",
                )
                emb = discord.Embed(description=f"Welcome, {interaction.user.mention}\n\nPlease wait for support", colour=self.client.colour)
                emb.set_author(name=f"{tickettype.capitalize()} Ticket {interaction.user}", icon_url=userpfp)
                await ticketchannel.send(
                    embed=emb,
                    view=CloseView(),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await interaction.followup.send(
                    f"{tickettype.capitalize()} ticket created! {ticketchannel.mention}",
                    ephemeral=True,
                )
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
                discord.InvalidData,
                TypeError,
                ValueError,
            ) as exc:
                print(f"Could not create ticket in category {categoryid}: {exc}")
                if ticketchannel is not None:
                    try:
                        await ticketchannel.delete(
                            reason="Rolling back incomplete Project L ticket"
                        )
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
                await interaction.followup.send(
                    "The ticket could not be created. Ask an administrator to check the configured category and bot permissions.",
                    ephemeral=True,
                )
            return

        elif interaction.data['custom_id'] == "transcript":
            if ticket_owner_id(interaction.channel) is None:
                await interaction.response.send_message(
                    "This is not a valid ticket channel.", ephemeral=True
                )
                return
            if not self.can_access_ticket(interaction):
                await interaction.response.send_message(
                    "Only the ticket owner or staff can export this ticket.",
                    ephemeral=True,
                )
                return

            author = getattr(interaction.message.embeds[0], "author", None)
            ticketopener = getattr(author, "name", None) or str(
                ticket_owner_id(interaction.channel)
            )
            transcript_path = Path("Transcripts") / (
                f"ticket-{interaction.channel.id}-{interaction.id}.txt"
            )
            await interaction.response.defer(thinking=True, ephemeral=True)

            with transcript_path.open("w", encoding="utf-8") as transcript_file:
                transcript_file.write(
                    f"[!] Ticket opened by {ticketopener}\n"
                    f"[!] Opened at: {str(interaction.message.created_at)[:16]}\n\n\n"
                )
                async for message in interaction.channel.history(
                    limit=500, oldest_first=True
                ):
                    if message.author.id == self.client.user.id:
                        continue
                    attachments = ", ".join(
                        attachment.url for attachment in message.attachments
                    )
                    transcript_file.write(
                        f"\n* {str(message.created_at)[:16]} - {message.author}:\n"
                        f"{message.content}\nAttachments: {attachments}\n\n"
                    )
                transcript_file.write(
                    f"\n\n[!] Ticket closed by {interaction.user}\n"
                    f"[!] Closed at: {str(interaction.created_at)[:16]}"
                )

            logfile = None
            try:
                logfile = discord.File(transcript_path, filename=transcript_path.name)
                logchan = self.client.get_channel(transcriptsid)
                if logchan is None:
                    await interaction.followup.send(
                        "The transcript channel is not configured; here is your transcript.",
                        file=logfile,
                        ephemeral=True,
                    )
                    return

                await logchan.send(
                    f"**Ticket {ticketopener}**\n\n"
                    f"**Closed by:** {interaction.user}\n"
                    f"**Closed at:** {str(interaction.created_at)[:16]}",
                    file=logfile,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await interaction.followup.send(
                    f"Transcript saved in {logchan.mention}.", ephemeral=True
                )
            except (OSError, discord.Forbidden, discord.HTTPException) as exc:
                print(f"Could not upload ticket transcript: {exc}")
                await interaction.followup.send(
                    "Transcript could not be saved.", ephemeral=True
                )
            finally:
                if logfile is not None:
                    logfile.close()
                transcript_path.unlink(missing_ok=True)


        elif interaction.data['custom_id'] == "close":
            if not self.can_access_ticket(interaction):
                await interaction.response.send_message(
                    "Only the ticket owner or staff can close this ticket.",
                    ephemeral=True,
                )
                return
            emb = discord.Embed(description=f"Ticket closed by {interaction.user.mention}", colour=self.client.colour)
            emb.set_author(name=interaction.message.embeds[0].author.name, icon_url=interaction.message.embeds[0].author.icon_url)
            await interaction.response.send_message(embed=emb, view=CloseView2())
            owner_id = ticket_owner_id(interaction.channel)
            owner = interaction.guild.get_member(owner_id) if owner_id else None
            if owner is not None:
                await interaction.channel.set_permissions(
                    owner,
                    view_channel=True,
                    send_messages=False,
                    read_message_history=True,
                    reason=f"Ticket closed by {interaction.user}",
                )

            return

        elif interaction.data['custom_id'] == "delete":
            if not self.is_staff(interaction):
                await interaction.response.send_message(
                    "Only staff can delete tickets.", ephemeral=True
                )
                return
            if ticket_owner_id(interaction.channel) is None:
                await interaction.response.send_message(
                    "This is not a valid ticket channel.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                f"Ticket will be deleted in 10 seconds. Deleted by {interaction.user.mention}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await asyncio.sleep(10)
            try:
                await interaction.channel.delete(
                    reason=f"Ticket deleted by {interaction.user}"
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                print(f"Could not delete ticket channel {interaction.channel.id}: {exc}")
                await interaction.followup.send(
                    "The ticket could not be deleted. Check my permissions.",
                    ephemeral=True,
                )
            return


        elif interaction.data['custom_id'] == "reopen":
            if not self.is_staff(interaction):
                await interaction.response.send_message(
                    "Only staff can reopen tickets.", ephemeral=True
                )
                return
            owner_id = ticket_owner_id(interaction.channel)
            if owner_id is None:
                await interaction.response.send_message(
                    "This is not a valid ticket channel.", ephemeral=True
                )
                return
            await interaction.response.send_message(f"Ticket Reopened by {interaction.user.mention}")
            owner = interaction.guild.get_member(owner_id)
            if owner is not None:
                await interaction.channel.set_permissions(
                    owner,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    reason=f"Ticket reopened by {interaction.user}",
                )

            return





async def setup(client):
    await client.add_cog(OnInteractionEvent(client))
