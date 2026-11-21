import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import sqlite3
import random
import numpy
import traceback
from contextlib import contextmanager
from pathlib import Path
from logFunction import print_interaction_info


USERS_DB_PATH = Path(__file__).resolve().parents[1] / "Data" / "users.db"


@contextmanager
def economy_transaction(path=USERS_DB_PATH):
    """Open, lock, commit, and always close one economy database transaction."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_user(conn, user_id):
    conn.execute(
        "INSERT OR IGNORE INTO users(userid, balance, invited, left) "
        "VALUES(?, 0, 0, 0)",
        (user_id,),
    )


def ensure_economy_users(user_ids, *, path=USERS_DB_PATH):
    """Ensure economy rows exist without replacing any existing user data."""
    with economy_transaction(path) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO users(userid, balance, invited, left) "
            "VALUES(?, 0, 0, 0)",
            ((user_id,) for user_id in dict.fromkeys(user_ids)),
        )


def credit_balance(user_id, amount, *, path=USERS_DB_PATH):
    """Atomically create or credit a user and return the resulting balance."""
    if amount < 0:
        raise ValueError("Credit amount must be non-negative.")
    with economy_transaction(path) as conn:
        conn.execute(
            "INSERT INTO users(userid, balance, invited, left) VALUES(?, ?, 0, 0) "
            "ON CONFLICT(userid) DO UPDATE SET "
            "balance = COALESCE(users.balance, 0) + excluded.balance",
            (user_id, amount),
        )
        return conn.execute(
            "SELECT balance FROM users WHERE userid = ?", (user_id,)
        ).fetchone()[0]


def debit_balance_to_zero(user_id, amount, *, path=USERS_DB_PATH):
    """Atomically debit a user, never below zero, and report whether it was new."""
    if amount < 0:
        raise ValueError("Debit amount must be non-negative.")
    with economy_transaction(path) as conn:
        existed = (
            conn.execute("SELECT 1 FROM users WHERE userid = ?", (user_id,)).fetchone()
            is not None
        )
        conn.execute(
            "INSERT INTO users(userid, balance, invited, left) VALUES(?, 0, 0, 0) "
            "ON CONFLICT(userid) DO UPDATE SET "
            "balance = MAX(COALESCE(users.balance, 0) - ?, 0)",
            (user_id, amount),
        )
        balance = conn.execute(
            "SELECT balance FROM users WHERE userid = ?", (user_id,)
        ).fetchone()[0]
        return balance, not existed


def transfer_balance(sender_id, recipient_id, amount, *, path=USERS_DB_PATH):
    """Atomically transfer funds, returning False when the sender is short."""
    if amount <= 0:
        raise ValueError("Transfer amount must be positive.")
    with economy_transaction(path) as conn:
        _ensure_user(conn, sender_id)
        if sender_id == recipient_id:
            balance = conn.execute(
                "SELECT COALESCE(balance, 0) FROM users WHERE userid = ?",
                (sender_id,),
            ).fetchone()[0]
            return balance >= amount
        updated = conn.execute(
            "UPDATE users SET balance = COALESCE(balance, 0) - ? "
            "WHERE userid = ? AND COALESCE(balance, 0) >= ?",
            (amount, sender_id, amount),
        )
        if updated.rowcount != 1:
            return False
        _ensure_user(conn, recipient_id)
        conn.execute(
            "UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE userid = ?",
            (amount, recipient_id),
        )
        return True


def settle_wager(user_id, stake, balance_delta, *, path=USERS_DB_PATH):
    """Apply a wager result only when the user can cover the original stake."""
    if stake <= 0:
        raise ValueError("Wager stake must be positive.")
    with economy_transaction(path) as conn:
        _ensure_user(conn, user_id)
        updated = conn.execute(
            "UPDATE users SET balance = COALESCE(balance, 0) + ? "
            "WHERE userid = ? AND COALESCE(balance, 0) >= ?",
            (balance_delta, user_id, stake),
        )
        return updated.rowcount == 1


def rob_balance_transfer(robber_id, victim_id, requested_amount, *, path=USERS_DB_PATH):
    """Atomically move up to the requested amount from victim to robber."""
    if requested_amount <= 0:
        raise ValueError("Rob amount must be positive.")
    with economy_transaction(path) as conn:
        _ensure_user(conn, robber_id)
        _ensure_user(conn, victim_id)
        victim_balance = conn.execute(
            "SELECT COALESCE(balance, 0) FROM users WHERE userid = ?",
            (victim_id,),
        ).fetchone()[0]
        amount = min(requested_amount, max(victim_balance, 0))
        if amount <= 0:
            return 0
        conn.execute(
            "UPDATE users SET balance = balance - ? WHERE userid = ?",
            (amount, victim_id),
        )
        conn.execute(
            "UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE userid = ?",
            (amount, robber_id),
        )
        return amount


class EconomyCommand(commands.Cog):
    def __init__(self, client):
        self.client = client

    def connect(self):
        try:
            conn = sqlite3.connect(f"Data/users.db")
            curs = conn.cursor()
            return conn, curs
        except Exception as exc:
            print(exc)
            return None

    def cooldown(self, remaining):
        if remaining < 60:
            remaining = round(remaining)
            return f"{remaining} second{'' if remaining == 1 else 's'}"
        elif remaining < 3600:
            remaining = round(remaining / 60)
            return f"{remaining} minute{'' if remaining == 1 else 's'}"
        else:
            remaining = round(remaining / 3600)
            return f"{remaining} hour{'' if remaining == 1 else 's'}"

    async def respond_command_error(self, interaction, error):
        print(f"[!] Economy command error: {error}")
        if isinstance(error, app_commands.MissingPermissions):
            message = "You do not have permission to use this command."
        else:
            message = "That economy command could not be completed. Please try again."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="dice", description=f"Roll a 50% dice")
    async def dice(
        self, interaction: discord.Interaction, bet: app_commands.Range[int, 1, 1000000]
    ):
        print_interaction_info(interaction)
        userid = interaction.user.id
        if interaction.user.avatar is None:
            userpfp = interaction.user.default_avatar
        else:
            userpfp = interaction.user.avatar

        roll = random.randrange(1, 100)
        won = roll >= 50
        try:
            settled = settle_wager(userid, bet, bet if won else -bet)
        except sqlite3.Error as exc:
            print(exc)
            await interaction.response.send_message(
                "Something went wrong, contact support. (Code 3)", ephemeral=True
            )
            return

        if not settled:
            emb = discord.Embed(colour=discord.Colour.red())
            emb.set_author(name=f"Insufficient Balance", icon_url=userpfp)
            await interaction.response.send_message(embed=emb)
            return

        if not won:
            emb = discord.Embed(colour=discord.Colour.red())
            # emb.add_field(name="Bet", value=f"${bet}", inline=True)
            emb.add_field(name="Profit", value=f"-${bet}", inline=True)
            emb.set_author(name=f"{roll}/100", icon_url=userpfp)

        else:
            emb = discord.Embed(colour=discord.Colour.green())
            # emb.add_field(name="Bet", value=f"${bet}", inline=True)
            emb.add_field(name="Profit", value=f"${bet}", inline=True)
            emb.set_author(name=f"{roll}/100", icon_url=userpfp)

        await interaction.response.send_message(embed=emb)
        return

    @dice.error
    async def catch(self, interaction, error):
        await self.respond_command_error(interaction, error)

    @app_commands.command(name="rob", description="Rob another user")
    @app_commands.checks.cooldown(10, 180, key=lambda i: i.user.id)
    async def rob(self, interaction: discord.Interaction, member: discord.Member):
        print_interaction_info(interaction)

        if member.id == interaction.user.id or member.bot:
            await interaction.response.send_message(
                "You cannot rob yourself or a bot.", ephemeral=True
            )
            return

        userid = interaction.user.id
        try:
            ensure_economy_users((userid, member.id))
        except sqlite3.Error as exc:
            print(exc)
            await interaction.response.send_message(
                "Something went wrong, contact support. (Code 3)", ephemeral=True
            )
            return

        rob = random.choice([True, False])
        if rob is False:
            arr = random.choice([True, False])
            if arr is False:
                await interaction.response.send_message(
                    f"{member.mention} got away! Robbery failed."
                )
                return
            else:
                fine = random.choice([10, 20, 30])
                try:
                    debit_balance_to_zero(userid, fine)
                except sqlite3.Error as exc:
                    print(exc)
                    await interaction.response.send_message(
                        "Something went wrong, contact support. (Code 3)",
                        ephemeral=True,
                    )
                    return

                await interaction.response.send_message(
                    f"{member.mention} called the police! You have been fined **${fine}** for robbery"
                )
                return

        try:
            num = rob_balance_transfer(userid, member.id, random.randrange(10, 40))
        except sqlite3.Error as exc:
            print(exc)
            await interaction.response.send_message(
                "Something went wrong, contact support. (Code 3)", ephemeral=True
            )
            return

        if num == 0:
            await interaction.response.send_message(
                f"{member.mention} got lucky and had **$0** in his wallet."
            )
            return

        await interaction.response.send_message(
            f"You robbed {member.mention} and received **${num}**"
        )
        return

    @rob.error
    async def catch(self, interaction, error):
        if isinstance(error, app_commands.CommandOnCooldown):
            wait = self.cooldown(error.retry_after)
            await interaction.response.send_message(
                f"Wait **{wait}** before you can rob again", ephemeral=True
            )
            return

        await self.respond_command_error(interaction, error)

    @app_commands.command(name="work", description="Work and receive rewards")
    @app_commands.checks.cooldown(1, 300, key=lambda i: i.user.id)
    async def work(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        if interaction.user.avatar is None:
            userpfp = interaction.user.default_avatar
        else:
            userpfp = interaction.user.avatar

        userid = interaction.user.id
        amount = random.randrange(10, 100)
        try:
            credit_balance(userid, amount)
        except sqlite3.Error as exc:
            print(exc)
            await interaction.response.send_message(
                "Something went wrong, contact support. (Code 3)", ephemeral=True
            )
            return

        emb = discord.Embed(
            description=f"You received **${amount}**", colour=discord.Colour.green()
        )
        emb.set_author(name=f"Work", icon_url=userpfp)
        await interaction.response.send_message(embed=emb)
        return

    @work.error
    async def catch(self, interaction, error):
        if isinstance(error, app_commands.CommandOnCooldown):
            wait = self.cooldown(error.retry_after)
            await interaction.response.send_message(
                f"Wait **{wait}** before you can work again", ephemeral=True
            )
            return

        await self.respond_command_error(interaction, error)

    @app_commands.command(name="daily", description="Claim a free daily reward")
    @app_commands.checks.cooldown(1, 86400, key=lambda i: i.user.id)
    async def daily(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        if interaction.user.avatar is None:
            userpfp = interaction.user.default_avatar
        else:
            userpfp = interaction.user.avatar

        userid = interaction.user.id
        amount = random.randrange(100, 250)
        try:
            credit_balance(userid, amount)
        except sqlite3.Error as exc:
            print(exc)
            await interaction.response.send_message(
                "Something went wrong, contact support. (Code 3)", ephemeral=True
            )
            return

        emb = discord.Embed(
            description=f"You received **${amount}**", colour=discord.Colour.green()
        )
        emb.set_author(name=f"Daily Reward", icon_url=userpfp)
        await interaction.response.send_message(embed=emb)
        return

    @daily.error
    async def catch(self, interaction, error):
        if isinstance(error, app_commands.CommandOnCooldown):
            wait = self.cooldown(error.retry_after)
            await interaction.response.send_message(
                f"Wait **{wait}** before you can claim your daily reward.",
                ephemeral=True,
            )
            return

        await self.respond_command_error(interaction, error)

    @app_commands.command(name="bet", description="Bet against someone else")
    async def bet(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, 1, 1000000],
    ):
        print_interaction_info(interaction)
        userid = interaction.user.id
        userid1 = member.id
        if interaction.user.avatar is None:
            userpfp = interaction.user.default_avatar
        else:
            userpfp = interaction.user.avatar

        if member.avatar is None:
            userpfp1 = member.default_avatar
        else:
            userpfp1 = member.avatar

        if interaction.user == member:
            await interaction.response.send_message(
                f"You can't bet against yourself", ephemeral=True
            )
            return

        try:
            ensure_economy_users((userid, userid1))
            with sqlite3.connect(USERS_DB_PATH) as conn:
                balances = dict(
                    conn.execute(
                        "SELECT userid, COALESCE(balance, 0) FROM users "
                        "WHERE userid IN (?, ?)",
                        (userid, userid1),
                    ).fetchall()
                )
        except sqlite3.Error as exc:
            print(exc)
            await interaction.response.send_message(
                "The economy database is temporarily unavailable.", ephemeral=True
            )
            return

        if balances.get(userid, 0) < amount:
            await interaction.response.send_message(
                f"You don't have **${amount}**", ephemeral=True
            )
            return
        if balances.get(userid1, 0) < amount:
            await interaction.response.send_message(
                f"{member.mention} does not have **${amount}**", ephemeral=True
            )
            return

        class ChoiceView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=300)
                self.resolved = False
                self.resolve_lock = asyncio.Lock()

            @discord.ui.button(emoji="✅", label="Accept", custom_id=f"accept")
            async def accept(self, inter: discord.Interaction, button: discord.Button):
                if inter.user.id != member.id:
                    await inter.response.send_message(
                        "This request is not for you.", ephemeral=True
                    )
                    return

                async with self.resolve_lock:
                    if self.resolved:
                        await inter.response.send_message(
                            "This bet has already been resolved.", ephemeral=True
                        )
                        return
                    try:
                        partlist = [interaction.user, member]
                        winner = random.choice(partlist)
                        partlist.remove(winner)
                        loser = partlist[0]
                        if not transfer_balance(loser.id, winner.id, amount):
                            await inter.response.send_message(
                                f"{loser.mention} no longer has enough money for this bet.",
                                ephemeral=True,
                            )
                            return

                        self.resolved = True
                        emb = inter.message.embeds[0]
                        emb.colour = discord.Colour.green()
                        emb.add_field(name="Winner", value=winner.mention, inline=False)
                        emb.set_footer(text=None)
                        await inter.response.edit_message(embed=emb, view=None)
                    except (sqlite3.Error, discord.HTTPException) as exc:
                        print(traceback.format_exc())
                        if inter.response.is_done():
                            await inter.followup.send(
                                "The bet could not be completed.", ephemeral=True
                            )
                        else:
                            await inter.response.send_message(
                                "The bet could not be completed.", ephemeral=True
                            )

            @discord.ui.button(emoji="❌", label="Decline", custom_id=f"decline")
            async def decline(self, inter: discord.Interaction, button: discord.Button):
                if inter.user.id != member.id:
                    await inter.response.send_message(
                        "This request is not for you.", ephemeral=True
                    )
                    return

                async with self.resolve_lock:
                    if self.resolved:
                        await inter.response.send_message(
                            "This bet has already been resolved.", ephemeral=True
                        )
                        return
                    self.resolved = True
                    await inter.response.edit_message(
                        content="Bet declined.", embed=None, view=None
                    )

        emb = discord.Embed(title="Bet Request", colour=discord.Colour.grey())
        emb.add_field(name="From", value=interaction.user.mention, inline=False)
        emb.add_field(name="Amount", value=f"${amount}", inline=False)
        emb.set_author(name=member, icon_url=userpfp1)
        emb.set_footer(text="Expires in 3 minutes")

        await interaction.response.send_message(embed=emb, view=ChoiceView())
        return

    @bet.error
    async def catch(self, interaction, error):
        await self.respond_command_error(interaction, error)

    @app_commands.command(name="crash", description="Play a game of crash")
    async def crash(
        self,
        interaction: discord.Interaction,
        bet: app_commands.Range[int, 1, 1000000],
        cashout: app_commands.Range[float, 1.01, 100.0],
    ):
        print_interaction_info(interaction)
        cash = cashout
        userid = interaction.user.id
        if interaction.user.avatar is None:
            userpfp = interaction.user.default_avatar
        else:
            userpfp = interaction.user.avatar

        nums = numpy.random.exponential(scale=4, size=4)
        x = round(random.choice(nums), 2)
        won = x >= cash
        payout = cash * bet
        balance_delta = (payout - bet) if won else -bet
        try:
            settled = settle_wager(userid, bet, balance_delta)
        except sqlite3.Error as exc:
            print(exc)
            await interaction.response.send_message(
                "The economy database is temporarily unavailable.", ephemeral=True
            )
            return
        if not settled:
            emb = discord.Embed(colour=discord.Colour.red())
            emb.set_author(name="Insufficient Balance", icon_url=userpfp)
            await interaction.response.send_message(embed=emb)
            return

        if not won:
            emb = discord.Embed(
                title=f"{1.00 if x <= 1 else x}x", colour=discord.Colour.red()
            )
            emb.add_field(name="Bet", value=f"${bet}", inline=True)
            emb.add_field(name="Profit", value=f"-${bet}", inline=True)
            emb.add_field(name="Cashout", value=f"{cash}x", inline=False)
            emb.set_author(name="Loss", icon_url=userpfp)
            await interaction.response.send_message(embed=emb)
            return
        else:
            emb = discord.Embed(
                title=f"{1.00 if x <= 1 else x}x", colour=discord.Colour.green()
            )
            emb.add_field(name="Bet", value=f"${bet}", inline=True)
            emb.add_field(
                name="Profit", value=f"${round(payout - bet, 2)}", inline=True
            )
            emb.add_field(name="Cashout", value=f"{cash}x", inline=False)
            emb.set_author(name="Win", icon_url=userpfp)
            await interaction.response.send_message(embed=emb)
            return

    @crash.error
    async def catch(self, interaction, error):
        await self.respond_command_error(interaction, error)

    @app_commands.command(
        name="leaderboard", description="Displays the top 10 richest users"
    )
    async def leaderboard(self, interaction: discord.Interaction):
        print_interaction_info(interaction)
        if interaction.user.avatar is None:
            userpfp = interaction.user.default_avatar
        else:
            userpfp = interaction.user.avatar
        result = self.connect()
        if result is None:
            await interaction.response.send_message(
                "Something went wrong, contact support. (Code 3)", ephemeral=True
            )
            return

        conn, curs = result

        query = f"""SELECT userid, balance FROM users ORDER BY balance DESC LIMIT 10;"""
        curs.execute(query)
        records = curs.fetchall()
        conn.close()

        if len(records) == 0:
            emb = discord.Embed(description="No users found", colour=self.client.colour)
            emb.set_author(name="Leaderboard", icon_url=userpfp)
            await interaction.response.send_message(embed=emb)
            return

        text = ""
        for index, record in enumerate(records):
            userid, balance = record
            text += f"**{index + 1}.** <@{userid}> - ``${round(balance)}``\n"

        emb = discord.Embed(description=text, colour=self.client.colour)
        emb.set_author(name="Leaderboard", icon_url=userpfp)
        await interaction.response.send_message(embed=emb)
        return

    @leaderboard.error
    async def catch(self, interaction, error):
        await self.respond_command_error(interaction, error)

    @app_commands.command(name="send", description="Send someone else money")
    @app_commands.checks.has_permissions(administrator=True)
    async def send(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, 1, 100000],
    ):
        print_interaction_info(interaction)
        userid = interaction.user.id
        try:
            transferred = transfer_balance(userid, member.id, amount)
        except sqlite3.Error as exc:
            print(exc)
            await interaction.response.send_message(
                "Something went wrong, contact support. (Code 3)", ephemeral=True
            )
            return

        if not transferred:
            await interaction.response.send_message(
                "Insufficient Balance", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"You sent **${amount}** to {member.mention}"
        )
        return

    @send.error
    async def catch(self, interaction, error):
        await self.respond_command_error(interaction, error)

    @app_commands.command(name="balance", description="Shows how much money you have")
    @app_commands.describe(member="Show somebody else's balance")
    async def balance(
        self, interaction: discord.Interaction, member: discord.Member = None
    ):
        print_interaction_info(interaction)
        if member is not None:
            user = member
            userid = member.id
            if member.avatar is None:
                userpfp = member.default_avatar
            else:
                userpfp = member.avatar
        else:
            user = interaction.user
            userid = interaction.user.id
            if interaction.user.avatar is None:
                userpfp = interaction.user.default_avatar
            else:
                userpfp = interaction.user.avatar

        try:
            ensure_economy_users((userid,))
            with sqlite3.connect(USERS_DB_PATH) as conn:
                balance = conn.execute(
                    "SELECT COALESCE(balance, 0) FROM users WHERE userid = ?",
                    (userid,),
                ).fetchone()[0]
        except sqlite3.Error as exc:
            print(exc)
            await interaction.response.send_message(
                "The economy database is temporarily unavailable.", ephemeral=True
            )
            return
        emb = discord.Embed(
            description=f"**${round(balance, 2)}**", colour=self.client.colour
        )
        emb.set_author(name=f"{user}", icon_url=userpfp)
        await interaction.response.send_message(embed=emb)
        return

    @balance.error
    async def catch(self, interaction, error):
        await self.respond_command_error(interaction, error)

    @app_commands.command(name="addmoney", description="Add money to a user (Admin)")
    @app_commands.checks.has_permissions(administrator=True)
    async def addmoney(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, 1, 100000],
    ):
        print_interaction_info(interaction)
        try:
            credit_balance(member.id, amount)
        except sqlite3.Error as exc:
            print(exc)
            await interaction.response.send_message(
                "Something went wrong, contact support. (Code 3)", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Added **${amount}** to {member.mention}"
        )
        return

    @addmoney.error
    async def catch(self, interaction, error):
        await self.respond_command_error(interaction, error)

    @app_commands.command(
        name="removemoney", description="Remove money from a user (Admin)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def removemoney(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, 1, 100000],
    ):
        print_interaction_info(interaction)
        try:
            _, created = debit_balance_to_zero(member.id, amount)
        except sqlite3.Error as exc:
            print(exc)
            await interaction.response.send_message(
                "Something went wrong, contact support. (Code 3)", ephemeral=True
            )
            return

        if created:
            await interaction.response.send_message(f"{member.mention} has **$0**")
            return

        await interaction.response.send_message(
            f"Removed **${amount}** from {member.mention}"
        )
        return

    @removemoney.error
    async def catch(self, interaction, error):
        await self.respond_command_error(interaction, error)


async def setup(client):
    await client.add_cog(EconomyCommand(client))
