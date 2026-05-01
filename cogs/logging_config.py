"""
Logging system configuration commands.

Command group: g!logconfig (alias: g!lc)

Subcommands:
    status                          — Show current logging configuration
    channel <category> <#channel>  — Set a per-category log channel
    clearchannel <category>         — Clear a per-category log channel
    global <#channel>               — Set the global fallback log channel
    clearglobal                     — Clear the global log channel
    disable <category>              — Disable logging for a category
    enable <category>               — Enable logging for a category
    categories                      — List all log categories and their state

Categories:
    members   — join/leave/ban/unban/update events
    messages  — delete/edit/bulk-delete events
    channels  — channel create/delete/update events
    roles     — role create/delete/update events
    server    — server update/invite events
    voice     — voice state change events
    all       — shorthand for all categories
"""

import logging

import discord
from discord.ext import commands

from utils.checks import require_permit, guild_only
from utils.helpers import make_embed, success_embed, error_embed, info_embed

log = logging.getLogger("bot.logging_config")

_CATEGORIES = ("members", "messages", "channels", "roles", "server", "voice")
_CAT_LABELS  = {
    "members":  "👥 Members",
    "messages": "💬 Messages",
    "channels": "📁 Channels",
    "roles":    "🎭 Roles",
    "server":   "🏠 Server",
    "voice":    "🔊 Voice",
}
_CAT_KEY = {cat: f"log_{cat}_channel" for cat in _CATEGORIES}


