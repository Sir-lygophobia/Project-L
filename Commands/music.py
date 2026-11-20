import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import asyncio
import shutil
from urllib.parse import urlparse
from logFunction import print_interaction_info


def is_supported_youtube_url(url):
    """Accept real YouTube HTTP(S) hosts without allowing look-alike domains."""
    try:
        parsed = urlparse(str(url).strip())
    except (TypeError, ValueError):
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    return hostname == "youtu.be" or hostname == "youtube.com" or hostname.endswith(
        ".youtube.com"
    )


def playable_entry(info):
    """Return a playable yt-dlp entry from either a video or playlist result."""
    if not isinstance(info, dict):
        raise ValueError("YouTube did not return playable media information.")
    if info.get("url"):
        return info
    for entry in info.get("entries") or ():
        if isinstance(entry, dict):
            try:
                return playable_entry(entry)
            except ValueError:
                continue
    raise ValueError("YouTube did not return a playable audio stream.")


def extract_playable_info(url, ydl_options=None):
    """Perform blocking yt-dlp extraction and normalize playlist results."""
    options = {"format": "bestaudio/best", "noplaylist": True}
    if ydl_options:
        options.update(ydl_options)
    with yt_dlp.YoutubeDL(options) as ydl:
        return playable_entry(ydl.extract_info(url, download=False))


async def respond(interaction, message, *, ephemeral=False):
    """Acknowledge an interaction whether or not it was already deferred."""
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(message, ephemeral=ephemeral)


def cleanup_source(source):
    cleanup = getattr(source, "cleanup", None)
    if callable(cleanup):
        cleanup()



class Start(commands.Cog):
    def __init__(self, client):
        self.client = client


    @app_commands.command(name="join", description="Make bot join your VC")
    async def join(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        member = interaction.guild.get_member(interaction.user.id)
        channel = getattr(getattr(member, "voice", None), "channel", None)
        if channel is None:
            await interaction.response.send_message(
                "You are not in a voice channel in this server!", ephemeral=True
            )
            return
        voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)
        try:
            if voice and voice.is_connected():
                await voice.move_to(channel)
            else:
                await channel.connect()
        except (discord.ClientException, discord.Forbidden, discord.HTTPException) as exc:
            print(f"[!] Could not join voice channel {channel.id}: {exc}")
            await interaction.response.send_message(
                "I could not join that voice channel. Check my Connect and Speak permissions.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Bot joined **{channel.name}** - Use /play to play a YouTube video."
        )
        return

    @app_commands.command(name="stop", description="Stop playing music.")
    async def stop(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)
        if voice is None:
            await interaction.response.send_message(f"Bot is not playing.", ephemeral=True)
            return
        elif voice.is_connected():
            try:
                await voice.disconnect(force=True)
            except (discord.ClientException, discord.HTTPException) as exc:
                print(f"[!] Could not disconnect voice client: {exc}")
                await interaction.response.send_message(
                    "I could not disconnect from voice.", ephemeral=True
                )
                return
            await interaction.response.send_message("Music playing stopped.")
            return
        else:
            await interaction.response.send_message(f"Bot is not playing.", ephemeral=True)
            return


    @app_commands.command(name="play", description="Play a youtube video")
    async def play(self, interaction: discord.Interaction, url: app_commands.Range[str, 1, 1000]):
        print_interaction_info(interaction)
        ffmpeg_options = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                          'options': '-vn'}
        member = interaction.guild.get_member(interaction.user.id)
        channel = getattr(getattr(member, "voice", None), "channel", None)
        if channel is None:
            await interaction.response.send_message(
                "You are not in a voice channel in this server!", ephemeral=True
            )
            return

        if not is_supported_youtube_url(url):
            await interaction.response.send_message("URL not supported.", ephemeral=True)
            return

        ffmpeg_executable = shutil.which("ffmpeg")
        if ffmpeg_executable is None:
            await interaction.response.send_message(
                "FFmpeg is not installed or is not available on PATH.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)
        try:
            if voice and voice.is_connected():
                await voice.move_to(channel)
            else:
                voice = await channel.connect()
            if not voice.is_connected():
                await interaction.followup.send(
                    "I could not establish the voice connection.", ephemeral=True
                )
                return

            song_info = await asyncio.to_thread(extract_playable_info, url)
            if voice.is_playing():
                voice.stop()
            source = discord.FFmpegPCMAudio(
                song_info["url"],
                **ffmpeg_options,
                executable=ffmpeg_executable,
            )
            try:
                voice.play(
                    source,
                    after=lambda error: print(f"[!] Music playback error: {error}")
                    if error
                    else None,
                )
            except Exception:
                cleanup_source(source)
                raise
            await interaction.followup.send(
                f"Started playing {url} in **{channel.name}**"
            )
        except (
            ValueError,
            OSError,
            discord.ClientException,
            discord.Forbidden,
            discord.HTTPException,
            yt_dlp.utils.DownloadError,
        ) as exc:
            print(f"[!] Music playback failed: {exc}")
            await interaction.followup.send(
                "Could not play that URL. Check the link and try again.",
                ephemeral=True,
            )
            return

        return


    @stop.error
    async def catch_error(self, interaction, error):
        print(f"[!] /stop failed: {error}")
        await respond(interaction, "Music could not be stopped.", ephemeral=True)


    @join.error
    async def catch_error(self, interaction, error):
        print(f"[!] /join failed: {error}")
        await respond(interaction, "I could not join voice.", ephemeral=True)


    @play.error
    async def catch_error(self, interaction, error):
        print(f"[!] /play failed: {error}")
        await respond(
            interaction,
            "Music playback could not be started. Check the URL and voice permissions.",
            ephemeral=True,
        )










async def setup(client):
    await client.add_cog(Start(client))
