import json
import discord
from discord.ext import commands, tasks
import asyncio
import os
import sys
import time
import datetime
import sqlite3
import random
import requests
from collections import deque
from easy_pil import Editor, load_image_async, Font
from Commands.members import mAddBtn
from config_loader import (
    BASE_DIR,
    ConfigError,
    configuration_warnings,
    load_config,
    load_role_ids,
    missing_required_settings,
)
from youtube_notifications import fetch_recent_youtube_content
from giveaway_utils import chunk_display_values, ended_description, parse_winner_count
import traceback


os.chdir(BASE_DIR)

try:
    config = load_config()
except ConfigError as exc:
    print(f"[!] Configuration error: {exc}", file=sys.stderr)
    raise SystemExit(2) from exc

token = config["token"]
guildId = config["guildId"]
verifiedRoleId = config["verified"]
kickMemberFunction = config["kickMemberFunction"]
welcomeid = config["welcome"]
welcome_image_id = config["welcome_images"]
botclientid = config["client_id"]
youtubeid = config["ytChannelId"]
twitchid = config["twChannelId"]
modmailid = config["modmail"]
ytchannel = config["ytChannel"]
twchannel = config["twitchChannelName"]
serverStats = config["serverStatChannelIds"]
clientid = config["twClientId"]
streamRole = config["streamRole"]
clientsecret = config["twClientsecret"]
friend = config["friendTwitch"]

previous = set()
notified = set()
notified_order = deque()
youtube_cache_primed = False
youtube_last_error = None
youtube_last_error_time = 0.0


def fetch_youtube_content(limit):
    """Fetch recent channel entries away from the Discord event loop."""
    return fetch_recent_youtube_content(ytchannel, limit)


def report_youtube_failure(context, exc):
    """Rate-limit repeated poll errors while retaining an actionable reason."""
    global youtube_last_error, youtube_last_error_time
    message = f"{type(exc).__name__}: {exc}"
    now = time.monotonic()
    if message != youtube_last_error or now - youtube_last_error_time >= 1800:
        print(f"[!] YouTube {context} failed; will retry later: {message}")
        youtube_last_error = message
        youtube_last_error_time = now


def mark_youtube_success():
    global youtube_last_error, youtube_last_error_time
    if youtube_last_error is not None:
        print("[OK] YouTube notifications recovered.")
    youtube_last_error = None
    youtube_last_error_time = 0.0

intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

client = commands.Bot(command_prefix="!", intents=intents, help_command=None, status=discord.Status.online,
                      activity=discord.Activity(type=discord.ActivityType.playing, name="/help"), application_id=botclientid or None)


async def sticky_ready(id, content,limit):
    @tasks.loop(seconds=12)
    async def stickyloop(channel: discord.TextChannel, sticky_object: dict):
        message, message_limit = sticky_object['content'], sticky_object['messageLimit']
            # print(message)
        if channel.id not in client.sticky:
            print('channel not found')
            stickyloop.stop()
            return
        elif client.sticky[channel.id]['content'] != message:
            print('stopping loop')
            stickyloop.stop()
            return

        try:
            msgz = []
            async for msg in channel.history(limit=message_limit, oldest_first=False):
                msgz.append(msg.content)

            if message not in msgz:
                await channel.send(f"{message}")
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            print(f"[!] Sticky update failed for channel {channel.id}: {exc}")
    sticky_object = {
            'content': content,
            'messageLimit': limit
        }
    # print(sticky_object)
    client.sticky[id] = sticky_object
    c = client.get_channel(id)
    if c is None:
        try:
            c = await client.fetch_channel(id)
        except discord.NotFound:
            client.sticky.pop(id, None)
            return "missing"
        except discord.Forbidden:
            client.sticky.pop(id, None)
            return "forbidden"
        except discord.HTTPException:
            client.sticky.pop(id, None)
            return "unavailable"
    channel_guild = getattr(c, "guild", None)
    if channel_guild is None or channel_guild.id != guildId:
        client.sticky.pop(id, None)
        return "wrong_server"
    if not hasattr(c, "history") or not hasattr(c, "send"):
        client.sticky.pop(id, None)
        return "unsupported"
    stickyloop.start(c, sticky_object)
    return None

