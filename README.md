# Project L

Project L is a Discord bot with moderation, tickets and modmail, reaction roles,
music, invite tracking, giveaways, economy games, welcome messages, logging,
sticky messages, Twitch notifications, YouTube notifications, and server stats.

## Quick start on Windows

Project L is tested with Python 3.11. The included setup script creates an
isolated `.venv`, installs every Python dependency, checks FFmpeg, initializes
the SQLite tables, verifies that all bot extensions load, and opens a graphical
configuration wizard.

1. Create a bot in the Discord Developer Portal.
2. On the bot page, enable these privileged gateway intents:
   - Presence Intent
   - Server Members Intent
   - Message Content Intent
3. Open PowerShell in this project directory and run:

   ```powershell
   .\setup.ps1
   ```

4. In the setup window, enter the Discord bot token and server ID. Fill in only
   the optional feature tabs you plan to use, then select **Save & Continue**.
   You can paste raw Discord IDs or copied channel, role, and member mentions.
5. Start the bot:

   ```powershell
   .\start.ps1
   ```

The first setup can take several minutes. Future starts only need
`.\start.ps1`. Startup automatically reruns dependency setup when
`requirements.txt` changes and prevents a second copy of the bot from launching
from the same folder. If required configuration is missing, start opens the
wizard automatically. Run `.\configure.ps1` whenever you want to change the
settings.

The wizard masks secret fields, validates related settings together, checks
configured channels, categories, and roles against Discord before saving, and
writes to the local, Git-ignored `Data/config.json` and `Data/roles.json` files.
To keep the bot token out of JSON entirely, set it before opening the wizard:

```powershell
$env:DISCORD_BOT_TOKEN = "your bot token"
.\setup.ps1
```

For unattended checks, `-NoGui` prevents a window from opening and returns an
error when required settings are incomplete:

```powershell
.\setup.ps1 -NoGui
.\start.ps1 -NoGui
```

## Configuration

The graphical wizard is the recommended configuration method. You may still
edit `Data/config.json` manually: Discord IDs must be JSON numbers without
quotes, `0` leaves an optional channel or role disabled, and an empty string
disables an optional account or API credential.

Important settings:

- `guildId`: the one Discord server this bot is allowed to handle.
- `client_id`: the Discord application ID. It may remain `0`; Discord can
  discover it after login.
- `welcome` and `welcome_images`: welcome output and image-hosting channels.
- `verified`: the role granted by the Verify button.
- `invitechannel`: member invite/join announcements.
- `modmail`: category used for modmail channels.
- `transcripts`: private staff channel that receives ticket transcripts.
- `voicecategory`: category for user-created voice channels.
- `tickets`: category IDs for help, suggestion, and staff-report tickets.
- `serverStatChannelIds`: renameable text, voice, or announcement channel IDs used for member statistics.
- `logChannelIds`: channels for audit events. Unset entries disable that log.
- `kickMemberFunction`: leave `false` unless you intentionally want the monthly
  cleanup of members who have remained unverified for more than 30 days.

YouTube notifications require `ytChannel` and `ytChannelId`. The source accepts
an `@handle`, a `UC...` channel ID, or a full YouTube channel URL. Twitch
notifications require `twClientId`, `twClientsecret`, and at least one output
channel. Twitch secrets can instead be supplied as environment variables:

```powershell
$env:TWITCH_CLIENT_ID = "your client id"
$env:TWITCH_CLIENT_SECRET = "your client secret"
```

Reaction-role menu IDs can be entered in the wizard. They are stored in
`Data/roles.json` as a JSON list; `Data/roles.example.json` is the safe manual
template:

```json
[
  123456789012345678,
  234567890123456789
]
```

## Verification and troubleshooting

Check the installation without connecting to Discord:

```powershell
.\.venv\Scripts\python.exe bot.py --check
```

Check whether the required configuration is complete without opening the GUI:

```powershell
.\.venv\Scripts\python.exe configure_gui.py --check
```

Useful checks:

```powershell
.\.venv\Scripts\python.exe -m pip check
ffmpeg -version
node --version
```

FFmpeg must be on `PATH` for `/play`. Node is used by current `yt-dlp` releases
for YouTube extraction. Both are already available on the machine where this
project was repaired.

If Discord rejects the connection for privileged intents, enable all three
listed intents in the Developer Portal and restart. If the bot reports an
unavailable channel, verify that the configured ID belongs to `guildId` and
that the bot can view and manage that resource.

Modmail, private voice, and each ticket destination require Discord **category**
IDs, not text-channel IDs. Saved sticky-message warnings come from `sticky.db`;
they indicate that the bot cannot access an older saved channel. Grant access
if it is still used, or run `/unsticky` from that channel before removing it.

Ticket channels are created privately: `@everyone` is denied access, while the
ticket opener, bot, administrators, and roles with Manage Channels are allowed.
Modmail also requires a staff-only category that denies View Channel to
`@everyone`. Users receive a delivery receipt after a successful modmail DM.

Use `/help` in Discord after the application commands finish syncing.

## Self-role panels

New `/reactionroles` panels upload `Data/self_roles.gif` directly to Discord,
so their embedded image does not expire. Images supplied to `/say` are also
reuploaded as durable message attachments.

If older Color, Gender, Age, Platform, Server Notification, or Game role panels
show **Image failed to load**, run `/repairselfroles` in that channel as an
administrator. The command edits only recognized bot-authored panels, preserves
their existing buttons and unrelated attachments, and is safe to run again.

## Data and privacy

- Ticket attachments remain on Discord; the bot no longer uploads them to a
  third-party image service.
- Ticket transcript files are uniquely named and deleted locally after the
  Discord upload attempt.
- Runtime `.db` files, credentials, virtual environments, and transcript text
  files are ignored by Git.
- The databases included with this extracted copy contain existing Discord
  records. Back them up and treat them as private; do not publish the archive.
