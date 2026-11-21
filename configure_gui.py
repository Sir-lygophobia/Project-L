"""Native graphical configuration wizard for Project L."""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

from config_loader import ConfigError, load_role_ids
from discord_provisioning import (
    DiscordProvisioningError,
    build_bot_invite_url,
    configuration_without_managed_channels,
    discover_bot_user_id,
    provision_guild_resources,
    validate_provisioning_access,
)
from discord_validation import DiscordValidationError, validate_live_discord_report
from setup_config import (
    EXAMPLE_CONFIG_PATH,
    build_configuration,
    configuration_status,
    flatten_config,
    load_editable_config,
    parse_discord_id,
    save_configuration,
)

DISCORD_APPLICATIONS_URL = "https://discord.com/developers/applications"
ART_DIR = Path(__file__).resolve().parent / "Data" / "art"
REQUIRED_GATEWAY_INTENTS = (
    "Presence Intent",
    "Server Members Intent",
    "Message Content Intent",
)


def build_bot_settings_url(client_id: int) -> str:
    """Return the Developer Portal Bot page for a validated bot account."""
    return f"{DISCORD_APPLICATIONS_URL}/{int(client_id)}/bot"


def required_gateway_intents_text() -> str:
    """Return the human-readable privileged intent checklist."""
    return ", ".join(REQUIRED_GATEWAY_INTENTS)


def format_validation_warnings(warnings: list[str]) -> str:
    """Format non-blocking Discord findings for a setup dialog."""
    return "\n".join(f"• {warning}" for warning in warnings)