channelName={
            'totalMembers':'👥〢Members',
            'totalBots':'🤖〢Bots',
            'totalBoosts':'🚀〢ʙᴏᴏsᴛs',
            'onlineMembers':'🟢〢Online',
            'offlineMembers':'🔴〢Offline',
        }

@tasks.loop(minutes=6)  # Set the interval to 5 minutes
async def update_stats():
    # Get server information
    guild = client.get_guild(guildId)

    # print('inside loop')

    if guild:
        totalMembers = guild.member_count
        totalBoosts = guild.premium_subscription_count
        totalBots = sum(1 for member in guild.members if member.bot)
        onlineMembers = sum(1 for member in guild.members if member.status == discord.Status.online)
        offlineMembers = totalMembers - onlineMembers

        # print(totalMembers)
        # print(totalBoosts)
        # print(totalBots)
        # print(onlineMembers)
        # print(offlineMembers)
        # print(serverStats.items())


        for stat_key, channel_id in serverStats.items():
            if not channel_id:
                continue
            try:
                channel = await guild.fetch_channel(channel_id)

                if channel:
                    stat_value = locals()[stat_key]
                    channel_name = channelName.get(stat_key, "Unknown")  # Get the corresponding channel name
                    # print('before the end')
                    # print(f'{channel_name} {stat_value}')
                    await channel.edit(name=f"{channel_name} {stat_value}")
                    # print('hey i reached the end')
                else:
                    print(f"Channel not found for {stat_key}.")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                print(f"Could not update {stat_key} channel {channel_id}: {exc}")
    else:
        print("Guild not found.")


def read_last_date():
    """Read the cleanup watermark, recreating a missing or malformed file."""
    try:
        with open(BASE_DIR / "date.json", "r", encoding="utf-8") as file:
            data = json.load(file)
        last_date_str = data.get("lastDate") if isinstance(data, dict) else None
        if last_date_str:
            return datetime.datetime.fromisoformat(last_date_str)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[!] Resetting invalid member-cleanup date file: {exc}")

    initial_date = datetime.datetime.utcnow()
    write_last_date(initial_date)
    return initial_date

def write_last_date(last_date):
    with open(BASE_DIR / "date.json", "w", encoding="utf-8") as file:
        json.dump({"lastDate": last_date.isoformat()}, file)

@tasks.loop(minutes=59)
async def check_members_task():
    if not kickMemberFunction:
        return
    try:
        current_time = datetime.datetime.utcnow()
        last_date = read_last_date()

        if (current_time - last_date).days <= 30:
            return
        guild = client.get_guild(guildId)
        if guild is None:
            print("Guild not found.")
            return
        verified_role = discord.utils.get(guild.roles, id=verifiedRoleId)
        if verified_role is None:
            print("Verified role not found in the guild.")
            return

        cutoff = discord.utils.utcnow() - datetime.timedelta(days=30)
        async for member in guild.fetch_members():
            try:
                joined_at = member.joined_at
                should_kick = (
                    not member.bot
                    and member.id not in (guild.owner_id, client.user.id)
                    and verified_role not in member.roles
                    and joined_at is not None
                    and joined_at <= cutoff
                )
                if should_kick:
                    await member.kick(reason="Inactive and not verified")
                    await asyncio.sleep(30)
            except discord.Forbidden:
                print(f"Skipping kick for {member} (role hierarchy or permission denied)")
            except (discord.HTTPException, OSError) as exc:
                print(f"Could not process member {member}: {exc}")
        write_last_date(current_time)
    except Exception as exc:
        # Keep the recurring loop alive if Discord or the watermark file fails.
        print(f"[!] Member cleanup check failed; will retry later: {exc}")

# @client.event
# async def on_guild_channel_update(before, after):
#     print('sexs')
@client.event
async def on_error(event, *args, **kwargs):
    error_message = traceback.format_exc()
    print(f"Error in event {event}: {error_message}")
