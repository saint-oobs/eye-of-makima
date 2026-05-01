"""
Heat breach handler — panic mode and escalated breach responses.

Responsibilities:
- Panic mode breach handling (mass-timeout, lockdown, role warnings)
- Panic mode auto-expiry task
- Breach escalation beyond normal strike flow
- Per-guild panic state tracking
"""

import asyncio
import logging
import time

import discord
from discord.ext import commands, tasks

from utils.helpers import make_embed, parse_duration

log = logging.getLogger("bot.heat_breach")


class HeatBreach(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # { guild_id: { "triggered_at": float, "raiders": [user_id, ...] } }
        self._panic_state: dict[int, dict] = {}
        self._panic_expiry_task.start()

    def cog_unload(self):
        self._panic_expiry_task.cancel()

    # ── Panic mode breach ──────────────────────────────────────

    async def handle_panic_breach(
        self,
        member: discord.Member,
        heat:   float,
        *,
        reason: str = "",
    ) -> None:
        """
        Handle a heat breach when panic mode is enabled.

        Flow:
        1. Add member to the raiders list for this guild
        2. If raiders >= raiders_to_trigger → activate full panic
        3. Otherwise → timeout the individual member
        """
        guild_id = member.guild.id
        cfg      = self.bot.config.get(guild_id)
        panic    = cfg.get("heat", {}).get("panic_mode", {})

        if not panic.get("enabled"):
            return

        trigger_count = panic.get("raiders_to_trigger", 3)
        state         = self._panic_state.setdefault(
            guild_id,
            {"triggered_at": None, "raiders": [], "active": False},
        )

        # Add to raider list if not already present
        if member.id not in state["raiders"]:
            state["raiders"].append(member.id)

        log.info(
            "Panic breach: guild=%d user=%d raiders=%d/%d | %s",
            guild_id, member.id, len(state["raiders"]), trigger_count, reason,
        )

        if not state["active"] and len(state["raiders"]) >= trigger_count:
            await self._activate_panic(member.guild, state, panic)
        else:
            # Panic not yet triggered — timeout individual raider
            await self._timeout_raider(member, reason)

    async def _activate_panic(
        self,
        guild:    discord.Guild,
        state:    dict,
        panic_cfg: dict,
    ) -> None:
        """Full panic mode activation."""
        if state["active"]:
            return  # Already active, don't double-trigger

        state["active"]       = True
        state["triggered_at"] = time.monotonic()
        duration_min          = panic_cfg.get("duration_minutes", 10)

        log.warning(
            "PANIC MODE ACTIVATED: guild=%d raiders=%d duration=%dm",
            guild.id, len(state["raiders"]), duration_min,
        )

        cfg         = self.bot.config.get(guild.id)
        log_channel = await self._get_log_channel(guild, cfg)

        # 1. Timeout all known raiders
        timeout_tasks = [
            self._timeout_raider(
                guild.get_member(uid),
                "Panic mode — mass raid detected",
            )
            for uid in state["raiders"]
            if guild.get_member(uid)
        ]
        await asyncio.gather(*timeout_tasks, return_exceptions=True)

        # 2. Optional: lockdown server
        if panic_cfg.get("lockdown_on_trigger", True):
            await self._lockdown_server(guild)

        # 3. Warn configured roles
        warned_roles = panic_cfg.get("warned_roles", [])
        await self._warn_roles(guild, warned_roles, duration_min)

        # 4. Log the activation
        if log_channel:
            embed = make_embed(
                title="🚨 PANIC MODE ACTIVATED",
                description=(
                    f"**{len(state['raiders'])}** raiders detected.\n"
                    f"Server is now in **panic mode** for **{duration_min} minutes**.\n\n"
                    f"All raiders have been timed out."
                    + (" Server locked down." if panic_cfg.get("lockdown_on_trigger") else "")
                ),
                colour=discord.Colour.dark_red(),
                fields=[
                    ("Raiders", "\n".join(f"<@{uid}>" for uid in state["raiders"]) or "*(none)*", False),
                    ("Duration", f"`{duration_min}m`", True),
                    ("Auto-Unlock", "✅" if panic_cfg.get("unlock_on_end") else "❌", True),
                ],
                timestamp=True,
            )
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                pass

        # Persist panic state to config
        cfg["heat"]["panic_mode"]["active"] = True
        self.bot.config.save(guild.id)

    async def _timeout_raider(
        self,
        member: discord.Member | None,
        reason: str,
    ) -> None:
        """Apply a standard panic-mode timeout to an individual raider."""
        if not member:
            return
        try:
            import datetime
            await member.timeout(
                discord.utils.utcnow() + datetime.timedelta(hours=1),
                reason=f"[Panic Mode] {reason}"[:512],
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not timeout raider %s: %s", member, exc)

    async def _lockdown_server(self, guild: discord.Guild) -> None:
        """Lock all text channels for @everyone."""
        everyone = guild.default_role
        for channel in guild.text_channels:
            perms = channel.overwrites_for(everyone)
            if perms.send_messages is not False:
                perms.send_messages = False
                try:
                    await channel.set_permissions(
                        everyone,
                        overwrite=perms,
                        reason="[Panic Mode] Raid lockdown",
                    )
                except discord.Forbidden:
                    pass

    async def _unlock_server(self, guild: discord.Guild) -> None:
        """Restore @everyone Send Messages in all locked channels."""
        everyone = guild.default_role
        for channel in guild.text_channels:
            perms = channel.overwrites_for(everyone)
            if perms.send_messages is False:
                perms.send_messages = None
                try:
                    await channel.set_permissions(
                        everyone,
                        overwrite=perms,
                        reason="[Panic Mode] Auto-unlock after expiry",
                    )
                except discord.Forbidden:
                    pass

    async def _warn_roles(
        self,
        guild:       discord.Guild,
        role_ids:    list[int],
        duration_min: int,
    ) -> None:
        """Ping designated roles about the panic activation."""
        if not role_ids:
            return

        # Find first available text channel to send the warning
        cfg         = self.bot.config.get(guild.id)
        log_channel = await self._get_log_channel(guild, cfg)
        if not log_channel:
            return

        mentions = " ".join(
            f"<@&{rid}>" for rid in role_ids
            if guild.get_role(rid)
        )
        if mentions:
            try:
                await log_channel.send(
                    f"⚠️ {mentions} — **PANIC MODE** activated! "
                    f"Raid in progress. Duration: **{duration_min}m**."
                )
            except discord.HTTPException:
                pass

    # ── Panic expiry task ──────────────────────────────────────

    @tasks.loop(seconds=30)
    async def _panic_expiry_task(self) -> None:
        """Check for expired panic modes every 30 seconds and deactivate them."""
        now = time.monotonic()

        for guild_id, state in list(self._panic_state.items()):
            if not state.get("active"):
                continue

            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            cfg          = self.bot.config.get(guild_id)
            panic_cfg    = cfg.get("heat", {}).get("panic_mode", {})
            duration_min = panic_cfg.get("duration_minutes", 10)
            triggered_at = state.get("triggered_at")

            if triggered_at is None:
                continue

            elapsed_min = (now - triggered_at) / 60.0

            if elapsed_min >= duration_min:
                await self._deactivate_panic(guild, state, panic_cfg)

    @_panic_expiry_task.before_loop
    async def _before_expiry(self) -> None:
        await self.bot.wait_until_ready()

    async def _deactivate_panic(
        self,
        guild:    discord.Guild,
        state:    dict,
        panic_cfg: dict,
    ) -> None:
        """Deactivate panic mode after duration expires."""
        state["active"]       = False
        state["triggered_at"] = None
        state["raiders"]      = []

        cfg = self.bot.config.get(guild.id)
        cfg["heat"]["panic_mode"]["active"] = False
        self.bot.config.save(guild.id)

        log.info("Panic mode deactivated: guild=%d", guild.id)

        # Optional: unlock server
        if panic_cfg.get("unlock_on_end", True):
            await self._unlock_server(guild)

        # Log deactivation
        log_channel = await self._get_log_channel(guild, cfg)
        if log_channel:
            embed = make_embed(
                title="✅ Panic Mode Deactivated",
                description=(
                    "Panic mode has expired. "
                    + ("Server channels have been unlocked." if panic_cfg.get("unlock_on_end") else "")
                ),
                colour=discord.Colour.green(),
                timestamp=True,
            )
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                pass

    # ── Panic state API ────────────────────────────────────────

    def is_panic_active(self, guild_id: int) -> bool:
        return self._panic_state.get(guild_id, {}).get("active", False)

    def get_panic_raiders(self, guild_id: int) -> list[int]:
        return self._panic_state.get(guild_id, {}).get("raiders", [])

    def clear_panic(self, guild_id: int) -> None:
        """Manually clear panic state (used by owner/admin commands)."""
        state = self._panic_state.get(guild_id)
        if state:
            state["active"]       = False
            state["triggered_at"] = None
            state["raiders"]      = []

    # ── Utilities ──────────────────────────────────────────────

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
    await bot.add_cog(HeatBreach(bot))