class LoggingConfig(commands.Cog, name="LoggingConfig"):
    def __init__(self, bot):
        self.bot = bot

    # ── Group ──────────────────────────────────────────────────

    @commands.group(name="logconfig", aliases=["lc"], invoke_without_command=True)
    @guild_only()
    async def logconfig(self, ctx: commands.Context) -> None:
        """Configure the server event logging system."""
        await ctx.send_help(ctx.command)

    # ── status ─────────────────────────────────────────────────

    @logconfig.command(name="status")
    @require_permit(2)
    async def lc_status(self, ctx: commands.Context) -> None:
        """Show the current logging configuration."""
        cfg      = self.bot.config.get(ctx.guild.id)
        global_ch_id = cfg.get("log_channel")
        global_ch    = ctx.guild.get_channel(global_ch_ch_id := global_ch_id) \
                        if global_ch_id else None
        disabled = cfg.get("log_disabled_categories", [])

        fields = [
            (
                "Global Fallback",
                global_ch.mention if global_ch else "*(not set)*",
                False,
            )
        ]

        for cat in _CATEGORIES:
            key   = _CAT_KEY[cat]
            ch_id = cfg.get(key)
            ch    = ctx.guild.get_channel(ch_id) if ch_id else None
            state = "❌ disabled" if cat in disabled else (
                ch.mention if ch else "*(uses global)*"
            )
            fields.append((_CAT_LABELS[cat], state, True))

        embed = make_embed(
            title="📋 Logging Configuration",
            colour=discord.Colour.blurple(),
            fields=fields,
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── global channel ─────────────────────────────────────────

    @logconfig.command(name="global")
    @require_permit(4)
    async def lc_global(
        self,
        ctx:     commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        """Set the global fallback log channel for all categories."""
        if not await self._check_perms(ctx, channel):
            return
        self.bot.config.set(ctx.guild.id, ["log_channel"], channel.id)
        await ctx.send(embed=success_embed(
            f"Global log channel set to {channel.mention}.\n"
            f"All categories without a specific channel will log here."
        ))

    @logconfig.command(name="clearglobal")
    @require_permit(4)
    async def lc_clearglobal(self, ctx: commands.Context) -> None:
        """Clear the global fallback log channel."""
        self.bot.config.set(ctx.guild.id, ["log_channel"], None)
        await ctx.send(embed=success_embed("Global log channel cleared."))

    # ── per-category channel ───────────────────────────────────

    @logconfig.command(name="channel")
    @require_permit(4)
    async def lc_channel(
        self,
        ctx:      commands.Context,
        category: str,
        channel:  discord.TextChannel,
    ) -> None:
        """
        Set a dedicated log channel for a specific category.

        Categories: members | messages | channels | roles | server | voice

        Example:
            g!lc channel messages #message-logs
            g!lc channel members  #member-logs
        """
        category = category.lower()
        if category not in _CATEGORIES:
            return await ctx.send(embed=error_embed(
                f"Unknown category `{category}`.\n"
                f"Valid: {', '.join(f'`{c}`' for c in _CATEGORIES)}"
            ))
        if not await self._check_perms(ctx, channel):
            return

        key = _CAT_KEY[category]
        self.bot.config.set(ctx.guild.id, [key], channel.id)
        await ctx.send(embed=success_embed(
            f"{_CAT_LABELS[category]} log channel set to {channel.mention}."
        ))

    @logconfig.command(name="clearchannel")
    @require_permit(4)
    async def lc_clearchannel(
        self,
        ctx:      commands.Context,
        category: str,
    ) -> None:
        """Clear the per-category log channel (falls back to global)."""
        category = category.lower()
        if category not in _CATEGORIES:
            return await ctx.send(embed=error_embed(
                f"Unknown category `{category}`.\n"
                f"Valid: {', '.join(f'`{c}`' for c in _CATEGORIES)}"
            ))

        key = _CAT_KEY[category]
        self.bot.config.set(ctx.guild.id, [key], None)
        await ctx.send(embed=success_embed(
            f"Cleared {_CAT_LABELS[category]} log channel — will fall back to global."
        ))

    # ── enable / disable ───────────────────────────────────────

    @logconfig.command(name="disable")
    @require_permit(3)
    async def lc_disable(
        self,
        ctx:      commands.Context,
        category: str,
    ) -> None:
        """
        Disable logging for a category.

        Use `all` to disable all categories.

        Example:
            g!lc disable voice
            g!lc disable all
        """
        to_disable = await self._resolve_categories(ctx, category)
        if to_disable is None:
            return

        cfg      = self.bot.config.get(ctx.guild.id)
        disabled = cfg.setdefault("log_disabled_categories", [])

        added = []
        for cat in to_disable:
            if cat not in disabled:
                disabled.append(cat)
                added.append(cat)

        self.bot.config.save(ctx.guild.id)

        if not added:
            return await ctx.send(embed=info_embed(
                "All specified categories were already disabled."
            ))

        labels = ", ".join(f"`{c}`" for c in added)
        await ctx.send(embed=success_embed(
            f"Disabled logging for: {labels}."
        ))

    @logconfig.command(name="enable")
    @require_permit(3)
    async def lc_enable(
        self,
        ctx:      commands.Context,
        category: str,
    ) -> None:
        """
        Re-enable logging for a previously disabled category.

        Use `all` to enable all categories.
        """
        to_enable = await self._resolve_categories(ctx, category)
        if to_enable is None:
            return

        cfg      = self.bot.config.get(ctx.guild.id)
        disabled = cfg.get("log_disabled_categories", [])

        removed = []
        for cat in to_enable:
            if cat in disabled:
                disabled.remove(cat)
                removed.append(cat)

        self.bot.config.save(ctx.guild.id)

        if not removed:
            return await ctx.send(embed=info_embed(
                "All specified categories were already enabled."
            ))

        labels = ", ".join(f"`{c}`" for c in removed)
        await ctx.send(embed=success_embed(
            f"Enabled logging for: {labels}."
        ))

    # ── categories ─────────────────────────────────────────────

    @logconfig.command(name="categories")
    @require_permit(2)
    async def lc_categories(self, ctx: commands.Context) -> None:
        """List all log categories and their current state."""
        cfg      = self.bot.config.get(ctx.guild.id)
        disabled = cfg.get("log_disabled_categories", [])

        lines = []
        for cat in _CATEGORIES:
            key      = _CAT_KEY[cat]
            ch_id    = cfg.get(key)
            ch       = ctx.guild.get_channel(ch_id) if ch_id else None
            state    = "❌" if cat in disabled else "✅"
            ch_str   = ch.mention if ch else "*(global)*"
            lines.append(f"{state} **{cat}** — {ch_str}")

        embed = make_embed(
            title="📋 Log Categories",
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
            footer="✅ enabled  ❌ disabled",
        )
        await ctx.send(embed=embed)

    # ── quicksetup ─────────────────────────────────────────────

    @logconfig.command(name="quicksetup")
    @require_permit(4)
    async def lc_quicksetup(
        self,
        ctx:     commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        """
        Quick-configure all log categories to a single channel.

        Sets both the global log channel and all per-category channels
        to the specified channel, and enables all categories.
        """
        if not await self._check_perms(ctx, channel):
            return

        cfg = self.bot.config.get(ctx.guild.id)
        cfg["log_channel"] = channel.id
        for key in _CAT_KEY.values():
            cfg[key] = channel.id
        cfg["log_disabled_categories"] = []
        self.bot.config.save(ctx.guild.id)

        await ctx.send(embed=success_embed(
            f"All log categories routed to {channel.mention}.\n"
            f"All categories enabled."
        ))

    @logconfig.command(name="reset")
    @require_permit(4)
    async def lc_reset(self, ctx: commands.Context) -> None:
        """Reset all logging configuration for this server."""
        cfg = self.bot.config.get(ctx.guild.id)
        cfg["log_channel"] = None
        cfg["log_disabled_categories"] = []
        for key in _CAT_KEY.values():
            cfg[key] = None
        self.bot.config.save(ctx.guild.id)

        await ctx.send(embed=success_embed(
            "Logging configuration reset. No events will be logged until "
            "you configure a channel with `g!lc global #channel`."
        ))

    # ══════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════

    async def _check_perms(
        self,
        ctx:     commands.Context,
        channel: discord.TextChannel,
    ) -> bool:
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.send_messages and perms.embed_links):
            await ctx.send(embed=error_embed(
                f"I need **Send Messages** and **Embed Links** in {channel.mention}."
            ))
            return False
        return True

    async def _resolve_categories(
        self,
        ctx:      commands.Context,
        category: str,
    ) -> list[str] | None:
        category = category.lower()
        if category == "all":
            return list(_CATEGORIES)
        if category not in _CATEGORIES:
            await ctx.send(embed=error_embed(
                f"Unknown category `{category}`.\n"
                f"Valid: {', '.join(f'`{c}`' for c in _CATEGORIES)} or `all`."
            ))
            return None
        return [category]


async def setup(bot):
    await bot.add_cog(LoggingConfig(bot))