@client.event
async def on_ready():

    print(f"[!] {time.strftime('%Y-%m-%d - %H:%M')}: Initiating bot. Please wait...",
          end='', flush=True)
    # while not youtubeloop.is_running() or not twitchloop.is_running() or not checker.is_running():
    #     await asyncio.sleep(1)

    print("\n[!] Bot is online")
    print(f"[!] Logged in as: {client.user} - {client.user.id}")
    if not client.persistent_state_loaded:
        with sqlite3.connect(BASE_DIR / "sticky.db") as conn:
            values = conn.execute("SELECT * FROM infos_salos").fetchall()
        skipped_stickies = 0
        for row in values:
            if row[1]:
                reason = await sticky_ready(row[0], row[1], row[2])
                if reason is not None:
                    skipped_stickies += 1
        if skipped_stickies:
            print(
                f"[!] Skipped {skipped_stickies} saved sticky message(s) that are "
                "missing, inaccessible, or from another server."
            )

        with sqlite3.connect("Data/user.db") as conn:
            values = conn.execute("SELECT * FROM roles").fetchall()
        for row in values:
            try:
                client.add_view(
                    mAddBtn(
                        content=row[4],
                        emoji=row[3],
                        role=discord.Object(id=row[2]),
                        custom_id=row[5],
                    ),
                    message_id=int(row[1]),
                )
            except Exception as exc:
                print(f"[!] Could not restore a role button: {exc}")
        client.persistent_state_loaded = True

    if guildId and any(serverStats.values()) and not update_stats.is_running():
        update_stats.start()

    if kickMemberFunction and verifiedRoleId and not check_members_task.is_running():
        check_members_task.start()

    if ytchannel and youtubeid and not youtubeloop.is_running():
        youtubeloop.start()

    twitch_enabled = (
        clientid
        and clientsecret
        and ((twchannel and twitchid) or friend)
    )
    if twitch_enabled and not twitchloop.is_running():
        twitchloop.start()

    if not checker.is_running():
        checker.start()


@tasks.loop(seconds=30)
async def _legacy_checker():
    conn = sqlite3.connect(f"Data/users.db")
    curs = conn.cursor()
    nower = int(str(time.time()).split(".")[0])
    run = f"SELECT id, channel, message FROM giveaways WHERE ends<={nower} AND ended=0"
    curs.execute(run)
    records = curs.fetchall()

    for part in records:
        giveawayid = part[0]
        query = f"""UPDATE giveaways SET (ended) = (1) WHERE id={giveawayid}"""
        curs.execute(query)
        conn.commit()

        channelid = part[1]
        messageid = part[2]
        channel = client.get_channel(channelid)
        if channel is None:
            query = f"""DELETE FROM giveaways WHERE id={giveawayid} AND channel={channelid}"""
            curs.execute(query)
            conn.commit()
            conn.close()
            return
        message = await channel.fetch_message(messageid)
        if message is None:
            query = f"""DELETE FROM giveaways WHERE id={giveawayid} AND message={messageid}"""
            curs.execute(query)
            conn.commit()
            conn.close()
            return

        if "Giveaway ended" in message.embeds[0].description:
            return

        prize = message.embeds[0].title
        winners = int(message.embeds[0].description.split(
            "Winners:** ")[1].split("**Ends")[0].replace("\n", "").replace(r"\n", ""))

        choices = []
        for r in message.reactions:
            if r.emoji == "🎉":
                async for user in r.users():
                    if user.id == client.user.id:
                        continue
                    choices.append(user)

                break

        if len(choices) < winners:
            await message.reply(f"Not enough participants for this giveaway.")
            return

        winnerlist = random.sample(choices, k=winners)
        winnerz = []
        for winner in winnerlist:
            winnerz.append(winner.mention)

        winnerz = str(winnerz).replace(
            "[", "").replace("]", "").replace("'", "")

        ember = message.embeds[0]
        ember.description = f'{message.embeds[0].description.split("React with")[0]}Giveaway ended.'
        await message.edit(embed=ember)

        emb = discord.Embed(title=prize, colour=discord.Colour.green())
        emb.add_field(name="Winners", value=winnerz, inline=False)
        emb.set_footer(text="Giveaway ended")
        await message.reply(embed=emb)
        continue


