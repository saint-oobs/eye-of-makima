"""
Join Raid command group. (Premium)

Command group: g!joinraid (alias: g!jr)

Subcommands:
    status                  — Show current joinraid configuration
    enable / disable        — Toggle joinraid detection
    set trigger <count>     — Set join count to trigger detection
    set period <minutes>    — Set the rolling window in minutes
    set action <action>     — Set the action applied to raiders
    set type <type>         — Set the account type filter
    flags nopfp <on|off>    — Toggle no-pfp flag
    flags age <on|off>      — Toggle new-account age flag
    flags minage <days>     — Set minimum age for age flag
    addrole <role>          — Add a role to warn on raid detection
    removerole <role>       — Remove a warned role
    window                  — Show current join window size
    clear                   — Clear the current join window
    logs                    — Show recent raid event log
"""

import logging

import discord
from discord.ext import commands

from utils.checks import require_permit, guild_only, premium_only
from utils.helpers import make_embed, success_embed, error_embed, info_embed
from utils.paginator import send_paginated, build_pages

log = logging.getLogger("bot.joinraid_commands")

_VALID_ACTIONS      = ("kick", "ban", "quarantine")
_VALID_ACCOUNT_TYPES = ("suspicious", "new", "all")


class JoinRaidCommands(commands.Cog, name="JoinRaid"):
    def __init__(self, bot):
        self.bot = bot

    # ── Group ──────────────────────────────────────────────────

    @commands.group(name="joinraid", aliases=["jr"], invoke_without_command=True)
    @guild_only()
    @premium_only()
    async def joinraid(self, ctx: commands.Context) -> None:
        """Join raid detection management. (Premium)"""
        await ctx.send_help(ctx.command)

    # ── status ─────────────────────────────────────────────────

    @joinraid.command(name="status")
    @require_permit(2)
    async def jr_status(self, ctx: commands.Context) -> None:
        """Show the current join raid configuration."""
        cfg = self.bot.config.get(ctx.guild.id)
        jrc = cfg.get("joinraid", {})

        def _fmt(val) -> str:
            if isinstance(val, bool):
                return "✅" if val else "❌"
            return f"`{val}`"

        age_flag   = jrc.get("age_flag", {})
        nopfp_flag = jrc.get("nopfp_flag", {})

        jr_cog     = self.bot.get_cog("JoinRaid")
        window_now = jr_cog.get_window_size(ctx.guild.id) if jr_cog else 0

        warned_roles = jrc.get("warned_roles", [])
        role_mentions = (
            " ".join(f"<@&{rid}>" for rid in warned_roles)
            if warned_roles else "*(none)*"
        )

        embed = make_embed(
            title="🚨 Join Raid Configuration",
            colour=discord.Colour.blurple(),
            fields=[
                ("Enabled",        _fmt(jrc.get("enabled", False)),                                  True),
                ("Trigger Count",  f"`{jrc.get('trigger_count', 10)}` joins",                        True),
                ("Window",         f"`{jrc.get('trigger_period_minutes', 5)}` minutes",              True),
                ("Action",         f"`{jrc.get('action', 'kick')}`",                                 True),
                ("Account Type",   f"`{jrc.get('account_type', 'suspicious')}`",                     True),
                ("Window Now",     f"`{window_now}` tracked joins",                                  True),
                ("Age Flag",       f"{_fmt(age_flag.get('enabled', True))} min `{age_flag.get('min_days', 2)}d`", True),
                ("No-PFP Flag",    _fmt(nopfp_flag.get("enabled", True)),                            True),
                ("Warned Roles",   role_mentions,                                                    False),
            ],
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── enable / disable ───────────────────────────────────────

    @joinraid.command(name="enable")
    @require_permit(3)
    async def jr_enable(self, ctx: commands.Context) -> None:
        """Enable join raid detection."""
        self.bot.config.set(ctx.guild.id, ["joinraid", "enabled"], True)
        await ctx.send(embed=success_embed("Join raid detection **enabled**."))

    @joinraid.command(name="disable")
    @require_permit(3)
    async def jr_disable(self, ctx: commands.Context) -> None:
        """Disable join raid detection."""
        self.bot.config.set(ctx.guild.id, ["joinraid", "enabled"], False)
        await ctx.send(embed=success_embed("Join raid detection **disabled**."))

    # ── set group ──────────────────────────────────────────────

    @joinraid.group(name="set", invoke_without_command=True)
    @guild_only()
    async def jr_set(self, ctx: commands.Context) -> None:
        """Update join raid settings."""
        await ctx.send_help(ctx.command)

    @jr_set.command(name="trigger")
    @require_permit(3)
    async def jr_set_trigger(self, ctx: commands.Context, count: int) -> None:
        """Set the number of joins required to trigger detection."""
        if count < 2:
            return await ctx.send(embed=error_embed(
                "Trigger count must be at least `2`."
            ))
        self.bot.config.set(ctx.guild.id, ["joinraid", "trigger_count"], count)
        await ctx.send(embed=success_embed(
            f"Raid trigger count set to **{count}** joins."
        ))

    @jr_set.command(name="period")
    @require_permit(3)
    async def jr_set_period(self, ctx: commands.Context, minutes: int) -> None:
        """Set the rolling window duration in minutes."""
        if not (1 <= minutes <= 60):
            return await ctx.send(embed=error_embed(
                "Period must be between `1` and `60` minutes."
            ))
        self.bot.config.set(
            ctx.guild.id, ["joinraid", "trigger_period_minutes"], minutes
        )
        await ctx.send(embed=success_embed(
            f"Raid detection window set to **{minutes} minute(s)**."
        ))

    @jr_set.command(name="action")
    @require_permit(3)
    async def jr_set_action(self, ctx: commands.Context, action: str) -> None:
        """
        Set the action applied to detected raiders.
        Valid: kick | ban | quarantine
        """
        action = action.lower()
        if action not in _VALID_ACTIONS:
            actions_fmt = ", ".join(f"`{a}`" for a in _VALID_ACTIONS)
            return await ctx.send(embed=error_embed(
                f"Invalid action `{action}`.\nValid actions: {actions_fmt}"
            ))
        self.bot.config.set(ctx.guild.id, ["joinraid", "action"], action)
        await ctx.send(embed=success_embed(
            f"Raid action set to **{action}**."
        ))

    @jr_set.command(name="type")
    @require_permit(3)
    async def jr_set_type(self, ctx: commands.Context, account_type: str) -> None:
        """
        Set which account types qualify for raid tracking.

        Types:
            suspicious — accounts with new/no-pfp flags (default)
            new        — only new accounts (age flag)
            all        — every join counts
        """
        account_type = account_type.lower()
        if account_type not in _VALID_ACCOUNT_TYPES:
            types_fmt = ", ".join(f"`{t}`" for t in _VALID_ACCOUNT_TYPES)
            return await ctx.send(embed=error_embed(
                f"Invalid type `{account_type}`.\nValid types: {types_fmt}"
            ))
        self.bot.config.set(ctx.guild.id, ["joinraid", "account_type"], account_type)
        await ctx.send(embed=success_embed(
            f"Account type filter set to **{account_type}**."
        ))

    # ── flags group ────────────────────────────────────────────

    @joinraid.group(name="flags", invoke_without_command=True)
    @guild_only()
    async def jr_flags(self, ctx: commands.Context) -> None:
        """Manage the account qualifier flags."""
        await ctx.send_help(ctx.command)

    @jr_flags.command(name="nopfp")
    @require_permit(3)
    async def flags_nopfp(self, ctx: commands.Context, toggle: str) -> None:
        """Toggle the no-profile-picture qualifier flag."""
        val = toggle.lower() in ("on", "enable", "true", "yes", "1")
        self.bot.config.set(
            ctx.guild.id, ["joinraid", "nopfp_flag", "enabled"], val
        )
        await ctx.send(embed=success_embed(
            f"No-PFP flag **{'enabled' if val else 'disabled'}**."
        ))

    @jr_flags.command(name="age")
    @require_permit(3)
    async def flags_age(self, ctx: commands.Context, toggle: str) -> None:
        """Toggle the new-account age qualifier flag."""
        val = toggle.lower() in ("on", "enable", "true", "yes", "1")
        self.bot.config.set(
            ctx.guild.id, ["joinraid", "age_flag", "enabled"], val
        )
        await ctx.send(embed=success_embed(
            f"Age flag **{'enabled' if val else 'disabled'}**."
        ))

    @jr_flags.command(name="minage")
    @require_permit(3)
    async def flags_minage(self, ctx: commands.Context, days: int) -> None:
        """Set the minimum account age (days) for the age flag."""
        if days < 0:
            return await ctx.send(embed=error_embed(
                "Days must be 0 or greater."
            ))
        self.bot.config.set(
            ctx.guild.id, ["joinraid", "age_flag", "min_days"], days
        )
        await ctx.send(embed=success_embed(
            f"Age flag minimum set to **{days} day(s)**."
        ))

    # ── Warned roles ───────────────────────────────────────────

    @joinraid.command(name="addrole")
    @require_permit(3)
    async def jr_addrole(
        self,
        ctx:  commands.Context,
        role: discord.Role,
    ) -> None:
        """Add a role to be pinged when a raid is detected."""
        cfg   = self.bot.config.get(ctx.guild.id)
        roles = cfg["joinraid"].setdefault("warned_roles", [])
        if role.id in roles:
            return await ctx.send(embed=info_embed(
                f"{role.mention} is already in the warned roles list."
            ))
        roles.append(role.id)
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed(
            f"Added {role.mention} to raid warn roles."
        ))

    @joinraid.command(name="removerole")
    @require_permit(3)
    async def jr_removerole(
        self,
        ctx:  commands.Context,
        role: discord.Role,
    ) -> None:
        """Remove a role from the raid detection warn list."""
        cfg   = self.bot.config.get(ctx.guild.id)
        roles = cfg["joinraid"].get("warned_roles", [])
        if role.id not in roles:
            return await ctx.send(embed=error_embed(
                f"{role.mention} is not in the warned roles list."
            ))
        roles.remove(role.id)
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed(
            f"Removed {role.mention} from raid warn roles."
        ))

    # ── window ─────────────────────────────────────────────────

    @joinraid.command(name="window")
    @require_permit(2)
    async def jr_window(self, ctx: commands.Context) -> None:
        """Show how many joins are currently tracked in the active window."""
        jr_cog = self.bot.get_cog("JoinRaid")
        if not jr_cog:
            return await ctx.send(embed=error_embed("JoinRaid cog not loaded."))

        count  = jr_cog.get_window_size(ctx.guild.id)
        cfg    = self.bot.config.get(ctx.guild.id)
        jrc    = cfg.get("joinraid", {})
        trigger = jrc.get("trigger_count", 10)
        period  = jrc.get("trigger_period_minutes", 5)

        bar_width = 10
        filled    = round(min(count / trigger, 1.0) * bar_width)
        bar       = "█" * filled + "░" * (bar_width - filled)
        pct       = int(min(count / trigger, 1.0) * 100)

        embed = make_embed(
            title="🚨 Join Raid Window",
            description=(
                f"`[{bar}]` {pct}%\n"
                f"**{count}** / **{trigger}** joins tracked "
                f"in the last **{period}** minute(s)."
            ),
            colour=(
                discord.Colour.red()    if count >= trigger      else
                discord.Colour.orange() if count >= trigger * 0.6 else
                discord.Colour.green()
            ),
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── clear ──────────────────────────────────────────────────

    @joinraid.command(name="clear")
    @require_permit(3)
    async def jr_clear(self, ctx: commands.Context) -> None:
        """Manually clear the current join tracking window."""
        jr_cog = self.bot.get_cog("JoinRaid")
        if jr_cog:
            jr_cog.clear_window(ctx.guild.id)
        await ctx.send(embed=success_embed(
            "Join raid window cleared."
        ))

    # ── logs ───────────────────────────────────────────────────

    @joinraid.command(name="logs")
    @require_permit(2)
    async def jr_logs(self, ctx: commands.Context) -> None:
        """Show recent raid event log entries for this server."""
        rows = await self.bot.db.fetchall(
            """
            SELECT user_id, action, joined_at
            FROM joinraid_events
            WHERE guild_id = ?
            ORDER BY joined_at DESC
            LIMIT 100
            """,
            (ctx.guild.id,),
        )

        if not rows:
            return await ctx.send(embed=info_embed(
                "No join raid events recorded for this server."
            ))

        lines = []
        for row in rows:
            ts     = str(row["joined_at"])[:19]
            uid    = row["user_id"]
            m      = ctx.guild.get_member(uid)
            name   = m.display_name if m else f"ID:{uid}"
            action = row["action"]
            lines.append(f"`{ts}` — **{name}** — `{action}`")

        pages = build_pages(
            lines,
            title="🚨 Join Raid Event Log",
            colour=discord.Colour.red(),
            per_page=15,
            numbered=False,
            footer=f"{len(rows)} total events",
        )
        await send_paginated(ctx, pages)


async def setup(bot):
    await bot.add_cog(JoinRaidCommands(bot))