import random
import sqlite3
import string
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from giveaway_utils import chunk_display_values, ended_description, parse_winner_count
from logFunction import print_interaction_info


DATABASE_PATH = Path(__file__).resolve().parents[1] / "Data" / "users.db"


class GiveawayCommands(commands.Cog):
    def __init__(self, client):
        self.client = client

    @staticmethod
    def _record(giveaway_id, channel_id):
        with sqlite3.connect(DATABASE_PATH) as conn:
            return conn.execute(
                "SELECT message, ended FROM giveaways WHERE id = ? AND channel = ?",
                (giveaway_id, channel_id),
            ).fetchone()

    @staticmethod
    def _mark_ended(giveaway_id):
        with sqlite3.connect(DATABASE_PATH) as conn:
            conn.execute(
                "UPDATE giveaways SET ended = 1 WHERE id = ?",
                (giveaway_id,),
            )

    async def _load_message(self, interaction, giveaway_id):
        record = self._record(giveaway_id, interaction.channel_id)
        if record is None:
            await interaction.followup.send("No giveaway found.", ephemeral=True)
            return None, None

        try:
            message = await interaction.channel.fetch_message(record[0])
        except discord.NotFound:
            await interaction.followup.send(
                "The giveaway message no longer exists.", ephemeral=True
            )
            return None, None
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[!] Could not fetch giveaway {giveaway_id}: {exc}")
            await interaction.followup.send(
                "I could not load that giveaway message. Check my channel permissions.",
                ephemeral=True,
            )
            return None, None

        if not message.embeds or not message.embeds[0].description:
            await interaction.followup.send(
                "That message is not a valid Project L giveaway.", ephemeral=True
            )
            return None, None
        return message, bool(record[1])

    async def _participants(self, message):
        participants = {}
        for reaction in message.reactions:
            if str(reaction.emoji) != "🎉":
                continue
            async for user in reaction.users():
                if user.id != self.client.user.id and not user.bot:
                    participants[user.id] = user
            break
        return list(participants.values())

    def _winner_embed(self, title, winners, footer):
        embed = discord.Embed(title=title, colour=self.client.colour)
        for index, mentions in enumerate(
            chunk_display_values([winner.mention for winner in winners])
        ):
            name = "Winners" if index == 0 else "Winners (continued)"
            embed.add_field(name=name, value=mentions, inline=False)
        embed.set_footer(text=footer)
        return embed

    @app_commands.command(name="reroll", description="Reroll a giveaway")
    @app_commands.checks.has_permissions(administrator=True)
    async def reroll(self, interaction: discord.Interaction, giveawayid: int):
        print_interaction_info(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            message, ended = await self._load_message(interaction, giveawayid)
            if message is None:
                return
            if not ended and "Giveaway ended" not in message.embeds[0].description:
                await interaction.followup.send(
                    "End this giveaway before rerolling it.", ephemeral=True
                )
                return

            winners = parse_winner_count(message.embeds[0].description)
            participants = await self._participants(message)
            if len(participants) < winners:
                await interaction.followup.send(
                    "Not enough participants for this giveaway.", ephemeral=True
                )
                return

            winner_list = random.sample(participants, k=winners)
            embed = self._winner_embed(
                message.embeds[0].title,
                winner_list,
                "Giveaway rerolled",
            )
            await interaction.followup.send(embed=embed)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[!] Could not reroll giveaway {giveawayid}: {exc}")
            await interaction.followup.send(
                "I could not read the reactions or post the result. Check my permissions.",
                ephemeral=True,
            )
        except sqlite3.Error as exc:
            print(f"[!] Giveaway database error: {exc}")
            await interaction.followup.send(
                "The giveaway database is temporarily unavailable.", ephemeral=True
            )

    @app_commands.command(name="end", description="End a giveaway")
    @app_commands.checks.has_permissions(administrator=True)
    async def end(self, interaction: discord.Interaction, giveawayid: int):
        print_interaction_info(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            message, ended = await self._load_message(interaction, giveawayid)
            if message is None:
                return
            embed = message.embeds[0]
            if ended or "Giveaway ended" in embed.description:
                if not ended:
                    self._mark_ended(giveawayid)
                await interaction.followup.send(
                    "Giveaway has already ended.", ephemeral=True
                )
                return

            winners = parse_winner_count(embed.description)
            participants = await self._participants(message)

            embed.description = ended_description(embed.description)
            await message.edit(embed=embed)
            self._mark_ended(giveawayid)

            if len(participants) < winners:
                await interaction.followup.send(
                    "Giveaway ended, but there were not enough participants.",
                    ephemeral=True,
                )
                return

            winner_list = random.sample(participants, k=winners)
            result = self._winner_embed(embed.title, winner_list, "Giveaway ended")
            await interaction.followup.send(embed=result)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            print(f"[!] Could not end giveaway {giveawayid}: {exc}")
            await interaction.followup.send(
                "I could not finish that giveaway. Check my message and reaction permissions.",
                ephemeral=True,
            )
        except sqlite3.Error as exc:
            print(f"[!] Giveaway database error: {exc}")
            await interaction.followup.send(
                "The giveaway database is temporarily unavailable.", ephemeral=True
            )

    @app_commands.command(name="giveaway", description="Start a giveaway")
    @app_commands.checks.has_permissions(administrator=True)
    async def giveaway(
        self,
        interaction: discord.Interaction,
        prize: app_commands.Range[str, 1, 150],
        winners: app_commands.Range[int, 1, 100],
        minutes: app_commands.Range[int, 1, 59] = None,
        hours: app_commands.Range[int, 1, 23] = None,
        days: app_commands.Range[int, 1, 365] = None,
    ):
        print_interaction_info(interaction)
        total_minutes = (minutes or 0) + (hours or 0) * 60 + (days or 0) * 1440
        if total_minutes == 0:
            await interaction.response.send_message(
                "Please give a duration.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        message = None
        try:
            with sqlite3.connect(DATABASE_PATH) as conn:
                for _ in range(100):
                    giveaway_id = int("".join(random.choices(string.digits, k=4)))
                    exists = conn.execute(
                        "SELECT 1 FROM giveaways WHERE id = ?", (giveaway_id,)
                    ).fetchone()
                    if exists is None:
                        break
                else:
                    raise RuntimeError("Could not allocate a giveaway ID.")

            ends_at = int(time.time()) + total_minutes * 60
            embed = discord.Embed(
                title=prize,
                description=(
                    f"**Started by:** {interaction.user.mention}\n"
                    f"**Winners:** {winners}\n"
                    f"**Ends at:** <t:{ends_at}>\n\n"
                    "React with 🎉 to enter."
                ),
                colour=self.client.colour,
            )
            embed.set_footer(text=f"ID: {giveaway_id}")
            message = await interaction.channel.send(embed=embed)
            await message.add_reaction("🎉")

            with sqlite3.connect(DATABASE_PATH) as conn:
                conn.execute(
                    "INSERT INTO giveaways(id, ends, channel, message, ended) "
                    "VALUES (?, ?, ?, ?, 0)",
                    (giveaway_id, ends_at, interaction.channel_id, message.id),
                )
            await interaction.followup.send(
                f"Giveaway created. ID: {giveaway_id}", ephemeral=True
            )
        except (discord.Forbidden, discord.HTTPException, sqlite3.Error, RuntimeError) as exc:
            print(f"[!] Could not create giveaway: {exc}")
            if message is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    pass
            await interaction.followup.send(
                "The giveaway could not be created. Check my channel permissions and try again.",
                ephemeral=True,
            )

    async def cog_app_command_error(self, interaction, error):
        print(f"[!] Giveaway command error: {error}")
        message = (
            "You need Administrator permission to use this command."
            if isinstance(error, app_commands.MissingPermissions)
            else "The giveaway command failed unexpectedly. Please try again."
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(client):
    await client.add_cog(GiveawayCommands(client))
