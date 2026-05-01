"""
Join Gate command group.

Command group: g!joingate (alias: g!jg)

Subcommands:
    status                      — Show current joingate configuration
    nopfp <enable|disable>      — Toggle no-pfp check
    nopfp action <action>       — Set action for no-pfp check
    age <enable|disable>        — Toggle account age check
    age set <days>              — Set minimum account age
    age action <action>         — Set action for age check
    bots <enable|disable>       — Toggle bot addition check
    bots action <action>        — Set action for bot addition check
    unverified <enable|disable> — Toggle unverified bot check
    adnames <enable|disable>    — Toggle advertising name check
    suspicious <enable|disable> — Toggle suspicious account check
    filter <enable|disable>     — Toggle username filter
    filter add <pattern>        — Add a username filter pattern
    filter remove <pattern>     — Remove a username filter pattern
    filter list                 — List all username filter patterns
"""

import logging

import discord
from discord.ext import commands

from utils.checks import require_permit, guild_only
from utils.helpers import make_embed, success_embed, error_embed, info_embed
from utils.paginator import send_paginated, build_pages

log = logging.getLogger("bot.joingate_commands")

_VALID_ACTIONS = ("kick", "ban", "quarantine", "log")


class JoinGateCommands(commands.Cog, name="JoinGate"):
    def __init__(self, bot):
        self.bot = bot

    # ── Helpers ────────────────────────────────────────────────

    def _action_valid(self, action: str) -> bool:
        return action.lower() in _VALID_ACTIONS

    def _fmt_action(self, action: str) -> str:
        icons = {"kick": "👢", "ban": "🔨", "quarantine": "🔒", "log": "📋"}
        return f"{icons.get(action, '❓')} `{action}`"

    # ── Group ──────────────────────────────────────────────────

    @commands.group(name="joingate", aliases=["jg"], invoke_without_command=True)
    @guild_only()
    async def joingate(self, ctx: commands.Context) -> None:
        """Join gate system management."""
        await ctx.send_help(ctx.command)

    # ── status ─────────────────────────────────────────────────

    @joingate.command(name="status")
    @require_permit(2)
    async def jg_status(self, ctx: commands.Context) -> None:
        """Show the current join gate configuration."""
        cfg = self.bot.config.get(ctx.guild.id)
        jg  = cfg.get("joingate", {})

        def _row(key: str, label: str) -> tuple[str, str, bool]:
            check   = jg.get(key, {})
            enabled = "✅" if check.get("enabled") else "❌"
            action  = check.get("action", "—")
            extras  = ""
            if key == "account_age":
                extras = f" | min `{check.get('min_days', 7)}d`"
            return (label, f"{enabled} {self._fmt_action(action)}{extras}", False)

        embed = make_embed(
            title="🚪 Join Gate Configuration",
            colour=discord.Colour.blurple(),
            fields=[
                _row("no_pfp",           "No Avatar"),
                _row("account_age",      "Account Age"),
                _row("bot_addition",     "Bot Addition"),
                _row("unverified_bots",  "Unverified Bots"),
                _row("advertising_names","Advertising Names"),
                _row("suspicious",       "Suspicious Accounts"),
                _row("username_filter",  "Username Filter"),
                (
                    "Filter Patterns",
                    f"`{len(jg.get('username_filter', {}).get('patterns', []))}` pattern(s) configured",
                    False,
                ),
            ],
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── No PFP ─────────────────────────────────────────────────

    @joingate.group(name="nopfp", invoke_without_command=True)
    @guild_only()
    async def jg_nopfp(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle the no-avatar check. Use: g!jg nopfp enable|disable"""
        if toggle is None:
            return await ctx.send_help(ctx.command)
        await self._toggle_check(ctx, "no_pfp", toggle)

    @jg_nopfp.command(name="action")
    @require_permit(3)
    async def nopfp_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the no-avatar check."""
        await self._set_action(ctx, "no_pfp", action)

    # ── Account age ────────────────────────────────────────────

    @joingate.group(name="age", invoke_without_command=True)
    @guild_only()
    async def jg_age(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle the account age check. Use: g!jg age enable|disable"""
        if toggle is None:
            return await ctx.send_help(ctx.command)
        await self._toggle_check(ctx, "account_age", toggle)

    @jg_age.command(name="set")
    @require_permit(3)
    async def age_set(self, ctx: commands.Context, days: int) -> None:
        """Set the minimum account age in days."""
        if days < 0:
            return await ctx.send(embed=error_embed("Days must be 0 or greater."))
        self.bot.config.set(ctx.guild.id, ["joingate", "account_age", "min_days"], days)
        await ctx.send(embed=success_embed(f"Minimum account age set to **{days} day(s)**."))

    @jg_age.command(name="action")
    @require_permit(3)
    async def age_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the account age check."""
        await self._set_action(ctx, "account_age", action)

    @jg_age.command(name="expose")
    @require_permit(3)
    async def age_expose(self, ctx: commands.Context, toggle: str) -> None:
        """Toggle whether the minimum age is shown in the kick/log reason."""
        val = toggle.lower() in ("true", "yes", "on", "enable")
        self.bot.config.set(
            ctx.guild.id, ["joingate", "account_age", "expose_min"], val
        )
        await ctx.send(embed=success_embed(
            f"Account age exposure **{'enabled' if val else 'disabled'}**."
        ))

    # ── Bot addition ───────────────────────────────────────────

    @joingate.group(name="bots", invoke_without_command=True)
    @guild_only()
    async def jg_bots(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle the bot addition check. Use: g!jg bots enable|disable"""
        if toggle is None:
            return await ctx.send_help(ctx.command)
        await self._toggle_check(ctx, "bot_addition", toggle)

    @jg_bots.command(name="action")
    @require_permit(3)
    async def bots_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the bot addition check."""
        await self._set_action(ctx, "bot_addition", action)

    # ── Unverified bots ────────────────────────────────────────

    @joingate.group(name="unverified", invoke_without_command=True)
    @guild_only()
    async def jg_unverified(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle the unverified bot check. Use: g!jg unverified enable|disable"""
        if toggle is None:
            return await ctx.send_help(ctx.command)
        await self._toggle_check(ctx, "unverified_bots", toggle)

    @jg_unverified.command(name="action")
    @require_permit(3)
    async def unverified_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the unverified bot check."""
        await self._set_action(ctx, "unverified_bots", action)

    # ── Advertising names ──────────────────────────────────────

    @joingate.group(name="adnames", invoke_without_command=True)
    @guild_only()
    async def jg_adnames(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle the advertising username check. Use: g!jg adnames enable|disable"""
        if toggle is None:
            return await ctx.send_help(ctx.command)
        await self._toggle_check(ctx, "advertising_names", toggle)

    @jg_adnames.command(name="action")
    @require_permit(3)
    async def adnames_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the advertising names check."""
        await self._set_action(ctx, "advertising_names", action)

    # ── Suspicious ─────────────────────────────────────────────

    @joingate.group(name="suspicious", invoke_without_command=True)
    @guild_only()
    async def jg_suspicious(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle the suspicious account check. Use: g!jg suspicious enable|disable"""
        if toggle is None:
            return await ctx.send_help(ctx.command)
        await self._toggle_check(ctx, "suspicious", toggle)

    @jg_suspicious.command(name="action")
    @require_permit(3)
    async def suspicious_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the suspicious account check."""
        await self._set_action(ctx, "suspicious", action)

    # ── Username filter ────────────────────────────────────────

    @joingate.group(name="filter", invoke_without_command=True)
    @guild_only()
    async def jg_filter(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle the username filter. Use: g!jg filter enable|disable"""
        if toggle is None:
            return await ctx.send_help(ctx.command)
        await self._toggle_check(ctx, "username_filter", toggle)

    @jg_filter.command(name="action")
    @require_permit(3)
    async def filter_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the username filter."""
        await self._set_action(ctx, "username_filter", action)

    @jg_filter.command(name="add")
    @require_permit(3)
    async def filter_add(self, ctx: commands.Context, *, pattern: str) -> None:
        """Add a regex pattern to the username filter."""
        import re as _re
        try:
            _re.compile(pattern)
        except _re.error as exc:
            return await ctx.send(embed=error_embed(
                f"Invalid regex pattern: `{exc}`"
            ))

        cfg      = self.bot.config.get(ctx.guild.id)
        patterns = cfg["joingate"]["username_filter"].setdefault("patterns", [])

        if pattern in patterns:
            return await ctx.send(embed=info_embed(
                f"Pattern `{pattern}` is already in the filter."
            ))
        if len(patterns) >= 50:
            return await ctx.send(embed=error_embed(
                "Maximum of 50 filter patterns reached."
            ))

        patterns.append(pattern)
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed(
            f"Added username filter pattern: `{pattern}`"
        ))

    @jg_filter.command(name="remove", aliases=["rm"])
    @require_permit(3)
    async def filter_remove(self, ctx: commands.Context, *, pattern: str) -> None:
        """Remove a regex pattern from the username filter."""
        cfg      = self.bot.config.get(ctx.guild.id)
        patterns = cfg["joingate"]["username_filter"].get("patterns", [])

        if pattern not in patterns:
            return await ctx.send(embed=error_embed(
                f"Pattern `{pattern}` not found in the filter."
            ))

        patterns.remove(pattern)
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed(
            f"Removed username filter pattern: `{pattern}`"
        ))

    @jg_filter.command(name="list")
    @require_permit(2)
    async def filter_list(self, ctx: commands.Context) -> None:
        """List all active username filter patterns."""
        cfg      = self.bot.config.get(ctx.guild.id)
        patterns = cfg["joingate"]["username_filter"].get("patterns", [])

        if not patterns:
            return await ctx.send(embed=info_embed(
                "No username filter patterns configured."
            ))

        pages = build_pages(
            patterns,
            title="🔍 Username Filter Patterns",
            colour=discord.Colour.blurple(),
            per_page=15,
            numbered=True,
        )
        await send_paginated(ctx, pages)

    # ── Shared helpers ─────────────────────────────────────────

    async def _toggle_check(
        self,
        ctx:       commands.Context,
        check_key: str,
        toggle:    str,
    ) -> None:
        """Enable or disable a named joingate check."""
        if not await require_permit(3).predicate(ctx):
            return

        val = toggle.lower() in ("enable", "on", "true", "yes", "1")
        if toggle.lower() not in (
            "enable", "disable", "on", "off", "true", "false", "yes", "no", "1", "0"
        ):
            return await ctx.send(embed=error_embed(
                f"Use `enable` or `disable`."
            ))

        self.bot.config.set(
            ctx.guild.id,
            ["joingate", check_key, "enabled"],
            val,
        )
        label = check_key.replace("_", " ").title()
        await ctx.send(embed=success_embed(
            f"**{label}** check **{'enabled' if val else 'disabled'}**."
        ))

    async def _set_action(
        self,
        ctx:       commands.Context,
        check_key: str,
        action:    str,
    ) -> None:
        """Set the action for a named joingate check."""
        action = action.lower()
        if not self._action_valid(action):
            actions_fmt = ", ".join(f"`{a}`" for a in _VALID_ACTIONS)
            return await ctx.send(embed=error_embed(
                f"Invalid action `{action}`.\nValid actions: {actions_fmt}"
            ))

        self.bot.config.set(
            ctx.guild.id,
            ["joingate", check_key, "action"],
            action,
        )
        label = check_key.replace("_", " ").title()
        await ctx.send(embed=success_embed(
            f"**{label}** action set to {self._fmt_action(action)}."
        ))


async def setup(bot):
    await bot.add_cog(JoinGateCommands(bot))