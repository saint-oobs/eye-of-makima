"""
Join Gate — filters members and bots on join.

Checks (in order):
    1. no_pfp              — no custom avatar
    2. account_age         — account too new
    3. bot_addition        — unauthorised bot added
    4. unverified_bots     — bot is not Discord-verified
    5. advertising_names   — username contains invite/promo patterns
    6. username_filter     — custom regex/keyword username filter
    7. suspicious          — heuristic suspicion score

Each check can independently:
    - kick
    - ban
    - quarantine
    - log (no action, just alert)
"""

import logging
import re

import discord
from discord.ext import commands

from utils.helpers import make_embed, account_age_days, has_default_avatar

log = logging.getLogger("bot.joingate")

# ── Advertising patterns ───────────────────────────────────────
_AD_PATTERNS: list[re.Pattern] = [
    re.compile(r"discord\.gg/",           re.IGNORECASE),
    re.compile(r"discord(?:app)?\.com/invite/", re.IGNORECASE),
    re.compile(r"\.gg/[a-zA-Z0-9]+",      re.IGNORECASE),
    re.compile(r"free\s*nitro",           re.IGNORECASE),
    re.compile(r"free\s*robux",           re.IGNORECASE),
    re.compile(r"bit\.ly/",               re.IGNORECASE),
    re.compile(r"t\.me/",                 re.IGNORECASE),
]

# ── Suspicion heuristics ───────────────────────────────────────
_SUSPICIOUS_NAME_PATTERNS: list[re.Pattern] = [
    re.compile(r"^[A-Z][a-z]+\d{4,}$"),      # RandomName1234
    re.compile(r"^user\d{6,}$",  re.IGNORECASE),
    re.compile(r"^[a-z]{2,5}\d{5,}$"),        # ab12345
    re.compile(r"discord.*bot",  re.IGNORECASE),
    re.compile(r"mod.*official", re.IGNORECASE),
    re.compile(r"admin.*official", re.IGNORECASE),
]


