"""
Verification command group.

Command group: g!verify (alias: g!vc)

Subcommands:
    status                      — Show current verification configuration
    enable / disable            — Toggle verification system
    mode <mode>                 — Set verification mode
    setrole verified <role>     — Set the verified role
    setrole unverified <role>   — Set the unverified role (holding role)
    setchannel <channel>        — Set the verification channel
    timeout kick <on|off>       — Toggle kick-on-timeout
    fail kick <on|off>          — Toggle kick-on-fail (captcha)
    panel                       — Post/refresh the reaction verify panel
    verify <member>             — Manually verify a member (staff)
    unverify <member>           — Manually remove verified role
    pending                     — List members currently awaiting verification
    reset <member>              — Reset a member's verification state
"""

import logging

import discord
from discord.ext import commands

from utils.checks import require_permit, guild_only, premium_only
from utils.helpers import make_embed, success_embed, error_embed, info_embed
from utils.paginator import send_paginated, build_pages

log = logging.getLogger("bot.verification_commands")

_VALID_MODES = ("none", "captcha", "reaction", "web")


class VerificationCommands(commands.Cog, name="Verification"):
    def __init__(self, bot):
        self.bot = bot

    # ── Group ──────────────────────────────────────────────────

    @commands.group(name="verify", aliases=["vc"], invoke_without_command=True)
    @guild_only()
    async def verify(self, ctx: commands.Context) -> None:
        """Verification system management."""
        await ctx.send_help(ctx.command)

    # ── status ─────────────────────────────────────────────────

    @verify.command(name="status")
    @require_permit(2)
    async def vc_status(self, ctx: commands.Context) -> None:
        """Show the current verification configuration."""
        cfg = self.bot.config.get(ctx.guild.id)
        vc  = cfg.get("verification", {})

        def _fmt(val) -> str:
            if isinstance(val, bool):
                return "✅" if val else "❌"
            return f"`{val}`"

        # Role resolution
        vr_id  = vc.get("verified_role")
        ur_id  = vc.get("unverified_role")
        ch_id  = vc.get("verify_channel")

        vr  = ctx.guild.get_role(vr_id)  if vr_id else None
        ur  = ctx.guild.get_role(ur_id)  if ur_id else None
        ch  = ctx.guild.get_channel(ch_id) if ch_id else None

        mode = vc.get("mode", "none")

        # Pending count
        ver_cog = self.bot.get_cog("Verification")
        pending = ver_cog._pending.get(ctx.guild.id, {}) if ver_cog else {}

        embed = make_embed(
            title="🔐 Verification Configuration",
            colour=discord.Colour.blurple(),
            fields=[
                ("Enabled",          _fmt(vc.get("enabled", False)),              True),
                ("Mode",             f"`{mode}`",                                 True),
                ("Pending Members",  f"`{len(pending)}`",                         True),
                ("Verified Role",    vr.mention  if vr  else "*(not set)*",       True),
                ("Unverified Role",  ur.mention  if ur  else "*(not set)*",       True),
                ("Verify Channel",   ch.mention  if ch  else "*(not set)*",       True),
                ("Kick on Timeout",  _fmt(vc.get("kick_on_timeout", True)),       True),
                ("Kick on Fail",     _fmt(vc.get("kick_on_fail",    True)),       True),
                ("Web Mode",         "Premium 🔒" if mode == "web" else "—",      True),
            ],
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── enable / disable ───────────────────────────────────────

    @verify.command(name="enable")
    @require_permit(3)
    async def vc_enable(self, ctx: commands.Context) -> None:
        """Enable the verification system."""
        self.bot.config.set(ctx.guild.id, ["verification", "enabled"], True)
        await ctx.send(embed=success_embed("Verification system **enabled**."))

    @verify.command(name="disable")
    @require_permit(3)
    async def vc_disable(self, ctx: commands.Context) -> None:
        """Disable the verification system."""
        self.bot.config.set(ctx.guild.id, ["verification", "enabled"], False)
        await ctx.send(embed=success_embed("Verification system **disabled**."))

    # ── mode ───────────────────────────────────────────────────

    @verify.command(name="mode")
    @require_permit(3)
    async def vc_mode(self, ctx: commands.Context, mode: str) -> None:
        """
        Set the verification mode.

        Modes:
            none      — Auto-verify all joins
            captcha   — DM text captcha
            reaction  — React to a message in verify channel
            web       — Click a link (Premium)
        """
        mode = mode.lower()
        if mode not in _VALID_MODES:
            modes_fmt = ", ".join(f"`{m}`" for m in _VALID_MODES)
            return await ctx.send(embed=error_embed(
                f"Invalid mode `{mode}`.\nValid modes: {modes_fmt}"
            ))

        # Web mode requires premium
        if mode == "web" and not await self.bot.is_premium(ctx.guild.id):
            return await ctx.send(embed=error_embed(
                "Web verification mode requires **Premium**. "
                "Upgrade at <https://guardbot.xyz/premium>."
            ))

        self.bot.config.set(ctx.guild.id, ["verification", "mode"], mode)
        await ctx.send(embed=success_embed(
            f"Verification mode set to **{mode}**."
        ))

    # ── setrole ────────────────────────────────────────────────

    @verify.group(name="setrole", invoke_without_command=True)
    @guild_only()
    async def vc_setrole(self, ctx: commands.Context) -> None:
        """Set verified or unverified roles."""
        await ctx.send_help(ctx.command)

    @vc_setrole.command(name="verified")
    @require_permit(3)
    async def setrole_verified(
        self,
        ctx:  commands.Context,
        role: discord.Role,
    ) -> None:
        """Set the role granted on successful verification."""
        self.bot.config.set(ctx.guild.id, ["verification", "verified_role"], role.id)
        await ctx.send(embed=success_embed(
            f"Verified role set to {role.mention}."
        ))

    @vc_setrole.command(name="unverified")
    @require_permit(3)
    async def setrole_unverified(
        self,
        ctx:  commands.Context,
        role: discord.Role,
    ) -> None:
        """Set the holding role applied to unverified members on join."""
        self.bot.config.set(ctx.guild.id, ["verification", "unverified_role"], role.id)
        await ctx.send(embed=success_embed(
            f"Unverified role set to {role.mention}."
        ))

    @vc_setrole.command(name="clear")
    @require_permit(3)
    async def setrole_clear(self, ctx: commands.Context, which: str) -> None:
        """
        Clear a role assignment.
        Which: verified | unverified
        """
        which = which.lower()
        if which not in ("verified", "unverified"):
            return await ctx.send(embed=error_embed(
                "Specify `verified` or `unverified`."
            ))
        key = f"{which}_role"
        self.bot.config.set(ctx.guild.id, ["verification", key], None)
        await ctx.send(embed=success_embed(
            f"Cleared the **{which}** role."
        ))

    # ── setchannel ─────────────────────────────────────────────

    @verify.command(name="setchannel")
    @require_permit(3)
    async def vc_setchannel(
        self,
        ctx:     commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        """Set the channel used for reaction or fallback verification."""
        self.bot.config.set(
            ctx.guild.id, ["verification", "verify_channel"], channel.id
        )
        await ctx.send(embed=success_embed(
            f"Verify channel set to {channel.mention}."
        ))

    # ── timeout / fail kick ────────────────────────────────────

    @verify.group(name="timeout", invoke_without_command=True)
    @guild_only()
    async def vc_timeout(self, ctx: commands.Context) -> None:
        """Timeout settings for verification."""
        await ctx.send_help(ctx.command)

    @vc_timeout.command(name="kick")
    @require_permit(3)
    async def timeout_kick(self, ctx: commands.Context, toggle: str) -> None:
        """Toggle whether members are kicked on verification timeout."""
        val = toggle.lower() in ("on", "true", "yes", "enable", "1")
        self.bot.config.set(
            ctx.guild.id, ["verification", "kick_on_timeout"], val
        )
        await ctx.send(embed=success_embed(
            f"Kick on timeout **{'enabled' if val else 'disabled'}**."
        ))

    @verify.group(name="fail", invoke_without_command=True)
    @guild_only()
    async def vc_fail(self, ctx: commands.Context) -> None:
        """Verification failure settings."""
        await ctx.send_help(ctx.command)

    @vc_fail.command(name="kick")
    @require_permit(3)
    async def fail_kick(self, ctx: commands.Context, toggle: str) -> None:
        """Toggle whether members are kicked after too many failed captcha attempts."""
        val = toggle.lower() in ("on", "true", "yes", "enable", "1")
        self.bot.config.set(
            ctx.guild.id, ["verification", "kick_on_fail"], val
        )
        await ctx.send(embed=success_embed(
            f"Kick on fail **{'enabled' if val else 'disabled'}**."
        ))

    # ── panel ──────────────────────────────────────────────────

    @verify.command(name="panel")
    @require_permit(3)
    async def vc_panel(self, ctx: commands.Context) -> None:
        """Post or refresh the reaction verification panel in the verify channel."""
        cfg = self.bot.config.get(ctx.guild.id)
        vc  = cfg.get("verification", {})

        if vc.get("mode") != "reaction":
            return await ctx.send(embed=error_embed(
                "Panel is only used in `reaction` mode.\n"
                "Run `g!vc mode reaction` first."
            ))

        ch_id = vc.get("verify_channel")
        if not ch_id:
            return await ctx.send(embed=error_embed(
                "No verify channel set. Run `g!vc setchannel #channel` first."
            ))

        channel = ctx.guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send(embed=error_embed(
                "Verify channel is not a valid text channel."
            ))

        ver_cog = self.bot.get_cog("Verification")
        if not ver_cog:
            return await ctx.send(embed=error_embed("Verification cog not loaded."))

        # Delete old panel if exists
        old_id = ver_cog.get_verify_message_id(ctx.guild.id)
        if old_id:
            try:
                old_msg = await channel.fetch_message(old_id)
                await old_msg.delete()
            except (discord.NotFound, discord.HTTPException):
                pass

        # Create fresh panel
        embed = make_embed(
            title="✅ Verify to Access the Server",
            description=(
                "React with ✅ below to verify yourself\n"
                "and gain access to the server."
            ),
            colour=discord.Colour.blurple(),
            footer="You must react to gain access.",
        )
        try:
            msg = await channel.send(embed=embed)
            await msg.add_reaction("✅")
            ver_cog.set_verify_message_id(ctx.guild.id, msg.id)
            await ctx.send(embed=success_embed(
                f"Verification panel posted in {channel.mention}."
            ))
        except discord.HTTPException as exc:
            await ctx.send(embed=error_embed(f"Failed to post panel: `{exc}`"))

    # ── manual verify ──────────────────────────────────────────

    @verify.command(name="member", aliases=["force"])
    @require_permit(2)
    async def vc_verify_member(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> None:
        """Manually verify a member, granting the verified role immediately."""
        cfg     = self.bot.config.get(ctx.guild.id)
        ver_cog = self.bot.get_cog("Verification")

        if ver_cog:
            ver_cog.force_verify(ctx.guild.id, member.id)
            await ver_cog._apply_verified(member, cfg)
        else:
            return await ctx.send(embed=error_embed("Verification cog not loaded."))

        await ctx.send(embed=success_embed(
            f"**{member.display_name}** has been manually verified."
        ))

    # ── unverify ───────────────────────────────────────────────

    @verify.command(name="unverify")
    @require_permit(2)
    async def vc_unverify(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> None:
        """Remove the verified role from a member."""
        cfg    = self.bot.config.get(ctx.guild.id)
        vc     = cfg.get("verification", {})
        vr_id  = vc.get("verified_role")

        if not vr_id:
            return await ctx.send(embed=error_embed("No verified role configured."))

        vr = ctx.guild.get_role(vr_id)
        if not vr:
            return await ctx.send(embed=error_embed("Verified role not found."))

        if vr not in member.roles:
            return await ctx.send(embed=info_embed(
                f"**{member.display_name}** does not have the verified role."
            ))

        try:
            await member.remove_roles(
                vr, reason=f"[Verification] Manually unverified by {ctx.author}"
            )
            await ctx.send(embed=success_embed(
                f"Removed verified role from **{member.display_name}**."
            ))
        except discord.Forbidden:
            await ctx.send(embed=error_embed("Missing permissions to remove role."))

    # ── pending ────────────────────────────────────────────────

    @verify.command(name="pending")
    @require_permit(2)
    async def vc_pending(self, ctx: commands.Context) -> None:
        """List members currently awaiting verification."""
        ver_cog = self.bot.get_cog("Verification")
        if not ver_cog:
            return await ctx.send(embed=error_embed("Verification cog not loaded."))

        pending = ver_cog._pending.get(ctx.guild.id, {})
        if not pending:
            return await ctx.send(embed=info_embed(
                "No members are currently pending verification."
            ))

        import time
        now   = time.monotonic()
        lines = []
        for uid, entry in pending.items():
            m       = ctx.guild.get_member(uid)
            name    = m.display_name if m else f"ID:{uid}"
            mode    = entry.get("mode", "?")
            expires = entry.get("expires_at", 0)
            rem     = max(0, int(expires - now))
            lines.append(
                f"**{name}** (`{uid}`) — mode: `{mode}` — "
                f"expires in `{rem}s`"
            )

        pages = build_pages(
            lines,
            title=f"🔐 Pending Verification ({len(pending)})",
            colour=discord.Colour.blurple(),
            per_page=10,
            numbered=True,
        )
        await send_paginated(ctx, pages)

    # ── reset ──────────────────────────────────────────────────

    @verify.command(name="reset")
    @require_permit(3)
    async def vc_reset(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> None:
        """
        Reset a member's verification state.
        Clears pending entry and removes both verified/unverified roles.
        """
        cfg     = self.bot.config.get(ctx.guild.id)
        vc      = cfg.get("verification", {})
        ver_cog = self.bot.get_cog("Verification")

        if ver_cog:
            ver_cog.force_verify(ctx.guild.id, member.id)

        roles_to_remove = []
        for key in ("verified_role", "unverified_role"):
            rid = vc.get(key)
            if rid:
                r = ctx.guild.get_role(rid)
                if r and r in member.roles:
                    roles_to_remove.append(r)

        if roles_to_remove:
            try:
                await member.remove_roles(
                    *roles_to_remove,
                    reason=f"[Verification] State reset by {ctx.author}",
                )
            except discord.Forbidden:
                return await ctx.send(embed=error_embed(
                    "Missing permissions to remove roles."
                ))

        await ctx.send(embed=success_embed(
            f"Verification state reset for **{member.display_name}**."
        ))


async def setup(bot):
    await bot.add_cog(VerificationCommands(bot))