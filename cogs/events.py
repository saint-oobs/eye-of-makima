"""
Core Discord event listeners.

Handles:
- Guild join / leave  (config create/cleanup, welcome log)
- Member join / leave (triggers joingate, joinraid, verification)
- Member update       (role change tracking for antinuke)
- Bot ready           (presence update)
"""

import logging

import discord
from discord.ext import commands

log = logging.getLogger("bot.events")


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Guild join ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined guild: %s (ID: %d, members: %d)", guild.name, guild.id, guild.member_count)
        self.bot.config.create_default(guild.id)

        # Update presence with new guild count
        await self.bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.bot.guilds)} servers | {self.bot.command_prefix}help",
            )
        )

        # Try to send a welcome message to the first writable text channel
        embed = discord.Embed(
            title=f"👋 Thanks for adding {self.bot.bot_name}!",
            description=(
                f"Use `{self.bot.command_prefix}setup` to configure the bot.\n"
                f"Use `{self.bot.command_prefix}help` to see all commands.\n\n"
                f"**Quick start:** Set a log channel with "
                f"`{self.bot.command_prefix}setup logchannel #channel` "
                f"to receive security alerts."
            ),
            colour=discord.Colour.green(),
        )
        for channel in guild.text_channels:
            perms = channel.permissions_for(guild.me)
            if perms.send_messages and perms.embed_links:
                try:
                    await channel.send(embed=embed)
                except discord.HTTPException:
                    pass
                break

    # ── Guild leave ────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info("Left guild: %s (ID: %d)", guild.name, guild.id)
        # Invalidate cache but keep config on disk
        # (allows re-join to restore settings)
        self.bot.config.invalidate(guild.id)

        await self.bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.bot.guilds)} servers | {self.bot.command_prefix}help",
            )
        )

    # ── Member join ────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            # Bot addition is handled by joingate separately
            await self._dispatch_bot_join(member)
            return

        guild = member.guild
        log.debug("Member joined: %s in %s", member, guild.name)

        # Fire joingate checks
        joingate_cog = self.bot.get_cog("JoinGate")
        if joingate_cog:
            await joingate_cog.process_join(member)

        # Fire joinraid detection
        joinraid_cog = self.bot.get_cog("JoinRaid")
        if joinraid_cog:
            await joinraid_cog.process_join(member)

        # Fire verification
        verification_cog = self.bot.get_cog("Verification")
        if verification_cog:
            await verification_cog.process_join(member)

    async def _dispatch_bot_join(self, member: discord.Member) -> None:
        """Route bot additions to JoinGate for unverified/unauthorised bot checks."""
        joingate_cog = self.bot.get_cog("JoinGate")
        if joingate_cog:
            await joingate_cog.process_bot_join(member)

    # ── Member leave ───────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild = member.guild
        log.debug("Member left: %s in %s", member, guild.name)

        # Clean up quarantine record if they were quarantined
        cfg = self.bot.config.get(guild.id)
        if member.id in cfg.get("_quarantined", []):
            cfg["_quarantined"] = [
                uid for uid in cfg["_quarantined"] if uid != member.id
            ]
            cfg["_saved_roles"].pop(str(member.id), None)
            self.bot.config.save(guild.id)
            log.info(
                "Cleaned up quarantine for leaving member %s in %s",
                member, guild.name,
            )

        # Clean up pending verification
        await self.bot.db.execute(
            "DELETE FROM verification_pending WHERE guild_id = ? AND user_id = ?",
            (guild.id, member.id),
        )

    # ── Member update ──────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ) -> None:
        # Role changes — check if quarantine role was removed externally
        if before.roles == after.roles:
            return

        cfg = self.bot.config.get(after.guild.id)
        qr_id = cfg.get("quarantine_role")
        if not qr_id:
            return

        # If quarantined member had their quarantine role manually removed
        # outside the bot — clean up the quarantine state
        had_qr   = any(r.id == qr_id for r in before.roles)
        has_qr   = any(r.id == qr_id for r in after.roles)
        in_list  = after.id in cfg.get("_quarantined", [])

        if had_qr and not has_qr and in_list:
            cfg["_quarantined"] = [
                uid for uid in cfg["_quarantined"] if uid != after.id
            ]
            cfg["_saved_roles"].pop(str(after.id), None)
            self.bot.config.save(after.guild.id)
            log.info(
                "Quarantine role manually removed from %s in %s — cleaned up state",
                after, after.guild.name,
            )

    # ── Guild update ───────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_update(
        self,
        before: discord.Guild,
        after: discord.Guild,
    ) -> None:
        # Log significant guild-level changes for antinuke monitoring
        antinuke_cog = self.bot.get_cog("AntiNuke")
        if antinuke_cog:
            await antinuke_cog.on_guild_update(before, after)

    # ── Role update ────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_role_update(
        self,
        before: discord.Role,
        after: discord.Role,
    ) -> None:
        # Dangerous permission additions to roles → antinuke check
        antinuke_cog = self.bot.get_cog("AntiNukeGuards")
        if antinuke_cog:
            await antinuke_cog.on_role_update(before, after)

    # ── Message events ─────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Ignore DMs and bot messages
        if not message.guild or message.author.bot:
            return

        # Dispatch to heat filter
        heat_cog = self.bot.get_cog("HeatFilters")
        if heat_cog:
            await heat_cog.process_message(message)

    @commands.Cog.listener()
    async def on_message_edit(
        self,
        before: discord.Message,
        after: discord.Message,
    ) -> None:
        # Re-scan edited messages through heat filters
        if not after.guild or after.author.bot:
            return
        if before.content == after.content:
            return  # Only content edits matter

        heat_cog = self.bot.get_cog("HeatFilters")
        if heat_cog:
            await heat_cog.process_message(after)

    # ── Audit log ──────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_audit_log_entry_create(
        self, entry: discord.AuditLogEntry
    ) -> None:
        """
        Route audit log entries to antinuke and modlog cogs.
        Requires discord.Intents.moderation = True.
        """
        antinuke_cog = self.bot.get_cog("AntiNuke")
        if antinuke_cog:
            await antinuke_cog.process_audit_entry(entry)

        modlog_cog = self.bot.get_cog("ModLog")
        if modlog_cog:
            await modlog_cog.process_audit_entry(entry)

    # ── Voice state ────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # Placeholder — extend for voice-based heat or modlog if needed
        pass

    # ── Error passthrough ──────────────────────────────────────
    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context) -> None:
        """Log every invoked command for audit purposes."""
        log.debug(
            "CMD  guild=%s  user=%s  cmd=%s  args=%s",
            ctx.guild.id if ctx.guild else "DM",
            ctx.author.id,
            ctx.command.qualified_name if ctx.command else "?",
            ctx.message.content[:80],
        )


async def setup(bot):
    await bot.add_cog(Events(bot))