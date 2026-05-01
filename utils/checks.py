"""
Reusable command checks used across all cogs.

Usage:
    from utils.checks import is_bot_owner, require_permit, guild_only

    @is_bot_owner()
    async def owner_command(self, ctx): ...

    @require_permit(3)
    async def trusted_admin_command(self, ctx): ...
"""

import discord
from discord.ext import commands

from utils.helpers import get_permit_level


# ── Bot owner ──────────────────────────────────────────────────

def is_bot_owner():
    """
    Passes only for users listed in OWNER_IDS (set on Bot via owner_ids).
    Uses discord.py's built-in is_owner() which checks bot.owner_ids.
    """
    async def predicate(ctx: commands.Context) -> bool:
        if not await ctx.bot.is_owner(ctx.author):
            await ctx.send(
                "🔒 This command is restricted to the **bot owner**.",
                delete_after=8,
            )
            return False
        return True
    return commands.check(predicate)


# ── Guild-only ─────────────────────────────────────────────────

def guild_only():
    """
    Fails in DMs. Sends a friendly message instead of raising silently.
    (discord.py has @commands.guild_only() but it raises NoPrivateMessage
    with no user-facing message — this version sends one.)
    """
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild:
            await ctx.send("❌ This command can only be used inside a server.")
            return False
        return True
    return commands.check(predicate)


# ── Permit levels ──────────────────────────────────────────────

def require_permit(level: int):
    """
    Require a minimum permit level (0–5).

    Levels:
        0 — Any member
        1 — Has a main role
        2 — Server admin (Administrator or Manage Guild perm)
        3 — Trusted admin (listed in cfg["trusted_admins"])
        4 — Extra owner (listed in cfg["extra_owners"])
        5 — Guild owner

    Sends an informative error message on failure.
    Automatically enforces guild_only (permit levels are meaningless in DMs).
    """
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild:
            await ctx.send("❌ This command can only be used inside a server.")
            return False
        cfg = ctx.bot.config.get(ctx.guild.id)
        permit = get_permit_level(ctx.author, cfg)
        if permit < level:
            _LEVEL_NAMES = {
                1: "Main Role",
                2: "Server Admin",
                3: "Trusted Admin",
                4: "Extra Owner",
                5: "Guild Owner",
            }
            required_name = _LEVEL_NAMES.get(level, f"Level {level}")
            await ctx.send(
                f"🔒 You need **{required_name}** permission or higher to use this command.",
                delete_after=8,
            )
            return False
        return True
    return commands.check(predicate)


# ── Bot has required permissions ───────────────────────────────

def bot_has_permissions(**perms):
    """
    Check that the bot has all specified permissions in the current channel.
    More descriptive than discord.py's built-in (lists exactly what's missing).

    Usage:
        @bot_has_permissions(ban_members=True, manage_roles=True)
        async def ban(self, ctx, member): ...
    """
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild:
            return True  # DMs — no guild permissions to check
        bot_member = ctx.guild.me
        channel_perms = ctx.channel.permissions_for(bot_member)
        missing = [
            perm for perm, required in perms.items()
            if required and not getattr(channel_perms, perm, False)
        ]
        if missing:
            readable = ", ".join(f"`{p.replace('_', ' ').title()}`" for p in missing)
            await ctx.send(
                f"❌ I'm missing the following permissions to run this command: {readable}",
                delete_after=10,
            )
            return False
        return True
    return commands.check(predicate)


# ── Feature enabled ────────────────────────────────────────────

def feature_enabled(*key_path: str):
    """
    Check that a nested config key evaluates to True.

    Usage:
        @feature_enabled("heat", "enabled")
        async def heat_status(self, ctx): ...

        @feature_enabled("antinuke", "enabled")
        async def nuke_stats(self, ctx): ...
    """
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild:
            return True
        cfg = ctx.bot.config.get(ctx.guild.id)
        node = cfg
        for key in key_path:
            if not isinstance(node, dict):
                break
            node = node.get(key)
        if not node:
            feature_name = " › ".join(key_path)
            await ctx.send(
                f"⚠️ **{feature_name}** is currently disabled. "
                f"Enable it first via the setup commands.",
                delete_after=8,
            )
            return False
        return True
    return commands.check(predicate)


# ── Premium ────────────────────────────────────────────────────

def premium_only():
    """
    Restricts the command to Premium guilds.
    Sends an upgrade prompt on failure.
    """
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild:
            return True
        if not await ctx.bot.is_premium(ctx.guild.id):
            await ctx.send(
                "⭐ This feature is available on **Premium** servers only.\n"
                "Contact the bot owner to upgrade.",
                delete_after=12,
            )
            return False
        return True
    return commands.check(predicate)


# ── Not quarantined ────────────────────────────────────────────

def not_quarantined():
    """
    Prevents quarantined members from using bot commands.
    """
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild:
            return True
        cfg = ctx.bot.config.get(ctx.guild.id)
        if ctx.author.id in cfg.get("_quarantined", []):
            await ctx.send(
                "🔒 You are currently quarantined and cannot use bot commands.",
                delete_after=8,
            )
            return False
        return True
    return commands.check(predicate)


# ── Cooldown helpers ───────────────────────────────────────────

def per_guild_cooldown(rate: int, per: float):
    """
    Apply a per-guild (not per-user) cooldown bucket.

    Usage:
        @per_guild_cooldown(rate=1, per=5.0)
        async def status(self, ctx): ...
    """
    return commands.cooldown(rate, per, commands.BucketType.guild)


def per_user_cooldown(rate: int, per: float):
    """
    Apply a per-user cooldown bucket.

    Usage:
        @per_user_cooldown(rate=3, per=10.0)
        async def warn(self, ctx, member): ...
    """
    return commands.cooldown(rate, per, commands.BucketType.user)


# ── Compound convenience decorators ───────────────────────────

def mod_command():
    """
    Shorthand for commands that require at minimum Server Admin (permit 2).
    Stacks: guild_only + require_permit(2).
    """
    def decorator(func):
        func = guild_only()(func)
        func = require_permit(2)(func)
        return func
    return decorator


def trusted_admin_command():
    """
    Shorthand for commands that require Trusted Admin (permit 3).
    Stacks: guild_only + require_permit(3).
    """
    def decorator(func):
        func = guild_only()(func)
        func = require_permit(3)(func)
        return func
    return decorator


def owner_command():
    """
    Shorthand for commands that require Extra Owner (permit 4).
    Stacks: guild_only + require_permit(4).
    """
    def decorator(func):
        func = guild_only()(func)
        func = require_permit(4)(func)
        return func
    return decorator