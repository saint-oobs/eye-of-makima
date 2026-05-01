"""
Anti-Nuke command group.

Command group: g!antinuke (alias: g!an)

Subcommands:
    status              — Show current antinuke configuration
    enable / disable    — Toggle antinuke system
    set <key> <value>   — Update a limit or setting
    limits              — Show all minute/hour action limits
    setlimit <window> <action> <value>  — Set a specific action limit
    panic               — Panic mode subgroup
    logs [member]       — Show recent antinuke action log for a member
    clear <member>      — Clear a member's action counter
"""

import logging

import discord
from discord.ext import commands

from utils.checks import require_permit, guild_only
from utils.helpers import (
    make_embed, success_embed, error_embed, info_embed
)
from utils.paginator import send_paginated, build_pages

log = logging.getLogger("bot.antinuke_commands")

_VALID_ACTIONS = (
    "ban", "kick", "channel_delete", "channel_create",
    "role_delete", "role_create", "webhook_create", "webhook_delete",
)


class AntiNukeCommands(commands.Cog, name="AntiNuke"):
    def __init__(self, bot):
        self.bot = bot

    # ── Group ──────────────────────────────────────────────────

    @commands.group(name="antinuke", aliases=["an"], invoke_without_command=True)
    @guild_only()
    async def antinuke(self, ctx: commands.Context) -> None:
        """Anti-nuke system management."""
        await ctx.send_help(ctx.command)

    # ── status ─────────────────────────────────────────────────

    @antinuke.command(name="status")
    @require_permit(2)
    async def an_status(self, ctx: commands.Context) -> None:
        """Show the current anti-nuke configuration."""
        cfg    = self.bot.config.get(ctx.guild.id)
        an_cfg = cfg.get("antinuke", {})

        def _fmt(val) -> str:
            if isinstance(val, bool):
                return "✅" if val else "❌"
            return f"`{val}`"

        panic_cfg  = an_cfg.get("panic_mode", {})
        panic_cog  = self.bot.get_cog("AntiNukePanic")
        panic_live = panic_cog.is_panic_active(ctx.guild.id) if panic_cog else False

        embed = make_embed(
            title="🛡️ Anti-Nuke Status",
            colour=discord.Colour.blurple(),
            fields=[
                ("Enabled",             _fmt(an_cfg.get("enabled", True)),                        True),
                ("Quarantine Hold",     _fmt(an_cfg.get("quarantine_hold", True)),                True),
                ("Monitor Perms",       _fmt(an_cfg.get("monitor_dangerous_role_perms", True)),   True),
                ("Prune Detection",     _fmt(an_cfg.get("prune_detection", False)),               True),
                ("Backups (Premium)",   _fmt(an_cfg.get("backups", {}).get("enabled", False)),    True),
                ("Panic Mode",          _fmt(panic_cfg.get("enabled", False)),                    True),
                ("Panic Active Now",    "🔴 YES" if panic_live else "🟢 No",                      True),
                ("Panic Threshold",     f"`{panic_cfg.get('raiders_to_trigger', 3)} nukers`",     True),
                ("Panic Duration",      f"`{panic_cfg.get('duration_minutes', 10)}m`",            True),
            ],
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── enable / disable ───────────────────────────────────────

    @antinuke.command(name="enable")
    @require_permit(4)
    async def an_enable(self, ctx: commands.Context) -> None:
        """Enable the anti-nuke system."""
        self.bot.config.set(ctx.guild.id, ["antinuke", "enabled"], True)
        await ctx.send(embed=success_embed("Anti-nuke system **enabled**."))

    @antinuke.command(name="disable")
    @require_permit(4)
    async def an_disable(self, ctx: commands.Context) -> None:
        """Disable the anti-nuke system."""
        self.bot.config.set(ctx.guild.id, ["antinuke", "enabled"], False)
        await ctx.send(embed=success_embed("Anti-nuke system **disabled**."))

    # ── set ────────────────────────────────────────────────────

    @antinuke.command(name="set")
    @require_permit(4)
    async def an_set(
        self,
        ctx:   commands.Context,
        key:   str,
        value: str,
    ) -> None:
        """
        Update an anti-nuke setting.

        Keys: quarantine | monitor_perms | prune_detection

        Examples:
            g!an set quarantine true
            g!an set prune_detection true
            g!an set monitor_perms false
        """
        _MAP = {
            "quarantine":      (["antinuke", "quarantine_hold"],              lambda v: v.lower() in ("true","yes","1","on")),
            "monitor_perms":   (["antinuke", "monitor_dangerous_role_perms"], lambda v: v.lower() in ("true","yes","1","on")),
            "prune_detection": (["antinuke", "prune_detection"],              lambda v: v.lower() in ("true","yes","1","on")),
        }
        if key not in _MAP:
            keys_fmt = ", ".join(f"`{k}`" for k in _MAP)
            return await ctx.send(embed=error_embed(
                f"Unknown key `{key}`.\nValid keys: {keys_fmt}"
            ))
        path, converter = _MAP[key]
        self.bot.config.set(ctx.guild.id, path, converter(value))
        await ctx.send(embed=success_embed(f"Set `{key}` → `{converter(value)}`."))

    # ── limits ─────────────────────────────────────────────────

    @antinuke.command(name="limits")
    @require_permit(2)
    async def an_limits(self, ctx: commands.Context) -> None:
        """Show all per-minute and per-hour action limits."""
        cfg    = self.bot.config.get(ctx.guild.id)
        an_cfg = cfg.get("antinuke", {})
        min_l  = an_cfg.get("minute_limit", {})
        hr_l   = an_cfg.get("hour_limit", {})

        lines = []
        for action in _VALID_ACTIONS:
            ml = min_l.get(action, "—")
            hl = hr_l.get(action, "—")
            lines.append(
                f"`{action:<18}` minute: `{ml:<4}` hour: `{hl}`"
            )

        embed = make_embed(
            title="⏱️ Anti-Nuke Action Limits",
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
            footer="Use g!an setlimit to change values.",
        )
        await ctx.send(embed=embed)

    # ── setlimit ───────────────────────────────────────────────

    @antinuke.command(name="setlimit")
    @require_permit(4)
    async def an_setlimit(
        self,
        ctx:    commands.Context,
        window: str,
        action: str,
        value:  int,
    ) -> None:
        """
        Set an action limit for a specific window.

        Windows: minute | hour
        Actions: ban | kick | channel_delete | channel_create |
                 role_delete | role_create | webhook_create | webhook_delete

        Example:
            g!an setlimit minute ban 2
            g!an setlimit hour kick 10
        """
        window = window.lower()
        action = action.lower()

        if window not in ("minute", "hour"):
            return await ctx.send(embed=error_embed(
                "Window must be `minute` or `hour`."
            ))
        if action not in _VALID_ACTIONS:
            actions_fmt = ", ".join(f"`{a}`" for a in _VALID_ACTIONS)
            return await ctx.send(embed=error_embed(
                f"Unknown action `{action}`.\nValid actions: {actions_fmt}"
            ))
        if value < 1:
            return await ctx.send(embed=error_embed("Limit must be at least `1`."))

        key = "minute_limit" if window == "minute" else "hour_limit"
        self.bot.config.set(ctx.guild.id, ["antinuke", key, action], value)
        await ctx.send(embed=success_embed(
            f"Set `{window}` limit for `{action}` → `{value}`."
        ))

    # ── logs ───────────────────────────────────────────────────

    @antinuke.command(name="logs")
    @require_permit(2)
    async def an_logs(
        self,
        ctx:    commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        """Show recent anti-nuke action log entries for a member (or all)."""
        if member:
            rows = await self.bot.db.fetchall(
                """
                SELECT action_type, performed_at
                FROM antinuke_actions
                WHERE guild_id = ? AND user_id = ?
                ORDER BY performed_at DESC
                LIMIT 50
                """,
                (ctx.guild.id, member.id),
            )
            title = f"🛡️ AntiNuke Log — {member.display_name}"
        else:
            rows = await self.bot.db.fetchall(
                """
                SELECT user_id, action_type, performed_at
                FROM antinuke_actions
                WHERE guild_id = ?
                ORDER BY performed_at DESC
                LIMIT 100
                """,
                (ctx.guild.id,),
            )
            title = "🛡️ AntiNuke Log — All"

        if not rows:
            return await ctx.send(embed=info_embed("No anti-nuke actions recorded."))

        lines = []
        for row in rows:
            ts = str(row["performed_at"])[:19]
            if member:
                lines.append(f"`{ts}` — `{row['action_type']}`")
            else:
                uid  = row["user_id"]
                m    = ctx.guild.get_member(uid)
                name = m.display_name if m else f"ID:{uid}"
                lines.append(f"`{ts}` — **{name}** — `{row['action_type']}`")

        pages = build_pages(
            lines,
            title=title,
            colour=discord.Colour.blurple(),
            per_page=15,
            numbered=False,
        )
        await send_paginated(ctx, pages)

    # ── clear ──────────────────────────────────────────────────

    @antinuke.command(name="clear")
    @require_permit(4)
    async def an_clear(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> None:
        """Clear the in-memory action counter for a member."""
        an_cog = self.bot.get_cog("AntiNuke")
        if an_cog:
            an_cog.clear_user_log(ctx.guild.id, member.id)
        await ctx.send(embed=success_embed(
            f"Cleared action counter for **{member.display_name}**."
        ))

    # ── panic group ────────────────────────────────────────────

    @antinuke.group(name="panic", invoke_without_command=True)
    @guild_only()
    async def an_panic(self, ctx: commands.Context) -> None:
        """Panic mode management."""
        await ctx.send_help(ctx.command)

    @an_panic.command(name="enable")
    @require_permit(4)
    async def panic_enable(self, ctx: commands.Context) -> None:
        """Enable panic mode for anti-nuke."""
        self.bot.config.set(ctx.guild.id, ["antinuke", "panic_mode", "enabled"], True)
        await ctx.send(embed=success_embed("Anti-nuke **panic mode** enabled."))

    @an_panic.command(name="disable")
    @require_permit(4)
    async def panic_disable(self, ctx: commands.Context) -> None:
        """Disable panic mode for anti-nuke."""
        self.bot.config.set(ctx.guild.id, ["antinuke", "panic_mode", "enabled"], False)
        await ctx.send(embed=success_embed("Anti-nuke **panic mode** disabled."))

    @an_panic.command(name="set")
    @require_permit(4)
    async def panic_set(
        self,
        ctx:   commands.Context,
        key:   str,
        value: str,
    ) -> None:
        """
        Update a panic mode setting.

        Keys: threshold | duration | lockdown | unlock

        Examples:
            g!an panic set threshold 5
            g!an panic set duration 15
            g!an panic set lockdown true
            g!an panic set unlock true
        """
        _MAP = {
            "threshold": (["antinuke", "panic_mode", "raiders_to_trigger"],  int),
            "duration":  (["antinuke", "panic_mode", "duration_minutes"],    int),
            "lockdown":  (["antinuke", "panic_mode", "lockdown_on_trigger"], lambda v: v.lower() in ("true","yes","1","on")),
            "unlock":    (["antinuke", "panic_mode", "unlock_on_end"],       lambda v: v.lower() in ("true","yes","1","on")),
        }
        if key not in _MAP:
            keys_fmt = ", ".join(f"`{k}`" for k in _MAP)
            return await ctx.send(embed=error_embed(
                f"Unknown key `{key}`.\nValid keys: {keys_fmt}"
            ))
        path, converter = _MAP[key]
        try:
            converted = converter(value)
        except (ValueError, TypeError):
            return await ctx.send(embed=error_embed(
                f"Invalid value `{value}` for key `{key}`."
            ))
        self.bot.config.set(ctx.guild.id, path, converted)
        await ctx.send(embed=success_embed(f"Set panic `{key}` → `{converted}`."))

    @an_panic.command(name="stop")
    @require_permit(4)
    async def panic_stop(self, ctx: commands.Context) -> None:
        """Manually deactivate panic mode if currently active."""
        panic_cog = self.bot.get_cog("AntiNukePanic")
        if not panic_cog or not panic_cog.is_panic_active(ctx.guild.id):
            return await ctx.send(embed=info_embed("Panic mode is not currently active."))
        panic_cog.manual_deactivate(ctx.guild.id)
        cfg = self.bot.config.get(ctx.guild.id)
        cfg["antinuke"]["panic_mode"]["active"] = False
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed("Panic mode manually **deactivated**."))

    @an_panic.command(name="addrole")
    @require_permit(4)
    async def panic_addrole(
        self,
        ctx:  commands.Context,
        role: discord.Role,
    ) -> None:
        """Add a role to be warned when panic mode activates."""
        cfg        = self.bot.config.get(ctx.guild.id)
        warn_roles = cfg["antinuke"]["panic_mode"].setdefault("warned_roles", [])
        if role.id in warn_roles:
            return await ctx.send(embed=info_embed(f"{role.mention} is already in the warn list."))
        warn_roles.append(role.id)
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed(f"Added {role.mention} to panic warn roles."))

    @an_panic.command(name="removerole")
    @require_permit(4)
    async def panic_removerole(
        self,
        ctx:  commands.Context,
        role: discord.Role,
    ) -> None:
        """Remove a role from the panic mode warn list."""
        cfg        = self.bot.config.get(ctx.guild.id)
        warn_roles = cfg["antinuke"]["panic_mode"].get("warned_roles", [])
        if role.id not in warn_roles:
            return await ctx.send(embed=error_embed(f"{role.mention} is not in the warn list."))
        warn_roles.remove(role.id)
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed(f"Removed {role.mention} from panic warn roles."))


async def setup(bot):
    await bot.add_cog(AntiNukeCommands(bot))