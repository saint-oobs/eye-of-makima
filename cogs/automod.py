"""
AutoMod core — message content inspection pipeline.

Checks (in order, short-circuits on first match):
    1. exempt          — staff / permitted users / channels / roles
    2. invite_filter   — Discord invite links
    3. link_filter     — external URLs (whitelist-aware)
    4. word_filter     — banned words / phrases (local + global wordlist)
    5. spam_filter     — message rate (count/window)
    6. mention_spam    — @mention count per message
    7. emoji_spam      — emoji count per message
    8. caps_lock       — excessive uppercase
    9. duplicate       — repeated identical messages

Each check can:
    - delete the message
    - warn  (adds a strike via ModerationCore)
    - mute
    - kick
    - ban
    - log only

Actions are configured per-check in guild config under "automod".
"""

import logging
import re
import time
import unicodedata
from collections import defaultdict, deque

import discord
from discord.ext import commands

from utils.helpers import make_embed

log = logging.getLogger("bot.automod")

# ── Invite pattern ─────────────────────────────────────────────
_INVITE_RE = re.compile(
    r"(?:discord(?:app)?\.(?:gg|com/invite)|invite\.gg)/([a-zA-Z0-9\-]+)",
    re.IGNORECASE,
)

# ── URL pattern ────────────────────────────────────────────────
_URL_RE = re.compile(
    r"https?://[^\s/$.?#].[^\s]*",
    re.IGNORECASE,
)

# ── Emoji pattern ──────────────────────────────────────────────
_CUSTOM_EMOJI_RE = re.compile(r"<a?:[a-zA-Z0-9_]+:\d+>")


def _count_emojis(text: str) -> int:
    """Count both unicode and custom Discord emojis."""
    custom   = len(_CUSTOM_EMOJI_RE.findall(text))
    unicode_ = sum(
        1 for ch in text
        if unicodedata.category(ch) in ("So", "Sm")
        or "\U0001F000" <= ch <= "\U0001FFFF"
        or "\U00002600" <= ch <= "\U000027BF"
    )
    return custom + unicode_


