"""
Quarantine system — manual and automated member isolation.

Responsibilities:
- Quarantine role setup and validation
- Manual quarantine / unquarantine commands
- Saved-roles restore on unquarantine
- Quarantine log persistence and lookup
- Auto-quarantine integration (called by AntiNuke, JoinGate)
- Quarantine channel management (read-only holding area)
"""

import logging

import discord
from discord.ext import commands

from utils.checks import require_permit, guild_only
from utils.helpers import make_embed, success_embed, error_embed, info_embed
from utils.paginator import send_paginated, build_pages

log = logging.getLogger("bot.quarantine")


class Quarantine(commands.Cog, name="Quarantine"):
    def __init__(self, bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════

    async def quarantine_member(
        self,
        guild:      discord.Guild,
        member:     discord.Member,
        reason:     str,
        moderator:  discord.Member | discord.ClientUser | None = None,
        *,
        save_roles: bool = True,
    ) -> bool:
        """
        Apply the quarantine role to a member, stripping all other roles.

        Returns True on success, False if quarantine role is not configured
        or permissions are insufficient.
        """
        cfg    = self.bot.config.get(guild.id)
        qr_id  = cfg.get("quarantine_role")
        if not qr_id:
            log.warning("quarantine_member called but no quarantine_role set in guild %d", guild.id)
            return False

        qr = guild.get_role(qr_id)
        if not qr:
            log.warning("Quarantine role %d not found in guild %d", qr_id, guild.id)
            return False

        # Save current roles before stripping
        if save_roles:
            saved = [r.id for r in member.roles if not r.is_default()]
            cfg.setdefault("_saved_roles", {})[str(member.id)] = saved

        try:
            await member.edit(
                roles=[qr],
                reason=f"[Quarantine] {reason}"[:512],
            )
        except discord.Forbidden:
            log.warning("Cannot quarantine %s — missing permissions", member)
            return False
        except discord.HTTPException as exc:
            log.error("Quarantine role edit failed for %s: %s", member, exc)
            return False

        # Track quarantined members
        q_list = cfg.setdefault("_quarantined", [])
        if member.id not in q_list:
            q_list.append(member.id)

        self.bot.config.save(guild.id)

        # Persist to DB
        mod_id = moderator.id if moderator else None
        await self._persist_quarantine(guild.id, member.id, mod_id, reason)

        await self._log_quarantine_event(
            guild, member, moderator,
            action="quarantine", reason=reason,
        )

        # DM the member
        cfg_misc = cfg.get("misc", {})
        if cfg_misc.get("dm_targets", True):
            await self._dm_member(
                member,
                title=f"🔒 You have been quarantined in {guild.name}",
                description=reason,
            )

        log.info("Quarantined %s in %s | %s", member, guild.name, reason)
        return True

    async def unquarantine_member(
        self,
        guild:       discord.Guild,
        member:      discord.Member,
        reason:      str,
        moderator:   discord.Member | discord.ClientUser | None = None,
        *,
        restore_roles: bool = True,
    ) -> bool:
        """
        Remove the quarantine role and optionally restore saved roles.

        Returns True on success.
        """
        cfg   = self.bot.config.get(guild.id)
        qr_id = cfg.get("quarantine_role")
        qr    = guild.get_role(qr_id) if qr_id else None

        roles_to_set: list[discord.Role] = []

        if restore_roles:
            saved_ids = cfg.get("_saved_roles", {}).pop(str(member.id), [])
            for rid in saved_ids:
                r = guild.get_role(rid)
                if r and r != qr:
                    roles_to_set.append(r)

        # Keep any roles the member currently has that aren't the quarantine role
        for role in member.roles:
            if role.is_default():
                continue
            if role == qr:
                continue
            if role not in roles_to_set:
                roles_to_set.append(role)

        try:
            await member.edit(
                roles=roles_to_set,
                reason=f"[Unquarantine] {reason}"[:512],
            )
        except discord.Forbidden:
            log.warning("Cannot unquarantine %s — missing permissions", member)
            return False
        except discord.HTTPException as exc:
            log.error("Unquarantine role edit failed for %s: %s", member, exc)
            return False

        # Remove from tracked list
        q_list = cfg.get("_quarantined", [])
        if member.id in q_list:
            q_list.remove(member.id)

        self.bot.config.save(guild.id)

        # Update DB record
        await self.bot.db.execute(
            """
            UPDATE quarantine_records
            SET released_at = CURRENT_TIMESTAMP, released_by = ?
            WHERE guild_id = ? AND user_id = ? AND released_at IS NULL
            """,
            (moderator.id if moderator else None, guild.id, member.id),
        )

        await self._log_quarantine_event(
            guild, member, moderator,
            action="unquarantine", reason=reason,
        )

        cfg_misc = cfg.get("misc", {})
        if cfg_misc.get("dm_targets", True):
            await self._dm_member(
                member,
                title=f"🔓 You have been released from quarantine in {guild.name}",
                description=reason,
            )

        log.info("Unquarantined %s in %s | %s", member, guild.name, reason)
        return True

    # ══════════════════════════════════════════════════════════
    # Commands
    # ══════════════════════════════════════════════════════════

    # ── quarantine ─────────────────────────────────────────────

    @commands.command(name="quarantine", aliases=["quar"])
    @guild_only()
    @require_permit(2)
    async def quarantine_cmd(
        self,
        ctx:    commands.Context,
        member: discord.Member,
        *,
        reason: str = "No reason provided.",
    ) -> None:
        """Quarantine a member — strip all roles and apply the quarantine role."""
        cfg = self.bot.config.get(ctx.guild.id)
        if not cfg.get("quarantine_role"):
            return await ctx.send(embed=error_embed(
                "No quarantine role configured.\n"
                "Run `g!qsetup role @role` to set one."
            ))

        if member.id == ctx.author.id:
            return await ctx.send(embed=error_embed("You cannot quarantine yourself."))
        if member.id == ctx.guild.owner_id:
            return await ctx.send(embed=error_embed("You cannot quarantine the server owner."))
        if member.id == self.bot.user.id:
            return await ctx.send(embed=error_embed("You cannot quarantine me."))

        qr_id = cfg.get("quarantine_role")
        qr    = ctx.guild.get_role(qr_id)
        if qr and qr in member.roles:
            return await ctx.send(embed=info_embed(
                f"**{member.display_name}** is already quarantined."
            ))

        success = await self.quarantine_member(
            ctx.guild, member, reason, ctx.author
        )
        if success:
            await ctx.send(embed=success_embed(
                f"**{member.display_name}** has been quarantined."
            ))
        else:
            await ctx.send(embed=error_embed(
                "Failed to quarantine — check my role permissions."
            ))

    # ── unquarantine ───────────────────────────────────────────

    @commands.command(name="unquarantine", aliases=["unquar", "release"])
    @guild_only()
    @require_permit(2)
    async def unquarantine_cmd(
        self,
        ctx:    commands.Context,
        member: discord.Member,
        *,
        reason: str = "No reason provided.",
    ) -> None:
        """Release a member from quarantine, restoring their previous roles."""
        cfg   = self.bot.config.get(ctx.guild.id)
        qr_id = cfg.get("quarantine_role")
        qr    = ctx.guild.get_role(qr_id) if qr_id else None

        if not qr or qr not in member.roles:
            return await ctx.send(embed=info_embed(
                f"**{member.display_name}** is not currently quarantined."
            ))

        success = await self.unquarantine_member(
            ctx.guild, member, reason, ctx.author
        )
        if success:
            await ctx.send(embed=success_embed(
                f"**{member.display_name}** has been released from quarantine."
            ))
        else:
            await ctx.send(embed=error_embed(
                "Failed to unquarantine — check my role permissions."
            ))

    # ── quarantine setup group ─────────────────────────────────

    @commands.group(name="qsetup", invoke_without_command=True)
    @guild_only()
    async def qsetup(self, ctx: commands.Context) -> None:
        """Quarantine system setup."""
        await ctx.send_help(ctx.command)

    @qsetup.command(name="role")
    @require_permit(4)
    async def qsetup_role(
        self,
        ctx:  commands.Context,
        role: discord.Role,
    ) -> None:
        """Set the quarantine role."""
        if role.managed:
            return await ctx.send(embed=error_embed(
                "Cannot use a managed/bot role as quarantine role."
            ))
        if role >= ctx.guild.me.top_role:
            return await ctx.send(embed=error_embed(
                "The quarantine role must be below my highest role."
            ))

        self.bot.config.set(ctx.guild.id, ["quarantine_role"], role.id)
        await ctx.send(embed=success_embed(
            f"Quarantine role set to {role.mention}."
        ))

    @qsetup.command(name="create")
    @require_permit(4)
    async def qsetup_create(self, ctx: commands.Context) -> None:
        """
        Auto-create a quarantine role and lock all channels.

        This will:
        1. Create a `Quarantined` role with no permissions
        2. Set it as the quarantine role
        3. Add channel overrides to deny Send Messages + Read History
           in all text channels
        """
        async with ctx.typing():
            try:
                qr = await ctx.guild.create_role(
                    name="Quarantined",
                    colour=discord.Colour.dark_gray(),
                    reason=f"[QuarantineSetup] Created by {ctx.author}",
                )
            except discord.Forbidden:
                return await ctx.send(embed=error_embed(
                    "I don't have permission to create roles."
                ))

            self.bot.config.set(ctx.guild.id, ["quarantine_role"], qr.id)

            failed_channels = 0
            for channel in ctx.guild.text_channels:
                try:
                    await channel.set_permissions(
                        qr,
                        send_messages=False,
                        read_message_history=False,
                        add_reactions=False,
                        reason="[QuarantineSetup] Deny quarantined members",
                    )
                except discord.Forbidden:
                    failed_channels += 1

            msg = f"Created quarantine role {qr.mention} and applied channel overrides."
            if failed_channels:
                msg += f"\n⚠️ Could not set overrides in `{failed_channels}` channel(s) — missing permissions."

            await ctx.send(embed=success_embed(msg))

    @qsetup.command(name="channel")
    @require_permit(4)
    async def qsetup_channel(
        self,
        ctx:     commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        """
        Set a dedicated quarantine channel where quarantined members can read
        but not interact with the rest of the server.
        """
        cfg   = self.bot.config.get(ctx.guild.id)
        qr_id = cfg.get("quarantine_role")
        qr    = ctx.guild.get_role(qr_id) if qr_id else None

        if not qr:
            return await ctx.send(embed=error_embed(
                "Set a quarantine role first with `g!qsetup role @role`."
            ))

        try:
            await channel.set_permissions(
                qr,
                read_messages=True,
                send_messages=True,
                read_message_history=True,
                reason="[QuarantineSetup] Allow quarantined members to read/write here",
            )
        except discord.Forbidden:
            return await ctx.send(embed=error_embed(
                f"I don't have permission to edit {channel.mention}."
            ))

        self.bot.config.set(ctx.guild.id, ["quarantine_channel"], channel.id)
        await ctx.send(embed=success_embed(
            f"Quarantine holding channel set to {channel.mention}.\n"
            f"Quarantined members can read and write there."
        ))

    @qsetup.command(name="status")
    @require_permit(2)
    async def qsetup_status(self, ctx: commands.Context) -> None:
        """Show the current quarantine configuration."""
        cfg   = self.bot.config.get(ctx.guild.id)
        qr_id = cfg.get("quarantine_role")
        qc_id = cfg.get("quarantine_channel")
        qr    = ctx.guild.get_role(qr_id)    if qr_id else None
        qc    = ctx.guild.get_channel(qc_id) if qc_id else None

        q_list  = cfg.get("_quarantined", [])
        members = [
            ctx.guild.get_member(uid) for uid in q_list
            if ctx.guild.get_member(uid)
        ]

        embed = make_embed(
            title="🔒 Quarantine Configuration",
            colour=discord.Colour.orange(),
            fields=[
                ("Quarantine Role",    qr.mention if qr else "*(not set)*",  True),
                ("Holding Channel",    qc.mention if qc else "*(not set)*",  True),
                ("Currently Quarantined",
                 f"`{len(members)}`" + (
                     "\n" + "\n".join(m.mention for m in members[:10])
                     + ("\n*(+ more)*" if len(members) > 10 else "")
                     if members else ""
                 ),
                 False),
            ],
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── list / logs ────────────────────────────────────────────

    @commands.command(name="quarantined")
    @guild_only()
    @require_permit(2)
    async def quarantined_list(self, ctx: commands.Context) -> None:
        """List all currently quarantined members."""
        cfg    = self.bot.config.get(ctx.guild.id)
        q_list = cfg.get("_quarantined", [])

        active = []
        for uid in q_list:
            m = ctx.guild.get_member(uid)
            if m:
                active.append(m)

        if not active:
            return await ctx.send(embed=info_embed(
                "No members are currently quarantined."
            ))

        lines = [f"**{m.display_name}** (`{m.id}`)" for m in active]
        pages = build_pages(
            lines,
            title=f"🔒 Quarantined Members ({len(active)})",
            colour=discord.Colour.orange(),
            per_page=15,
            numbered=True,
        )
        await send_paginated(ctx, pages)

    @commands.command(name="qlogs")
    @guild_only()
    @require_permit(2)
    async def qlogs(
        self,
        ctx:    commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        """Show quarantine log entries, optionally filtered by member."""
        if member:
            rows = await self.bot.db.fetchall(
                """
                SELECT quarantined_by, reason, quarantined_at, released_at, released_by
                FROM quarantine_records
                WHERE guild_id = ? AND user_id = ?
                ORDER BY quarantined_at DESC
                LIMIT 50
                """,
                (ctx.guild.id, member.id),
            )
            title = f"🔒 Quarantine Log — {member.display_name}"
        else:
            rows = await self.bot.db.fetchall(
                """
                SELECT user_id, quarantined_by, reason, quarantined_at, released_at
                FROM quarantine_records
                WHERE guild_id = ?
                ORDER BY quarantined_at DESC
                LIMIT 100
                """,
                (ctx.guild.id,),
            )
            title = "🔒 Quarantine Log — All"

        if not rows:
            return await ctx.send(embed=info_embed("No quarantine records found."))

        lines = []
        for row in rows:
            ts      = str(row["quarantined_at"])[:16]
            uid     = row.get("user_id", member.id if member else 0)
            m       = ctx.guild.get_member(uid)
            name    = m.display_name if m else f"ID:{uid}"
            reason  = (row["reason"] or "*(none)*")[:50]
            rel     = "✅" if row.get("released_at") else "🔒"
            lines.append(
                f"`{ts}` {rel} **{name}** — {reason}"
            )

        pages = build_pages(
            lines,
            title=title,
            colour=discord.Colour.orange(),
            per_page=10,
            numbered=False,
        )
        await send_paginated(ctx, pages)

    # ══════════════════════════════════════════════════════════
    # Internals
    # ══════════════════════════════════════════════════════════

    async def _persist_quarantine(
        self,
        guild_id:     int,
        user_id:      int,
        moderator_id: int | None,
        reason:       str,
    ) -> None:
        try:
            await self.bot.db.execute(
                """
                INSERT OR REPLACE INTO quarantine_records
                    (guild_id, user_id, quarantined_by, reason)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, user_id, moderator_id, reason),
            )
        except Exception as exc:
            log.error("Failed to persist quarantine record: %s", exc)

    async def _log_quarantine_event(
        self,
        guild:     discord.Guild,
        member:    discord.Member,
        moderator: discord.Member | discord.ClientUser | None,
        action:    str,
        reason:    str,
    ) -> None:
        cfg     = self.bot.config.get(guild.id)
        ch_id   = cfg.get("log_channel")
        if not ch_id:
            return
        channel = guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        colour = discord.Colour.orange() if action == "quarantine" \
            else discord.Colour.green()
        icon   = "🔒" if action == "quarantine" else "🔓"

        embed = make_embed(
            title=f"{icon} {action.title()}",
            description=f"{member.mention} (`{member.id}`)",
            colour=colour,
            fields=[
                ("Moderator", str(moderator) if moderator else "*(system)*", True),
                ("Reason",    reason,                                         False),
            ],
            timestamp=True,
        )
        embed.set_thumbnail(url=(member.avatar or member.default_avatar).url)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error("Quarantine log failed: %s", exc)

    async def _dm_member(
        self,
        member:      discord.Member,
        title:       str,
        description: str,
    ) -> None:
        embed = make_embed(
            title=title,
            description=description,
            colour=discord.Colour.orange(),
        )
        try:
            await member.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot):
    await bot.add_cog(Quarantine(bot))