def update_giveaway_record(giveaway_id, *, delete=False):
    try:
        with sqlite3.connect("Data/users.db") as conn:
            if delete:
                conn.execute("DELETE FROM giveaways WHERE id = ?", (giveaway_id,))
            else:
                conn.execute(
                    "UPDATE giveaways SET ended = 1 WHERE id = ?", (giveaway_id,)
                )
        return True
    except sqlite3.Error as exc:
        print(f"[!] Could not update giveaway {giveaway_id}: {exc}")
        return False


@tasks.loop(seconds=30)
async def checker():
    try:
        with sqlite3.connect("Data/users.db") as conn:
            records = conn.execute(
                "SELECT id, channel, message FROM giveaways "
                "WHERE ends <= ? AND ended = 0",
                (int(time.time()),),
            ).fetchall()
    except sqlite3.Error as exc:
        print(f"[!] Giveaway database check failed; will retry later: {exc}")
        return

    for giveaway_id, channel_id, message_id in records:
        try:
            channel = client.get_channel(channel_id)
            if channel is None:
                update_giveaway_record(giveaway_id, delete=True)
                continue

            message = await channel.fetch_message(message_id)
            if not message.embeds or not message.embeds[0].description:
                update_giveaway_record(giveaway_id, delete=True)
                continue

            embed = message.embeds[0]
            if "Giveaway ended" in embed.description:
                update_giveaway_record(giveaway_id)
                continue
            winners = parse_winner_count(embed.description)

            participants = {}
            for reaction in message.reactions:
                if str(reaction.emoji) != "\N{PARTY POPPER}":
                    continue
                async for user in reaction.users():
                    if user.id != client.user.id:
                        participants[user.id] = user
                break

            embed.description = ended_description(embed.description)
            await message.edit(embed=embed)

            if len(participants) < winners:
                await message.reply("Not enough participants for this giveaway.")
                update_giveaway_record(giveaway_id)
                continue

            winner_list = random.sample(list(participants.values()), k=winners)
            result_embed = discord.Embed(
                title=embed.title,
                colour=discord.Colour.green(),
            )
            for index, winner_mentions in enumerate(
                chunk_display_values([winner.mention for winner in winner_list])
            ):
                field_name = "Winners" if index == 0 else "Winners (continued)"
                result_embed.add_field(name=field_name, value=winner_mentions, inline=False)
            result_embed.set_footer(text="Giveaway ended")
            await message.reply(embed=result_embed)
            update_giveaway_record(giveaway_id)
        except discord.NotFound:
            update_giveaway_record(giveaway_id, delete=True)
        except (discord.Forbidden, discord.HTTPException, sqlite3.Error, ValueError) as exc:
            print(f"[!] Could not finalize giveaway {giveaway_id}; will retry: {exc}")
        except Exception as exc:
            print(f"[!] Unexpected giveaway error for {giveaway_id}; will retry: {exc}")


def fetch_twitch_streams():
    """Return configured live streams using bounded blocking HTTP calls."""
    body = {
        "client_id": clientid,
        "client_secret": clientsecret,
        "grant_type": "client_credentials",
    }
    with requests.Session() as session:
        response = session.post(
            "https://id.twitch.tv/oauth2/token",
            data=body,
            timeout=15,
        )
        response.raise_for_status()
        access_token = response.json()["access_token"]
        headers = {
            "Client-ID": clientid,
            "Authorization": f"Bearer {access_token}",
        }

        configured_channels = []
        if twchannel and twitchid:
            configured_channels.append((twitchid, twchannel, True))
        configured_channels.extend(
            (item.get("friendChannelId"), item.get("friendChannel"), False)
            for item in friend
            if item.get("friendChannelId") and item.get("friendChannel")
        )

        live_streams = []
        for discord_channel_id, twitch_name, is_primary in configured_channels:
            response = session.get(
                "https://api.twitch.tv/helix/streams",
                params={"user_login": twitch_name},
                headers=headers,
                timeout=15,
            )
            response.raise_for_status()
            streams = response.json().get("data", [])
            if streams:
                live_streams.append(
                    (discord_channel_id, twitch_name, is_primary, streams[0])
                )
        return live_streams


