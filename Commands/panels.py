import json
import discord
from discord import app_commands
from discord.ext import commands
import time
import os
import asyncio
import requests
from logFunction import print_interaction_info
from config_loader import BASE_DIR, load_config, load_role_ids


SELF_ROLES_IMAGE = BASE_DIR / "Data" / "self_roles.gif"
SELF_ROLE_PANEL_TITLES = frozenset(
    {
        "color roles",
        "gender roles",
        "age roles",
        "platform roles",
        "server notifications",
        "game roles",
    }
)


def normalized_panel_title(title):
    """Normalize decorated Discord embed titles for safe panel matching."""
    undecorated = str(title or "").replace("*", "").replace("_", "")
    return " ".join(undecorated.split()).casefold()


def is_self_role_panel_message(message, bot_user_id):
    """Return whether this is one of Project L's known button role panels."""
    if getattr(getattr(message, "author", None), "id", None) != bot_user_id:
        return False
    embeds = getattr(message, "embeds", [])
    components = getattr(message, "components", [])
    return bool(
        embeds
        and components
        and normalized_panel_title(embeds[0].title) in SELF_ROLE_PANEL_TITLES
    )


def _panel_image_is_current(message, filename):
    """Return whether the expected durable attachment already backs the embed."""
    attachments = getattr(message, "attachments", [])
    if not any(
        str(getattr(attachment, "filename", "")).casefold() == filename.casefold()
        for attachment in attachments
    ):
        return False
    image_url = str(getattr(message.embeds[0].image, "url", "") or "")
    return filename.casefold() in image_url.casefold()


