"""Native graphical configuration wizard for Project L."""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser

from config_loader import ConfigError, load_role_ids
from discord_validation import DiscordValidationError, validate_live_discord_report
from setup_config import (
    EXAMPLE_CONFIG_PATH,
    build_configuration,
    configuration_status,
    flatten_config,
    load_editable_config,
    save_configuration,
)


DISCORD_APPLICATIONS_URL = "https://discord.com/developers/applications"


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
        style.configure("ErrorStatus.TLabel", background="#ffffff", foreground="#b42318")
        style.configure("SuccessStatus.TLabel", background="#ffffff", foreground="#18794e")

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
            text="Enter the Discord IDs for the features you want to use. Blank optional fields stay disabled.",
            background="#17243c",
            foreground="#d9e2f2",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        self.notebook = self.ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(14, 8))

        self.tabs = {}
        for key, title in (
            ("core", "1. Core"),
            ("channels", "2. Channels & Roles"),
            ("tickets", "3. Tickets & Logs"),
            ("stats", "4. Server Stats"),
            ("integrations", "5. Integrations"),
        ):
            tab = ScrollableTab(self.notebook, self.tk, self.ttk)
            self.notebook.add(tab.outer, text=title)
            self.tabs[key] = tab

        self._build_core_tab()
        self._build_channels_tab()
        self._build_tickets_tab()
        self._build_stats_tab()
        self._build_integrations_tab()

        footer = self.tk.Frame(self.root, background="#ffffff", padx=18, pady=12)
        footer.pack(fill="x", side="bottom")
        footer.columnconfigure(0, weight=1)
        self.status = self.ttk.Label(
            footer,
            text="Required fields are marked with *.",
            style="Status.TLabel",
            wraplength=480,
        )
        self.status.grid(row=0, column=0, sticky="w")
        self.ttk.Button(footer, text="Cancel", command=self.cancel).grid(
            row=0, column=1, padx=(8, 0)
        )
        self.ttk.Button(footer, text="Validate", command=self.validate_only).grid(
            row=0, column=2, padx=(8, 0)
        )
        self.ttk.Button(
            footer,
            text="Save & Continue",
            command=self.save,
            style="Accent.TButton",
        ).grid(row=0, column=3, padx=(8, 0))

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
        self._entry(
            "core",
            "client_id",
            "Application ID",
            "Developer Portal → General Information → Application ID. Optional; Discord can discover it after login.",
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
            text="Open Discord Developer Portal",
            command=lambda: webbrowser.open(DISCORD_APPLICATIONS_URL),
        ).pack(side="left", padx=(14, 0))

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
            "Welcome and membership",
            "Enter only the features you want. Each value is a Discord snowflake ID, not a channel name.",
        )
        fields = (
            ("welcome", "Welcome Channel ID", "Receives the welcome embed."),
            (
                "welcome_images",
                "Welcome Image Channel ID",
                "Hosts generated welcome images. Requires a Welcome Channel ID.",
            ),
            ("invitechannel", "Invite Log Channel ID", "Receives join and inviter announcements."),
            ("verified", "Verified Role ID", "Granted by the Verify button."),
            ("streamRole", "Streamer Role ID", "Mentioned for the primary Twitch notification."),
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
            "Ticket and modmail resources",
            "The ticket buttons use the three category IDs. Transcript access and destructive ticket actions are permission-checked.",
        )
        fields = (
            ("transcripts", "Transcript Channel ID", "Private staff channel that receives ticket text files."),
            ("modmail", "Modmail Category ID", "Category where user DM conversations are mirrored."),
            ("voicecategory", "Private Voice Category ID", "Category for user-created private voice channels."),
            ("tickets.category_help", "Help Ticket Category ID", "Category used by the Help ticket button."),
            (
                "tickets.category_suggestions",
                "Suggestion Ticket Category ID",
                "Category used by the Suggestions ticket button.",
            ),
            ("tickets.category_report", "Staff Report Category ID", "Category used by the Report Staff button."),
            ("logs", "Legacy Logs Channel ID", "Compatibility field retained for older configurations."),
        )
        for key, label, help_text in fields:
            self._entry("tickets", key, label, help_text)

        self._section(
            "tickets",
            "Audit log channels",
            "Each event type can be enabled independently.",
        )
        for key, label in (
            ("logChannelIds.Message", "Message Log Channel ID"),
            ("logChannelIds.Ban_Unban", "Ban / Unban Log Channel ID"),
            ("logChannelIds.Join_Leave", "Join / Leave Log Channel ID"),
            ("logChannelIds.Role", "Role Log Channel ID"),
            ("logChannelIds.Channel", "Channel Log Channel ID"),
        ):
            self._entry("tickets", key, label, "Leave blank to disable this event log.")

    def _build_stats_tab(self):
        self._section(
            "stats",
            "Server statistic channels",
            "The bot renames configured channels every six minutes. It needs Manage Channels permission.",
        )
        for key, label in (
            ("serverStatChannelIds.totalMembers", "Total Members Channel ID"),
            ("serverStatChannelIds.totalBots", "Total Bots Channel ID"),
            ("serverStatChannelIds.totalBoosts", "Total Boosts Channel ID"),
            ("serverStatChannelIds.onlineMembers", "Online Members Channel ID"),
            ("serverStatChannelIds.offlineMembers", "Offline Members Channel ID"),
        ):
            self._entry("stats", key, label, "Leave blank to disable this statistic.")

    def _build_integrations_tab(self):
        self._section(
            "integrations",
            "YouTube notifications",
            "Both fields are required together. Leave both blank to disable YouTube checks.",
        )
        self._entry(
            "integrations",
            "ytChannel",
            "YouTube Channel",
            "Enter an @handle, UC... channel ID, or full YouTube channel URL.",
        )
        self._entry(
            "integrations",
            "ytChannelId",
            "Discord Output Channel ID",
            "Where new YouTube uploads are announced.",
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
            "twChannelId",
            "Primary Discord Output ID",
            "Where the primary stream announcement is sent.",
        )
        self._entry(
            "integrations",
            "twClientId",
            "Twitch Client ID",
            "Twitch Developer Console application client ID.",
            secret=True,
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
            "One per line: Discord output channel ID, Twitch username",
            height=6,
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

    def validated_values(self):
        return build_configuration(
            self.collect_values(), self.base_config, environ=os.environ
        )

    def validated_live_values(self):
        config, role_ids = self.validated_values()
        self.set_status("Checking Discord resources...")
        self.root.update_idletasks()
        effective_token = config["token"] or os.environ.get("DISCORD_BOT_TOKEN", "")
        report = validate_live_discord_report(
            config,
            role_ids,
            token=effective_token,
        )
        if report.errors:
            raise ConfigError("\n".join(f"• {error}" for error in report.errors))
        return config, role_ids, report.warnings

    def validate_only(self):
        try:
            _config, role_ids, warnings = self.validated_live_values()
        except (ConfigError, DiscordValidationError) as exc:
            self.set_status("Please correct the highlighted configuration errors.", error=True)
            self.messagebox.showerror("Configuration needs attention", str(exc), parent=self.root)
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
            "All configured Discord resources exist and have the expected types. "
            f"{len(role_ids)} reaction roles are configured.",
            parent=self.root,
        )

    def save(self):
        try:
            config, role_ids, warnings = self.validated_live_values()
            save_configuration(config, role_ids)
        except (ConfigError, DiscordValidationError, OSError) as exc:
            self.set_status("Configuration was not saved.", error=True)
            self.messagebox.showerror("Could not save configuration", str(exc), parent=self.root)
            return

        self.saved = True
        self.set_status("Configuration saved successfully.", success=True)
        if warnings:
            self.messagebox.showwarning(
                "Setup saved with warnings",
                "Configuration saved. Project L is ready to continue starting. "
                "Review these findings when practical:\n\n"
                f"{format_validation_warnings(warnings)}",
                parent=self.root,
            )
        else:
            self.messagebox.showinfo(
                "Setup complete",
                "Configuration saved. Project L is ready to continue starting.",
                parent=self.root,
            )
        self.root.destroy()

    def cancel(self):
        self.root.destroy()

    def set_status(self, text: str, *, error: bool = False, success: bool = False):
        style = "ErrorStatus.TLabel" if error else "SuccessStatus.TLabel" if success else "Status.TLabel"
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
    except ImportError as exc:
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
