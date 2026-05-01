"""
Heat filters — message analysis and heat assignment.

Each enabled filter analyses an incoming message and adds heat
to the sender via HeatEngine.add_heat(). After all filters run,
HeatState.check_breach() determines if the threshold was crossed.

Filter pipeline (in order):
    1. normal_message      — baseline heat per message
    2. similar_message     — repeated/duplicate content detection
    3. advertisement       — invite links / self-promotion
    4. nsfw_websites       — known NSFW domain list
    5. malicious_websites  — known malware / phishing domains
    6. emojis              — excessive emoji usage
    7. characters          — message length heat
    8. new_lines           — excessive line breaks
    9. mentions            — @user / @everyone / @here mentions
   10. attachments         — file attachments
   11. words_blacklist     — local words + global words + per-server remote words
   12. links_blacklist     — specific URL blacklist
"""

import logging
import re
import time
from collections import defaultdict

import discord
from discord.ext import commands

log = logging.getLogger("bot.heat_filters")

# ── Domain lists ───────────────────────────────────────────────

_NSFW_DOMAINS: frozenset[str] = frozenset({
    "pornhub.com", "xvideos.com", "xnxx.com", "redtube.com",
    "youporn.com", "tube8.com", "spankbang.com", "xhamster.com",
    "onlyfans.com", "fansly.com",
})

_MALICIOUS_DOMAINS: frozenset[str] = frozenset({
    "grabify.link", "iplogger.org", "2no.co", "yip.su",
    "blasze.com", "ps3cfw.com", "freegiftcards.co",
    "discordnitro.gift", "discord-nitro.gift", "dlscord.com",
    "steamcommunity.ru", "steampowered.ru", "csgofast.com",
    "free-robux.club", "roblox-promo.com",
})

# ── Advertisement patterns ─────────────────────────────────────

_INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/[a-zA-Z0-9\-]+",
    re.IGNORECASE,
)

_URL_RE = re.compile(
    r"https?://(?:[a-zA-Z0-9\-]+\.)+[a-zA-Z]{2,}(?:/[^\s]*)?",
    re.IGNORECASE,
)

_DOMAIN_RE = re.compile(
    r"(?:https?://)?(?:www\.)?([a-zA-Z0-9\-]+\.[a-zA-Z]{2,})(?:/|$|\s)",
    re.IGNORECASE,
)


