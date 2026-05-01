"""
Anti-Nuke core — audit log processor and action counter.

Responsibilities:
- Process on_audit_log_entry_create events
- Track per-guild per-user action counts (minute + hour windows)
- Detect when a user exceeds configured limits
- Dispatch quarantine/ban response via _punish()
- Route to AntiNukePanic when panic threshold is hit
- Prune detection (mass kick via prune)
"""

import asyncio
import logging
import time
from collections import defaultdict

import discord
from discord.ext import commands

from utils.helpers import make_embed

log = logging.getLogger("bot.antinuke")

# Audit log action → config key mapping
_ACTION_MAP: dict[discord.AuditLogAction, str] = {
    discord.AuditLogAction.ban:            "ban",
    discord.AuditLogAction.kick:           "kick",
    discord.AuditLogAction.channel_delete: "channel_delete",
    discord.AuditLogAction.channel_create: "channel_create",
    discord.AuditLogAction.role_delete:    "role_delete",
    discord.AuditLogAction.role_create:    "role_create",
    discord.AuditLogAction.webhook_create: "webhook_create",
    discord.AuditLogAction.webhook_delete: "webhook_delete",
}

# How many seconds back to count for minute/hour windows
_MINUTE_WINDOW = 60
_HOUR_WINDOW   = 3600