async def repair_self_role_panel_images(channel, bot_user_id):
    """Replace expired panel image URLs with durable same-message attachments."""
    repaired = 0
    failures = []
    async for message in channel.history(limit=100, oldest_first=False):
        if not is_self_role_panel_message(message, bot_user_id):
            continue

        filename = f"self_roles-{message.id}.gif"
        if _panel_image_is_current(message, filename):
            continue

        image_file = None
        try:
            image_file = discord.File(SELF_ROLES_IMAGE, filename=filename)
            embed = discord.Embed.from_dict(message.embeds[0].to_dict())
            embed.set_image(url=f"attachment://{filename}")
            preserved = [
                attachment
                for attachment in message.attachments
                if str(getattr(attachment, "filename", "")).casefold()
                != filename.casefold()
            ]
            await message.edit(embed=embed, attachments=[*preserved, image_file])
            repaired += 1
        except (OSError, discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            failures.append((message.id, exc))
        finally:
            if image_file is not None:
                image_file.close()
    return repaired, failures



class PanelsCommand(commands.Cog):
    def __init__(self, client):
        self.client = client


    @app_commands.command(name="reactionroles", description="Send configured reaction roles panel (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def reactionroles(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        roles = load_role_ids()

        class DropdownMenu(discord.ui.Select):
            def __init__(self, options):
                super().__init__(placeholder=f"Select a role.", min_values=1, max_values=1,
                                 options=options, custom_id="roles")


        class DropdownView(discord.ui.View):
            def __init__(self, options):
                super().__init__(timeout=None)
                self.add_item(DropdownMenu(options))


        optionlist = []
        num = 0
        for option in roles:
            role = interaction.guild.get_role(option)
            if role is None:
                continue
            if len(optionlist) >= 25:
                break

            num += 1
            optionlist.append(discord.SelectOption(
                emoji="👤",
                label=f"{num}. {role.name}",
                description="",
                value=str(role.id),
            ))

        if len(optionlist) == 0:
            await interaction.response.send_message("No roles are configured.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        emb = discord.Embed(title=f"Reaction Roles", description=f"Receive your roles here.",
                            colour=self.client.colour)
        image_file = None
        try:
            if SELF_ROLES_IMAGE.is_file():
                image_file = discord.File(SELF_ROLES_IMAGE, filename="self_roles.gif")
                emb.set_image(url="attachment://self_roles.gif")
            send_options = {"embed": emb, "view": DropdownView(optionlist)}
            if image_file is not None:
                send_options["file"] = image_file
            await interaction.channel.send(**send_options)
        except (OSError, discord.Forbidden, discord.HTTPException) as exc:
            print(f"[!] Could not send reaction-role panel: {exc}")
            await interaction.followup.send(
                "The reaction-role panel could not be sent. Check my channel, embed, "
                "and attachment permissions.",
                ephemeral=True,
            )
            return
        finally:
            if image_file is not None:
                image_file.close()
        await interaction.followup.send("Reaction roles panel sent.", ephemeral=True)
        return

    @app_commands.command(
        name="repairselfroles",
        description="Repair expired images on self-role panels in this channel",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def repairselfroles(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        await interaction.response.defer(ephemeral=True)
        if not SELF_ROLES_IMAGE.is_file():
            await interaction.followup.send(
                "Data/self_roles.gif is missing, so the panels could not be repaired.",
                ephemeral=True,
            )
            return

        repaired, failures = await repair_self_role_panel_images(
            interaction.channel, self.client.user.id
        )
        if failures:
            failed_ids = ", ".join(str(message_id) for message_id, _ in failures)
            print(f"[!] Could not repair self-role panel message(s): {failed_ids}")
        summary = f"Repaired {repaired} self-role panel image(s)."
        if failures:
            summary += f" {len(failures)} panel(s) could not be edited."
        elif repaired == 0:
            summary = "No recognized self-role panel images needed repair in this channel."
        await interaction.followup.send(summary, ephemeral=True)


    @app_commands.command(name="verifypanel", description="Sends verify panel (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def verifypanel(self, interaction: discord.Interaction):
        print_interaction_info(interaction)

        class VerifyView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=None)
                self.add_item(discord.ui.Button(emoji="✅", label=f"Verify", style=discord.ButtonStyle.grey, custom_id=f"verify", disabled=False))

        await interaction.response.send_message("Verify panel sent", ephemeral=True)
        emb1 = discord.Embed(colour=self.client.colour)
        emb1.set_image(url="https://i.ibb.co/Zg2csC5/welcome.gif")

        emb2 = discord.Embed(title="<a:green_crown:1105699034749992982> Welcome to Lygo's Land Discord Server <a:green_crown:1105699034749992982>", description=f"<a:green_dsd:1105696364609929216> This server is completely based around Gaming & Community you guys can interact with each other and fun <a:green_dsd:1105696364609929216>\n\n<a:green_fire:1105696590557093948> Despite that, anyone is welcome to join the community and have a fun time. Personalize your server profile & take suitable roles for yourself — Check out ⁠✨〢ꜱᴇʟꜰ-ʀᴏʟᴇꜱ <a:green_fire:1105696590557093948>", colour=self.client.colour)
        emb2.set_image(url=self.client.greenline)

        emb3 = discord.Embed(description=f"<a:green_arrow:1105701292036325416> For additional questions or to contact the support team please open a ticket from ⁠❓〢ᴛɪᴄᴋᴇᴛ and the staff team get back to you as soon as they can. <a:green_arrowleft:1105696390744649822>", colour=self.client.colour)
        emb3.set_image(url=self.client.greenline)
        # emb3.add_field(name="<7a833636820f45009448b5ddf288f705:1078711266593493103> Official Links!", value=f"")


        emb4 = discord.Embed(title=f"<a:green_crown:1105699034749992982> Server Verification! <a:green_crown:1105699034749992982>", description=f"**<a:green_dsd:1105696364609929216> Click the Verify button below This will grant you access tothe rest of the server! <a:green_dsd:1105696364609929216>**",
                            colour=self.client.colour)
        emb4.set_image(url="https://i.ibb.co/1QcjMWG/verify.gif")
        await interaction.channel.send(embeds=[emb1, emb2, emb3, emb4], view=VerifyView())
        return


    @app_commands.command(name="voicepanel", description="Sends voice channel panel (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def voicepanel(self, interaction: discord.Interaction):
        print_interaction_info(interaction)

        class VerifyView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=None)
                self.add_item(discord.ui.Button(emoji="🔊", label=f"Create VC", style=discord.ButtonStyle.grey, custom_id=f"voicechannel", disabled=False))

        await interaction.response.send_message("Voice channel panel sent", ephemeral=True)
        emb = discord.Embed(title=f"Voice Channels", description=f"Click here to create a voice channel (Streamer only)",
                            colour=self.client.colour)
        emb.set_image(url="https://i.ibb.co/BVTKyXb/greenline.gif")
        await interaction.channel.send(embed=emb, view=VerifyView())
        return



    @app_commands.command(name="ticketpanel", description="Sends ticket panel (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketpanel(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        config = load_config()
        invalid_categories = []
        for ticket_name, category_id in config["tickets"].items():
            category = interaction.guild.get_channel(category_id)
            if category is None and category_id:
                try:
                    category = await interaction.guild.fetch_channel(category_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    category = None
            if not isinstance(category, discord.CategoryChannel):
                invalid_categories.append(ticket_name.removeprefix("category_"))
        if invalid_categories:
            await interaction.response.send_message(
                "Ticket panel not sent. Fix these category settings first: "
                + ", ".join(invalid_categories),
                ephemeral=True,
            )
            return

        class TicketView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=None)
                self.add_item(discord.ui.Button(emoji="👥", label=f"Help", style=discord.ButtonStyle.grey, custom_id=f"ticket_help", disabled=False))
                self.add_item(discord.ui.Button(emoji="📬", label=f"Suggestions", style=discord.ButtonStyle.grey, custom_id=f"ticket_suggestions", disabled=False))
                self.add_item(discord.ui.Button(emoji="⛔", label=f"Report Staff", style=discord.ButtonStyle.grey, custom_id=f"ticket_report", disabled=False))

        await interaction.response.send_message("Panel sent", ephemeral=True)
        emb = discord.Embed(title=f"Tickets", description=f"To contact the support team click a button below to open a ticket.",
                            colour=self.client.colour)
        emb.set_image(url=self.client.greenline)


        await interaction.channel.send(embed=emb, view=TicketView())
        return







async def setup(client):
    await client.add_cog(PanelsCommand(client))
