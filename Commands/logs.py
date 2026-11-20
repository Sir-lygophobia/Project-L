import json
import discord
from discord.ext import commands
import asyncio
import datetime

print("[!] Loading logging extension")


def embed_field_text(value, limit=1024):
    """Return nonempty text that fits in one Discord embed field."""
    text = str(value or "\u200b")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class LogsCommand(commands.Cog):
    def __init__(self, client):
        self.client = client

    @staticmethod
    async def recent_audit_entry(guild, action, target_id):
        try:
            async for entry in guild.audit_logs(
                limit=50,
                action=action,
                after=datetime.datetime.utcnow() - datetime.timedelta(seconds=15),
                oldest_first=False,
            ):
                if getattr(entry.target, "id", None) == target_id:
                    return entry
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[!] Could not read audit logs in guild {guild.id}: {exc}")
        return None


    async def post(
        self,
        guild,
        type,
        configName,
        user=None,
        target=None,
        reason=None,
        role=None,
        channel=None,
        before=None,
        after=None,
        content=None,
        media_urls=None  # Add a parameter for media URLs
    ):
        try:
            with open("Data/config.json", "r", encoding="utf-8") as f:
                config = json.load(f)
            logs = config["logChannelIds"]
            channelid = logs.get(configName)
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            print(f"[!] Could not load logging configuration: {exc}")
            return

        if guild is None or guild.id != config["guildId"] or not channelid:
            return

        logschannel = self.client.get_channel(channelid)
        if logschannel is None:
            try:
                logschannel = await self.client.fetch_channel(channelid)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                print(f"Could not access logs channel {channelid}: {exc}")
                return
        if getattr(logschannel, "guild", None) != guild:
            print("No logs channel configured.")
            return

        emb = discord.Embed(color=self.client.colour, timestamp=datetime.datetime.utcnow())
        if target is not None:
            emb.add_field(name="User", value=embed_field_text(f"{target.mention} ({target.id})"), inline=True)
            emb.set_author(name=target.name, icon_url=target.display_avatar.url)
        if user is not None:
            emb.add_field(name="Moderator", value=embed_field_text(f"{user.mention} ({user.id})"), inline=True)
        if before is not None:
            emb.add_field(name="Before", value=embed_field_text(before), inline=False)
        if after is not None:
            emb.add_field(name="After", value=embed_field_text(after), inline=False)
        if reason is not None:
            emb.add_field(name="Reason", value=embed_field_text(reason), inline=False)
        if role is not None:
            emb.add_field(name="Role", value=embed_field_text(role), inline=False)
        if channel is not None:
            emb.add_field(name="Channel", value=embed_field_text(channel), inline=False)
        if content is not None:
            emb.add_field(name="Content", value=embed_field_text(content), inline=False)

        # Check for media attachments
        if media_urls:
            media_field_value = "\n".join(media_urls)
            emb.add_field(name="Media Attachments", value=embed_field_text(media_field_value), inline=False)
        emb.set_footer(text=guild.name)
        try:
            await logschannel.send(content=f"**{type}**", embed=emb)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            print(f"[!] Could not send {type} log to channel {channelid}: {exc}")
        return


    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        print(f"Message edited: {before.id}")

        if before.content == after.content:
            return
        await LogsCommand.post(self, guild=before.guild, type="Message Edited",configName='Message', target=before.author, channel=before.channel.mention, before=before.content, after=after.content)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.Member):
        await asyncio.sleep(0.5)
        found_entry = await self.recent_audit_entry(
            guild, discord.AuditLogAction.ban, user.id
        )

        if found_entry is None:
            return

        await LogsCommand.post(self, guild=guild, type="User Banned",configName='Ban_Unban' ,user=found_entry.user, target=user, reason=found_entry.reason)




    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.Member):
        await asyncio.sleep(0.5)
        found_entry = await self.recent_audit_entry(
            guild, discord.AuditLogAction.unban, user.id
        )
        if found_entry is None:
            return
        await LogsCommand.post(self, guild=guild, type="User Unbanned",configName='Ban_Unban', user=found_entry.user, target=user, reason=found_entry.reason)


    @commands.Cog.listener()
    async def on_member_remove(self, user: discord.Member):

        await LogsCommand.post(self, guild=user.guild, type="User Left", configName='Join_Leave', user=None,target=user, reason=None)




    @commands.Cog.listener()
    async def on_member_join(self, user: discord.Member):
        await asyncio.sleep(0.5)
        await LogsCommand.post(self, guild=user.guild, type="User Joined", configName='Join_Leave' ,target=user)


    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles == after.roles:
            return
        await asyncio.sleep(0.5)
        found_entry = await self.recent_audit_entry(
            after.guild, discord.AuditLogAction.member_role_update, after.id
        )

        if found_entry is None:
            return

        added_role = None
        for role in after.roles:
            if role.name == "@everyone":
                continue
            if role not in before.roles:
                added_role = role.mention
                break

        removed_role = None
        for role in before.roles:
            if role.name == "@everyone":
                continue
            if role not in after.roles:
                removed_role = role.mention
                break

        if added_role is not None:
            await LogsCommand.post(self, guild=after.guild, type="Role Added", configName='Role', user=found_entry.user,target=after, reason=found_entry.reason, role=added_role)
        elif removed_role is not None:
            await LogsCommand.post(self, guild=after.guild, type="Role Removed", configName='Role', user=found_entry.user,target=after, reason=found_entry.reason, role=removed_role)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        print(f"Message deleted: {message.id}")
        content = message.content
        media_urls = [attachment.url for attachment in message.attachments]

        if not content and media_urls:
            content = "Media attachment(s) deleted."

        await LogsCommand.post(
            self,
            guild=message.guild,
            type="Message Deleted",configName='Message',
            target=message.author,
            channel=message.channel.mention,
            content=content,
            media_urls=media_urls  # Pass the media URLs to the post method
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(self,before,after):
        if before.name != after.name:
            await LogsCommand.post(
            self,
            guild=before.guild,
            type="Channel Updated",configName='Channel',
            channel=after.mention,
            before=before.name,
            after=after.name,
        )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        await asyncio.sleep(0.5)
        await LogsCommand.post(self, guild=role.guild, type="Role Created",configName='Role', role=role.mention)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await LogsCommand.post(self, guild=role.guild, type="Role Deleted",configName='Role', role=role.name)


async def setup(client):
    await client.add_cog(LogsCommand(client))