@tasks.loop(seconds=60)
async def twitchloop():
    try:
        streams = await asyncio.to_thread(fetch_twitch_streams)
    except Exception as exc:
        print(f"[!] Twitch notification check failed: {exc}")
        return

    for discord_channel_id, twitch_name, is_primary, stream_data in streams:
        channel = client.get_channel(discord_channel_id)
        if channel is None:
            print(f"[!] Twitch notification channel {discord_channel_id} is unavailable.")
            continue

        stream_id = stream_data.get("id")
        if not stream_id or stream_id in notified:
            continue
        game_name = stream_data.get("game_name")
        if is_primary:
            role_ping = f"<@&{streamRole}> " if streamRole else ""
            message = f"**{role_ping}I'm live!**\nhttps://www.twitch.tv/{twchannel}"
        else:
            message = (
                f"**Server friend '{twitch_name}' is live!**\n"
                f"https://www.twitch.tv/{twitch_name}"
            )
        if game_name:
            message += f"\nPlaying: {game_name}"
        try:
            await channel.send(message)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            print(
                f"[!] Twitch notification send failed for {twitch_name}; "
                f"will retry: {exc}"
            )
            continue
        notified.add(stream_id)
        notified_order.append(stream_id)
        while len(notified_order) > 5000:
            notified.discard(notified_order.popleft())


@tasks.loop(minutes=5)
async def youtubeloop():
    global youtube_cache_primed
    try:
        channel = client.get_channel(youtubeid)
        if channel is None:
            print(f"[!] YouTube notification channel {youtubeid} is unavailable.")
            return

        fetched_content = await asyncio.to_thread(fetch_youtube_content, 3)
        mark_youtube_success()

        if not youtube_cache_primed:
            for results in fetched_content:
                for result in results:
                    video_id = result.get("videoId")
                    if video_id:
                        previous.add(video_id)
            youtube_cache_primed = True
            print(
                f"[OK] YouTube notifications initialized with {len(previous)} recent item(s)."
            )
            return

        for results in fetched_content:
            for result in results:
                video_id = result.get("videoId")
                if video_id and video_id not in previous:
                    await channel.send(
                        f"**{channel.guild.default_role} New Upload**\n"
                        f"https://www.youtube.com/watch?v={video_id}"
                    )
                    previous.add(video_id)
    except Exception as exc:
        report_youtube_failure("notification check", exc)


@client.event
async def on_guild_join(guild: discord.Guild):
    if guild.id == guildId:
        await client.tree.sync(guild=guild)
    else:
        print(f"[!] Ignoring unconfigured guild {guild.id}.")


@client.event
async def on_member_join(member: discord.Member):
    if member.guild.id != guildId:
        return
    welcome = client.get_channel(welcomeid)
    if welcome is None:
        print(f"[!] Welcome channel {welcomeid} is unavailable.")
        return
    embed = discord.Embed(
        title="**╭━━━━━✦WELCOME✦━━━━━━━━━━━━━━━━➧**", colour=client.colour
    )
    embed.add_field(
        name="\u200b",
        value=f"<a:green_crown_sparkle:1105696367667597372> Hello {member} <a:green_crown_sparkle:1105696367667597372>",
        inline=False,
    )
    embed.add_field(
        name="\u200b",
        value=f"<a:green_online:1105696576350978111> Welcome to **{member.guild.name}** <a:green_online:1105696576350978111>",
        inline=False,
    )
    embed.add_field(
        name="<a:green_heartstatic:1108584890305351731> Your presence is a valuable addition <a:green_heartstatic:1108584890305351731>",
        value="**━━━━━━━━━━━✦INFO✦━━━━━━━━━━━━━━━━➧**\n<a:green_planet:1105696371631202344> Read our Rules here <#1042619540774846605>\n<a:green_planet:1105696371631202344>  For Help, open a <#1105676532095127643>\n<a:green_planet:1105696371631202344> Customize Your Profile <#1105673962647715951> \n**╰━━━━━━✦ENJOY✦━━━━━━━━━━━━━━━━━━➧**",
        inline=False,
    )

    try:
        background = Editor("Data/welcome.jpg")
        profile_image = await load_image_async(str(member.display_avatar.url))
        profile = Editor(profile_image).resize((250, 250)).circle_image()
        poppins = Font.poppins(size=75, variant="bold")
        poppins_small = Font.poppins(size=50, variant="light")
        background.paste(profile, (390, 50))
        background.ellipse((390, 50), 250, 250, outline="black", stroke_width=4)
        background.text((525, 300), "WELCOME !", color="white", font=poppins, align="center")
        background.text((525, 360), member.name, color="white", font=poppins_small, align="center")

        welcome_image = client.get_channel(welcome_image_id)
        if welcome_image is None:
            raise RuntimeError(f"Welcome image channel {welcome_image_id} is unavailable.")
        file = discord.File(fp=background.image_bytes, filename="wlcbg.jpg")
        image_message = await welcome_image.send(file=file)
        if image_message.attachments:
            embed.set_image(url=image_message.attachments[0].url)
    except Exception as exc:
        print(f"[!] Welcome image generation failed; sending text embed instead: {exc}")

    try:
        await welcome.send(embed=embed)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
        print(f"[!] Could not send welcome message for member {member.id}: {exc}")

