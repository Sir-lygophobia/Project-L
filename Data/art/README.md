# Project L art pack

These files are bundled locally so Discord embeds do not rely on expiring or
third-party image URLs.

| File | Canvas | Used by |
| --- | --- | --- |
| `project_l_logo.png` | 1024 x 1024, PNG | Bot/application avatar and optional server icon |
| `project_l_server_banner.png` | 1920 x 1080, PNG | Discord server banner (16:9) |
| `verification.png` | 1200 x 400, PNG | Verification panel, automatic |
| `private_voice.png` | 1200 x 400, PNG | Private-voice panel, automatic |
| `tickets.png` | 1200 x 400, PNG | Ticket panel, automatic |

The optional `emojis/` directory contains five 128 x 128 transparent PNGs.
Setup creates only a missing emoji with the corresponding stable name and never
replaces or deletes an existing server expression:

| File | Server emoji name |
| --- | --- |
| `emojis/projectl_verified.png` | `projectl_verified` |
| `emojis/projectl_ticket.png` | `projectl_ticket` |
| `emojis/projectl_voice.png` | `projectl_voice` |
| `emojis/projectl_giveaway.png` | `projectl_giveaway` |
| `emojis/projectl_coin.png` | `projectl_coin` |

Each emoji PNG must be at most 256 KiB and no larger than 512 x 512. Invalid or
missing optional art is reported as a warning and never prevents core server
setup.

The bot uploads the three panel banners as attachments on the same messages as
their embeds. This makes the image URLs durable and keeps a missing art file
from affecting the buttons. `Commands/panels.py` falls back to a text-only
embed if an optional banner is unavailable.

Two established assets remain one directory above this folder:

- `../welcome.jpg` is 1024 x 500 and is composited with each new member's
  avatar and name.
- `../self_roles.gif` is 720 x 170 and is uploaded with reaction-role panels.

Discord application avatars and server branding cannot be safely changed by
the local provisioning process. Upload `project_l_logo.png` on the bot's
Developer Portal page. A server owner can use the same file as the server icon
and upload `project_l_server_banner.png` in **Server Settings > Overview** when
the server has access to the banner feature.
