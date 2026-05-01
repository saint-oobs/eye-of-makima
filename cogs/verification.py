"""
Verification system — gate new members behind a challenge.

Modes:
    none      — No verification, assign verified role immediately on join
    captcha   — Bot DMs a text captcha; member replies in DM
    reaction  — Member reacts to a message in a verify channel
    web       — Member clicks a link, completes web challenge (Premium)

Flow:
    1. Member joins → assigned unverified_role (if set)
    2. Verification challenge is sent (mode-dependent)
    3. On success → verified_role granted, unverified_role removed
    4. On timeout → kick or keep (configurable)
    5. On failure (captcha wrong 3×) → kick or keep

Supports:
    - Custom verify channel
    - Custom verified / unverified roles
    - Kick on timeout
    - DM fallback
    - Re-verify command
"""

import asyncio
import logging
import random
import string
import time

import discord
from discord.ext import commands

from utils.helpers import make_embed

log = logging.getLogger("bot.verification")

# ── Captcha config ─────────────────────────────────────────────
_CAPTCHA_LENGTH   = 6
_CAPTCHA_CHARS    = string.ascii_uppercase + string.digits
_CAPTCHA_TIMEOUT  = 120   # seconds
_CAPTCHA_ATTEMPTS = 3

# ── Reaction config ────────────────────────────────────────────
_VERIFY_EMOJI = "✅"


