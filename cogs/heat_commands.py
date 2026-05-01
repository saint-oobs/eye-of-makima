"""
Heat system commands.

Command group: g!heat

Subcommands:
    status [member]         — Show heat & strikes for a member (or self)
    leaderboard             — Top 10 hottest members in the server
    reset <member>          — Reset heat & strikes for a member
    config                  — Show current heat system configuration
    set <key> <value>       — Update a heat config value
    enable / disable        — Toggle the heat system on/off
    blacklist               — Word blacklist management subgroup
    unlock                  — Remove auto-lockdown from all channels
"""

import logging

import discord
from discord.ext import commands

from utils.checks import require_permit, guild_only, feature_enabled
from utils.helpers import make_embed, success_embed, error_embed, info_embed
from utils.paginator import send_paginated, build_pages

log = logging.getLogger("bot.heat_commands")


class HeatCommands(commands.Cog, name="Heat"):
    def __init__(self, bot):
        self.bot = bot

    # ── Group ──────────────────────────────────────────────────

    @commands.group(name="heat", invoke_without_command=True)
    @guild_only()
    async def heat(self, ctx: commands.Context) -> None:
        """Heat system management. Use subcommands to manage."""
        await ctx.send_help(ctx.command)

    # ── status ─────────────────────────────────────────────────

    @heat.command(name="status")
    @guild_only()
    async def heat_status(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        """Show current heat and strike count for a member (default: yourself)."""
        target     = member or ctx.author
        cfg        = self.bot.config.get(ctx.guild.id)
        heat_cfg   = cfg.get("heat", {})
        max_heat   = heat_cfg.get("max_heat", 85.0)
        heat_cog   = self.bot.get_cog("HeatEngine")
        state_cog  = self.bot.get_cog("HeatState")

        if not heat_cog:
            return await ctx.send(embed=error_embed("Heat engine is not loaded."))

        heat    = heat_cog.get_heat(ctx.guild.id, target.id)
        strikes = heat_cog.get_strikes(ctx.guild.id, target.id)
        cap     = heat_cfg.get("strikes_cap", 3)
        bar     = state_cog.heat_bar(heat, max_heat) if state_cog else ""

        colour = (
            discord.Colour.red()    if heat >= max_heat        else
            discord.Colour.orange() if heat >= max_heat * 0.6  else
            discord.Colour.green()
        )

        embed = make_embed(
            title=f"🌡️ Heat Status — {target.display_name}",
            description=f"{bar} `{heat:.1f} / {max_heat:.1f}`",
            colour=colour,
            fields=[
                ("Strikes",      f"`{strikes} / {cap}`",                          True),
                ("System",       "✅ Enabled" if heat_cfg.get("enabled") else "❌ Disabled", True),
                ("Degradation",  f"`{heat_cfg.get('degradation_per_second', 0.5)}/s`", True),
            ],
            timestamp=True,
        )
        embed.set_thumbnail(url=(target.avatar or target.default_avatar).url)
        await ctx.send(embed=embed)

    # ── leaderboard ────────────────────────────────────────────

    @heat.command(name="leaderboard", aliases=["lb", "top"])
    @guild_only()
    async def heat_leaderboard(self, ctx: commands.Context) -> None:
        """Show the top 10 members by current heat."""
        heat_cog = self.bot.get_cog("HeatEngine")
        if not heat_cog:
            return await ctx.send(embed=error_embed("Heat engine is not loaded."))

        cfg      = self.bot.config.get(ctx.guild.id)
        max_heat = cfg.get("heat", {}).get("max_heat", 85.0)
        board    = heat_cog.get_leaderboard(ctx.guild.id, limit=10)

        if not board:
            return await ctx.send(embed=info_embed("No members currently have active heat."))

        lines = []
        for rank, (user_id, heat, strikes) in enumerate(board, start=1):
            member = ctx.guild.get_member(user_id)
            name   = member.display_name if member else f"User {user_id}"
            pct    = int(min(heat / max_heat, 1.0) * 100)
            bar    = "█" * (pct // 10) + "░" * (10 - pct // 10)
            lines.append(
                f"`{rank:>2}.` **{name}** — `{heat:.1f}/{max_heat:.1f}` "
                f"[{bar}] {pct}% — strikes: `{strikes}`"
            )

        embed = make_embed(
            title=f"🌡️ Heat Leaderboard — {ctx.guild.name}",
            description="\n".join(lines),
            colour=discord.Colour.orange(),
            footer=f"Tracking {heat_cog.guild_user_count(ctx.guild.id)} members",
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── reset ──────────────────────────────────────────────────

    @heat.command(name="reset")
    @require_permit(2)
    async def heat_reset(
        self,
        ctx: commands.Context,
        member: discord.Member,
    ) -> None:
        """Reset heat and strikes for a member."""
        heat_cog = self.bot.get_cog("HeatEngine")
        if not heat_cog:
            return await ctx.send(embed=error_embed("Heat engine is not loaded."))

        heat_cog.reset_heat(ctx.guild.id, member.id)
        await ctx.send(
            embed=success_embed(
                f"Heat and strikes reset for **{member.display_name}**."
            )
        )

    # ── enable ─────────────────────────────────────────────────

    @heat.command(name="enable")
    @require_permit(3)
    async def heat_enable(self, ctx: commands.Context) -> None:
        """Enable the heat system for this server."""
        self.bot.config.set(ctx.guild.id, ["heat", "enabled"], True)
        await ctx.send(embed=success_embed("Heat system **enabled**."))

    # ── disable ────────────────────────────────────────────────

    @heat.command(name="disable")
    @require_permit(3)
    async def heat_disable(self, ctx: commands.Context) -> None:
        """Disable the heat system for this server."""
        self.bot.config.set(ctx.guild.id, ["heat", "enabled"], False)
        await ctx.send(embed=success_embed("Heat system **disabled**."))

    # ── config ─────────────────────────────────────────────────

    @heat.command(name="config")
    @require_permit(2)
    async def heat_config(self, ctx: commands.Context) -> None:
        """Show the current heat system configuration."""
        cfg      = self.bot.config.get(ctx.guild.id)
        heat_cfg = cfg.get("heat", {})
        filters  = heat_cfg.get("filters", {})

        def _fmt(val) -> str:
            if isinstance(val, bool):
                return "✅" if val else "❌"
            return f"`{val}`"

        filter_lines = []
        for name, fdata in filters.items():
            if isinstance(fdata, dict):
                enabled = fdata.get("enabled", False)
                icon    = "✅" if enabled else "❌"
                filter_lines.append(f"{icon} `{name}`")

        embed = make_embed(
            title="⚙️ Heat Configuration",
            colour=discord.Colour.blurple(),
            fields=[
                ("Enabled",         _fmt(heat_cfg.get("enabled", True)),                   True),
                ("Max Heat",        _fmt(heat_cfg.get("max_heat", 85.0)),                  True),
                ("Degradation/s",   _fmt(heat_cfg.get("degradation_per_second", 0.5)),     True),
                ("Strikes Cap",     _fmt(heat_cfg.get("strikes_cap", 3)),                  True),
                ("Timeout/Strike",  _fmt(heat_cfg.get("timeout_per_strike", "5m")),        True),
                ("Timeout at Cap",  _fmt(heat_cfg.get("timeout_at_cap", "1h")),            True),
                ("Reset on TO",     _fmt(heat_cfg.get("reset_heat_on_timeout", True)),     True),
                ("Anti-Spam",       _fmt(heat_cfg.get("anti_spam", True)),                 True),
                ("Multiplier",      _fmt(heat_cfg.get("multiplier", False)),               True),
                ("Filters",         "\n".join(filter_lines) or "*(none)*",                 False),
            ],
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── set ────────────────────────────────────────────────────

    @heat.command(name="set")
    @require_permit(3)
    async def heat_set(
        self,
        ctx:   commands.Context,
        key:   str,
        value: str,
    ) -> None:
        """
        Update a heat config value.

        Keys: max_heat | degradation | strikes_cap | timeout_per_strike |
              timeout_at_cap | reset_on_timeout | anti_spam | webhooks

        Examples:
            g!heat set max_heat 100
            g!heat set strikes_cap 5
            g!heat set timeout_per_strike 10m
        """
        _MAP = {
            "max_heat":             (["heat", "max_heat"],              float),
            "degradation":          (["heat", "degradation_per_second"],float),
            "strikes_cap":          (["heat", "strikes_cap"],           int),
            "timeout_per_strike":   (["heat", "timeout_per_strike"],    str),
            "timeout_at_cap":       (["heat", "timeout_at_cap"],        str),
            "reset_on_timeout":     (["heat", "reset_heat_on_timeout"], lambda v: v.lower() in ("true","yes","1","on")),
            "anti_spam":            (["heat", "anti_spam"],             lambda v: v.lower() in ("true","yes","1","on")),
            "webhooks":             (["heat", "monitor_webhooks"],      lambda v: v.lower() in ("true","yes","1","on")),
        }

        if key not in _MAP:
            keys_fmt = ", ".join(f"`{k}`" for k in _MAP)
            return await ctx.send(
                embed=error_embed(f"Unknown key `{key}`.\nValid keys: {keys_fmt}")
            )

        path, converter = _MAP[key]
        try:
            converted = converter(value)
        except (ValueError, TypeError):
            return await ctx.send(
                embed=error_embed(f"Invalid value `{value}` for key `{key}`.")
            )

        self.bot.config.set(ctx.guild.id, path, converted)
        await ctx.send(
            embed=success_embed(f"Set `{key}` → `{converted}`.")
        )

    # ── unlock ─────────────────────────────────────────────────

    @heat.command(name="unlock")
    @require_permit(3)
    async def heat_unlock(self, ctx: commands.Context) -> None:
        """Remove auto-lockdown from all channels (restore @everyone Send Messages)."""
        everyone = ctx.guild.default_role
        unlocked = 0

        for channel in ctx.guild.text_channels:
            perms = channel.overwrites_for(everyone)
            if perms.send_messages is False:
                perms.send_messages = None  # Reset to inherit
                try:
                    await channel.set_permissions(
                        everyone,
                        overwrite=perms,
                        reason=f"[Heat] Manual unlock by {ctx.author}",
                    )
                    unlocked += 1
                except discord.Forbidden:
                    pass

        if unlocked:
            await ctx.send(
                embed=success_embed(f"Unlocked **{unlocked}** channel(s). Server is no longer locked down.")
            )
        else:
            await ctx.send(
                embed=info_embed("No locked channels found.")
            )

    # ── blacklist group ────────────────────────────────────────

    @heat.group(name="blacklist", aliases=["bl"], invoke_without_command=True)
    @guild_only()
    async def heat_blacklist(self, ctx: commands.Context) -> None:
        """Word blacklist management. Use subcommands."""
        await ctx.send_help(ctx.command)

    @heat_blacklist.command(name="addword", aliases=["add"])
    @require_permit(3)
    async def bl_addword(self, ctx: commands.Context, *, word: str) -> None:
        """Add a word to the server's local blacklist."""
        word = word.lower().strip()
        cfg  = self.bot.config.get(ctx.guild.id)
        words: list = cfg["heat"]["filters"]["words_blacklist"]["words"]

        if word in words:
            return await ctx.send(embed=info_embed(f"`{word}` is already in the blacklist."))

        words.append(word)
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed(f"Added `{word}` to the word blacklist."))

    @heat_blacklist.command(name="removeword", aliases=["remove", "rm"])
    @require_permit(3)
    async def bl_removeword(self, ctx: commands.Context, *, word: str) -> None:
        """Remove a word from the server's local blacklist."""
        word = word.lower().strip()
        cfg  = self.bot.config.get(ctx.guild.id)
        words: list = cfg["heat"]["filters"]["words_blacklist"]["words"]

        if word not in words:
            return await ctx.send(embed=error_embed(f"`{word}` is not in the blacklist."))

        words.remove(word)
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed(f"Removed `{word}` from the word blacklist."))

    @heat_blacklist.command(name="list")
    @require_permit(2)
    async def bl_list(self, ctx: commands.Context) -> None:
        """List all words in the server's local blacklist."""
        cfg   = self.bot.config.get(ctx.guild.id)
        words = cfg["heat"]["filters"]["words_blacklist"].get("words", [])

        if not words:
            return await ctx.send(embed=info_embed("The local word blacklist is empty."))

        pages = build_pages(
            sorted(words),
            title="🚫 Word Blacklist",
            colour=discord.Colour.red(),
            per_page=20,
            numbered=True,
        )
        await send_paginated(ctx, pages)

    @heat_blacklist.command(name="enable")
    @require_permit(3)
    async def bl_enable(self, ctx: commands.Context) -> None:
        """Enable the word blacklist filter."""
        self.bot.config.set(
            ctx.guild.id,
            ["heat", "filters", "words_blacklist", "enabled"],
            True,
        )
        await ctx.send(embed=success_embed("Word blacklist filter **enabled**."))

    @heat_blacklist.command(name="disable")
    @require_permit(3)
    async def bl_disable(self, ctx: commands.Context) -> None:
        """Disable the word blacklist filter."""
        self.bot.config.set(
            ctx.guild.id,
            ["heat", "filters", "words_blacklist", "enabled"],
            False,
        )
        await ctx.send(embed=success_embed("Word blacklist filter **disabled**."))

    @heat_blacklist.command(name="setheat")
    @require_permit(3)
    async def bl_setheat(self, ctx: commands.Context, amount: float) -> None:
        """Set the heat added when a blacklisted word is detected."""
        if amount <= 0:
            return await ctx.send(embed=error_embed("Heat amount must be greater than 0."))
        self.bot.config.set(
            ctx.guild.id,
            ["heat", "filters", "words_blacklist", "heat"],
            amount,
        )
        await ctx.send(embed=success_embed(f"Word blacklist heat set to `{amount}`."))


async def setup(bot):
    await bot.add_cog(HeatCommands(bot))