"""
Moderation core — strike system and manual mod actions.

Responsibilities:
- Strike (warn) system with configurable thresholds + auto-punishments
- Manual commands: warn, mute, unmute, kick, ban, unban, softban
- Case management: case lookup, edit reason, delete case
- Strike history per user
- DM notifications to actioned members
- Full audit log to log_channel
"""

import asyncio
import datetime
import logging

import discord
from discord.ext import commands

from utils.checks import require_permit, guild_only
from utils.helpers import (
    make_embed, success_embed, error_embed, info_embed,
    parse_duration, format_duration,
)
from utils.paginator import send_paginated, build_pages

log = logging.getLogger("bot.moderation")

# ── Strike threshold actions ───────────────────────────────────
_THRESHOLD_ACTIONS = ("mute", "kick", "ban", "none")


class ModerationCore(commands.Cog, name="Moderation"):
    def __init__(self, bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════════
    # Public API — used by AutoMod, HeatMap, etc.
    # ══════════════════════════════════════════════════════════

    async def add_strike(
        self,
        guild:     discord.Guild,
        member:    discord.Member,
        reason:    str,
        moderator: discord.Member | discord.ClientUser,
        *,
        silent:    bool = False,
    ) -> int:
        """
        Add one strike to a member. Returns new total strike count.
        Triggers threshold punishments if applicable.
        """
        case_id = await self._create_case(
            guild, member, moderator,
            action="warn", reason=reason,
        )

        # Count active strikes
        total = await self._count_strikes(guild.id, member.id)

        if not silent:
            await self._dm_member(
                member,
                title=f"⚠️ Warning in {guild.name}",
                description=reason,
                colour=discord.Colour.yellow(),
                extras={"Strike": f"`#{total}`"},
            )

        await self._log_action(
            guild, member, moderator,
            action="warn", reason=reason,
            case_id=case_id, extras={"Strike Count": f"`{total}`"},
        )

        # Auto-punishment thresholds
        cfg        = self.bot.config.get(guild.id)
        thresholds = cfg.get("moderation", {}).get("strike_thresholds", {})

        for str_count, punishment in thresholds.items():
            if total == int(str_count):
                await self._apply_threshold_punishment(
                    guild, member, int(str_count), punishment
                )
                break

        return total

    # ══════════════════════════════════════════════════════════
    # Commands
    # ══════════════════════════════════════════════════════════

    # ── warn ───────────────────────────────────────────────────

    @commands.command(name="warn")
    @guild_only()
    @require_permit(2)
    async def warn(
        self,
        ctx:    commands.Context,
        member: discord.Member,
        *,
        reason: str = "No reason provided.",
    ) -> None:
        """Warn a member, adding a strike to their record."""
        if not await self._target_check(ctx, member):
            return

        total = await self.add_strike(
            ctx.guild, member, reason, ctx.author, silent=False
        )
        await ctx.send(embed=success_embed(
            f"**{member.display_name}** warned. "
            f"They now have **{total}** strike(s)."
        ))

    # ── mute ───────────────────────────────────────────────────

    @commands.command(name="mute", aliases=["timeout"])
    @guild_only()
    @require_permit(2)
    async def mute(
        self,
        ctx:      commands.Context,
        member:   discord.Member,
        duration: str = "10m",
        *,
        reason:   str = "No reason provided.",
    ) -> None:
        """
        Timeout a member for a duration.

        Duration format: 1m | 1h | 1d | 1w
        Max: 28 days (Discord limit)
        """
        if not await self._target_check(ctx, member):
            return

        delta = parse_duration(duration)
        if delta is None:
            return await ctx.send(embed=error_embed(
                "Invalid duration. Examples: `10m`, `2h`, `1d`, `7d`"
            ))
        if delta.total_seconds() > 60 * 60 * 24 * 28:
            return await ctx.send(embed=error_embed(
                "Maximum timeout duration is **28 days**."
            ))

        try:
            await member.timeout(
                discord.utils.utcnow() + delta,
                reason=f"[Mute by {ctx.author}] {reason}"[:512],
            )
        except discord.Forbidden:
            return await ctx.send(embed=error_embed(
                "I don't have permission to timeout this member."
            ))

        case_id = await self._create_case(
            ctx.guild, member, ctx.author,
            action="mute", reason=reason,
            duration=int(delta.total_seconds()),
        )

        await self._dm_member(
            member,
            title=f"🔇 Muted in {ctx.guild.name}",
            description=reason,
            colour=discord.Colour.orange(),
            extras={"Duration": format_duration(delta)},
        )
        await self._log_action(
            ctx.guild, member, ctx.author,
            action="mute", reason=reason,
            case_id=case_id,
            extras={"Duration": format_duration(delta)},
        )
        await ctx.send(embed=success_embed(
            f"**{member.display_name}** muted for **{format_duration(delta)}**."
        ))

    # ── unmute ─────────────────────────────────────────────────

    @commands.command(name="unmute", aliases=["untimeout"])
    @guild_only()
    @require_permit(2)
    async def unmute(
        self,
        ctx:    commands.Context,
        member: discord.Member,
        *,
        reason: str = "No reason provided.",
    ) -> None:
        """Remove a timeout from a member."""
        if not member.is_timed_out():
            return await ctx.send(embed=info_embed(
                f"**{member.display_name}** is not currently timed out."
            ))
        try:
            await member.timeout(None, reason=f"[Unmute by {ctx.author}] {reason}"[:512])
        except discord.Forbidden:
            return await ctx.send(embed=error_embed(
                "I don't have permission to remove this timeout."
            ))

        case_id = await self._create_case(
            ctx.guild, member, ctx.author,
            action="unmute", reason=reason,
        )
        await self._log_action(
            ctx.guild, member, ctx.author,
            action="unmute", reason=reason, case_id=case_id,
        )
        await ctx.send(embed=success_embed(
            f"**{member.display_name}**'s timeout removed."
        ))

    # ── kick ───────────────────────────────────────────────────

    @commands.command(name="kick")
    @guild_only()
    @require_permit(2)
    async def kick(
        self,
        ctx:    commands.Context,
        member: discord.Member,
        *,
        reason: str = "No reason provided.",
    ) -> None:
        """Kick a member from the server."""
        if not await self._target_check(ctx, member):
            return

        await self._dm_member(
            member,
            title=f"👢 Kicked from {ctx.guild.name}",
            description=reason,
            colour=discord.Colour.orange(),
        )
        try:
            await member.kick(reason=f"[Kick by {ctx.author}] {reason}"[:512])
        except discord.Forbidden:
            return await ctx.send(embed=error_embed(
                "I don't have permission to kick this member."
            ))

        case_id = await self._create_case(
            ctx.guild, member, ctx.author,
            action="kick", reason=reason,
        )
        await self._log_action(
            ctx.guild, member, ctx.author,
            action="kick", reason=reason, case_id=case_id,
        )
        await ctx.send(embed=success_embed(
            f"**{member.display_name}** has been kicked."
        ))

    # ── ban ────────────────────────────────────────────────────

    @commands.command(name="ban")
    @guild_only()
    @require_permit(3)
    async def ban(
        self,
        ctx:          commands.Context,
        user:         discord.User | discord.Member,
        delete_days:  int = 0,
        *,
        reason:       str = "No reason provided.",
    ) -> None:
        """
        Ban a user from the server.

        delete_days: Number of days of messages to delete (0–7)
        Supports banning users not in the server (hackban) by ID.
        """
        if isinstance(user, discord.Member):
            if not await self._target_check(ctx, user):
                return
            await self._dm_member(
                user,
                title=f"🔨 Banned from {ctx.guild.name}",
                description=reason,
                colour=discord.Colour.red(),
            )

        delete_days = max(0, min(7, delete_days))
        try:
            await ctx.guild.ban(
                user,
                reason=f"[Ban by {ctx.author}] {reason}"[:512],
                delete_message_days=delete_days,
            )
        except discord.Forbidden:
            return await ctx.send(embed=error_embed(
                "I don't have permission to ban this user."
            ))

        case_id = await self._create_case(
            ctx.guild, user, ctx.author,
            action="ban", reason=reason,
        )
        await self._log_action(
            ctx.guild, user, ctx.author,
            action="ban", reason=reason, case_id=case_id,
        )
        await ctx.send(embed=success_embed(
            f"**{user}** has been banned."
        ))

    # ── unban ──────────────────────────────────────────────────

    @commands.command(name="unban")
    @guild_only()
    @require_permit(3)
    async def unban(
        self,
        ctx:    commands.Context,
        user:   str,
        *,
        reason: str = "No reason provided.",
    ) -> None:
        """
        Unban a user by ID or Username#Discriminator.

        Example: g!unban 123456789012345678
        """
        target = await self._resolve_banned_user(ctx, user)
        if not target:
            return await ctx.send(embed=error_embed(
                f"Could not find banned user matching `{user}`."
            ))

        try:
            await ctx.guild.unban(
                target,
                reason=f"[Unban by {ctx.author}] {reason}"[:512],
            )
        except discord.Forbidden:
            return await ctx.send(embed=error_embed(
                "I don't have permission to unban this user."
            ))
        except discord.NotFound:
            return await ctx.send(embed=error_embed(
                "This user is not banned."
            ))

        case_id = await self._create_case(
            ctx.guild, target, ctx.author,
            action="unban", reason=reason,
        )
        await self._log_action(
            ctx.guild, target, ctx.author,
            action="unban", reason=reason, case_id=case_id,
        )
        await ctx.send(embed=success_embed(
            f"**{target}** has been unbanned."
        ))

    # ── softban ────────────────────────────────────────────────

    @commands.command(name="softban")
    @guild_only()
    @require_permit(2)
    async def softban(
        self,
        ctx:    commands.Context,
        member: discord.Member,
        *,
        reason: str = "No reason provided.",
    ) -> None:
        """
        Softban a member — ban then immediately unban to purge messages.
        Deletes 7 days of messages.
        """
        if not await self._target_check(ctx, member):
            return

        await self._dm_member(
            member,
            title=f"👢 Softbanned from {ctx.guild.name}",
            description=reason,
            colour=discord.Colour.orange(),
        )
        try:
            await ctx.guild.ban(
                member,
                reason=f"[Softban by {ctx.author}] {reason}"[:512],
                delete_message_days=7,
            )
            await ctx.guild.unban(
                member,
                reason="Softban — immediate unban",
            )
        except discord.Forbidden:
            return await ctx.send(embed=error_embed(
                "I don't have permission to softban this member."
            ))

        case_id = await self._create_case(
            ctx.guild, member, ctx.author,
            action="softban", reason=reason,
        )
        await self._log_action(
            ctx.guild, member, ctx.author,
            action="softban", reason=reason, case_id=case_id,
        )
        await ctx.send(embed=success_embed(
            f"**{member.display_name}** has been softbanned."
        ))

    # ── strikes / history ──────────────────────────────────────

    @commands.command(name="strikes", aliases=["warnings", "history"])
    @guild_only()
    @require_permit(1)
    async def strikes(
        self,
        ctx:    commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        """Show the strike history for a member (default: yourself)."""
        target = member or ctx.author

        rows = await self.bot.db.fetchall(
            """
            SELECT case_id, action, reason, moderator_id, created_at
            FROM mod_cases
            WHERE guild_id = ? AND user_id = ? AND action = 'warn'
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (ctx.guild.id, target.id),
        )

        active = await self._count_strikes(ctx.guild.id, target.id)

        if not rows:
            return await ctx.send(embed=info_embed(
                f"**{target.display_name}** has no strikes on record."
            ))

        lines = []
        for row in rows:
            ts  = str(row["created_at"])[:16]
            mod = ctx.guild.get_member(row["moderator_id"])
            mod_name = mod.display_name if mod else f"ID:{row['moderator_id']}"
            lines.append(
                f"`#{row['case_id']}` `{ts}` — {row['reason'][:60]} "
                f"*(by {mod_name})*"
            )

        pages = build_pages(
            lines,
            title=f"⚠️ Strikes — {target.display_name} ({active} active)",
            colour=discord.Colour.yellow(),
            per_page=10,
            numbered=False,
        )
        await send_paginated(ctx, pages)

    @commands.command(name="clearstrikes", aliases=["clearwarns"])
    @guild_only()
    @require_permit(3)
    async def clearstrikes(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> None:
        """Clear all active strikes for a member."""
        await self.bot.db.execute(
            """
            UPDATE mod_cases
            SET active = 0
            WHERE guild_id = ? AND user_id = ? AND action = 'warn' AND active = 1
            """,
            (ctx.guild.id, member.id),
        )
        await ctx.send(embed=success_embed(
            f"Cleared all strikes for **{member.display_name}**."
        ))

    # ── case ───────────────────────────────────────────────────

    @commands.command(name="case")
    @guild_only()
    @require_permit(2)
    async def case(self, ctx: commands.Context, case_id: int) -> None:
        """Look up a specific moderation case by ID."""
        row = await self.bot.db.fetchone(
            """
            SELECT * FROM mod_cases
            WHERE guild_id = ? AND case_id = ?
            """,
            (ctx.guild.id, case_id),
        )
        if not row:
            return await ctx.send(embed=error_embed(
                f"No case `#{case_id}` found in this server."
            ))

        user_id = row["user_id"]
        mod_id  = row["moderator_id"]
        user    = ctx.guild.get_member(user_id) or await self.bot.fetch_user(user_id)
        mod     = ctx.guild.get_member(mod_id)

        embed = make_embed(
            title=f"📁 Case #{case_id} — {row['action'].title()}",
            colour=self._action_colour(row["action"]),
            fields=[
                ("User",       f"{user} (`{user_id}`)",              True),
                ("Moderator",  f"{mod or mod_id}",                   True),
                ("Action",     f"`{row['action']}`",                  True),
                ("Reason",     row["reason"] or "*(none)*",           False),
                ("Active",     "✅" if row["active"] else "❌",       True),
                ("Created",    str(row["created_at"])[:19],           True),
            ],
            timestamp=True,
        )
        if hasattr(user, "avatar") and user.avatar:
            embed.set_thumbnail(url=user.avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name="editcase")
    @guild_only()
    @require_permit(2)
    async def editcase(
        self,
        ctx:     commands.Context,
        case_id: int,
        *,
        reason:  str,
    ) -> None:
        """Edit the reason for a moderation case."""
        row = await self.bot.db.fetchone(
            "SELECT case_id FROM mod_cases WHERE guild_id = ? AND case_id = ?",
            (ctx.guild.id, case_id),
        )
        if not row:
            return await ctx.send(embed=error_embed(
                f"No case `#{case_id}` found in this server."
            ))
        await self.bot.db.execute(
            "UPDATE mod_cases SET reason = ? WHERE guild_id = ? AND case_id = ?",
            (reason, ctx.guild.id, case_id),
        )
        await ctx.send(embed=success_embed(
            f"Updated reason for case `#{case_id}`."
        ))

    @commands.command(name="deletecase")
    @guild_only()
    @require_permit(4)
    async def deletecase(
        self,
        ctx:     commands.Context,
        case_id: int,
    ) -> None:
        """Permanently delete a moderation case. (Extra Owner+)"""
        row = await self.bot.db.fetchone(
            "SELECT case_id FROM mod_cases WHERE guild_id = ? AND case_id = ?",
            (ctx.guild.id, case_id),
        )
        if not row:
            return await ctx.send(embed=error_embed(
                f"No case `#{case_id}` found in this server."
            ))
        await self.bot.db.execute(
            "DELETE FROM mod_cases WHERE guild_id = ? AND case_id = ?",
            (ctx.guild.id, case_id),
        )
        await ctx.send(embed=success_embed(
            f"Deleted case `#{case_id}`."
        ))

    # ── modlogs ────────────────────────────────────────────────

    @commands.command(name="modlogs")
    @guild_only()
    @require_permit(2)
    async def modlogs(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> None:
        """Show all moderation cases for a member."""
        rows = await self.bot.db.fetchall(
            """
            SELECT case_id, action, reason, moderator_id, created_at
            FROM mod_cases
            WHERE guild_id = ? AND user_id = ?
            ORDER BY created_at DESC
            LIMIT 100
            """,
            (ctx.guild.id, member.id),
        )

        if not rows:
            return await ctx.send(embed=info_embed(
                f"No moderation history for **{member.display_name}**."
            ))

        lines = []
        for row in rows:
            ts       = str(row["created_at"])[:16]
            mod      = ctx.guild.get_member(row["moderator_id"])
            mod_name = mod.display_name if mod else f"ID:{row['moderator_id']}"
            lines.append(
                f"`#{row['case_id']}` `[{row['action']:<8}]` "
                f"`{ts}` — {row['reason'][:50]} *(by {mod_name})*"
            )

        pages = build_pages(
            lines,
            title=f"📁 Mod Log — {member.display_name} ({len(rows)} cases)",
            colour=discord.Colour.blurple(),
            per_page=10,
            numbered=False,
        )
        await send_paginated(ctx, pages)

    # ══════════════════════════════════════════════════════════
    # Strike threshold automation
    # ══════════════════════════════════════════════════════════

    async def _apply_threshold_punishment(
        self,
        guild:      discord.Guild,
        member:     discord.Member,
        threshold:  int,
        punishment: dict,
    ) -> None:
        action   = punishment.get("action", "none")
        duration = punishment.get("duration_minutes")

        log.info(
            "Threshold %d reached for %s in %s — applying %s",
            threshold, member, guild.name, action,
        )

        if action == "mute" and duration:
            delta = datetime.timedelta(minutes=duration)
            try:
                await member.timeout(
                    discord.utils.utcnow() + delta,
                    reason=f"[AutoStrike] Threshold {threshold} reached",
                )
                await self._dm_member(
                    member,
                    title=f"🔇 Auto-muted in {guild.name}",
                    description=f"You reached **{threshold}** strikes.",
                    colour=discord.Colour.orange(),
                    extras={"Duration": format_duration(delta)},
                )
            except discord.Forbidden:
                pass

        elif action == "kick":
            try:
                await self._dm_member(
                    member,
                    title=f"👢 Auto-kicked from {guild.name}",
                    description=f"You reached **{threshold}** strikes.",
                    colour=discord.Colour.orange(),
                )
                await guild.kick(
                    member,
                    reason=f"[AutoStrike] Threshold {threshold} reached",
                )
            except discord.Forbidden:
                pass

        elif action == "ban":
            try:
                await self._dm_member(
                    member,
                    title=f"🔨 Auto-banned from {guild.name}",
                    description=f"You reached **{threshold}** strikes.",
                    colour=discord.Colour.red(),
                )
                await guild.ban(
                    member,
                    reason=f"[AutoStrike] Threshold {threshold} reached",
                    delete_message_days=0,
                )
            except discord.Forbidden:
                pass

        cfg = self.bot.config.get(guild.id)
        await self._log_action(
            guild, member, guild.me,
            action=f"auto_{action}",
            reason=f"Strike threshold {threshold} reached",
            case_id=None,
            extras={"Threshold": f"`{threshold}`"},
        )

    # ══════════════════════════════════════════════════════════
    # Internals
    # ══════════════════════════════════════════════════════════

    async def _create_case(
        self,
        guild:     discord.Guild,
        user:      discord.User | discord.Member,
        moderator: discord.Member | discord.ClientUser,
        action:    str,
        reason:    str,
        duration:  int | None = None,
    ) -> int:
        row = await self.bot.db.fetchone(
            """
            INSERT INTO mod_cases
                (guild_id, user_id, moderator_id, action, reason, duration_seconds, active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING case_id
            """,
            (
                guild.id, user.id, moderator.id,
                action, reason, duration,
                1 if action == "warn" else 0,
            ),
        )
        return row["case_id"] if row else 0

    async def _count_strikes(self, guild_id: int, user_id: int) -> int:
        row = await self.bot.db.fetchone(
            """
            SELECT COUNT(*) AS cnt FROM mod_cases
            WHERE guild_id = ? AND user_id = ? AND action = 'warn' AND active = 1
            """,
            (guild_id, user_id),
        )
        return row["cnt"] if row else 0

    async def _log_action(
        self,
        guild:     discord.Guild,
        user:      discord.User | discord.Member,
        moderator: discord.Member | discord.ClientUser,
        action:    str,
        reason:    str,
        case_id:   int | None,
        extras:    dict | None = None,
    ) -> None:
        cfg     = self.bot.config.get(guild.id)
        ch_id   = cfg.get("log_channel")
        if not ch_id:
            return
        channel = guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        fields = [
            ("User",      f"{user} (`{user.id}`)",       True),
            ("Moderator", f"{moderator}",                True),
            ("Reason",    reason or "*(none)*",           False),
        ]
        if case_id:
            fields.append(("Case", f"`#{case_id}`", True))
        if extras:
            for k, v in extras.items():
                fields.append((k, v, True))

        embed = make_embed(
            title=f"🔨 {action.replace('_', ' ').title()}",
            colour=self._action_colour(action),
            fields=fields,
            timestamp=True,
        )
        avatar = getattr(user, "avatar", None) or getattr(user, "default_avatar", None)
        if avatar:
            embed.set_thumbnail(url=avatar.url)

        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error("Moderation log failed: %s", exc)

    async def _dm_member(
        self,
        member:  discord.Member,
        title:   str,
        description: str,
        colour:  discord.Colour,
        extras:  dict | None = None,
    ) -> None:
        fields = []
        if extras:
            fields = [(k, v, True) for k, v in extras.items()]

        embed = make_embed(
            title=title,
            description=description,
            colour=colour,
            fields=fields,
        )
        try:
            await member.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _target_check(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> bool:
        if member.id == ctx.author.id:
            await ctx.send(embed=error_embed("You cannot action yourself."))
            return False
        if member.id == ctx.guild.owner_id:
            await ctx.send(embed=error_embed("You cannot action the server owner."))
            return False
        if member.id == self.bot.user.id:
            await ctx.send(embed=error_embed("You cannot action me."))
            return False
        if ctx.guild.me.top_role <= member.top_role:
            await ctx.send(embed=error_embed(
                "My highest role is not above this member's highest role."
            ))
            return False
        if ctx.author.top_role <= member.top_role and ctx.author.id != ctx.guild.owner_id:
            await ctx.send(embed=error_embed(
                "Your highest role is not above this member's highest role."
            ))
            return False
        return True

    async def _resolve_banned_user(
        self,
        ctx:  commands.Context,
        query: str,
    ) -> discord.User | None:
        # Try by ID
        if query.isdigit():
            try:
                entry = await ctx.guild.fetch_ban(discord.Object(id=int(query)))
                return entry.user
            except discord.NotFound:
                pass

        # Try by name#disc or display name
        query_lower = query.lower()
        async for entry in ctx.guild.bans(limit=None):
            if (
                str(entry.user).lower() == query_lower
                or entry.user.name.lower() == query_lower
            ):
                return entry.user
        return None

    @staticmethod
    def _action_colour(action: str) -> discord.Colour:
        return {
            "warn":     discord.Colour.yellow(),
            "mute":     discord.Colour.orange(),
            "unmute":   discord.Colour.green(),
            "kick":     discord.Colour.orange(),
            "ban":      discord.Colour.red(),
            "unban":    discord.Colour.green(),
            "softban":  discord.Colour.orange(),
        }.get(action, discord.Colour.blurple())


async def setup(bot):
    await bot.add_cog(ModerationCore(bot))