"""
**╭━━━━━✦WELCOME✦━━━━━━━━━━━━━━━━➧**
> :green_crown_sparkle:Hello <@1138150310435627069> :green_crown_sparkle:
> 📢Welcome to **Lygo's Land** 📢
> ❤️ Your presence is a valuable addition :green_heartstatic:
**━━━━━━━━━━━✦INFO✦━━━━━━━━━━━━━━━━➧**
> 👉🏻 Read our Rules here <#1042619540774846605>
> :green_planet:  For Help, open a <#1105676532095127643>
> :green_planet:  Customize Your Profile <#1105673962647715951>
**╰━━━━━━✦ENJOY✦━━━━━━━━━━━━━━━━━━➧**"""


def initialize_databases():
    """Create all runtime tables and safely migrate the warnings schema."""
    (BASE_DIR / "Data").mkdir(exist_ok=True)
    (BASE_DIR / "Transcripts").mkdir(exist_ok=True)

    with sqlite3.connect(BASE_DIR / "Data" / "users.db") as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "userid INTEGER PRIMARY KEY, balance INTEGER, invited INTEGER, "
            "left INTEGER, voice INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS giveaways ("
            "id INTEGER, ends INTEGER, channel INTEGER, message INTEGER, ended INTEGER)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS warnings ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, userid INTEGER NOT NULL, "
            "reason TEXT, warner INTEGER)"
        )

        warning_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(warnings)").fetchall()
        }
        if "id" not in warning_columns:
            legacy_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='warnings_legacy'"
            ).fetchone()
            if not legacy_exists:
                conn.execute("ALTER TABLE warnings RENAME TO warnings_legacy")
                conn.execute(
                    "CREATE TABLE warnings ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, userid INTEGER NOT NULL, "
                    "reason TEXT, warner INTEGER)"
                )
                conn.execute(
                    "INSERT INTO warnings (userid, reason, warner) "
                    "SELECT userid, reason, warner FROM warnings_legacy"
                )
                print("[!] Migrated warnings database; preserved warnings_legacy.")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS warnings_userid_idx ON warnings(userid)"
        )

    with sqlite3.connect(BASE_DIR / "Data" / "user.db") as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS roles ("
            "channelid INTEGER, messageid INTEGER, roleid INTEGER, "
            "emoji TEXT, content TEXT, custom_id TEXT)"
        )
        role_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(roles)").fetchall()
        }
        if "custom_id" not in role_columns:
            conn.execute("ALTER TABLE roles ADD COLUMN custom_id TEXT")

        reserved_component_ids = (
            "verify",
            "roles",
            "modmail_del",
            "transcript",
            "close",
            "delete",
            "reopen",
            "ticket_help",
            "ticket_suggestions",
            "ticket_report",
            "voicechannel",
        )
        placeholders = ", ".join("?" for _ in reserved_component_ids)
        conn.execute(
            f"UPDATE roles SET custom_id = CASE "
            f"WHEN content IN ({placeholders}) THEN 'role_button:' || roleid "
            "ELSE content END "
            "WHERE custom_id IS NULL OR custom_id = ''",
            reserved_component_ids,
        )

    with sqlite3.connect(BASE_DIR / "sticky.db") as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS infos_salos ("
            "id_salon INTEGER PRIMARY KEY, contenu TEXT, message_limit INTEGER)"
        )


