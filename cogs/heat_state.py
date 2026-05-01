"""
Heat state helpers — breach detection and action dispatch.

Responsibilities:
- Check if a user's heat has crossed the max_heat threshold
- Determine the correct action (timeout vs lockdown vs panic)
- Dispatch the action and send the log embed
- Auto-lockdown trigger when mention threshold is breached
- Inactivity heat (fires when a previously active user goes quiet)
"""

import logging

import discord
from discord.ext import commands

from utils.helpers import make_embed

log = logging.getLogger("bot.heat_state")


class HeatState(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Breach check ───────────────────────────────────────────

    async def check_breach(
        self,
        member:  discord.Member,
        heat:    float,
        *,
        reason:  str = "",
        channel: discord.TextChannel | None = None,
    ) -> bool:
        """
        Check if heat has crossed max_heat and dispatch action if so.

        Returns True if a breach action was taken, False otherwise.
        """
        cfg      = self.bot.config.get(member.guild.id)
        heat_cfg = cfg.get("heat", {})

        if not heat_cfg.get("enabled", True):
            return False

        max_heat = heat_cfg.get("max_heat", 85.0)
        if heat < max_heat:
            return False

        heat_cog = self.bot.get_cog("HeatEngine")
        if not heat_cog:
            return False

        log.info(
            "Heat breach: guild=%d user=%d heat=%.1f max=%.1f | %s",
            member.guild.id, member.id, heat, max_heat, reason,
        )

        # Check panic mode first
        panic_cfg = heat_cfg.get("panic_mode", {})
        if panic_cfg.get("enabled"):
            breach_cog = self.bot.get_cog("HeatBreach")
            if breach_cog:
                await breach_cog.handle_panic_breach(member, heat, reason=reason)
            return True

        # Standard strike / timeout flow
        acted = await heat_cog.apply_strike(member, reason=reason or "Heat threshold exceeded")

        if acted:
            await self._log_breach(member, heat, max_heat, reason=reason, channel=channel)

            # Check auto-lockdown
            await self._check_auto_lockdown(member.guild, heat_cfg)

        return acted

    # ── Auto-lockdown ──────────────────────────────────────────

    async def _check_auto_lockdown(
        self,
        guild:    discord.Guild,
        heat_cfg: dict,
    ) -> None:
        """
        If auto_lockdown is enabled, check if the mention threshold
        has been breached recently and lock the server down.
        """
        lockdown_cfg = heat_cfg.get("auto_lockdown", {})
        if not lockdown_cfg.get("enabled"):
            return

        threshold = lockdown_cfg.get("mention_threshold", 5)
        heat_cog  = self.bot.get_cog("HeatEngine")
        if not heat_cog:
            return

        # Count users currently above 50% heat as a proxy for active threat
        leaderboard  = heat_cog.get_leaderboard(guild.id, limit=50)
        max_heat     = heat_cfg.get("max_heat", 85.0)
        hot_users    = [u for u in leaderboard if u[1] >= max_heat * 0.5]

        if len(hot_users) >= threshold:
            log.warning(
                "Auto-lockdown triggered: guild=%d hot_users=%d threshold=%d",
                guild.id, len(hot_users), threshold,
            )
            await self._execute_lockdown(guild)

    async def _execute_lockdown(self, guild: discord.Guild) -> None:
        """Lock all text channels by removing Send Messages from @everyone."""
        cfg         = self.bot.config.get(guild.id)
        log_channel = await self._get_log_channel(guild, cfg)
        everyone    = guild.default_role
        locked      = 0

        for channel in guild.text_channels:
            perms = channel.overwrites_for(everyone)
            if perms.send_messages is not False:
                perms.send_messages = False
                try:
                    await channel.set_permissions(
                        everyone,
                        overwrite=perms,
                        reason="[Heat] Auto-lockdown triggered",
                    )
                    locked += 1
                except discord.Forbidden:
                    pass

        if log_channel:
            embed = make_embed(
                title="🔒 Auto-Lockdown Activated",
                description=(
                    f"Heat threshold breach triggered an automatic server lockdown.\n"
                    f"**{locked}** channels locked.\n\n"
                    f"Use `{self.bot.command_prefix}heat unlock` to restore access."
                ),
                colour=discord.Colour.red(),
                timestamp=True,
            )
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                pass

    # ── Breach log ─────────────────────────────────────────────

    async def _log_breach(
        self,
        member:   discord.Member,
        heat:     float,
        max_heat: float,
        *,
        reason:   str = "",
        channel:  discord.TextChannel | None = None,
    ) -> None:
        """Send a breach notification to the log channel."""
        cfg         = self.bot.config.get(member.guild.id)
        log_channel = channel or await self._get_log_channel(member.guild, cfg)

        if not log_channel:
            return

        heat_cog = self.bot.get_cog("HeatEngine")
        strikes  = heat_cog.get_strikes(member.guild.id, member.id) if heat_cog else 0
        cap      = cfg.get("heat", {}).get("strikes_cap", 3)

        bar       = self._heat_bar(heat, max_heat)
        colour    = discord.Colour.red() if heat >= max_heat else discord.Colour.orange()

        embed = make_embed(
            title="🌡️ Heat Breach",
            description=(
                f"**{member.mention}** (`{member}`) exceeded the heat threshold.\n"
                f"{bar} `{heat:.1f}/{max_heat:.1f}`"
            ),
            colour=colour,
            fields=[
                ("Strikes",  f"`{strikes}/{cap}`",          True),
                ("Reason",   reason or "*(not specified)*", True),
                ("User ID",  f"`{member.id}`",              True),
            ],
            timestamp=True,
        )
        embed.set_thumbnail(url=(member.avatar or member.default_avatar).url)

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error("Failed to send breach log: %s", exc)

    # ── Inactivity heat ────────────────────────────────────────

    async def apply_inactivity_heat(
        self,
        member:  discord.Member,
        channel: discord.TextChannel,
    ) -> None:
        """
        Apply inactivity heat when a previously active user suddenly
        goes quiet after high heat.

        Called by HeatFilters when a user's heat is non-zero but
        they haven't sent a message for `inactivity_window` seconds.
        """
        cfg      = self.bot.config.get(member.guild.id)
        heat_cfg = cfg.get("heat", {})
        filter_  = heat_cfg.get("filters", {}).get("inactivity", {})

        if not filter_.get("enabled"):
            return

        heat_cog = self.bot.get_cog("HeatEngine")
        if not heat_cog:
            return

        amount   = filter_.get("heat", 5.0)
        new_heat = await heat_cog.add_heat(
            member, amount, source="inactivity"
        )
        await self.check_breach(
            member, new_heat,
            reason="Inactivity after active heat period",
            channel=channel,
        )

    # ── Utilities ──────────────────────────────────────────────

    def _heat_bar(self, heat: float, max_heat: float, width: int = 10) -> str:
        """Return a Unicode progress bar representing heat / max_heat."""
        ratio    = min(heat / max_heat, 1.0) if max_heat > 0 else 0.0
        filled   = round(ratio * width)
        empty    = width - filled
        bar      = "█" * filled + "░" * empty
        pct      = int(ratio * 100)
        return f"`[{bar}]` {pct}%"

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

    # ── Public helpers (used by heat_commands) ─────────────────

    def heat_bar(self, heat: float, max_heat: float, width: int = 10) -> str:
        return self._heat_bar(heat, max_heat, width)


async def setup(bot):
    await bot.add_cog(HeatState(bot))