class HeatFilters(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Per-guild per-user recent message cache for similarity detection
        # { guild_id: { user_id: [content_str, ...] } }
        self._recent: dict[int, dict[int, list[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._recent_max = 5   # messages to keep per user
        self._similar_threshold = 0.75  # Jaccard similarity threshold

    # ── Main entry point ───────────────────────────────────────

    async def process_message(self, message: discord.Message) -> None:
        """
        Run all enabled filters against a message.
        Called by Events.on_message and Events.on_message_edit.
        """
        if not message.guild:
            return
        if message.author.bot:
            return

        cfg      = self.bot.config.get(message.guild.id)
        heat_cfg = cfg.get("heat", {})

        if not heat_cfg.get("enabled", True):
            return

        # Webhooks
        if message.webhook_id and not heat_cfg.get("monitor_webhooks", True):
            return

        member = message.guild.get_member(message.author.id)
        if not member:
            return

        # Skip bot owner and guild owner
        if await self.bot.is_owner(member) or member.id == message.guild.owner_id:
            return

        filters  = heat_cfg.get("filters", {})
        total_heat = 0.0
        reason_parts: list[str] = []

        heat_engine = self.bot.get_cog("HeatEngine")
        if not heat_engine:
            return

        # ── 1. Normal message ──────────────────────────────────
        f = filters.get("normal_message", {})
        if f.get("enabled", True):
            amount = f.get("heat", 1.5)
            total_heat += amount

        # ── 2. Similar message ─────────────────────────────────
        f = filters.get("similar_message", {})
        if f.get("enabled", True) and message.content:
            if self._is_similar(message.guild.id, member.id, message.content):
                amount = f.get("heat", 10.0)
                total_heat += amount
                reason_parts.append("repeated message")

        # ── 3. Advertisement ───────────────────────────────────
        f = filters.get("advertisement", {})
        if f.get("enabled", True) and message.content:
            if _INVITE_RE.search(message.content):
                amount = f.get("heat", 20.0)
                total_heat += amount
                reason_parts.append("discord invite link")
                # Immediate action if configured
                action = f.get("action", "")
                if action == "timeout":
                    await self._immediate_action(member, action, "Advertisement detected")

        # ── 4. NSFW websites ───────────────────────────────────
        f = filters.get("nsfw_websites", {})
        if f.get("enabled", True) and message.content:
            if self._has_domain(message.content, _NSFW_DOMAINS):
                amount = f.get("heat", 30.0)
                total_heat += amount
                reason_parts.append("NSFW website link")

        # ── 5. Malicious websites ──────────────────────────────
        f = filters.get("malicious_websites", {})
        if f.get("enabled", True) and message.content:
            if self._has_domain(message.content, _MALICIOUS_DOMAINS):
                amount = f.get("heat", 80.0)
                total_heat += amount
                reason_parts.append("malicious/phishing link")

        # ── 6. Emojis ──────────────────────────────────────────
        f = filters.get("emojis", {})
        if f.get("enabled", True) and message.content:
            emoji_count = self._count_emojis(message.content)
            if emoji_count > 0:
                heat_per = f.get("heat_per_emoji", 2.0)
                amount   = emoji_count * heat_per
                total_heat += amount
                if emoji_count >= 5:
                    reason_parts.append(f"excessive emojis ({emoji_count})")

        # ── 7. Characters ──────────────────────────────────────
        f = filters.get("characters", {})
        if f.get("enabled", True) and message.content:
            char_count = len(message.content)
            if char_count > 100:
                heat_per_100 = f.get("heat_per_100_chars", 0.5)
                amount = (char_count / 100) * heat_per_100
                total_heat += amount

        # ── 8. New lines ───────────────────────────────────────
        f = filters.get("new_lines", {})
        if f.get("enabled", True) and message.content:
            line_count = message.content.count("\n")
            if line_count > 0:
                heat_per_line = f.get("heat_per_line", 1.0)
                amount = line_count * heat_per_line
                total_heat += amount
                if line_count >= 5:
                    reason_parts.append(f"excessive new lines ({line_count})")

        # ── 9. Mentions ────────────────────────────────────────
        f = filters.get("mentions", {})
        if f.get("enabled", True):
            mention_count = len(message.mentions) + len(message.role_mentions)
            has_everyone  = message.mention_everyone

            if mention_count > 0:
                heat_per = f.get("heat_per_mention", 8.0)
                amount   = mention_count * heat_per
                total_heat += amount
                reason_parts.append(f"mass mentions ({mention_count})")

            if has_everyone:
                multiplier = f.get("everyone_multiplier", 5)
                total_heat += heat_per * multiplier
                reason_parts.append("@everyone/@here mention")

        # ── 10. Attachments ────────────────────────────────────
        f = filters.get("attachments", {})
        if f.get("enabled", True) and message.attachments:
            amount = f.get("heat", 3.0) * len(message.attachments)
            total_heat += amount

        # ── 11. Words blacklist ────────────────────────────────
        f = filters.get("words_blacklist", {})
        if f.get("enabled", False) and message.content:
            hit = await self._check_words_blacklist(
                message.guild.id, message.content, f
            )
            if hit:
                amount = f.get("heat", 25.0)
                total_heat += amount
                reason_parts.append(f"blacklisted word: `{hit}`")

        # ── 12. Links blacklist ────────────────────────────────
        f = filters.get("links_blacklist", {})
        if f.get("enabled", False) and message.content:
            hit = self._check_links_blacklist(message.content, f.get("links", []))
            if hit:
                total_heat += 30.0
                reason_parts.append(f"blacklisted link: `{hit}`")

        # ── Apply total heat ───────────────────────────────────
        if total_heat > 0:
            new_heat = await heat_engine.add_heat(
                member,
                total_heat,
                source="message filters",
                reason=", ".join(reason_parts) if reason_parts else "normal message",
            )

            # Update recent message cache
            if message.content:
                self._update_recent(message.guild.id, member.id, message.content)

            # Breach check
            heat_state = self.bot.get_cog("HeatState")
            if heat_state:
                await heat_state.check_breach(
                    member,
                    new_heat,
                    reason=", ".join(reason_parts) if reason_parts else "accumulated heat",
                    channel=message.channel
                    if isinstance(message.channel, discord.TextChannel)
                    else None,
                )

    # ── Words blacklist (3-layer) ──────────────────────────────

    async def _check_words_blacklist(
        self,
        guild_id: int,
        content:  str,
        f_cfg:    dict,
    ) -> str | None:
        """
        Check content against three word layers in order:
        1. Server-local words  (config["heat"]["filters"]["words_blacklist"]["words"])
        2. Global words        (global_words DB table — bot-owner managed)
        3. Per-server remote   (remote_words DB table — Premium per-server URLs)

        Returns the matched word, or None if no match.
        """
        content_lower = content.lower()
        words_lower   = content_lower.split()

        def _match(word_list: list[str]) -> str | None:
            for w in word_list:
                w = w.lower().strip()
                if not w:
                    continue
                if w in content_lower:
                    return w
            return None

        # Layer 1: Local words
        local_words = f_cfg.get("words", [])
        hit = _match(local_words)
        if hit:
            return hit

        # Layer 2: Global words from DB
        try:
            rows = await self.bot.db.fetchall(
                "SELECT word FROM global_words"
            )
            global_words = [r["word"] for r in rows]
            hit = _match(global_words)
            if hit:
                return hit
        except Exception as exc:
            log.error("Failed to fetch global_words: %s", exc)

        # Layer 3: Per-server remote words (Premium)
        try:
            rows = await self.bot.db.fetchall(
                "SELECT word FROM remote_words WHERE guild_id = ?",
                (guild_id,),
            )
            remote_words = [r["word"] for r in rows]
            hit = _match(remote_words)
            if hit:
                return hit
        except Exception as exc:
            log.error("Failed to fetch remote_words: %s", exc)

        return None

    # ── Links blacklist ────────────────────────────────────────

    def _check_links_blacklist(
        self, content: str, blacklist: list[str]
    ) -> str | None:
        content_lower = content.lower()
        for entry in blacklist:
            entry = entry.lower().strip().rstrip("/")
            if entry and entry in content_lower:
                return entry
        return None

    # ── Similarity detection (Jaccard) ────────────────────────

    def _is_similar(
        self,
        guild_id: int,
        user_id:  int,
        content:  str,
    ) -> bool:
        recent = self._recent[guild_id][user_id]
        if not recent:
            return False
        tokens = set(content.lower().split())
        if not tokens:
            return False
        for prev in recent:
            prev_tokens = set(prev.lower().split())
            if not prev_tokens:
                continue
            intersection = tokens & prev_tokens
            union        = tokens | prev_tokens
            similarity   = len(intersection) / len(union) if union else 0.0
            if similarity >= self._similar_threshold:
                return True
        return False

    def _update_recent(
        self,
        guild_id: int,
        user_id:  int,
        content:  str,
    ) -> None:
        cache = self._recent[guild_id][user_id]
        cache.append(content)
        if len(cache) > self._recent_max:
            cache.pop(0)

    # ── Helpers ────────────────────────────────────────────────

    def _has_domain(self, content: str, domain_set: frozenset[str]) -> bool:
        """Check if any URL in content matches a domain in the set."""
        for match in _DOMAIN_RE.finditer(content):
            domain = match.group(1).lower()
            if domain in domain_set:
                return True
            # Check root domain (e.g., sub.pornhub.com → pornhub.com)
            parts = domain.split(".")
            if len(parts) >= 2:
                root = ".".join(parts[-2:])
                if root in domain_set:
                    return True
        return False

    def _count_emojis(self, content: str) -> int:
        """Count both Unicode emoji and custom Discord emoji in a string."""
        custom   = len(re.findall(r"<a?:\w+:\d+>", content))
        # Basic Unicode emoji range (common blocks)
        unicode_ = len(re.findall(
            r"[\U0001F600-\U0001F64F"
            r"\U0001F300-\U0001F5FF"
            r"\U0001F680-\U0001F6FF"
            r"\U0001F1E0-\U0001F1FF"
            r"\U00002702-\U000027B0"
            r"\U000024C2-\U0001F251]+",
            content
        ))
        return custom + unicode_

    async def _immediate_action(
        self,
        member: discord.Member,
        action: str,
        reason: str,
    ) -> None:
        """Apply an immediate action (timeout/kick/ban) regardless of heat level."""
        if action == "timeout":
            try:
                import datetime
                await member.timeout(
                    discord.utils.utcnow() + datetime.timedelta(minutes=5),
                    reason=f"[Heat Filter] {reason}"[:512],
                )
            except discord.Forbidden:
                pass
        elif action == "kick":
            try:
                await member.kick(reason=f"[Heat Filter] {reason}"[:512])
            except discord.Forbidden:
                pass
        elif action == "ban":
            try:
                await member.ban(reason=f"[Heat Filter] {reason}"[:512], delete_message_days=1)
            except discord.Forbidden:
                pass


async def setup(bot):
    await bot.add_cog(HeatFilters(bot))