class AntiNuke(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # { guild_id: { user_id: { action_type: [(timestamp, ...), ...] } } }
        self._action_log: dict[int, dict[int, dict[str, list[float]]]] = \
            defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

        self._lock = asyncio.Lock()

    # ── Audit log entry ────────────────────────────────────────

    async def process_audit_entry(
        self, entry: discord.AuditLogEntry
    ) -> None:
        """
        Main entry point from Events.on_audit_log_entry_create.
        Routes each audit action through the counter and limit checker.
        """
        action_key = _ACTION_MAP.get(entry.action)
        if not action_key:
            return

        guild = entry.guild
        if not guild:
            return

        cfg = self.bot.config.get(guild.id)
        if not cfg.get("antinuke", {}).get("enabled", True):
            return

        # Ignore actions performed by the bot itself
        if entry.user_id == self.bot.user.id:
            return

        # Ignore permitted users (permit 4+)
        if await self._is_permitted(guild, entry.user_id):
            return

        # Prune detection
        if (
            action_key == "kick"
            and cfg.get("antinuke", {}).get("prune_detection", False)
            and entry.reason
            and "prune" in entry.reason.lower()
        ):
            await self._handle_prune(guild, entry)
            return

        async with self._lock:
            now = time.monotonic()
            user_log = self._action_log[guild.id][entry.user_id]
            user_log[action_key].append(now)

            # Prune old entries outside the hour window
            cutoff_hour = now - _HOUR_WINDOW
            user_log[action_key] = [
                t for t in user_log[action_key] if t > cutoff_hour
            ]

            minute_count = sum(
                1 for t in user_log[action_key] if t > now - _MINUTE_WINDOW
            )
            hour_count = len(user_log[action_key])

        an_cfg        = cfg.get("antinuke", {})
        minute_limits = an_cfg.get("minute_limit", {})
        hour_limits   = an_cfg.get("hour_limit", {})

        minute_limit = minute_limits.get(action_key, 999)
        hour_limit   = hour_limits.get(action_key, 999)

        breached      = False
        breach_reason = ""

        if minute_count >= minute_limit:
            breached      = True
            breach_reason = (
                f"{minute_count} `{action_key}` actions in the last minute "
                f"(limit: {minute_limit})"
            )
        elif hour_count >= hour_limit:
            breached      = True
            breach_reason = (
                f"{hour_count} `{action_key}` actions in the last hour "
                f"(limit: {hour_limit})"
            )

        if breached:
            log.warning(
                "AntiNuke breach: guild=%d user=%d action=%s | %s",
                guild.id, entry.user_id, action_key, breach_reason,
            )
            await self._punish(guild, entry.user_id, breach_reason, action_key)
            await self._persist_action(guild.id, entry.user_id, action_key)

    # ── Punishment ─────────────────────────────────────────────

    async def _punish(
        self,
        guild:        discord.Guild,
        user_id:      int,
        reason:       str,
        action_type:  str,
    ) -> None:
        """
        Respond to a nuke breach:
        1. Attempt to quarantine the user (remove all roles + apply quarantine role)
        2. If quarantine role not configured → ban
        3. Log to log_channel
        4. Attempt to revert the last destructive action if possible
        5. Notify AntiNukePanic for panic-mode handling
        """
        cfg     = self.bot.config.get(guild.id)
        an_cfg  = cfg.get("antinuke", {})

        member  = guild.get_member(user_id)
        punished_as = "unknown"

        if member:
            # Don't punish guild owner or bot
            if member.id == guild.owner_id or member.bot:
                return

            qr_id = cfg.get("quarantine_role")
            if qr_id and an_cfg.get("quarantine_hold", True):
                punished_as = await self._quarantine(guild, member, reason)
            else:
                punished_as = await self._ban(guild, member, reason)
        else:
            # User already left — ban by ID
            punished_as = await self._ban_id(guild, user_id, reason)

        await self._log_action(guild, user_id, reason, action_type, punished_as)

        # Notify panic cog
        panic_cog = self.bot.get_cog("AntiNukePanic")
        if panic_cog:
            await panic_cog.record_nuker(guild, user_id, action_type, reason)

    async def _quarantine(
        self,
        guild:  discord.Guild,
        member: discord.Member,
        reason: str,
    ) -> str:
        cfg    = self.bot.config.get(guild.id)
        qr_id  = cfg.get("quarantine_role")
        qr     = guild.get_role(qr_id)

        if not qr:
            return await self._ban(guild, member, reason)

        # Save current roles
        saved = [r.id for r in member.roles if not r.is_default()]
        cfg["_saved_roles"][str(member.id)] = saved

        try:
            await member.edit(
                roles=[qr],
                reason=f"[AntiNuke] {reason}"[:512],
            )
            if member.id not in cfg.get("_quarantined", []):
                cfg.setdefault("_quarantined", []).append(member.id)
            self.bot.config.save(guild.id)

            # Persist to DB
            await self.bot.db.execute(
                """
                INSERT OR REPLACE INTO quarantine_records
                    (guild_id, user_id, quarantined_by, reason, saved_roles)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    guild.id, member.id, self.bot.user.id,
                    reason, str(saved),
                ),
            )
            log.info("Quarantined nuker %s in %s", member, guild.name)
            return "quarantined"
        except discord.Forbidden:
            log.warning("Cannot quarantine %s — missing permissions", member)
            return await self._ban(guild, member, reason)

    async def _ban(
        self,
        guild:  discord.Guild,
        member: discord.Member,
        reason: str,
    ) -> str:
        try:
            await guild.ban(
                member,
                reason=f"[AntiNuke] {reason}"[:512],
                delete_message_days=0,
            )
            log.info("Banned nuker %s in %s", member, guild.name)
            return "banned"
        except discord.Forbidden:
            log.warning("Cannot ban %s — missing permissions", member)
            return "failed"

    async def _ban_id(
        self,
        guild:   discord.Guild,
        user_id: int,
        reason:  str,
    ) -> str:
        try:
            await guild.ban(
                discord.Object(id=user_id),
                reason=f"[AntiNuke] {reason}"[:512],
                delete_message_days=0,
            )
            log.info("Banned nuker ID %d in %s", user_id, guild.name)
            return "banned"
        except discord.Forbidden:
            log.warning("Cannot ban ID %d — missing permissions", user_id)
            return "failed"

    # ── Prune detection ────────────────────────────────────────

    async def _handle_prune(
        self,
        guild: discord.Guild,
        entry: discord.AuditLogEntry,
    ) -> None:
        """Handle mass-kick via guild prune as a nuke action."""
        pruned = getattr(entry, "extra", {})
        count  = getattr(pruned, "count", 0) if pruned else 0
        reason = f"Mass prune of {count} members"
        log.warning(
            "Prune detected: guild=%d user=%d count=%d",
            guild.id, entry.user_id, count,
        )
        await self._punish(guild, entry.user_id, reason, "kick")

    # ── Guild update passthrough ───────────────────────────────

    async def on_guild_update(
        self,
        before: discord.Guild,
        after:  discord.Guild,
    ) -> None:
        """Detect suspicious guild-level changes (vanity URL hijack, etc.)."""
        # Placeholder for future guild-level nuke detection
        pass

    # ── Logging ────────────────────────────────────────────────

    async def _log_action(
        self,
        guild:       discord.Guild,
        user_id:     int,
        reason:      str,
        action_type: str,
        outcome:     str,
    ) -> None:
        cfg         = self.bot.config.get(guild.id)
        ch_id       = cfg.get("log_channel")
        if not ch_id:
            return
        channel = guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        member  = guild.get_member(user_id)
        mention = f"<@{user_id}>" if not member else member.mention
        name    = str(member) if member else f"ID: {user_id}"

        colour = (
            discord.Colour.dark_red() if outcome in ("banned", "quarantined")
            else discord.Colour.orange()
        )

        embed = make_embed(
            title="🛡️ Anti-Nuke Triggered",
            description=f"**{name}** ({mention}) was **{outcome}**.",
            colour=colour,
            fields=[
                ("Action Type", f"`{action_type}`", True),
                ("Outcome",     f"`{outcome}`",     True),
                ("Reason",      reason,             False),
                ("User ID",     f"`{user_id}`",     True),
            ],
            timestamp=True,
        )
        if member:
            embed.set_thumbnail(url=(member.avatar or member.default_avatar).url)

        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error("Failed to send antinuke log: %s", exc)

    # ── Persistence ────────────────────────────────────────────

    async def _persist_action(
        self,
        guild_id:    int,
        user_id:     int,
        action_type: str,
    ) -> None:
        try:
            await self.bot.db.execute(
                """
                INSERT INTO antinuke_actions (guild_id, user_id, action_type)
                VALUES (?, ?, ?)
                """,
                (guild_id, user_id, action_type),
            )
        except Exception as exc:
            log.error("Failed to persist antinuke action: %s", exc)

    # ── Permission check ───────────────────────────────────────

    async def _is_permitted(self, guild: discord.Guild, user_id: int) -> bool:
        """Return True if the user has permit level 4+ (extra owner or guild owner)."""
        if user_id == guild.owner_id:
            return True
        cfg = self.bot.config.get(guild.id)
        return user_id in cfg.get("extra_owners", [])

    # ── Public API ─────────────────────────────────────────────

    def get_action_counts(
        self,
        guild_id: int,
        user_id:  int,
    ) -> dict[str, tuple[int, int]]:
        """
        Return current minute/hour action counts for a user.
        Returns { action_type: (minute_count, hour_count) }
        """
        now      = time.monotonic()
        user_log = self._action_log.get(guild_id, {}).get(user_id, {})
        result   = {}
        for action_type, timestamps in user_log.items():
            minute = sum(1 for t in timestamps if t > now - _MINUTE_WINDOW)
            hour   = len(timestamps)
            result[action_type] = (minute, hour)
        return result

    def clear_user_log(self, guild_id: int, user_id: int) -> None:
        """Clear action log for a specific user (used after whitelisting)."""
        self._action_log.get(guild_id, {}).pop(user_id, None)


async def setup(bot):
    await bot.add_cog(AntiNuke(bot))