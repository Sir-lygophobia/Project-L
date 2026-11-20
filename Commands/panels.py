"""Reusable, durable Discord panels for Project L."""

from __future__ import annotations

from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands

from config_loader import BASE_DIR, load_config, load_role_ids
from logFunction import print_interaction_info


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
PANEL_COMPONENT_IDS = {
    "verification": frozenset({"verify"}),
    "private_voice": frozenset({"voicechannel"}),
    "tickets": frozenset({"ticket_help", "ticket_suggestions", "ticket_report"}),
    "reaction_roles": frozenset({"roles"}),
}


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
        except (
            OSError,
            discord.Forbidden,
            discord.NotFound,
            discord.HTTPException,
        ) as exc:
            failures.append((message.id, exc))
        finally:
            if image_file is not None:
                image_file.close()
    return repaired, failures


def _component_custom_ids(message) -> set[str]:
    result = set()
    for row in getattr(message, "components", []):
        children = getattr(row, "children", [row])
        for component in children:
            custom_id = getattr(component, "custom_id", None)
            if custom_id:
                result.add(str(custom_id))
    return result


def panel_kind_for_message(message, bot_user_id: int) -> str | None:
    """Identify a bot-authored managed panel by its stable component IDs."""
    if getattr(getattr(message, "author", None), "id", None) != bot_user_id:
        return None
    component_ids = _component_custom_ids(message)
    for kind, required in PANEL_COMPONENT_IDS.items():
        if required.issubset(component_ids):
            return kind
    return None


class VerificationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                emoji="✅",
                label="Verify",
                style=discord.ButtonStyle.success,
                custom_id="verify",
            )
        )


class PrivateVoiceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                emoji="🔊",
                label="Create private voice channel",
                style=discord.ButtonStyle.primary,
                custom_id="voicechannel",
            )
        )


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for emoji, label, custom_id in (
            ("👥", "Help", "ticket_help"),
            ("📬", "Suggestion", "ticket_suggestions"),
            ("🛡️", "Report staff", "ticket_report"),
        ):
            self.add_item(
                discord.ui.Button(
                    emoji=emoji,
                    label=label,
                    style=discord.ButtonStyle.secondary,
                    custom_id=custom_id,
                )
            )


class ReactionRoleView(discord.ui.View):
    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Select(
                placeholder="Select a role",
                min_values=1,
                max_values=1,
                options=options,
                custom_id="roles",
            )
        )


def reaction_role_options(guild, role_ids: list[int]) -> list[discord.SelectOption]:
    options = []
    bot_member = getattr(guild, "me", None)
    for role_id in role_ids:
        role = guild.get_role(int(role_id))
        if (
            role is None
            or role.id == guild.id
            or (hasattr(role, "is_default") and role.is_default())
            or getattr(role, "managed", False)
            or (bot_member is not None and role >= bot_member.top_role)
        ):
            continue
        options.append(
            discord.SelectOption(
                emoji="👤",
                label=role.name[:100],
                value=str(role.id),
            )
        )
        if len(options) == 25:
            break
    return options


def verification_embed(guild, colour) -> discord.Embed:
    embed = discord.Embed(
        title=f"Welcome to {guild.name}",
        description=(
            "Select **Verify** below to receive the server's verified role. "
            "If the button fails, ask an administrator to check the bot's role hierarchy."
        ),
        colour=colour,
    )
    embed.set_footer(text="Project L • Verification")
    return embed


def private_voice_embed(colour) -> discord.Embed:
    embed = discord.Embed(
        title="Private voice channels",
        description=(
            "Streamers can create a private voice channel with the button below. "
            "The creator receives permission to manage that channel."
        ),
        colour=colour,
    )
    embed.set_footer(text="Project L • Private Voice")
    return embed


def ticket_embed(colour) -> discord.Embed:
    embed = discord.Embed(
        title="Support tickets",
        description="Choose the ticket type that best matches what you need.",
        colour=colour,
    )
    embed.set_footer(text="Project L • Tickets")
    return embed


async def send_reaction_role_panel(channel, guild, colour):
    options = reaction_role_options(guild, load_role_ids())
    if not options:
        raise ValueError("No assignable reaction roles are configured.")
    embed = discord.Embed(
        title="Reaction Roles",
        description="Choose a role from the menu below.",
        colour=colour,
    )
    embed.set_footer(text="Project L • Reaction Roles")
    image_file = None
    try:
        if SELF_ROLES_IMAGE.is_file():
            image_file = discord.File(SELF_ROLES_IMAGE, filename="self_roles.gif")
            embed.set_image(url="attachment://self_roles.gif")
        send_options = {"embed": embed, "view": ReactionRoleView(options)}
        if image_file is not None:
            send_options["file"] = image_file
        return await channel.send(**send_options)
    finally:
        if image_file is not None:
            image_file.close()


