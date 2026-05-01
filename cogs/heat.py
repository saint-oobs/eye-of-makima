"""
Heat system — core state engine.

Responsibilities:
- Per-user heat value storage (in-memory + DB persistence)
- Heat addition with multiplier support (Premium)
- Heat degradation task (runs every second)
- Strike tracking and timeout escalation
- Heat reset on timeout
- Panic mode trigger
"""

import asyncio
import logging
import time

import discord
from discord.ext import commands, tasks

from utils.helpers import parse_duration

log = logging.getLogger("bot.heat")

# Maximum timeout discord allows (28 days in seconds)
_MAX_TIMEOUT_SECONDS = 28 * 24 * 3600


class HeatEngine(commands.Cog):
    """
    Core heat state engine.

    heat_state structure (per guild, per user):
    {
        "heat":       float,   # current heat value (0.0 – max_heat)
        "strikes":    int,     # number of timeouts issued this session
        "last_seen":  float,   # unix timestamp of last message
    }
    """

    def __init__(self, bot):
        self.bot = bot
        # { guild_id: { user_id: { heat, strikes, last_seen } } }
        self._state: dict[int, dict[int, dict]] = {}
        self._lock  = asyncio.Lock()
        self._degrade_task.start()

    def cog_unload(self):
        self._degrade_task.cancel()

    # ── State access ───────────────────────────────────────────

    def _get(self, guild_id: int, user_id: int) -> dict:
        """Return (creating if needed) the heat record for a user."""
        guild_state = self._state.setdefault(guild_id, {})
        if user_id not in guild_state:
            guild_state[user_id] = {
                "heat":      0.0,
                "strikes":   0,
                "last_seen": time.monotonic(),
            }
        return guild_state[user_id]

    def get_heat(self, guild_id: int, user_id: int) -> float:
        return self._get(guild_id, user_id)["heat"]

    def get_strikes(self, guild_id: int, user_id: int) -> int:
        return self._get(guild_id, user_id)["strikes"]

    def reset_heat(self, guild_id: int, user_id: int) -> None:
        record = self._get(guild_id, user_id)
        record["heat"]    = 0.0
        record["strikes"] = 0
        log.debug("Heat reset: guild=%d user=%d", guild_id, user_id)

    def reset_heat_only(self, guild_id: int, user_id: int) -> None:
        """Reset heat but preserve strike count."""
        self._get(guild_id, user_id)["heat"] = 0.0

    # ── Heat addition ──────────────────────────────────────────

    async def add_heat(
        self,
        member:    discord.Member,
        amount:    float,
        *,
        reason:    str = "",
        source:    str = "",
    ) -> float:
        """
        Add heat to a member and return the new heat value.

        Applies multiplier for Premium guilds if enabled.
        Updates last_seen timestamp.
        Persists to DB asynchronously (fire-and-forget).
        """
        cfg        = self.bot.config.get(member.guild.id)
        heat_cfg   = cfg.get("heat", {})

        if not heat_cfg.get("enabled", True):
            return 0.0

        record = self._get(member.guild.id, member.id)
        record["last_seen"] = time.monotonic()

        # Premium multiplier
        multiplier = 1.0
        if heat_cfg.get("multiplier") and await self.bot.is_premium(member.guild.id):
            multiplier = heat_cfg.get("multiplier_value", 1.0)

        record["heat"] = min(
            record["heat"] + (amount * multiplier),
            heat_cfg.get("max_heat", 85.0) * 2,  # allow overshoot for instant breach
        )

        log.debug(
            "Heat +%.1f (×%.1f) → %.1f | guild=%d user=%d | %s",
            amount, multiplier, record["heat"],
            member.guild.id, member.id,
            source or reason,
        )

        # Persist async (don't await — fire and forget to keep dispatch fast)
        asyncio.create_task(
            self._persist_heat(member.guild.id, member.id, record["heat"], record["strikes"])
        )

        return record["heat"]

    async def _persist_heat(
        self,
        guild_id: int,
        user_id:  int,
        heat:     float,
        strikes:  int,
    ) -> None:
        try:
            await self.bot.db.upsert(
                "heat_state",
                {
                    "guild_id":   guild_id,
                    "user_id":    user_id,
                    "heat":       round(heat, 3),
                    "strikes":    strikes,
                    "updated_at": "CURRENT_TIMESTAMP",
                },
                conflict_cols=["guild_id", "user_id"],
            )
        except Exception as exc:
            log.error("Failed to persist heat: %s", exc)

    # ── Strike / timeout escalation ────────────────────────────

    async def apply_strike(
        self,
        member: discord.Member,
        reason: str = "Heat threshold exceeded",
    ) -> bool:
        """
        Issue a timeout strike to a member.

        - Increments strike counter
        - Applies timeout_per_strike duration up to strikes_cap
        - At strikes_cap: applies timeout_at_cap
        - Optionally resets heat after timeout
        - Returns True if action was taken, False if bot lacked permissions
        """
        cfg      = self.bot.config.get(member.guild.id)
        heat_cfg = cfg.get("heat", {})
        record   = self._get(member.guild.id, member.id)

        record["strikes"] += 1
        strikes     = record["strikes"]
        cap         = heat_cfg.get("strikes_cap", 3)
        per_strike  = heat_cfg.get("timeout_per_strike", "5m")
        at_cap      = heat_cfg.get("timeout_at_cap", "1h")

        duration_str = at_cap if strikes >= cap else per_strike
        seconds      = parse_duration(duration_str) or 300
        seconds      = min(seconds, _MAX_TIMEOUT_SECONDS)

        log.info(
            "Strike %d/%d → timeout %s | guild=%d user=%d | %s",
            strikes, cap, duration_str,
            member.guild.id, member.id, reason,
        )

        try:
            await member.timeout(
                discord.utils.utcnow() + __import__("datetime").timedelta(seconds=seconds),
                reason=f"[Heat] Strike {strikes}/{cap} — {reason}"[:512],
            )
        except discord.Forbidden:
            log.warning(
                "Cannot timeout %s in %s — missing permissions",
                member, member.guild.name,
            )
            return False
        except discord.HTTPException as exc:
            log.error("Timeout HTTP error for %s: %s", member, exc)
            return False

        # Optionally reset heat after timeout
        if heat_cfg.get("reset_heat_on_timeout", True):
            self.reset_heat_only(member.guild.id, member.id)

        # Persist updated strikes
        asyncio.create_task(
            self._persist_heat(member.guild.id, member.id, record["heat"], strikes)
        )

        return True

    # ── Degradation task ───────────────────────────────────────

    @tasks.loop(seconds=1)
    async def _degrade_task(self) -> None:
        """
        Degrade heat for all tracked users every second.
        Uses monotonic clock delta to stay accurate regardless of task jitter.
        """
        now = time.monotonic()
        to_clean: list[tuple[int, int]] = []

        async with self._lock:
            for guild_id, users in self._state.items():
                cfg      = self.bot.config.get(guild_id)
                heat_cfg = cfg.get("heat", {})

                if not heat_cfg.get("enabled", True):
                    continue

                deg_per_sec = heat_cfg.get("degradation_per_second", 0.5)

                for user_id, record in users.items():
                    elapsed  = now - record.get("_last_tick", now)
                    record["_last_tick"] = now

                    if record["heat"] > 0:
                        record["heat"] = max(0.0, record["heat"] - (deg_per_sec * elapsed))

                    # Mark for cleanup if heat is 0 and no recent activity (5 min)
                    if record["heat"] == 0.0 and (now - record["last_seen"]) > 300:
                        to_clean.append((guild_id, user_id))

            # Remove idle zero-heat records to keep memory clean
            for guild_id, user_id in to_clean:
                self._state.get(guild_id, {}).pop(user_id, None)

    @_degrade_task.before_loop
    async def _before_degrade(self) -> None:
        await self.bot.wait_until_ready()
        await self._restore_from_db()

    # ── DB restore ─────────────────────────────────────────────

    async def _restore_from_db(self) -> None:
        """
        Restore non-zero heat states from DB on startup.
        Ensures heat persists across bot restarts.
        """
        try:
            rows = await self.bot.db.fetchall(
                "SELECT guild_id, user_id, heat, strikes FROM heat_state "
                "WHERE heat > 0"
            )
            for row in rows:
                record = self._get(row["guild_id"], row["user_id"])
                record["heat"]    = row["heat"]
                record["strikes"] = row["strikes"]
            log.info("Restored %d heat records from DB.", len(rows))
        except Exception as exc:
            log.error("Failed to restore heat from DB: %s", exc)

    # ── Public snapshot ────────────────────────────────────────

    def get_leaderboard(
        self,
        guild_id: int,
        limit:    int = 10,
    ) -> list[tuple[int, float, int]]:
        """
        Return top-N users by heat for a guild.
        Returns list of (user_id, heat, strikes) sorted by heat descending.
        """
        users = self._state.get(guild_id, {})
        ranked = sorted(
            ((uid, rec["heat"], rec["strikes"]) for uid, rec in users.items() if rec["heat"] > 0),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:limit]

    def guild_user_count(self, guild_id: int) -> int:
        """Return number of users currently tracked for a guild."""
        return len(self._state.get(guild_id, {}))


async def setup(bot):
    await bot.add_cog(HeatEngine(bot))