def _pct_caps(text: str) -> float:
    """Return percentage of alphabetic characters that are uppercase."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return 0.0
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters)


class AutoMod(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Rate-limit tracking: { guild_id: { user_id: deque[timestamp] } }
        self._msg_log: dict[int, dict[int, deque]] = \
            defaultdict(lambda: defaultdict(deque))

        # Duplicate tracking: { guild_id: { user_id: [last_n_messages] } }
        self._recent_msgs: dict[int, dict[int, deque]] = \
            defaultdict(lambda: defaultdict(lambda: deque(maxlen=5)))

    # ── Main pipeline ──────────────────────────────────────────

    async def process_message(self, message: discord.Message) -> None:
        """
        Run the full automod pipeline on a message.
        Called from Events.on_message and on_message_edit.
        """
        if not message.guild:
            return
        if message.author.bot:
            return

        guild  = message.guild
        member = message.author
        cfg    = self.bot.config.get(guild.id)
        am     = cfg.get("automod", {})

        if not am.get("enabled", True):
            return

        # ── Exemption check ────────────────────────────────────
        if await self._is_exempt(message, am):
            return

        # ── Run checks in order ────────────────────────────────
        checks = [
            self._check_invites,
            self._check_links,
            self._check_words,
            self._check_spam,
            self._check_mention_spam,
            self._check_emoji_spam,
            self._check_caps,
            self._check_duplicate,
        ]

        for check in checks:
            triggered = await check(message, am, cfg)
            if triggered:
                return  # Short-circuit on first match

    # ── Exemption ──────────────────────────────────────────────

    async def _is_exempt(
        self,
        message: discord.Message,
        am:      dict,
    ) -> bool:
        """Return True if the author or channel is exempt from automod."""
        member  = message.author
        guild   = message.guild
        cfg     = self.bot.config.get(guild.id)

        # Bot owner / guild owner always exempt
        if member.id == guild.owner_id:
            return True
        if await self.bot.is_owner(member):
            return True

        # Permit level 3+ are exempt
        permit = await self._get_permit_level(member, cfg)
        if permit >= am.get("exempt_permit_level", 3):
            return True

        # Exempt channels
        exempt_channels = am.get("exempt_channels", [])
        if message.channel.id in exempt_channels:
            return True

        # Exempt roles
        exempt_roles = am.get("exempt_roles", [])
        member_role_ids = {r.id for r in member.roles}
        if member_role_ids & set(exempt_roles):
            return True

        return False

    # ── 1. Invite filter ───────────────────────────────────────

    async def _check_invites(
        self,
        message: discord.Message,
        am:      dict,
        cfg:     dict,
    ) -> bool:
        check = am.get("invite_filter", {})
        if not check.get("enabled", True):
            return False

        matches = _INVITE_RE.findall(message.content)
        if not matches:
            return False

        # Whitelist — allow invites to this server itself
        whitelist = check.get("whitelist_codes", [])
        if all(code in whitelist for code in matches):
            return False

        await self._enforce(
            message, cfg,
            action=check.get("action", "delete_warn"),
            reason="Discord invite link",
            check_name="invite_filter",
        )
        return True

    # ── 2. Link filter ─────────────────────────────────────────

    async def _check_links(
        self,
        message: discord.Message,
        am:      dict,
        cfg:     dict,
    ) -> bool:
        check = am.get("link_filter", {})
        if not check.get("enabled", False):
            return False

        urls = _URL_RE.findall(message.content)
        if not urls:
            return False

        whitelist  = check.get("whitelist_domains", [])
        mode       = check.get("mode", "blacklist")  # "whitelist" | "blacklist"
        bl_domains = check.get("blacklist_domains", [])

        for url in urls:
            # Extract domain
            domain_match = re.match(r"https?://([^/?\s]+)", url, re.IGNORECASE)
            if not domain_match:
                continue
            domain = domain_match.group(1).lower()

            if mode == "whitelist":
                # Block all links not in whitelist
                if not any(domain.endswith(w.lower()) for w in whitelist):
                    await self._enforce(
                        message, cfg,
                        action=check.get("action", "delete_warn"),
                        reason=f"Non-whitelisted link: `{domain}`",
                        check_name="link_filter",
                    )
                    return True

            elif mode == "blacklist":
                if any(domain.endswith(b.lower()) for b in bl_domains):
                    await self._enforce(
                        message, cfg,
                        action=check.get("action", "delete_warn"),
                        reason=f"Blacklisted domain: `{domain}`",
                        check_name="link_filter",
                    )
                    return True

        return False

    # ── 3. Word filter ─────────────────────────────────────────

    async def _check_words(
        self,
        message: discord.Message,
        am:      dict,
        cfg:     dict,
    ) -> bool:
        check = am.get("word_filter", {})
        if not check.get("enabled", True):
            return False

        content   = message.content.lower()
        words     = check.get("words", [])
        use_regex = check.get("use_regex", False)

        # Global wordlist from bot owner
        global_words = self.bot.config.global_wordlist()

        all_words = list(words) + list(global_words)

        for word in all_words:
            try:
                if use_regex:
                    if re.search(word, content, re.IGNORECASE):
                        matched = word
                        break
                else:
                    # Whole-word match
                    pattern = rf"\b{re.escape(word.lower())}\b"
                    if re.search(pattern, content):
                        matched = word
                        break
            except re.error:
                continue
        else:
            return False

        await self._enforce(
            message, cfg,
            action=check.get("action", "delete_warn"),
            reason=f"Banned word/phrase detected",
            check_name="word_filter",
        )
        return True

    # ── 4. Spam filter ─────────────────────────────────────────

    async def _check_spam(
        self,
        message: discord.Message,
        am:      dict,
        cfg:     dict,
    ) -> bool:
        check = am.get("spam_filter", {})
        if not check.get("enabled", True):
            return False

        limit  = check.get("message_count", 5)
        window = check.get("window_seconds", 5)
        now    = time.monotonic()

        guild_log  = self._msg_log[message.guild.id]
        user_deque = guild_log[message.author.id]

        user_deque.append(now)

        # Prune outside window
        while user_deque and user_deque[0] < now - window:
            user_deque.popleft()

        if len(user_deque) < limit:
            return False

        user_deque.clear()

        await self._enforce(
            message, cfg,
            action=check.get("action", "delete_mute"),
            reason=f"Spam: {limit} messages in {window}s",
            check_name="spam_filter",
        )
        return True

    # ── 5. Mention spam ────────────────────────────────────────

    async def _check_mention_spam(
        self,
        message: discord.Message,
        am:      dict,
        cfg:     dict,
    ) -> bool:
        check = am.get("mention_spam", {})
        if not check.get("enabled", True):
            return False

        limit = check.get("max_mentions", 5)
        count = (
            len(message.mentions)
            + len(message.role_mentions)
            + (1 if message.mention_everyone else 0)
        )

        if count < limit:
            return False

        await self._enforce(
            message, cfg,
            action=check.get("action", "delete_warn"),
            reason=f"Mention spam: {count} mentions",
            check_name="mention_spam",
        )
        return True

    # ── 6. Emoji spam ──────────────────────────────────────────

    async def _check_emoji_spam(
        self,
        message: discord.Message,
        am:      dict,
        cfg:     dict,
    ) -> bool:
        check = am.get("emoji_spam", {})
        if not check.get("enabled", False):
            return False

        limit = check.get("max_emojis", 10)
        count = _count_emojis(message.content)

        if count < limit:
            return False

        await self._enforce(
            message, cfg,
            action=check.get("action", "delete"),
            reason=f"Emoji spam: {count} emojis",
            check_name="emoji_spam",
        )
        return True

    # ── 7. Caps filter ─────────────────────────────────────────

    async def _check_caps(
        self,
        message: discord.Message,
        am:      dict,
        cfg:     dict,
    ) -> bool:
        check = am.get("caps_filter", {})
        if not check.get("enabled", False):
            return False

        threshold = check.get("threshold_pct", 70) / 100
        pct       = _pct_caps(message.content)

        if pct < threshold:
            return False

        await self._enforce(
            message, cfg,
            action=check.get("action", "delete"),
            reason=f"Excessive caps ({int(pct * 100)}%)",
            check_name="caps_filter",
        )
        return True

    # ── 8. Duplicate filter ────────────────────────────────────

    async def _check_duplicate(
        self,
        message: discord.Message,
        am:      dict,
        cfg:     dict,
    ) -> bool:
        check = am.get("duplicate_filter", {})
        if not check.get("enabled", False):
            return False

        limit   = check.get("max_duplicates", 3)
        content = message.content.strip().lower()

        history = self._recent_msgs[message.guild.id][message.author.id]
        count   = sum(1 for m in history if m == content)
        history.append(content)

        if count < limit - 1:
            return False

        await self._enforce(
            message, cfg,
            action=check.get("action", "delete_warn"),
            reason=f"Duplicate message sent {count + 1} times",
            check_name="duplicate_filter",
        )
        return True

    # ── Enforcement ────────────────────────────────────────────

    async def _enforce(
        self,
        message:    discord.Message,
        cfg:        dict,
        action:     str,
        reason:     str,
        check_name: str,
    ) -> None:
        """
        Apply the configured action to the message/author and log it.

        Action strings (compound, processed left to right):
            delete        — delete the message
            warn          — add a strike via ModerationCore
            mute          — timeout the member
            kick          — kick the member
            ban           — ban the member
            log           — log only, no deletion

        Compound examples: "delete_warn", "delete_mute", "delete"
        """
        member  = message.author
        guild   = message.guild
        parts   = [p.strip() for p in action.split("_")]

        deleted = False
        if "delete" in parts:
            try:
                await message.delete()
                deleted = True
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass

        if "warn" in parts:
            mod_cog = self.bot.get_cog("ModerationCore")
            if mod_cog:
                await mod_cog.add_strike(
                    guild=guild,
                    member=member,
                    reason=f"[AutoMod:{check_name}] {reason}",
                    moderator=guild.me,
                    silent=True,
                )

        if "mute" in parts:
            import datetime
            mute_dur = cfg.get("automod", {}).get(
                check_name, {}
            ).get("mute_duration_minutes", 5)
            try:
                await member.timeout(
                    discord.utils.utcnow() + datetime.timedelta(minutes=mute_dur),
                    reason=f"[AutoMod:{check_name}] {reason}",
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        if "kick" in parts:
            try:
                await guild.kick(
                    member,
                    reason=f"[AutoMod:{check_name}] {reason}",
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        if "ban" in parts:
            try:
                await guild.ban(
                    member,
                    reason=f"[AutoMod:{check_name}] {reason}",
                    delete_message_days=0,
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        await self._log_action(
            message=message,
            cfg=cfg,
            check_name=check_name,
            reason=reason,
            action=action,
            deleted=deleted,
        )

        log.info(
            "AutoMod [%s] %s — user=%s guild=%s | %s",
            check_name, action, member, guild.name, reason,
        )

    # ── Logging ────────────────────────────────────────────────

    async def _log_action(
        self,
        message:    discord.Message,
        cfg:        dict,
        check_name: str,
        reason:     str,
        action:     str,
        deleted:    bool,
    ) -> None:
        ch_id = cfg.get("log_channel")
        if not ch_id:
            return
        channel = message.guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        preview = (message.content or "*(empty)*")[:300]
        member  = message.author

        embed = make_embed(
            title=f"🤖 AutoMod — {check_name.replace('_', ' ').title()}",
            description=(
                f"{member.mention} (`{member.id}`) in "
                f"{message.channel.mention}"
            ),
            colour=discord.Colour.orange(),
            fields=[
                ("Reason",   reason,              False),
                ("Action",   f"`{action}`",        True),
                ("Deleted",  "✅" if deleted else "❌", True),
                ("Content",  f"```\n{preview}\n```", False),
            ],
            timestamp=True,
        )
        embed.set_thumbnail(url=(member.avatar or member.default_avatar).url)

        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error("AutoMod log failed: %s", exc)

    # ── Utilities ──────────────────────────────────────────────

    async def _get_permit_level(
        self,
        member: discord.Member,
        cfg:    dict,
    ) -> int:
        if member.id == member.guild.owner_id:
            return 5
        if member.id in cfg.get("extra_owners", []):
            return 4
        permit_roles = cfg.get("permit_roles", {})
        for level in range(3, 0, -1):
            role_ids = permit_roles.get(str(level), [])
            if any(r.id in role_ids for r in member.roles):
                return level
        return 0

    def clear_user_cache(self, guild_id: int, user_id: int) -> None:
        """Clear spam/duplicate tracking for a user (used by mod commands)."""
        self._msg_log.get(guild_id, {}).pop(user_id, None)
        self._recent_msgs.get(guild_id, {}).pop(user_id, None)


async def setup(bot):
    await bot.add_cog(AutoMod(bot))