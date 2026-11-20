# Project L

Project L is a Discord bot with moderation, tickets and modmail, reaction roles,
music, invite tracking, giveaways, economy games, welcome messages, logging,
sticky messages, Twitch notifications, YouTube notifications, and server stats.

## Quick start on Windows

Project L is tested with Python 3.11. The included setup script creates an
isolated `.venv`, installs every Python dependency, checks FFmpeg, initializes
the SQLite tables, verifies that all bot extensions load, and opens a graphical
configuration wizard.

1. Create a bot in the Discord Developer Portal and invite it with the `bot`
   and `applications.commands` scopes.
   Give its role **Manage Channels**, **Manage Roles**, **View Channels**,
   **Send Messages**, **Embed Links**, **Attach Files**, and
   **Read Message History**, **Connect**, and **Speak**.
2. On the bot page, enable these privileged gateway intents:
   - Presence Intent
   - Server Members Intent
   - Message Content Intent
3. Open PowerShell in this project directory and run:

   ```powershell
   .\setup.ps1
   ```

4. In the setup window, enter the Discord bot token and server ID. Fill in only
   the optional roles and integrations you plan to use, then select
   **Save & Create Setup**. Project L creates, secures, and remembers all of its
   required channels and categories automatically.
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

The wizard masks secret fields, validates related settings together, checks the
bot's permissions, reuses valid Project L resources, creates anything missing,
and saves the generated IDs to the local, Git-ignored `Data/config.json` and
`Data/roles.json` files. Running setup again is safe and does not delete server
channels.

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

The graphical wizard is the recommended configuration method. Channel and
category IDs in `Data/config.json` are internal setup state; users do not need
to find or enter them. You may still edit the file manually for advanced use:
Discord IDs must be JSON numbers without quotes, `0` disables an optional role,
and an empty string disables an optional account or API credential.

Important settings:

- `guildId`: the one Discord server this bot is allowed to handle.
- `client_id`: discovered from the bot token during setup.
- `welcome`: automatic welcome output channel. Generated welcome images are
  attached directly to the welcome message.
- `verified`: the role granted by the Verify button.
- `invitechannel`, `modmail`, `transcripts`, `voicecategory`, `tickets`,
  `serverStatChannelIds`, and `logChannelIds` are filled automatically by the
  wizard. Audit events share one private staff log channel.
- `kickMemberFunction`: leave `false` unless you intentionally want the monthly
  cleanup of members who have remained unverified for more than 30 days.

YouTube notifications only require `ytChannel`; setup creates the Discord alert
channel. The source accepts an `@handle`, a `UC...` channel ID, or a full
YouTube channel URL. Twitch notifications require `twClientId`,
`twClientsecret`, and at least one streamer username. Primary and friend stream
alerts share the automatic alert channel. Twitch secrets can instead be
supplied as environment variables:

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
listed intents in the Developer Portal and restart. If a managed channel is
deleted or unavailable, run `.\configure.ps1` again; setup recreates only the
missing Project L resources.

Saved sticky-message warnings come from `sticky.db`; they indicate that the bot
cannot access an older saved channel. Grant access if it is still used, or run
`/unsticky` from that channel before removing it.

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
