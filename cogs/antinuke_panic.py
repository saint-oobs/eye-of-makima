"""
Anti-Nuke panic mode — threshold-based server lockdown.

Responsibilities:
- Track number of distinct nukers detected within a time window
- Trigger full server panic lockdown when threshold is reached
- Auto-expire panic mode after configured duration
- Warn designated roles on panic activation
- Unlock server on expiry if configured
"""

import asyncio
import logging
import time
from collections import defaultdict

import discord
from discord.ext import commands, tasks

from utils.helpers import make_embed

log = logging.getLogger("bot.antinuke_panic")

_NUKER_WINDOW_SECONDS = 300  # 5-minute window for nuker count


class AntiNukePanic(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # { guild_id: { "nukers": [(user_id, timestamp), ...], "active": bool, "triggered_at": float } }
        self._state: dict[int, dict] = defaultdict(lambda: {
            "nukers":       [],
            "active":       False,
            "triggered_at": None,
        })

        self._lock = asyncio.Lock()
        self._expiry_task.start()

    def cog_unload(self):
        self._expiry_task.cancel()

    # ── Record nuker ───────────────────────────────────────────

    async def record_nuker(
        self,
        guild:       discord.Guild,
        user_id:     int,
        action_type: str,
        reason:      str,
    ) -> None:
        """
        Called by AntiNuke._punish() each time a nuker is actioned.
        Tracks unique nukers and triggers panic if threshold is reached.
        """
        cfg     = self.bot.config.get(guild.id)
        an_cfg  = cfg.get("antinuke", {})
        panic   = an_cfg.get("panic_mode", {})

        if not panic.get("enabled", False):
            return

        trigger_count = panic.get("raiders_to_trigger", 3)
        now           = time.monotonic()

        async with self._lock:
            state  = self._state[guild.id]

            # Prune old nuker records outside the window
            state["nukers"] = [
                (uid, ts) for uid, ts in state["nukers"]
                if now - ts < _NUKER_WINDOW_SECONDS
            ]

            # Add this nuker if not already recorded
            existing_ids = {uid for uid, _ in state["nukers"]}
            if user_id not in existing_ids:
                state["nukers"].append((user_id, now))

            nuker_count = len(state["nukers"])

        log.info(
            "AntiNuke panic tracker: guild=%d nukers=%d/%d | user=%d action=%s",
            guild.id, nuker_count, trigger_count, user_id, action_type,
        )

        if not self._state[guild.id]["active"] and nuker_count >= trigger_count:
            await self._activate_panic(guild, panic)

    # ── Panic activation ───────────────────────────────────────

    async def _activate_panic(
        self,
        guild:     discord.Guild,
        panic_cfg: dict,
    ) -> None:
        """Activate full anti-nuke panic mode for the guild."""
        async with self._lock:
            state = self._state[guild.id]
            if state["active"]:
                return  # Already active
            state["active"]       = True
            state["triggered_at"] = time.monotonic()

        duration_min  = panic_cfg.get("duration_minutes", 10)
        lockdown      = panic_cfg.get("lockdown_on_trigger", True)
        warned_roles  = panic_cfg.get("warned_roles", [])

        log.warning(
            "ANTINUKE PANIC ACTIVATED: guild=%d nukers=%d duration=%dm lockdown=%s",
            guild.id,
            len(self._state[guild.id]["nukers"]),
            duration_min,
            lockdown,
        )

        cfg = self.bot.config.get(guild.id)

        # 1. Lockdown server
        if lockdown:
            await self._lockdown(guild)

        # 2. Warn roles
        log_channel = await self._get_log_channel(guild, cfg)
        if warned_roles and log_channel:
            mentions = " ".join(
                f"<@&{rid}>"
                for rid in warned_roles
                if guild.get_role(rid)
            )
            if mentions:
                try:
                    await log_channel.send(
                        f"🚨 {mentions} — **ANTI-NUKE PANIC** activated! "
                        f"Nuke attempt detected. Duration: **{duration_min}m**."
                    )
                except discord.HTTPException:
                    pass

        # 3. Log embed
        if log_channel:
            nuker_ids  = [uid for uid, _ in self._state[guild.id]["nukers"]]
            nuker_list = "\n".join(f"<@{uid}>" for uid in nuker_ids) or "*(none recorded)*"

            embed = make_embed(
                title="🚨 ANTI-NUKE PANIC MODE ACTIVATED",
                description=(
                    f"Multiple nukers detected within a short window.\n"
                    f"**{len(nuker_ids)}** malicious actors actioned.\n\n"
                    + ("🔒 Server has been **locked down**.\n" if lockdown else "")
                    + f"Panic mode will expire in **{duration_min} minutes**."
                ),
                colour=discord.Colour.dark_red(),
                fields=[
                    ("Nukers Detected", nuker_list, False),
                    ("Duration",        f"`{duration_min}m`", True),
                    ("Auto-Unlock",
                     "✅" if panic_cfg.get("unlock_on_end", True) else "❌", True),
                ],
                timestamp=True,
            )
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                pass

        # 4. Persist active state to config
        cfg["antinuke"]["panic_mode"]["active"] = True
        self.bot.config.save(guild.id)

    # ── Panic deactivation ─────────────────────────────────────

    async def _deactivate_panic(
        self,
        guild:     discord.Guild,
        panic_cfg: dict,
    ) -> None:
        """Deactivate panic mode after expiry."""
        async with self._lock:
            state = self._state[guild.id]
            state["active"]       = False
            state["triggered_at"] = None
            state["nukers"]       = []

        cfg = self.bot.config.get(guild.id)
        cfg["antinuke"]["panic_mode"]["active"] = False
        self.bot.config.save(guild.id)

        log.info("AntiNuke panic deactivated: guild=%d", guild.id)

        # Optional unlock
        if panic_cfg.get("unlock_on_end", True):
            await self._unlock(guild)

        log_channel = await self._get_log_channel(guild, cfg)
        if log_channel:
            embed = make_embed(
                title="✅ Anti-Nuke Panic Mode Deactivated",
                description=(
                    "Panic mode has expired and the threat window has closed."
                    + ("\nServer channels have been **unlocked**."
                       if panic_cfg.get("unlock_on_end") else "")
                ),
                colour=discord.Colour.green(),
                timestamp=True,
            )
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                pass

    # ── Expiry task ────────────────────────────────────────────

    @tasks.loop(seconds=30)
    async def _expiry_task(self) -> None:
        now = time.monotonic()

        for guild_id, state in list(self._state.items()):
            if not state.get("active"):
                continue

            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            cfg          = self.bot.config.get(guild_id)
            panic_cfg    = cfg.get("antinuke", {}).get("panic_mode", {})
            duration_min = panic_cfg.get("duration_minutes", 10)
            triggered_at = state.get("triggered_at")

            if triggered_at is None:
                continue

            elapsed_min = (now - triggered_at) / 60.0
            if elapsed_min >= duration_min:
                await self._deactivate_panic(guild, panic_cfg)

    @_expiry_task.before_loop
    async def _before_expiry(self) -> None:
        await self.bot.wait_until_ready()

    # ── Lockdown / Unlock ──────────────────────────────────────

    async def _lockdown(self, guild: discord.Guild) -> None:
        everyone = guild.default_role
        for channel in guild.text_channels:
            perms = channel.overwrites_for(everyone)
            if perms.send_messages is not False:
                perms.send_messages = False
                try:
                    await channel.set_permissions(
                        everyone,
                        overwrite=perms,
                        reason="[AntiNuke Panic] Lockdown activated",
                    )
                except discord.Forbidden:
                    pass

    async def _unlock(self, guild: discord.Guild) -> None:
        everyone = guild.default_role
        for channel in guild.text_channels:
            perms = channel.overwrites_for(everyone)
            if perms.send_messages is False:
                perms.send_messages = None
                try:
                    await channel.set_permissions(
                        everyone,
                        overwrite=perms,
                        reason="[AntiNuke Panic] Auto-unlock after expiry",
                    )
                except discord.Forbidden:
                    pass

    # ── Public API ─────────────────────────────────────────────

    def is_panic_active(self, guild_id: int) -> bool:
        return self._state[guild_id]["active"]

    def get_nukers(self, guild_id: int) -> list[int]:
        return [uid for uid, _ in self._state[guild_id]["nukers"]]

    def manual_deactivate(self, guild_id: int) -> None:
        """Manually reset panic state (called by admin commands)."""
        state = self._state[guild_id]
        state["active"]       = False
        state["triggered_at"] = None
        state["nukers"]       = []

    # ── Utility ────────────────────────────────────────────────

    async def _get_log_channel(
        self,
        guild: discord.Guild,
        cfg:   dict,
    ) -> discord.TextChannel | None:
        ch_id = cfg.get("log_channel")
        if not ch_id:
            return None
        channel = guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return None
        if not channel.permissions_for(guild.me).send_messages:
            return None
        return channel


async def setup(bot):
    await bot.add_cog(AntiNukePanic(bot))