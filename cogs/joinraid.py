"""
Join Raid detection — Premium feature.

Detects coordinated join waves (raids) by tracking member joins
within a rolling time window. When the trigger count is reached,
all flagged members in the window are actioned.

Raid qualification criteria (configurable):
- Join count within trigger_period_minutes
- account_type filter: "suspicious" | "new" | "all"
- Optional flags: nopfp_flag, age_flag

When triggered:
- Actions all qualifying members in the window
- Sends a raid alert to the log channel
- Warns configured roles
- Persists raid event to DB
"""

import asyncio
import logging
import time
from collections import deque

import discord
from discord.ext import commands

from utils.helpers import (
    make_embed, account_age_days, has_default_avatar
)

log = logging.getLogger("bot.joinraid")

# How long to keep join records in the window (seconds)
_WINDOW_BUFFER_SECONDS = 600  # 10 min max


class JoinRaid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # { guild_id: deque of (user_id, timestamp, score) }
        self._join_windows: dict[int, deque] = {}
        self._lock = asyncio.Lock()

        # Cooldown: prevent re-triggering within 60s of last trigger
        # { guild_id: last_trigger_timestamp }
        self._cooldowns: dict[int, float] = {}
        self._cooldown_seconds = 60

    # ── Main entry point ───────────────────────────────────────

    async def process_join(self, member: discord.Member) -> None:
        """
        Called by Events.on_member_join for every human member.
        Records the join and checks for raid conditions.
        """
        guild = member.guild
        cfg   = self.bot.config.get(guild.id)
        jrc   = cfg.get("joinraid", {})

        if not jrc.get("enabled", False):
            return

        # Premium check
        if not await self.bot.is_premium(guild.id):
            return

        now   = time.monotonic()
        score = self._score_member(member, jrc)

        # Skip members who don't meet the account_type filter
        if score == 0:
            return

        async with self._lock:
            window = self._join_windows.setdefault(guild.id, deque())

            # Add this join
            window.append((member.id, now, score))

            # Prune joins outside the window
            period  = jrc.get("trigger_period_minutes", 5) * 60
            cutoff  = now - period
            while window and window[0][1] < cutoff:
                window.popleft()

            trigger_count = jrc.get("trigger_count", 10)
            current_count = len(window)

        log.debug(
            "JoinRaid window: guild=%d count=%d/%d member=%s",
            guild.id, current_count, trigger_count, member,
        )

        if current_count >= trigger_count:
            await self._trigger_raid(guild, jrc, cfg)

    # ── Raid trigger ───────────────────────────────────────────

    async def _trigger_raid(
        self,
        guild: discord.Guild,
        jrc:   dict,
        cfg:   dict,
    ) -> None:
        """
        Handle a detected raid.
        Applies configured action to all members in the current window.
        """
        now = time.monotonic()

        # Cooldown check — prevent double-triggering
        last = self._cooldowns.get(guild.id, 0)
        if now - last < self._cooldown_seconds:
            log.debug("JoinRaid cooldown active for guild=%d", guild.id)
            return

        self._cooldowns[guild.id] = now

        async with self._lock:
            window  = self._join_windows.get(guild.id, deque())
            members = list(window)
            window.clear()

        if not members:
            return

        action  = jrc.get("action", "kick")
        actioned: list[int] = []
        failed:   list[int] = []

        log.warning(
            "RAID DETECTED: guild=%d members=%d action=%s",
            guild.id, len(members), action,
        )

        for user_id, _, _ in members:
            member = guild.get_member(user_id)
            if not member:
                continue
            success = await self._apply_action(member, action, cfg)
            if success:
                actioned.append(user_id)
                await self._persist_event(guild.id, user_id, action)
            else:
                failed.append(user_id)

        await self._send_raid_alert(guild, cfg, jrc, actioned, failed, action)
        await self._warn_roles(guild, cfg, jrc, len(actioned))

    # ── Action application ─────────────────────────────────────

    async def _apply_action(
        self,
        member: discord.Member,
        action: str,
        cfg:    dict,
    ) -> bool:
        """Apply kick/ban/quarantine to a raiding member."""
        guild = member.guild

        if member.id == guild.owner_id or member.bot:
            return False

        try:
            if action == "kick":
                await member.kick(reason="[JoinRaid] Coordinated raid detected")
                return True

            elif action == "ban":
                await guild.ban(
                    member,
                    reason="[JoinRaid] Coordinated raid detected",
                    delete_message_days=1,
                )
                return True

            elif action == "quarantine":
                qr_id = cfg.get("quarantine_role")
                qr    = guild.get_role(qr_id) if qr_id else None
                if qr:
                    await member.edit(
                        roles=[qr],
                        reason="[JoinRaid] Coordinated raid detected",
                    )
                    cfg.setdefault("_quarantined", []).append(member.id)
                    self.bot.config.save(guild.id)
                    return True
                else:
                    # Fallback to kick if no quarantine role
                    await member.kick(reason="[JoinRaid] Raid detected (no quarantine role)")
                    return True

        except discord.Forbidden:
            log.warning("Cannot %s raider %s — missing permissions", action, member)
            return False
        except discord.HTTPException as exc:
            log.error("HTTP error actioning raider %s: %s", member, exc)
            return False

        return False

    # ── Member scoring ─────────────────────────────────────────

    def _score_member(self, member: discord.Member, jrc: dict) -> int:
        """
        Determine if a member qualifies for raid tracking.

        Returns:
            0  — member does not qualify (skip)
            1+ — member qualifies (higher = more suspicious)
        """
        account_type = jrc.get("account_type", "suspicious")
        score        = 0

        # "all" → every join counts
        if account_type == "all":
            return 1

        age    = account_age_days(member)
        no_pfp = has_default_avatar(member)

        # Age flag
        age_flag = jrc.get("age_flag", {})
        if age_flag.get("enabled", True):
            min_days = age_flag.get("min_days", 2)
            if age < min_days:
                score += 2

        # No-pfp flag
        nopfp_flag = jrc.get("nopfp_flag", {})
        if nopfp_flag.get("enabled", True) and no_pfp:
            score += 1

        # "new" → only age-flagged members
        if account_type == "new":
            age_min = jrc.get("age_flag", {}).get("min_days", 2)
            return 2 if age < age_min else 0

        # "suspicious" (default) → needs at least 1 flag
        return score

    # ── Alerts ────────────────────────────────────────────────

    async def _send_raid_alert(
        self,
        guild:    discord.Guild,
        cfg:      dict,
        jrc:      dict,
        actioned: list[int],
        failed:   list[int],
        action:   str,
    ) -> None:
        ch_id = cfg.get("log_channel")
        if not ch_id:
            return
        channel = guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        actioned_list = (
            "\n".join(f"<@{uid}>" for uid in actioned[:20])
            + ("\n*(+ more)*" if len(actioned) > 20 else "")
        ) or "*(none)*"

        embed = make_embed(
            title="🚨 Join Raid Detected",
            description=(
                f"**{len(actioned) + len(failed)}** suspicious members joined "
                f"within `{jrc.get('trigger_period_minutes', 5)}` minutes.\n"
                f"Action taken: **{action}**"
            ),
            colour=discord.Colour.dark_red(),
            fields=[
                ("Actioned",    f"`{len(actioned)}`",  True),
                ("Failed",      f"`{len(failed)}`",    True),
                ("Action",      f"`{action}`",         True),
                ("Members",     actioned_list,         False),
            ],
            timestamp=True,
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error("JoinRaid alert failed: %s", exc)

    async def _warn_roles(
        self,
        guild:    discord.Guild,
        cfg:      dict,
        jrc:      dict,
        count:    int,
    ) -> None:
        warned_roles = jrc.get("warned_roles", [])
        if not warned_roles:
            return

        ch_id = cfg.get("log_channel")
        if not ch_id:
            return
        channel = guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        mentions = " ".join(
            f"<@&{rid}>"
            for rid in warned_roles
            if guild.get_role(rid)
        )
        if mentions:
            try:
                await channel.send(
                    f"⚠️ {mentions} — **Raid detected!** "
                    f"`{count}` members actioned."
                )
            except discord.HTTPException:
                pass

    # ── Persistence ────────────────────────────────────────────

    async def _persist_event(
        self,
        guild_id: int,
        user_id:  int,
        action:   str,
    ) -> None:
        try:
            await self.bot.db.execute(
                """
                INSERT INTO joinraid_events (guild_id, user_id, action)
                VALUES (?, ?, ?)
                """,
                (guild_id, user_id, action),
            )
        except Exception as exc:
            log.error("Failed to persist joinraid event: %s", exc)

    # ── Public API ─────────────────────────────────────────────

    def get_window_size(self, guild_id: int) -> int:
        """Return the current number of joins in the tracking window."""
        return len(self._join_windows.get(guild_id, deque()))

    def clear_window(self, guild_id: int) -> None:
        """Manually clear the join window (used by admin commands)."""
        if guild_id in self._join_windows:
            self._join_windows[guild_id].clear()
        self._cooldowns.pop(guild_id, None)


async def setup(bot):
    await bot.add_cog(JoinRaid(bot))