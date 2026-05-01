"""
Global error handler cog.

Catches all unhandled command errors and produces a clean, user-facing
embed response. Logs full tracebacks for unexpected errors.

Handled errors:
    CommandNotFound          — silently ignore
    NotInGuild               — DM-only context guard
    MissingPermit            — insufficient permit level
    NotBotOwner              — owner-only command
    MissingPermissions       — bot missing Discord permissions
    BotMissingPermissions    — bot is missing permissions
    MissingRequiredArgument  — incomplete command usage
    BadArgument              — invalid argument type/value
    CommandOnCooldown        — rate-limited
    CheckFailure             — generic check failure
    GuildNotConfigured       — feature not set up
    FeatureDisabled          — feature turned off
    PremiumRequired          — premium-only feature
    ActionFailed             — moderation action failed
    HierarchyError           — role hierarchy issue
    UserNotFound / MemberNotFound
    NoPrivateMessage         — guild-only commands
    DisabledCommand          — command turned off
    * Everything else        — unexpected, logged with traceback
"""

import logging
import traceback

import discord
from discord.ext import commands

from utils.errors import (
    ActionFailed,
    BotTargetError,
    FeatureDisabled,
    GuildNotConfigured,
    HierarchyError,
    MissingPermit,
    NotBotOwner,
    NotInGuild,
    OwnerTargetError,
    PremiumRequired,
    SelfTargetError,
)

log = logging.getLogger("bot.errors")


class ErrorHandler(commands.Cog, name="ErrorHandler"):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(
        self,
        ctx:   commands.Context,
        error: commands.CommandError,
    ) -> None:
        # Unwrap CommandInvokeError
        error = getattr(error, "original", error)

        # ── Silently ignore ────────────────────────────────────
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.DisabledCommand):
            return

        # ── Custom bot errors ──────────────────────────────────
        if isinstance(error, NotInGuild):
            return await self._send(ctx, "❌", "This command can only be used in a server.")

        if isinstance(error, NotBotOwner):
            return await self._send(ctx, "🔒", "This command is restricted to bot owners.")

        if isinstance(error, MissingPermit):
            return await self._send(
                ctx, "🔒",
                f"You need **permit level {error.required}** to use this command.\n"
                f"Your current permit level is **{error.current}**.",
            )

        if isinstance(error, PremiumRequired):
            return await self._send(
                ctx, "⭐",
                f"**{error.feature}** requires a premium subscription.",
            )

        if isinstance(error, GuildNotConfigured):
            return await self._send(
                ctx, "⚙️",
                f"This feature isn't configured yet.\n"
                f"Missing setting: `{error.key}`\n"
                f"Use `{ctx.clean_prefix}setup` to configure the bot.",
            )

        if isinstance(error, FeatureDisabled):
            return await self._send(
                ctx, "🚫",
                f"The `{error.feature}` feature is currently disabled for this server.",
            )

        if isinstance(error, HierarchyError):
            return await self._send(
                ctx, "⚠️",
                "I can't perform that action — the target's role is above mine in the hierarchy.",
            )

        if isinstance(error, ActionFailed):
            return await self._send(
                ctx, "❌",
                f"Action failed: {error.reason}",
            )

        if isinstance(error, SelfTargetError):
            return await self._send(ctx, "❌", "You cannot target yourself with this command.")

        if isinstance(error, BotTargetError):
            return await self._send(ctx, "❌", "You cannot target me with this command.")

        if isinstance(error, OwnerTargetError):
            return await self._send(ctx, "❌", "You cannot target the server owner with this command.")

        # ── discord.py built-in errors ─────────────────────────
        if isinstance(error, commands.MissingRequiredArgument):
            return await self._send(
                ctx, "❓",
                f"Missing required argument: `{error.param.name}`\n"
                f"Usage: `{ctx.clean_prefix}{ctx.command.qualified_name} "
                f"{ctx.command.signature}`",
            )

        if isinstance(error, commands.BadArgument):
            return await self._send(
                ctx, "❓",
                f"Invalid argument: {error}\n"
                f"Usage: `{ctx.clean_prefix}{ctx.command.qualified_name} "
                f"{ctx.command.signature}`",
            )

        if isinstance(error, commands.TooManyArguments):
            return await self._send(
                ctx, "❓",
                f"Too many arguments.\n"
                f"Usage: `{ctx.clean_prefix}{ctx.command.qualified_name} "
                f"{ctx.command.signature}`",
            )

        if isinstance(error, commands.MissingPermissions):
            perms = ", ".join(
                p.replace("_", " ").title()
                for p in error.missing_permissions
            )
            return await self._send(
                ctx, "🔒",
                f"You are missing the following permissions: **{perms}**",
            )

        if isinstance(error, commands.BotMissingPermissions):
            perms = ", ".join(
                p.replace("_", " ").title()
                for p in error.missing_permissions
            )
            return await self._send(
                ctx, "⚠️",
                f"I am missing the following permissions: **{perms}**",
            )

        if isinstance(error, commands.CommandOnCooldown):
            return await self._send(
                ctx, "⏳",
                f"This command is on cooldown. Try again in "
                f"**{error.retry_after:.1f}s**.",
            )

        if isinstance(error, commands.NoPrivateMessage):
            return await self._send(ctx, "❌", "This command cannot be used in DMs.")

        if isinstance(error, (commands.UserNotFound, commands.MemberNotFound)):
            return await self._send(ctx, "❓", f"Could not find that user: `{error.argument}`")

        if isinstance(error, commands.ChannelNotFound):
            return await self._send(ctx, "❓", f"Could not find that channel: `{error.argument}`")

        if isinstance(error, commands.RoleNotFound):
            return await self._send(ctx, "❓", f"Could not find that role: `{error.argument}`")

        if isinstance(error, commands.CheckFailure):
            return await self._send(ctx, "🔒", "You don't have permission to use this command.")

        # ── Unexpected / unhandled ─────────────────────────────
        log.error(
            "Unhandled command error in %s by %s:\n%s",
            ctx.command,
            ctx.author,
            "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        )

        await self._send(
            ctx, "💥",
            "An unexpected error occurred. This has been logged.\n"
            f"If this keeps happening, please contact a bot administrator.",
        )

    # ── Helper ─────────────────────────────────────────────────

    async def _send(
        self,
        ctx:   commands.Context,
        icon:  str,
        text:  str,
    ) -> None:
        embed = discord.Embed(
            description=f"{icon} {text}",
            colour=discord.Colour.red(),
        )
        try:
            await ctx.send(embed=embed, delete_after=15)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(ErrorHandler(bot))