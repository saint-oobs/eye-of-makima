"""
Server setup and configuration wizard.

Allows server administrators (permit 4+) to configure all bot features
interactively. Settings are persisted to the database via bot.config.

Commands:
    setup                  — Interactive setup wizard overview
    setup prefix <pfx>     — Change command prefix
    setup logs <channel>   — Set mod-log channel
    setup muterole <role>  — Set the mute role
    setup joinrole <role>  — Set the auto-join role
    setup modrole <role>   — Set moderator role (permit 3)
    setup adminrole <role> — Set admin role (permit 4)
    setup appealbans       — Toggle ban appeal DMs
    setup view             — Show current config
    setup reset            — Reset all config to defaults
"""

import logging

import discord
from discord.ext import commands

from utils.checks  import require_permit, guild_only
from utils.embeds  import make_embed, ok, fail, info
from utils.views   import ConfirmView, DangerConfirmView

log = logging.getLogger("bot.setup")

_BOOL_MAP = {
    "on": True, "yes": True, "true": True,  "1": True,
    "off": False, "no": False, "false": False, "0": False,
}


class Setup(commands.Cog, name="Setup"):
    """Server configuration and setup wizard."""

    def __init__(self, bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════════
    # Root command
    # ══════════════════════════════════════════════════════════

    @commands.group(
        name="setup",
        invoke_without_command=True,
        aliases=["config", "configure"],
    )
    @guild_only()
    @require_permit(4)
    async def setup_cmd(self, ctx: commands.Context) -> None:
        """Display the setup wizard overview and current config summary."""
        cfg    = await self.bot.config.get(ctx.guild.id)
        prefix = cfg.get("prefix", self.bot.default_prefix)

        def ch(key: str) -> str:
            cid = cfg.get(key)
            return f"<#{cid}>" if cid else "*(not set)*"

        def role(key: str) -> str:
            rid = cfg.get(key)
            return f"<@&{rid}>" if rid else "*(not set)*"

        embed = make_embed(
            title="⚙️ Setup Wizard",
            description=(
                f"Use the subcommands below to configure each feature.\n"
                f"Run `{ctx.clean_prefix}setup view` to see all current settings.\n\n"
                f"**Quick Status:**"
            ),
            colour=discord.Colour.blurple(),
            fields=[
                ("Prefix",       f"`{prefix}`",          True),
                ("Mod Logs",     ch("log_channel"),       True),
                ("Mute Role",    role("mute_role"),       True),
                ("Join Role",    role("join_role"),       True),
                ("Mod Role",     role("mod_role"),        True),
                ("Admin Role",   role("admin_role"),      True),
            ],
            footer=f"Run {ctx.clean_prefix}setup <subcommand> to change a setting.",
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ══════════════════════════════════════════════════════════
    # Subcommands
    # ══════════════════════════════════════════════════════════

    @setup_cmd.command(name="prefix")
    async def setup_prefix(
        self,
        ctx:    commands.Context,
        prefix: str,
    ) -> None:
        """Change the command prefix for this server."""
        if len(prefix) > 5:
            return await ctx.send(embed=fail("Prefix must be 5 characters or fewer."))
        await self.bot.config.set(ctx.guild.id, "prefix", prefix)
        await ctx.send(embed=ok(
            f"Prefix updated to `{prefix}`.\n"
            f"Commands are now invoked with `{prefix}help`."
        ))

    @setup_cmd.command(name="logs", aliases=["logchannel", "modlogs"])
    async def setup_logs(
        self,
        ctx:     commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        """Set the channel where moderation logs are posted."""
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return await ctx.send(embed=fail(
                f"I need **Send Messages** and **Embed Links** in {channel.mention}."
            ))
        await self.bot.config.set(ctx.guild.id, "log_channel", channel.id)
        await ctx.send(embed=ok(f"Mod logs will be posted in {channel.mention}."))

    @setup_cmd.command(name="muterole")
    async def setup_mute_role(
        self,
        ctx:  commands.Context,
        role: discord.Role,
    ) -> None:
        """Set the role applied when a member is muted."""
        if role.managed:
            return await ctx.send(embed=fail("Cannot use a bot-managed role as mute role."))
        if role >= ctx.guild.me.top_role:
            return await ctx.send(embed=fail(
                "That role is above my highest role — I can't assign it."
            ))
        await self.bot.config.set(ctx.guild.id, "mute_role", role.id)
        await ctx.send(embed=ok(f"Mute role set to {role.mention}."))

    @setup_cmd.command(name="joinrole", aliases=["autorole"])
    async def setup_join_role(
        self,
        ctx:  commands.Context,
        role: discord.Role | None = None,
    ) -> None:
        """
        Set the role automatically assigned to new members.
        Run without arguments to disable auto-role.
        """
        if role is None:
            await self.bot.config.set(ctx.guild.id, "join_role", None)
            return await ctx.send(embed=ok("Auto-join role disabled."))

        if role.managed:
            return await ctx.send(embed=fail("Cannot use a bot-managed role as join role."))
        if role >= ctx.guild.me.top_role:
            return await ctx.send(embed=fail(
                "That role is above my highest role — I can't assign it."
            ))
        await self.bot.config.set(ctx.guild.id, "join_role", role.id)
        await ctx.send(embed=ok(f"New members will automatically receive {role.mention}."))

    @setup_cmd.command(name="modrole")
    async def setup_mod_role(
        self,
        ctx:  commands.Context,
        role: discord.Role,
    ) -> None:
        """Set the Moderator role (grants permit level 3)."""
        await self.bot.config.set(ctx.guild.id, "mod_role", role.id)
        await ctx.send(embed=ok(
            f"{role.mention} is now the **Moderator** role (permit 3)."
        ))

    @setup_cmd.command(name="adminrole")
    async def setup_admin_role(
        self,
        ctx:  commands.Context,
        role: discord.Role,
    ) -> None:
        """Set the Admin role (grants permit level 4)."""
        await self.bot.config.set(ctx.guild.id, "admin_role", role.id)
        await ctx.send(embed=ok(
            f"{role.mention} is now the **Admin** role (permit 4)."
        ))

    @setup_cmd.command(name="appealbans", aliases=["dmappeal"])
    async def setup_appeal_bans(
        self,
        ctx:   commands.Context,
        value: str = "toggle",
    ) -> None:
        """Toggle whether banned users receive a DM with appeal info."""
        cfg     = await self.bot.config.get(ctx.guild.id)
        current = cfg.get("appeal_bans", False)

        if value.lower() == "toggle":
            new_val = not current
        else:
            new_val = _BOOL_MAP.get(value.lower())
            if new_val is None:
                return await ctx.send(embed=fail(
                    "Use `on`, `off`, or `toggle`."
                ))

        await self.bot.config.set(ctx.guild.id, "appeal_bans", new_val)
        state = "**enabled**" if new_val else "**disabled**"
        await ctx.send(embed=ok(f"Ban appeal DMs are now {state}."))

    # ── View ───────────────────────────────────────────────────

    @setup_cmd.command(name="view", aliases=["show", "current"])
    async def setup_view(self, ctx: commands.Context) -> None:
        """Display all current configuration settings."""
        cfg = await self.bot.config.get(ctx.guild.id)

        def ch(key: str) -> str:
            cid = cfg.get(key)
            return f"<#{cid}>" if cid else "*(not set)*"

        def role(key: str) -> str:
            rid = cfg.get(key)
            return f"<@&{rid}>" if rid else "*(not set)*"

        def b(key: str, default: bool = False) -> str:
            return "✅ Enabled" if cfg.get(key, default) else "❌ Disabled"

        prefix = cfg.get("prefix", self.bot.default_prefix)

        embed = make_embed(
            title=f"⚙️ Config — {ctx.guild.name}",
            colour=discord.Colour.blurple(),
            fields=[
                # Core
                ("Prefix",             f"`{prefix}`",               True),
                ("Mod Log Channel",    ch("log_channel"),            True),
                ("\u200b",             "\u200b",                     True),  # spacer
                # Roles
                ("Mute Role",          role("mute_role"),            True),
                ("Join Role",          role("join_role"),            True),
                ("Mod Role",           role("mod_role"),             True),
                ("Admin Role",         role("admin_role"),           True),
                ("\u200b",             "\u200b",                     True),
                ("\u200b",             "\u200b",                     True),
                # Toggles
                ("Appeal Ban DMs",     b("appeal_bans"),             True),
                ("Automod",            b("automod_enabled", True),   True),
                ("Verification",       b("verify_enabled"),          True),
                ("Logging",            b("logging_enabled", True),   True),
                ("Welcome Messages",   b("welcome_enabled"),         True),
                ("Anti-Nuke",          b("antinuke_enabled"),        True),
            ],
            footer=f"Guild ID: {ctx.guild.id}",
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── Reset ──────────────────────────────────────────────────

    @setup_cmd.command(name="reset")
    async def setup_reset(self, ctx: commands.Context) -> None:
        """Reset ALL configuration for this server to defaults."""
        view = DangerConfirmView(ctx.author, timeout=30.0)
        msg  = await ctx.send(
            embed=make_embed(
                description=(
                    "⚠️ **This will wipe ALL bot configuration for this server.**\n"
                    "All settings — prefix, roles, channels, feature toggles — "
                    "will be reset to their defaults.\n\n"
                    "Are you absolutely sure?"
                ),
                colour=discord.Colour.red(),
            ),
            view=view,
        )
        view.message = msg
        await view.wait()

        if not view.confirmed:
            return await ctx.send(embed=info("Reset cancelled."))

        await self.bot.config.delete(ctx.guild.id)
        await ctx.send(embed=ok(
            "All configuration has been reset to defaults.\n"
            f"Run `{ctx.clean_prefix}setup` to reconfigure."
        ))


async def setup(bot):
    await bot.add_cog(Setup(bot))