class Verification(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # { guild_id: { user_id: { code, attempts, expires_at, msg_id } } }
        self._pending: dict[int, dict[int, dict]] = {}

        # { guild_id: verify_message_id }  (reaction mode)
        self._verify_messages: dict[int, int] = {}

    # ── Join entry point ───────────────────────────────────────

    async def process_join(self, member: discord.Member) -> None:
        """Called by Events.on_member_join after JoinGate passes."""
        guild = member.guild
        cfg   = self.bot.config.get(guild.id)
        vc    = cfg.get("verification", {})

        if not vc.get("enabled", False):
            return

        mode = vc.get("mode", "none")

        # Apply unverified role immediately
        unverified_role_id = vc.get("unverified_role")
        if unverified_role_id:
            ur = guild.get_role(unverified_role_id)
            if ur:
                try:
                    await member.add_roles(
                        ur, reason="[Verification] Awaiting verification"
                    )
                except discord.Forbidden:
                    pass

        if mode == "none":
            await self._grant_verified(member, cfg)
        elif mode == "captcha":
            await self._send_captcha(member, cfg)
        elif mode == "reaction":
            await self._prompt_reaction(member, cfg)
        elif mode == "web":
            await self._send_web_link(member, cfg)

    # ── mode: none ─────────────────────────────────────────────

    async def _grant_verified(
        self,
        member: discord.Member,
        cfg:    dict,
    ) -> None:
        """Immediately grant verified role with no challenge."""
        await self._apply_verified(member, cfg)

    # ── mode: captcha ──────────────────────────────────────────

    async def _send_captcha(
        self,
        member: discord.Member,
        cfg:    dict,
    ) -> None:
        """DM the member a random text captcha."""
        vc   = cfg.get("verification", {})
        code = self._generate_captcha()

        guild_pending = self._pending.setdefault(member.guild.id, {})
        guild_pending[member.id] = {
            "code":       code,
            "attempts":   0,
            "expires_at": time.monotonic() + _CAPTCHA_TIMEOUT,
            "mode":       "captcha",
        }

        embed = make_embed(
            title=f"✅ Verify in {member.guild.name}",
            description=(
                f"To gain access, type the code below **exactly** in this DM:\n\n"
                f"```\n{code}\n```\n"
                f"You have **{_CAPTCHA_TIMEOUT // 60} minutes** and "
                f"**{_CAPTCHA_ATTEMPTS} attempts**."
            ),
            colour=discord.Colour.blurple(),
            footer="Case-sensitive. Do not include spaces.",
        )

        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            # Can't DM — try verify channel
            await self._channel_captcha_fallback(member, cfg, code)
            return

        log.debug("Captcha sent to %s in %s", member, member.guild.name)

        # Schedule timeout
        asyncio.create_task(
            self._captcha_timeout(member, cfg),
            name=f"captcha-timeout-{member.guild.id}-{member.id}",
        )

    async def _channel_captcha_fallback(
        self,
        member: discord.Member,
        cfg:    dict,
        code:   str,
    ) -> None:
        """Send captcha in verify channel if DM failed."""
        vc      = cfg.get("verification", {})
        ch_id   = vc.get("verify_channel")
        channel = member.guild.get_channel(ch_id) if ch_id else None
        if not channel:
            return

        embed = make_embed(
            title=f"✅ {member.display_name} — Verify Here",
            description=(
                f"Type the code below to verify:\n```\n{code}\n```"
            ),
            colour=discord.Colour.blurple(),
        )
        try:
            msg = await channel.send(member.mention, embed=embed)
            self._pending[member.guild.id][member.id]["channel_msg_id"] = msg.id
            self._pending[member.guild.id][member.id]["channel_id"]     = channel.id
        except discord.HTTPException:
            pass

        asyncio.create_task(
            self._captcha_timeout(member, cfg),
            name=f"captcha-timeout-fallback-{member.guild.id}-{member.id}",
        )

    async def _captcha_timeout(
        self,
        member: discord.Member,
        cfg:    dict,
    ) -> None:
        """Kick or log member after captcha timeout."""
        await asyncio.sleep(_CAPTCHA_TIMEOUT)
        guild_pending = self._pending.get(member.guild.id, {})
        if member.id not in guild_pending:
            return  # Already verified

        guild_pending.pop(member.id, None)
        vc = cfg.get("verification", {})
        log.info("Captcha timeout for %s in %s", member, member.guild.name)

        if vc.get("kick_on_timeout", True):
            try:
                await member.send(embed=make_embed(
                    title="Verification Timed Out",
                    description=(
                        f"You did not verify in **{member.guild.name}** in time.\n"
                        "You may rejoin and try again."
                    ),
                    colour=discord.Colour.red(),
                ))
            except discord.Forbidden:
                pass
            try:
                await member.kick(reason="[Verification] Captcha timed out")
            except discord.Forbidden:
                pass
        else:
            await self._log_verify_event(
                member.guild, cfg, member,
                outcome="timeout", mode="captcha",
            )

    # ── DM response handler ────────────────────────────────────

    async def handle_dm_response(self, message: discord.Message) -> None:
        """
        Called from Events.on_message when a DM is received.
        Checks if the message is a captcha response.
        """
        user = message.author
        code = message.content.strip()

        for guild in self.bot.guilds:
            pending = self._pending.get(guild.id, {})
            if user.id not in pending:
                continue

            entry = pending[user.id]
            if entry.get("mode") != "captcha":
                continue

            if time.monotonic() > entry["expires_at"]:
                pending.pop(user.id, None)
                try:
                    await message.channel.send(embed=make_embed(
                        title="Code Expired",
                        description="Your verification code has expired. Please rejoin.",
                        colour=discord.Colour.red(),
                    ))
                except discord.Forbidden:
                    pass
                return

            entry["attempts"] += 1

            if code == entry["code"]:
                pending.pop(user.id, None)
                member = guild.get_member(user.id)
                if member:
                    cfg = self.bot.config.get(guild.id)
                    await self._apply_verified(member, cfg)
                    try:
                        await message.channel.send(embed=make_embed(
                            title="✅ Verified!",
                            description=f"You have been verified in **{guild.name}**.",
                            colour=discord.Colour.green(),
                        ))
                    except discord.Forbidden:
                        pass
            else:
                remaining = _CAPTCHA_ATTEMPTS - entry["attempts"]
                if remaining <= 0:
                    pending.pop(user.id, None)
                    try:
                        await message.channel.send(embed=make_embed(
                            title="Verification Failed",
                            description=(
                                "Too many incorrect attempts.\n"
                                "You may rejoin to try again."
                            ),
                            colour=discord.Colour.red(),
                        ))
                    except discord.Forbidden:
                        pass

                    member = guild.get_member(user.id)
                    if member:
                        cfg = self.bot.config.get(guild.id)
                        if cfg.get("verification", {}).get("kick_on_fail", True):
                            try:
                                await member.kick(
                                    reason="[Verification] Captcha failed"
                                )
                            except discord.Forbidden:
                                pass
                else:
                    try:
                        await message.channel.send(embed=make_embed(
                            title="❌ Incorrect Code",
                            description=(
                                f"That code is incorrect.\n"
                                f"**{remaining}** attempt(s) remaining."
                            ),
                            colour=discord.Colour.orange(),
                        ))
                    except discord.Forbidden:
                        pass
            return  # Only handle first matching guild

    # ── mode: reaction ─────────────────────────────────────────

    async def _prompt_reaction(
        self,
        member: discord.Member,
        cfg:    dict,
    ) -> None:
        """
        Point the member to the reaction verify channel.
        Ensures the verify message exists; creates it if not.
        """
        vc      = cfg.get("verification", {})
        ch_id   = vc.get("verify_channel")
        channel = member.guild.get_channel(ch_id) if ch_id else None

        if not channel:
            log.warning(
                "Reaction verify: no verify_channel set for guild %d",
                member.guild.id,
            )
            return

        await self._ensure_reaction_message(member.guild, cfg, channel)

        try:
            embed = make_embed(
                title=f"Welcome to {member.guild.name}!",
                description=(
                    f"To gain access, head to {channel.mention} "
                    f"and react with {_VERIFY_EMOJI}."
                ),
                colour=discord.Colour.blurple(),
            )
            await member.send(embed=embed)
        except discord.Forbidden:
            pass

    async def _ensure_reaction_message(
        self,
        guild:   discord.Guild,
        cfg:     dict,
        channel: discord.TextChannel,
    ) -> None:
        """Create or retrieve the standing reaction-verify message."""
        existing_id = self._verify_messages.get(guild.id)
        if existing_id:
            try:
                await channel.fetch_message(existing_id)
                return  # Already exists
            except (discord.NotFound, discord.HTTPException):
                pass  # Message was deleted — recreate

        vc  = cfg.get("verification", {})
        embed = make_embed(
            title="✅ Verify to Access the Server",
            description=(
                f"React with {_VERIFY_EMOJI} below to verify yourself\n"
                "and gain access to the server."
            ),
            colour=discord.Colour.blurple(),
            footer="You must react to gain access.",
        )
        try:
            msg = await channel.send(embed=embed)
            await msg.add_reaction(_VERIFY_EMOJI)
            self._verify_messages[guild.id] = msg.id
        except discord.HTTPException as exc:
            log.error("Could not create verify message: %s", exc)

    async def handle_reaction_add(
        self,
        payload: discord.RawReactionActionEvent,
    ) -> None:
        """
        Called from Events.on_raw_reaction_add.
        Checks if the reaction is on the verify message.
        """
        if payload.user_id == self.bot.user.id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        expected_msg_id = self._verify_messages.get(guild.id)
        if not expected_msg_id or payload.message_id != expected_msg_id:
            return

        if str(payload.emoji) != _VERIFY_EMOJI:
            return

        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        cfg = self.bot.config.get(guild.id)
        await self._apply_verified(member, cfg)
        log.info("Reaction verify: %s in %s", member, guild.name)

    # ── mode: web ──────────────────────────────────────────────

    async def _send_web_link(
        self,
        member: discord.Member,
        cfg:    dict,
    ) -> None:
        """Send the member a web verification link. (Premium)"""
        import os
        base_url = os.getenv("VERIFY_WEB_BASE_URL", "")
        if not base_url:
            log.warning("Web verify mode set but VERIFY_WEB_BASE_URL not configured")
            return

        token = self._generate_captcha(length=32)
        url   = f"{base_url}/verify?guild={member.guild.id}&user={member.id}&token={token}"

        # Store token for later validation (in-memory)
        self._pending.setdefault(member.guild.id, {})[member.id] = {
            "mode":       "web",
            "token":      token,
            "expires_at": time.monotonic() + 600,  # 10 minutes
        }

        embed = make_embed(
            title=f"✅ Verify in {member.guild.name}",
            description=(
                f"Click the link below to complete verification:\n\n"
                f"[**Verify Here**]({url})\n\n"
                f"Link expires in **10 minutes**."
            ),
            colour=discord.Colour.blurple(),
        )
        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            log.warning("Cannot DM web verify link to %s", member)

    async def handle_web_callback(
        self,
        guild_id: int,
        user_id:  int,
        token:    str,
    ) -> bool:
        """
        Called by the web server when a member completes web verification.
        Returns True on success.
        """
        pending = self._pending.get(guild_id, {}).get(user_id)
        if not pending or pending.get("mode") != "web":
            return False
        if pending.get("token") != token:
            return False
        if time.monotonic() > pending.get("expires_at", 0):
            self._pending.get(guild_id, {}).pop(user_id, None)
            return False

        self._pending.get(guild_id, {}).pop(user_id, None)
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return False
        member = guild.get_member(user_id)
        if not member:
            return False

        cfg = self.bot.config.get(guild_id)
        await self._apply_verified(member, cfg)
        return True

    # ── Applying verified state ────────────────────────────────

    async def _apply_verified(
        self,
        member: discord.Member,
        cfg:    dict,
    ) -> None:
        """Grant verified role, remove unverified role, log the event."""
        guild = member.guild
        vc    = cfg.get("verification", {})

        roles_to_add    = []
        roles_to_remove = []

        verified_id = vc.get("verified_role")
        if verified_id:
            vr = guild.get_role(verified_id)
            if vr:
                roles_to_add.append(vr)

        unverified_id = vc.get("unverified_role")
        if unverified_id:
            ur = guild.get_role(unverified_id)
            if ur and ur in member.roles:
                roles_to_remove.append(ur)

        try:
            if roles_to_add:
                await member.add_roles(
                    *roles_to_add,
                    reason="[Verification] Member verified",
                )
            if roles_to_remove:
                await member.remove_roles(
                    *roles_to_remove,
                    reason="[Verification] Unverified role removed",
                )
        except discord.Forbidden:
            log.warning("Cannot apply verified roles to %s", member)
            return
        except discord.HTTPException as exc:
            log.error("Role update error for %s: %s", member, exc)
            return

        await self._log_verify_event(guild, cfg, member, outcome="verified",
                                     mode=vc.get("mode", "none"))
        log.info("Verified: %s in %s", member, guild.name)

    # ── Logging ────────────────────────────────────────────────

    async def _log_verify_event(
        self,
        guild:   discord.Guild,
        cfg:     dict,
        member:  discord.Member,
        outcome: str,
        mode:    str,
    ) -> None:
        ch_id = cfg.get("log_channel")
        if not ch_id:
            return
        channel = guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        colour = {
            "verified": discord.Colour.green(),
            "timeout":  discord.Colour.orange(),
            "failed":   discord.Colour.red(),
        }.get(outcome, discord.Colour.blurple())

        embed = make_embed(
            title=f"🔐 Verification — {outcome.title()}",
            description=f"{member.mention} (`{member.id}`)",
            colour=colour,
            fields=[
                ("Mode",    f"`{mode}`",    True),
                ("Outcome", f"`{outcome}`", True),
            ],
            timestamp=True,
        )
        embed.set_thumbnail(url=(member.avatar or member.default_avatar).url)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _generate_captcha(length: int = _CAPTCHA_LENGTH) -> str:
        return "".join(random.choices(_CAPTCHA_CHARS, k=length))

    def is_pending(self, guild_id: int, user_id: int) -> bool:
        return user_id in self._pending.get(guild_id, {})

    def force_verify(self, guild_id: int, user_id: int) -> None:
        """Remove a member from pending (used by manual verify command)."""
        self._pending.get(guild_id, {}).pop(user_id, None)

    def get_verify_message_id(self, guild_id: int) -> int | None:
        return self._verify_messages.get(guild_id)

    def set_verify_message_id(self, guild_id: int, msg_id: int) -> None:
        self._verify_messages[guild_id] = msg_id


async def setup(bot):
    await bot.add_cog(Verification(bot))