class JoinGate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Main entry points ──────────────────────────────────────

    async def process_join(self, member: discord.Member) -> None:
        """Run all join gate checks for a human member."""
        cfg     = self.bot.config.get(member.guild.id)
        jg      = cfg.get("joingate", {})

        checks = [
            self._check_no_pfp,
            self._check_account_age,
            self._check_advertising_name,
            self._check_username_filter,
            self._check_suspicious,
        ]

        for check in checks:
            acted = await check(member, jg, cfg)
            if acted:
                return  # Stop after first triggered check

    async def process_bot_join(self, bot_member: discord.Member) -> None:
        """Run join gate checks for a bot member."""
        cfg = self.bot.config.get(bot_member.guild.id)
        jg  = cfg.get("joingate", {})

        # Check 1: bot_addition — is this bot authorised?
        await self._check_bot_addition(bot_member, jg, cfg)

        # Check 2: unverified_bots
        await self._check_unverified_bot(bot_member, jg, cfg)

        # Check 3: dangerous roles (forward to guards)
        guards = self.bot.get_cog("AntiNukeGuards")
        if guards:
            await guards.on_bot_dangerous_role(
                bot_member.guild,
                bot_member,
                bot_member.roles,
            )

    # ── Individual checks ──────────────────────────────────────

    async def _check_no_pfp(
        self,
        member: discord.Member,
        jg:     dict,
        cfg:    dict,
    ) -> bool:
        check = jg.get("no_pfp", {})
        if not check.get("enabled", False):
            return False
        if not has_default_avatar(member):
            return False

        return await self._enforce(
            member, cfg,
            action=check.get("action", "kick"),
            reason="No profile picture",
            check_name="no_pfp",
        )

    async def _check_account_age(
        self,
        member: discord.Member,
        jg:     dict,
        cfg:    dict,
    ) -> bool:
        check    = jg.get("account_age", {})
        if not check.get("enabled", False):
            return False

        min_days = check.get("min_days", 7)
        age      = account_age_days(member)

        if age >= min_days:
            return False

        reason = f"Account too new ({age} days old, minimum {min_days})"
        if check.get("expose_min", True):
            reason = f"Account created {age} day(s) ago (minimum {min_days})"

        return await self._enforce(
            member, cfg,
            action=check.get("action", "kick"),
            reason=reason,
            check_name="account_age",
        )

    async def _check_advertising_name(
        self,
        member: discord.Member,
        jg:     dict,
        cfg:    dict,
    ) -> bool:
        check = jg.get("advertising_names", {})
        if not check.get("enabled", True):
            return False

        name = member.name + " " + (member.display_name or "")
        for pattern in _AD_PATTERNS:
            if pattern.search(name):
                return await self._enforce(
                    member, cfg,
                    action=check.get("action", "kick"),
                    reason=f"Advertising username detected: `{member.name}`",
                    check_name="advertising_names",
                )
        return False

    async def _check_username_filter(
        self,
        member: discord.Member,
        jg:     dict,
        cfg:    dict,
    ) -> bool:
        check    = jg.get("username_filter", {})
        if not check.get("enabled", False):
            return False

        patterns = check.get("patterns", [])
        name     = member.name.lower()

        for pattern in patterns:
            try:
                if re.search(pattern, name, re.IGNORECASE):
                    return await self._enforce(
                        member, cfg,
                        action=check.get("action", "kick"),
                        reason=f"Username matched filter pattern: `{pattern}`",
                        check_name="username_filter",
                    )
            except re.error:
                # Invalid regex in config — skip silently
                log.warning("Invalid username filter pattern: %s", pattern)

        return False

    async def _check_suspicious(
        self,
        member: discord.Member,
        jg:     dict,
        cfg:    dict,
    ) -> bool:
        check = jg.get("suspicious", {})
        if not check.get("enabled", False):
            return False

        score  = self._suspicion_score(member)
        if score < 3:
            return False

        return await self._enforce(
            member, cfg,
            action=check.get("action", "log"),
            reason=f"Suspicious account (score: {score}/5)",
            check_name="suspicious",
        )

    async def _check_bot_addition(
        self,
        bot_member: discord.Member,
        jg:         dict,
        cfg:        dict,
    ) -> bool:
        check = jg.get("bot_addition", {})
        if not check.get("enabled", True):
            return False

        # Check if any extra_owner or server owner authorised this bot
        # We do this by checking the audit log for who added it
        executor_id = await self._find_bot_adder(bot_member.guild, bot_member.id)
        if executor_id is None:
            return False

        # Permit level 4+ can add bots
        if executor_id == bot_member.guild.owner_id:
            return False
        if executor_id in cfg.get("extra_owners", []):
            return False

        # Log the addition regardless
        await self._log_join_action(
            bot_member, cfg,
            action="logged",
            reason=f"Bot added by non-owner <@{executor_id}>",
            check_name="bot_addition",
            colour=discord.Colour.orange(),
        )

        action = check.get("action", "kick")
        if action in ("kick", "ban"):
            return await self._enforce(
                bot_member, cfg,
                action=action,
                reason=f"Unauthorised bot addition by user {executor_id}",
                check_name="bot_addition",
            )
        return False

    async def _check_unverified_bot(
        self,
        bot_member: discord.Member,
        jg:         dict,
        cfg:        dict,
    ) -> bool:
        check = jg.get("unverified_bots", {})
        if not check.get("enabled", True):
            return False

        # discord.py exposes public_flags on User
        user = bot_member._user if hasattr(bot_member, "_user") else bot_member
        flags = getattr(user, "public_flags", None)
        if flags is None:
            return False

        is_verified = getattr(flags, "verified_bot", False)
        if is_verified:
            return False  # Verified bots are fine

        return await self._enforce(
            bot_member, cfg,
            action=check.get("action", "kick"),
            reason="Unverified bot (not Discord-verified)",
            check_name="unverified_bots",
        )

    # ── Enforcement ────────────────────────────────────────────

    async def _enforce(
        self,
        member:     discord.Member,
        cfg:        dict,
        action:     str,
        reason:     str,
        check_name: str,
    ) -> bool:
        """
        Execute the configured action and log it.
        Returns True if a non-log action was taken.
        """
        guild  = member.guild
        colour = {
            "kick":       discord.Colour.orange(),
            "ban":        discord.Colour.red(),
            "quarantine": discord.Colour.gold(),
            "log":        discord.Colour.blurple(),
        }.get(action, discord.Colour.blurple())

        # DM the member before action (if enabled)
        if cfg.get("misc", {}).get("dm_targets", True) and action != "log":
            await self._dm_member(member, action, reason)

        acted = True
        if action == "kick":
            try:
                await member.kick(reason=f"[JoinGate:{check_name}] {reason}"[:512])
            except discord.Forbidden:
                log.warning("Cannot kick %s — missing permissions", member)
                acted = False
        elif action == "ban":
            try:
                await guild.ban(
                    member,
                    reason=f"[JoinGate:{check_name}] {reason}"[:512],
                    delete_message_days=0,
                )
            except discord.Forbidden:
                log.warning("Cannot ban %s — missing permissions", member)
                acted = False
        elif action == "quarantine":
            qr_id = cfg.get("quarantine_role")
            qr    = guild.get_role(qr_id) if qr_id else None
            if qr:
                try:
                    await member.edit(
                        roles=[qr],
                        reason=f"[JoinGate:{check_name}] {reason}"[:512],
                    )
                    cfg.setdefault("_quarantined", []).append(member.id)
                    self.bot.config.save(guild.id)
                except discord.Forbidden:
                    log.warning("Cannot quarantine %s — missing permissions", member)
                    acted = False
            else:
                log.warning("Quarantine action but no quarantine_role set in guild %d", guild.id)
                acted = False
        else:
            acted = False  # "log" — no action

        await self._log_join_action(
            member, cfg,
            action=action if acted else "log_only",
            reason=reason,
            check_name=check_name,
            colour=colour,
        )

        log.info(
            "JoinGate [%s] %s — user=%s guild=%s",
            check_name, action, member, guild.name,
        )
        return acted or action == "log"

    # ── Logging ────────────────────────────────────────────────

    async def _log_join_action(
        self,
        member:     discord.Member,
        cfg:        dict,
        action:     str,
        reason:     str,
        check_name: str,
        colour:     discord.Colour,
    ) -> None:
        ch_id = cfg.get("log_channel")
        if not ch_id:
            return
        channel = member.guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        age = account_age_days(member)
        embed = make_embed(
            title=f"🚪 JoinGate — {check_name.replace('_', ' ').title()}",
            description=f"**{member}** (`{member.id}`) — action: **{action}**",
            colour=colour,
            fields=[
                ("Reason",       reason,                                      False),
                ("Account Age",  f"`{age} days`",                            True),
                ("Has Avatar",   "✅" if member.avatar else "❌",             True),
                ("Bot",          "✅" if member.bot else "❌",                True),
                ("User ID",      f"`{member.id}`",                           True),
            ],
            timestamp=True,
        )
        embed.set_thumbnail(url=(member.avatar or member.default_avatar).url)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error("JoinGate log failed: %s", exc)

    # ── Helpers ────────────────────────────────────────────────

    def _suspicion_score(self, member: discord.Member) -> int:
        """
        Calculate a heuristic suspicion score (0–5).
        Higher = more suspicious.
        """
        score = 0
        age   = account_age_days(member)

        if age < 1:
            score += 2
        elif age < 7:
            score += 1

        if has_default_avatar(member):
            score += 1

        for pattern in _SUSPICIOUS_NAME_PATTERNS:
            if pattern.search(member.name):
                score += 1
                break

        return score

    async def _find_bot_adder(
        self,
        guild:  discord.Guild,
        bot_id: int,
    ) -> int | None:
        """Scan audit log for who added this bot."""
        try:
            async for entry in guild.audit_logs(
                action=discord.AuditLogAction.bot_add, limit=5
            ):
                target = getattr(entry, "target", None)
                if target and getattr(target, "id", None) == bot_id:
                    return entry.user_id
        except (discord.Forbidden, discord.HTTPException):
            pass
        return None

    async def _dm_member(
        self,
        member: discord.Member,
        action: str,
        reason: str,
    ) -> None:
        """Attempt to DM the member before applying an action."""
        try:
            embed = make_embed(
                title=f"You were {action}ed from {member.guild.name}",
                description=f"**Reason:** {reason}",
                colour=discord.Colour.orange(),
            )
            await member.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot):
    await bot.add_cog(JoinGate(bot))