async def configured_guild_only(interaction: discord.Interaction):
    if interaction.guild_id == guildId:
        return True
    if not interaction.response.is_done():
        await interaction.response.send_message(
            "This bot is not configured for this server.",
            ephemeral=True,
        )
    return False


client.tree.interaction_check = configured_guild_only


@client.event
async def setup_hook():
    initialize_databases()
    try:
        synced_commands = await client.tree.sync()
        print(f"[!] Synced {len(synced_commands)} commands")
    except Exception as exc:
        print(f"[!] Command sync failed and will be retried on the next start: {exc}")
    print("[!] Runtime databases ready")


async def loadcogs():
    for filename in sorted(os.listdir("Commands")):
        if filename.endswith(".py") and not filename.startswith("_"):
            await client.load_extension(f"Commands.{filename[:-3]}")


def configure_client_state():
    client.sticky = {}
    client.persistent_state_loaded = False
    client.configured_guild_id = guildId
    client.colour = discord.Colour.from_rgb(101, 255, 0)
    client.greenline = "https://i.ibb.co/BVTKyXb/greenline.gif"
    client.modmailid = modmailid


async def check_installation():
    initialize_databases()
    configure_client_state()
    configured_role_ids = load_role_ids()
    print(f"[OK] Validated {len(configured_role_ids)} reaction-role menu IDs")
    async with client:
        await loadcogs()
        loaded = sorted(client.extensions)
        print(f"[OK] Loaded {len(loaded)} extensions: {', '.join(loaded)}")

        with sqlite3.connect("Data/user.db") as conn:
            saved_role_buttons = conn.execute(
                "SELECT roleid, emoji, content, custom_id FROM roles"
            ).fetchall()
        for role_id, emoji, content, custom_id in saved_role_buttons:
            view = mAddBtn(
                content=content,
                emoji=emoji,
                role=discord.Object(id=role_id),
                custom_id=custom_id,
            )
            if len(view.children) != 1:
                raise RuntimeError(
                    f"Saved role button for role {role_id} could not be restored."
                )
        print(f"[OK] Validated {len(saved_role_buttons)} saved role buttons")

        for extension in reversed(loaded):
            await client.unload_extension(extension)

    warnings = configuration_warnings(config)
    if warnings:
        print("[i] Optional configuration: " + "; ".join(warnings) + ".")
    missing = missing_required_settings(config)
    if missing:
        print("[i] Add these settings before connecting: " + ", ".join(missing) + ".")
    print("[OK] Project L installation check passed.")


async def startup():
    configure_client_state()
    async with client:
        await loadcogs()
        await client.start(token)


def main():
    if "--check" in sys.argv[1:]:
        asyncio.run(check_installation())
        return 0

    missing = missing_required_settings(config)
    if missing:
        print("[!] Configuration is incomplete:", file=sys.stderr)
        for setting in missing:
            print(f"    - {setting}", file=sys.stderr)
        print(
            "Copy values into Data/config.json, or set DISCORD_BOT_TOKEN, then run again.",
            file=sys.stderr,
        )
        return 2

    for warning in configuration_warnings(config):
        print(f"[i] {warning}.")

    try:
        asyncio.run(startup())
    except discord.PrivilegedIntentsRequired:
        print(
            "[!] Enable Server Members, Presence, and Message Content intents in "
            "the Discord Developer Portal.",
            file=sys.stderr,
        )
        return 3
    except discord.LoginFailure:
        print("[!] Discord rejected the configured bot token.", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("\n[!] Bot stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