async def send_managed_panel(kind: str, channel, guild, colour):
    if kind == "verification":
        return await channel.send(
            embed=verification_embed(guild, colour), view=VerificationView()
        )
    if kind == "private_voice":
        return await channel.send(
            embed=private_voice_embed(colour), view=PrivateVoiceView()
        )
    if kind == "tickets":
        return await channel.send(embed=ticket_embed(colour), view=TicketView())
    if kind == "reaction_roles":
        return await send_reaction_role_panel(channel, guild, colour)
    raise ValueError(f"Unknown managed panel kind: {kind}")


@dataclass
class PanelInstallReport:
    created: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


async def ensure_automatic_panels(client) -> PanelInstallReport:
    """Install missing standard panels in the setup-managed panels channel."""
    report = PanelInstallReport()
    config = load_config()
    guild = client.get_guild(config["guildId"])
    channel_id = int(config.get("managedResources", {}).get("panelsChannel", 0) or 0)
    if guild is None or not channel_id:
        report.failed.append("panels channel is not configured")
        return report
    channel = guild.get_channel(channel_id) or client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            report.failed.append(f"panels channel is unavailable: {exc}")
            return report

    existing = set()
    try:
        async for message in channel.history(limit=100, oldest_first=False):
            kind = panel_kind_for_message(message, client.user.id)
            if kind:
                existing.add(kind)
    except (discord.Forbidden, discord.HTTPException) as exc:
        report.failed.append(f"could not inspect existing panels: {exc}")
        return report

    desired = ["verification", "private_voice", "tickets"]
    if reaction_role_options(guild, load_role_ids()):
        desired.append("reaction_roles")
    for kind in desired:
        if kind in existing:
            report.existing.append(kind)
            continue
        try:
            await send_managed_panel(kind, channel, guild, client.colour)
            report.created.append(kind)
        except (
            ValueError,
            OSError,
            discord.Forbidden,
            discord.NotFound,
            discord.HTTPException,
        ) as exc:
            print(f"[!] Could not install {kind} panel: {exc}")
            report.failed.append(kind)
    return report


class PanelsCommand(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(
        name="reactionroles", description="Send the configured reaction-role panel"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def reactionroles(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            await send_reaction_role_panel(
                interaction.channel, interaction.guild, self.client.colour
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except (OSError, discord.Forbidden, discord.HTTPException) as exc:
            print(f"[!] Could not send reaction-role panel: {exc}")
            await interaction.followup.send(
                "I could not send that panel. Check my channel permissions.",
                ephemeral=True,
            )
            return
        await interaction.followup.send("Reaction-role panel sent.", ephemeral=True)

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
        summary = f"Repaired {repaired} self-role panel image(s)."
        if failures:
            summary += f" {len(failures)} panel(s) could not be edited."
        elif repaired == 0:
            summary = "No recognized self-role panel images needed repair here."
        await interaction.followup.send(summary, ephemeral=True)

    @app_commands.command(name="verifypanel", description="Send the verification panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def verifypanel(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        config = load_config()
        if interaction.guild.get_role(config["verified"]) is None:
            await interaction.response.send_message(
                "The verified role is unavailable. Run setup again first.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await send_managed_panel(
            "verification", interaction.channel, interaction.guild, self.client.colour
        )
        await interaction.followup.send("Verification panel sent.", ephemeral=True)

    @app_commands.command(name="voicepanel", description="Send the private-voice panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def voicepanel(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        config = load_config()
        role = interaction.guild.get_role(config["streamRole"])
        category = interaction.guild.get_channel(config["voicecategory"])
        if role is None or not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "The streamer role or private-voice category is unavailable. Run setup again.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await send_managed_panel(
            "private_voice", interaction.channel, interaction.guild, self.client.colour
        )
        await interaction.followup.send("Private-voice panel sent.", ephemeral=True)

    @app_commands.command(name="ticketpanel", description="Send the ticket panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketpanel(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        config = load_config()
        invalid = [
            key.removeprefix("category_")
            for key, channel_id in config["tickets"].items()
            if not isinstance(
                interaction.guild.get_channel(channel_id), discord.CategoryChannel
            )
        ]
        if invalid:
            await interaction.response.send_message(
                "Ticket panel not sent; rerun setup for: " + ", ".join(invalid),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await send_managed_panel(
            "tickets", interaction.channel, interaction.guild, self.client.colour
        )
        await interaction.followup.send("Ticket panel sent.", ephemeral=True)


async def setup(client):
    await client.add_cog(PanelsCommand(client))