class ScrollableTab:
    """A small scrollable ttk frame used by each wizard tab."""

    def __init__(self, parent, tkinter, ttk):
        self.tk = tkinter
        self.outer = ttk.Frame(parent)
        self.canvas = tkinter.Canvas(
            self.outer,
            borderwidth=0,
            highlightthickness=0,
            background="#f5f7fb",
        )
        self.scrollbar = ttk.Scrollbar(
            self.outer, orient="vertical", command=self.canvas.yview
        )
        self.content = ttk.Frame(self.canvas, style="Page.TFrame", padding=(24, 18))
        self.window = self.canvas.create_window(
            (0, 0), window=self.content, anchor="nw"
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.content.columnconfigure(0, weight=1)
        self.content.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_content)
        self.canvas.bind_all("<MouseWheel>", self._mousewheel, add="+")

    def _update_scroll_region(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_content(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _mousewheel(self, event):
        if self.canvas.winfo_ismapped():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


class ProjectLSetupWizard:
    def __init__(self, root, tkinter, ttk, messagebox):
        self.root = root
        self.tk = tkinter
        self.ttk = ttk
        self.messagebox = messagebox
        self.saved = False
        self.variables: dict[str, object] = {}
        self.text_widgets: dict[str, object] = {}
        self.secret_entries: list[object] = []
        self.image_refs: list[object] = []
        self.startup_warnings: list[str] = []

        self.base_config = self._load_config_for_editing()
        role_ids = self._load_roles_for_editing()
        self.initial_values = flatten_config(self.base_config, role_ids)

        self._configure_window()
        self._configure_styles()
        self._build_layout()
        if self.startup_warnings:
            self.set_status(" ".join(self.startup_warnings), error=True)

    def _load_config_for_editing(self):
        try:
            return load_editable_config()
        except ConfigError as exc:
            self.startup_warnings.append(
                f"The existing config could not be read. Saving will replace it. {exc}"
            )
            return json.loads(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))

    def _load_roles_for_editing(self):
        try:
            return load_role_ids()
        except ConfigError as exc:
            self.startup_warnings.append(
                f"Reaction roles could not be read and will start empty. {exc}"
            )
            return []

    def _configure_window(self):
        self.root.title("Project L Setup")
        self.root.geometry("900x760")
        self.root.minsize(760, 620)
        self.root.configure(background="#eef2f7")
        self.root.protocol("WM_DELETE_WINDOW", self.cancel)

        self.root.update_idletasks()
        width, height = 900, 760
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _configure_styles(self):
        style = self.ttk.Style(self.root)
        available = style.theme_names()
        if "vista" in available:
            style.theme_use("vista")
        elif "clam" in available:
            style.theme_use("clam")
        style.configure("Page.TFrame", background="#f5f7fb")
        style.configure(
            "Section.TLabel",
            background="#f5f7fb",
            foreground="#18243a",
            font=("Segoe UI Semibold", 13),
        )
        style.configure(
            "Field.TLabel",
            background="#f5f7fb",
            foreground="#162033",
            font=("Segoe UI Semibold", 10),
        )
        style.configure(
            "Help.TLabel",
            background="#f5f7fb",
            foreground="#65728a",
            font=("Segoe UI", 9),
        )
        style.configure("Required.TLabel", foreground="#bd2c2c")
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10))
        style.configure("Status.TLabel", background="#ffffff", foreground="#516079")
        style.configure(
            "ErrorStatus.TLabel", background="#ffffff", foreground="#b42318"
        )
        style.configure(
            "SuccessStatus.TLabel", background="#ffffff", foreground="#18794e"
        )

    def _build_layout(self):
        header = self.tk.Frame(self.root, background="#17243c", padx=28, pady=20)
        header.pack(fill="x")
        self.tk.Label(
            header,
            text="Project L Setup",
            background="#17243c",
            foreground="white",
            font=("Segoe UI Semibold", 22),
        ).pack(anchor="w")
        self.tk.Label(
            header,
            text="Enter your bot and server details. Project L creates and secures its channels automatically.",
            background="#17243c",
            foreground="#d9e2f2",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        self.notebook = self.ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(14, 8))

        self.tabs = {}
        for key, title in (
            ("core", "1. Core"),
            ("channels", "2. Roles"),
            ("tickets", "3. Automatic Setup"),
            ("stats", "4. Server Stats"),
            ("integrations", "5. Integrations"),
            ("branding", "6. Art & Branding"),
        ):
            tab = ScrollableTab(self.notebook, self.tk, self.ttk)
            self.notebook.add(tab.outer, text=title)
            self.tabs[key] = tab

        self._build_core_tab()
        self._build_channels_tab()
        self._build_tickets_tab()
        self._build_stats_tab()
        self._build_integrations_tab()
        self._build_branding_tab()

        footer = self.tk.Frame(self.root, background="#ffffff", padx=18, pady=12)
        footer.pack(fill="x", side="bottom")
        footer.columnconfigure(0, weight=1)
        self.status = self.ttk.Label(
            footer,
            text="Required fields are marked with *.",
            style="Status.TLabel",
            wraplength=760,
        )
        self.status.grid(
            row=0,
            column=0,
            columnspan=4,
            sticky="ew",
            pady=(0, 8),
        )
        self.ttk.Button(footer, text="Cancel", command=self.cancel).grid(
            row=1, column=1, padx=(8, 0)
        )
        self.ttk.Button(
            footer, text="Check Readiness", command=self.validate_only
        ).grid(
            row=1, column=2, padx=(8, 0)
        )
        self.ttk.Button(
            footer,
            text="Save & Create",
            command=self.save,
            style="Accent.TButton",
        ).grid(row=1, column=3, padx=(8, 0))

    def _section(self, tab_name: str, title: str, description: str = ""):
        parent = self.tabs[tab_name].content
        row = parent.grid_size()[1]
        self.ttk.Label(parent, text=title, style="Section.TLabel").grid(
            row=row, column=0, sticky="w", pady=(12 if row else 0, 2)
        )
        if description:
            self.ttk.Label(
                parent,
                text=description,
                style="Help.TLabel",
                wraplength=760,
                justify="left",
            ).grid(row=row + 1, column=0, sticky="ew", pady=(0, 10))

    def _entry(
        self,
        tab_name: str,
        key: str,
        label: str,
        description: str,
        *,
        required: bool = False,
        secret: bool = False,
    ):
        parent = self.tabs[tab_name].content
        row = parent.grid_size()[1]
        field = self.ttk.Frame(parent, style="Page.TFrame")
        field.grid(row=row, column=0, sticky="ew", pady=(3, 9))
        field.columnconfigure(1, weight=1)
        label_text = f"{label} *" if required else label
        self.ttk.Label(field, text=label_text, style="Field.TLabel", width=29).grid(
            row=0, column=0, sticky="nw", padx=(0, 14), pady=3
        )
        initial = self.initial_values.get(key, "")
        variable = self.tk.StringVar(value="" if initial in (0, None) else str(initial))
        entry = self.ttk.Entry(field, textvariable=variable, show="•" if secret else "")
        entry.grid(row=0, column=1, sticky="ew", pady=2)
        self.ttk.Label(
            field,
            text=description,
            style="Help.TLabel",
            wraplength=520,
            justify="left",
        ).grid(row=1, column=1, sticky="w", pady=(1, 0))
        self.variables[key] = variable
        if secret:
            self.secret_entries.append(entry)
        return entry

    def _text(
        self,
        tab_name: str,
        key: str,
        label: str,
        description: str,
        *,
        height: int = 5,
    ):
        parent = self.tabs[tab_name].content
        row = parent.grid_size()[1]
        field = self.ttk.Frame(parent, style="Page.TFrame")
        field.grid(row=row, column=0, sticky="ew", pady=(3, 10))
        field.columnconfigure(1, weight=1)
        self.ttk.Label(field, text=label, style="Field.TLabel", width=29).grid(
            row=0, column=0, sticky="nw", padx=(0, 14), pady=3
        )
        text = self.tk.Text(
            field,
            height=height,
            wrap="none",
            font=("Consolas", 10),
            relief="solid",
            borderwidth=1,
        )
        text.grid(row=0, column=1, sticky="ew")
        text.insert("1.0", str(self.initial_values.get(key, "")))
        self.ttk.Label(
            field,
            text=description,
            style="Help.TLabel",
            wraplength=520,
            justify="left",
        ).grid(row=1, column=1, sticky="w", pady=(4, 0))
        self.text_widgets[key] = text

    def _build_core_tab(self):
        self._section(
            "core",
            "First-time Discord setup",
            (
                "1. Open the Developer Portal and create an application. On its Bot "
                "page, create/add the bot if Discord offers that option.\n"
                f"2. Under Privileged Gateway Intents, enable "
                f"{required_gateway_intents_text()}, then save.\n"
                "3. Reset/copy the bot token and paste it below. Never share or commit it.\n"
                "4. In Discord, enable User Settings → Advanced → Developer Mode, "
                "then right-click your server and copy its Server ID.\n"
                "5. Invite the bot, authorize it, then use Check Readiness and Save & Create."
            ),
        )
        onboarding_controls = self.ttk.Frame(
            self.tabs["core"].content, style="Page.TFrame"
        )
        onboarding_controls.grid(
            row=self.tabs["core"].content.grid_size()[1],
            column=0,
            sticky="w",
            pady=(0, 8),
        )
        self.ttk.Button(
            onboarding_controls,
            text="1. Open Developer Portal",
            command=lambda: webbrowser.open(DISCORD_APPLICATIONS_URL),
        ).grid(row=0, column=0, sticky="w")
        self.ttk.Button(
            onboarding_controls,
            text="Open This Bot's Settings (after token)",
            command=self.open_bot_settings,
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))

        intents_confirmed = self.tk.BooleanVar(
            value=bool(self.initial_values.get("client_id", 0))
        )
        self.gateway_intents_confirmed = intents_confirmed
        self.ttk.Checkbutton(
            self.tabs["core"].content,
            text=(
                "I enabled Presence, Server Members, and Message Content intents "
                "on this bot's Bot page."
            ),
            variable=intents_confirmed,
        ).grid(
            row=self.tabs["core"].content.grid_size()[1],
            column=0,
            sticky="w",
            pady=(0, 14),
        )
        self.ttk.Label(
            self.tabs["core"].content,
            text=(
                "Discord does not allow a bot to turn these switches on for you. "
                "Project L asks you to confirm them and verifies their status during "
                "the readiness check."
            ),
            style="Help.TLabel",
            wraplength=760,
            justify="left",
        ).grid(
            row=self.tabs["core"].content.grid_size()[1],
            column=0,
            sticky="w",
            pady=(0, 14),
        )

        self._section(
            "core",
            "Discord connection",
            "Only the token and server ID are required to start. The token is stored only in the local, Git-ignored config file unless DISCORD_BOT_TOKEN is already set.",
        )
        self._entry(
            "core",
            "token",
            "Discord Bot Token",
            "Developer Portal → Bot → Reset Token. This field is masked.",
            required=not bool(os.environ.get("DISCORD_BOT_TOKEN", "").strip()),
            secret=True,
        )
        self._entry(
            "core",
            "guildId",
            "Discord Server ID",
            "Right-click your server after enabling Developer Mode, then choose Copy Server ID.",
            required=True,
        )
        controls = self.ttk.Frame(self.tabs["core"].content, style="Page.TFrame")
        controls.grid(
            row=self.tabs["core"].content.grid_size()[1],
            column=0,
            sticky="w",
            pady=(3, 12),
        )
        show_secrets = self.tk.BooleanVar(value=False)
        self.ttk.Checkbutton(
            controls,
            text="Show secret fields",
            variable=show_secrets,
            command=lambda: self._toggle_secrets(show_secrets.get()),
        ).pack(side="left")
        self.ttk.Button(
            controls,
            text="Invite Bot to Server",
            command=self.open_bot_invite,
        ).pack(side="left", padx=(14, 0))

        self.ttk.Label(
            self.tabs["core"].content,
            text=(
                "The invite button requests only the permissions Project L uses. "
                "If the bot is already in the server, opening the invite again updates "
                "its server permissions without creating a second bot."
            ),
            style="Help.TLabel",
            wraplength=760,
            justify="left",
        ).grid(
            row=self.tabs["core"].content.grid_size()[1],
            column=0,
            sticky="w",
            pady=(0, 12),
        )

        self._section(
            "core",
            "Safety",
            "This feature is disabled by default. When enabled, the monthly sweep only removes non-bot members who have remained unverified for over 30 days.",
        )
        cleanup = self.tk.BooleanVar(
            value=bool(self.initial_values.get("kickMemberFunction", False))
        )
        self.variables["kickMemberFunction"] = cleanup
        self.ttk.Checkbutton(
            self.tabs["core"].content,
            text="Enable 30-day unverified-member cleanup",
            variable=cleanup,
        ).grid(
            row=self.tabs["core"].content.grid_size()[1],
            column=0,
            sticky="w",
            pady=(2, 12),
        )

        message_logging = self.tk.BooleanVar(
            value=bool(self.initial_values.get("logMessageContent", False))
        )
        self.variables["logMessageContent"] = message_logging
        self.ttk.Checkbutton(
            self.tabs["core"].content,
            text="Include edited/deleted message text and attachment URLs in logs",
            variable=message_logging,
        ).grid(
            row=self.tabs["core"].content.grid_size()[1],
            column=0,
            sticky="w",
            pady=(2, 2),
        )
        self.ttk.Label(
            self.tabs["core"].content,
            text=(
                "Privacy-sensitive and disabled by default. Metadata-only logging "
                "continues when this option is off."
            ),
            style="Help.TLabel",
        ).grid(
            row=self.tabs["core"].content.grid_size()[1],
            column=0,
            sticky="w",
            pady=(0, 12),
        )

        if os.environ.get("DISCORD_BOT_TOKEN", "").strip():
            self.ttk.Label(
                self.tabs["core"].content,
                text="✓ DISCORD_BOT_TOKEN is already set; the token field may remain blank.",
                style="Help.TLabel",
            ).grid(
                row=self.tabs["core"].content.grid_size()[1],
                column=0,
                sticky="w",
                pady=(0, 10),
            )

    def _build_channels_tab(self):
        self._section(
            "channels",
            "Existing role overrides (optional)",
            "Leave these blank and Project L creates safe verified and streamer roles automatically. Enter an ID only to reuse an existing zero-permission role below the bot's highest role.",
        )
        fields = (
            (
                "verified",
                "Verified Role ID",
                "Optional existing role; otherwise setup creates Project L Verified.",
            ),
            (
                "streamRole",
                "Streamer Role ID",
                "Optional existing role; otherwise setup creates Project L Streamer.",
            ),
        )
        for key, label, help_text in fields:
            self._entry("channels", key, label, help_text)

        self._section(
            "channels",
            "Reaction-role menu",
            "Add one Discord role ID per line. The menu supports up to 25 unique roles.",
        )
        self._text(
            "channels",
            "roleIds",
            "Reaction Role IDs",
            "One ID per line, or comma-separated.",
            height=7,
        )

    def _build_tickets_tab(self):
        self._section(
            "tickets",
            "Created automatically",
            "When you choose Save & Create Setup, Project L reuses its existing managed resources or creates the missing ones. You never need to copy a channel ID.",
        )
        self._section(
            "tickets",
            "Public resources",
            "A community category with read-only welcome and setup-panel channels and, when an integration is enabled, one shared YouTube/Twitch alerts channel.",
        )
        self._section(
            "tickets",
            "Private staff resources",
            "A secured staff category with logs and ticket transcripts, separate private categories for modmail, help tickets, suggestions, staff reports, and user-created voice channels. Nonstaff members cannot view them.",
        )
        self._section(
            "tickets",
            "Safe reruns",
            "Setup never deletes channels. It keeps valid saved resources, finds Project L resources by their managed marker, and creates only what is missing.",
        )

    def _build_stats_tab(self):
        self._section(
            "stats",
            "Automatic server statistics",
            "Project L creates a Server Stats category with five locked voice counters for members, bots, boosts, online members, and offline members. Members can see the counters but cannot join them.",
        )
        self._section(
            "stats",
            "Required bot permission",
            "The bot must have Manage Channels so it can create these resources and refresh the counter names every six minutes.",
        )

    def _build_integrations_tab(self):
        self._section(
            "integrations",
            "YouTube notifications",
            "Leave the channel blank to disable YouTube checks. The Discord alerts channel is created automatically.",
        )
        self._entry(
            "integrations",
            "ytChannel",
            "YouTube Channel",
            "Enter an @handle, UC... channel ID, or full YouTube channel URL.",
        )
        self._section(
            "integrations",
            "Twitch notifications",
            "Credentials may instead come from TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET environment variables.",
        )
        self._entry(
            "integrations",
            "twitchChannelName",
            "Primary Twitch Username",
            "The primary stream to announce. Do not include the full URL.",
        )
        self._entry(
            "integrations",
            "twClientId",
            "Twitch Client ID",
            "Twitch Developer Console application client ID.",
        )
        self._entry(
            "integrations",
            "twClientsecret",
            "Twitch Client Secret",
            "Twitch Developer Console application secret. This field is masked.",
            secret=True,
        )
        self._text(
            "integrations",
            "friendTwitch",
            "Friend Streams",
            "One Twitch username per line. All alerts use the automatic Project L alerts channel.",
            height=6,
        )

    def _build_branding_tab(self):
        self._section(
            "branding",
            "Included Project L art pack",
            (
                "Panel banners and five projectl_* server emojis install automatically. "
                "Setup only creates missing emoji names; it never replaces or deletes "
                "your existing server emojis."
            ),
        )

        preview_frame = self.ttk.Frame(
            self.tabs["branding"].content, style="Page.TFrame"
        )
        preview_frame.grid(
            row=self.tabs["branding"].content.grid_size()[1],
            column=0,
            sticky="ew",
            pady=(2, 12),
        )
        preview_frame.columnconfigure(1, weight=1)
        previews = (
            ("Project L logo", ART_DIR / "project_l_logo.png", 4),
            ("Server banner", ART_DIR / "project_l_server_banner.png", 5),
        )
        for row, (label, image_path, subsample) in enumerate(previews):
            self.ttk.Label(
                preview_frame, text=label, style="Field.TLabel", width=18
            ).grid(row=row, column=0, sticky="nw", padx=(0, 14), pady=(3, 12))
            try:
                image = self.tk.PhotoImage(file=str(image_path)).subsample(subsample)
                self.image_refs.append(image)
                self.tk.Label(
                    preview_frame,
                    image=image,
                    background="#0a0d0b",
                    borderwidth=1,
                    relief="solid",
                ).grid(row=row, column=1, sticky="w", pady=(3, 12))
            except self.tk.TclError:
                self.ttk.Label(
                    preview_frame,
                    text=f"Missing: {image_path.name}",
                    style="Help.TLabel",
                ).grid(row=row, column=1, sticky="w", pady=(3, 12))

        self._section(
            "branding",
            "Bot and server branding",
            (
                "Discord keeps application and server branding under owner control. "
                "Use the buttons below to open the included files and the correct "
                "Discord pages. Upload project_l_logo.png as the bot/server icon and "
                "project_l_server_banner.png as the server banner. Project L will not "
                "overwrite branding already chosen by the server owner."
            ),
        )
        controls = self.ttk.Frame(
            self.tabs["branding"].content, style="Page.TFrame"
        )
        controls.grid(
            row=self.tabs["branding"].content.grid_size()[1],
            column=0,
            sticky="w",
            pady=(2, 12),
        )
        self.ttk.Button(
            controls,
            text="Open Included Art Folder",
            command=self.open_art_folder,
        ).pack(side="left")
        self.ttk.Button(
            controls,
            text="Open Bot Branding Page",
            command=self.open_bot_settings,
        ).pack(side="left", padx=(10, 0))
        self.ttk.Button(
            controls,
            text="Open Server in Discord",
            command=self.open_server_in_discord,
        ).pack(side="left", padx=(10, 0))

        self._section(
            "branding",
            "What setup applies automatically",
            (
                "Verification, ticket, private-voice, reaction-role, and personalized "
                "welcome artwork is attached directly to Discord messages. No external "
                "image host is required, and missing optional art never disables a button."
            ),
        )

    def _toggle_secrets(self, show: bool):
        for entry in self.secret_entries:
            entry.configure(show="" if show else "•")

    def collect_values(self):
        values = {}
        for key, variable in self.variables.items():
            values[key] = variable.get()
        for key, widget in self.text_widgets.items():
            values[key] = widget.get("1.0", "end").strip()
        return values

    def _effective_token(self) -> str:
        """Return the entered token or the local environment override."""
        return (
            self.variables["token"].get().strip()
            or os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        )

    def open_bot_settings(self):
        """Open the exact Developer Portal Bot page resolved from the token."""
        try:
            self.set_status("Checking the token and locating this bot's settings...")
            self.root.update_idletasks()
            client_id = discover_bot_user_id(self._effective_token())
        except (DiscordProvisioningError, ValueError) as exc:
            self.set_status("Enter a valid bot token first.", error=True)
            self.messagebox.showerror(
                "Could not locate bot settings", str(exc), parent=self.root
            )
            return

        url = build_bot_settings_url(client_id)
        if not webbrowser.open(url):
            self.set_status("The Developer Portal could not be opened.", error=True)
            self.messagebox.showerror(
                "Could not open Discord",
                "Copy and open this URL in your browser:\n\n" + url,
                parent=self.root,
            )
            return
        self.set_status(
            "Opened this bot's settings. Enable all three privileged intents.",
            success=True,
        )

    def open_art_folder(self):
        """Open the bundled art folder without changing Discord branding."""
        try:
            startfile = getattr(os, "startfile", None)
            if startfile is not None:
                startfile(str(ART_DIR))
            elif not webbrowser.open(ART_DIR.resolve().as_uri()):
                raise OSError("No file browser accepted the art folder.")
        except OSError as exc:
            self.set_status("The included art folder could not be opened.", error=True)
            self.messagebox.showerror(
                "Could not open art folder", str(exc), parent=self.root
            )
            return
        self.set_status("Opened the included Project L art pack.", success=True)

    def open_server_in_discord(self):
        """Open the configured server so its owner can apply optional branding."""
        try:
            guild_id = parse_discord_id(
                self.variables["guildId"].get(), "Discord server ID", required=True
            )
        except ConfigError as exc:
            self.set_status("Enter a valid Discord server ID first.", error=True)
            self.messagebox.showerror(
                "Could not open server", str(exc), parent=self.root
            )
            return
        url = f"https://discord.com/channels/{guild_id}"
        if not webbrowser.open(url):
            self.set_status("Discord could not be opened.", error=True)
            self.messagebox.showerror(
                "Could not open Discord",
                "Copy and open this URL in your browser:\n\n" + url,
                parent=self.root,
            )
            return
        self.set_status(
            "Opened the server. Use Server Settings → Overview for its icon and banner.",
            success=True,
        )

    def open_bot_invite(self):
        """Open an exact, guild-scoped Discord OAuth invite for this bot."""
        try:
            guild_id = parse_discord_id(
                self.variables["guildId"].get(),
                "Discord server ID",
                required=True,
            )
            token = self._effective_token()
            self.set_status("Checking the bot token and preparing its invite...")
            self.root.update_idletasks()
            client_id = discover_bot_user_id(token)
        except (ConfigError, DiscordProvisioningError, ValueError) as exc:
            self.set_status("Enter a valid bot token and server ID first.", error=True)
            self.messagebox.showerror(
                "Could not create bot invite", str(exc), parent=self.root
            )
            return

        url = build_bot_invite_url(client_id, guild_id)
        if not webbrowser.open(url):
            self.set_status("The Discord invite could not be opened.", error=True)
            self.messagebox.showerror(
                "Could not open Discord",
                "Copy and open this URL in your browser:\n\n" + url,
                parent=self.root,
            )
            return
        self.set_status(
            "Opened Discord with Project L's required permissions.", success=True
        )

    def validated_values(self):
        if not self.gateway_intents_confirmed.get():
            raise ConfigError(
                f"Confirm that {required_gateway_intents_text()} are enabled on "
                "the bot's Developer Portal Bot page."
            )
        return build_configuration(
            self.collect_values(),
            self.base_config,
            environ=os.environ,
            auto_managed_channels=True,
        )

    def validated_live_values(self, *, provision: bool = False):
        config, role_ids = self.validated_values()
        effective_token = config["token"] or os.environ.get("DISCORD_BOT_TOKEN", "")
        provisioning_report = None
        if provision:
            self.set_status("Creating and securing Project L server resources...")
            self.root.update_idletasks()
            config, provisioning_report = provision_guild_resources(
                config,
                token=effective_token,
            )
            validation_config = config
        else:
            self.set_status("Checking the bot account and server permissions...")
            self.root.update_idletasks()
            config["client_id"] = validate_provisioning_access(
                config,
                token=effective_token,
            )
            validation_config = configuration_without_managed_channels(config)

        self.set_status("Checking Discord permissions and roles...")
        self.root.update_idletasks()
        report = validate_live_discord_report(
            validation_config,
            role_ids,
            token=effective_token,
        )
        if report.errors:
            raise ConfigError("\n".join(f"• {error}" for error in report.errors))
        warnings = list(report.warnings)
        if provisioning_report is not None:
            warnings.extend(
                f"Optional art: {warning}"
                for warning in provisioning_report.warnings
            )
        return config, role_ids, warnings, provisioning_report

    def validate_only(self):
        try:
            _config, role_ids, warnings, _provisioning = self.validated_live_values()
        except (ConfigError, DiscordProvisioningError, DiscordValidationError) as exc:
            self.set_status(
                "Please correct the highlighted configuration errors.", error=True
            )
            self.messagebox.showerror(
                "Configuration needs attention", str(exc), parent=self.root
            )
            return
        if warnings:
            self.set_status(
                "Configuration is valid with security warnings.", success=True
            )
            self.messagebox.showwarning(
                "Configuration warnings",
                "The configuration can be saved, but review these findings:\n\n"
                f"{format_validation_warnings(warnings)}",
                parent=self.root,
            )
            return
        self.set_status("Configuration is valid.", success=True)
        self.messagebox.showinfo(
            "Configuration is valid",
            "The bot token, server access, and permissions are valid. Saving will "
            "create or reuse the Project L server resources automatically. "
            f"{len(role_ids)} reaction roles are configured.",
            parent=self.root,
        )

    def save(self):
        try:
            config, role_ids, warnings, provisioning = self.validated_live_values(
                provision=True
            )
            save_configuration(config, role_ids)
        except (
            ConfigError,
            DiscordProvisioningError,
            DiscordValidationError,
            OSError,
        ) as exc:
            self.set_status("Configuration was not saved.", error=True)
            self.messagebox.showerror(
                "Could not save configuration", str(exc), parent=self.root
            )
            return

        self.saved = True
        provisioning_summary = (
            provisioning.summary() if provisioning else "no changes needed"
        )
        self.set_status(
            f"Configuration saved; server setup {provisioning_summary}.", success=True
        )
        if warnings:
            self.messagebox.showwarning(
                "Setup saved with warnings",
                f"Configuration saved; server setup {provisioning_summary}. "
                "Project L is ready to continue starting. "
                "Review these findings when practical:\n\n"
                f"{format_validation_warnings(warnings)}",
                parent=self.root,
            )
        else:
            self.messagebox.showinfo(
                "Setup complete",
                f"Configuration saved; server setup {provisioning_summary}. "
                "Project L is ready to continue starting.",
                parent=self.root,
            )
        self.root.destroy()

    def cancel(self):
        self.root.destroy()

    def set_status(self, text: str, *, error: bool = False, success: bool = False):
        style = (
            "ErrorStatus.TLabel"
            if error
            else "SuccessStatus.TLabel"
            if success
            else "Status.TLabel"
        )
        self.status.configure(text=text, style=style)


def check_status(*, quiet: bool = False) -> int:
    complete, reasons = configuration_status()
    if complete:
        if not quiet:
            print("Project L configuration is complete.")
        return 0
    if not quiet:
        print("Project L configuration is incomplete:")
        for reason in reasons:
            print(f"  - {reason}")
    return 2


def run_gui() -> int:
    try:
        import tkinter
        from tkinter import messagebox, ttk
    except ImportError:
        print(
            "Tkinter is unavailable. Repair the Python 3.11 installation with Tcl/Tk support.",
            file=sys.stderr,
        )
        return 3

    try:
        root = tkinter.Tk()
    except tkinter.TclError as exc:
        print(f"Could not open the setup window: {exc}", file=sys.stderr)
        return 3

    wizard = ProjectLSetupWizard(root, tkinter, ttk, messagebox)
    root.mainloop()
    return 0 if wizard.saved else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure Project L")
    parser.add_argument(
        "--check",
        action="store_true",
        help="check configuration without opening a window",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress status output when used with --check",
    )
    arguments = parser.parse_args(argv)
    if arguments.check:
        return check_status(quiet=arguments.quiet)
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
