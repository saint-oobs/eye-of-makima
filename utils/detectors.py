"""
Content detection utilities used by automod and heat scoring.

Provides regex-based detectors for:
    - Discord invite links
    - External URLs
    - Mention spam (users, roles, @everyone/@here)
    - Emoji spam
    - Repeated character sequences (zalgo / keyboard mash)
    - Caps ratio
    - Duplicate / repeated messages
    - Newline flood
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter


# ── Compiled patterns ──────────────────────────────────────────

_INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:discord\.(?:gg|io|me|li|com/invite)|discordapp\.com/invite)"
    r"/[\w-]{2,32}",
    re.IGNORECASE,
)

_URL_RE = re.compile(
    r"https?://"
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"
    r"localhost|\d{1,3}(?:\.\d{1,3}){3})"
    r"(?::\d+)?"
    r"(?:/?|[/?]\S+)",
    re.IGNORECASE,
)

_USER_MENTION_RE = re.compile(r"<@!?(\d{15,21})>")
_ROLE_MENTION_RE = re.compile(r"<@&(\d{15,21})>")
_EVERYONE_RE     = re.compile(r"@(?:everyone|here)")

_EMOJI_RE = re.compile(
    r"<a?:[a-zA-Z0-9_]{2,32}:\d{15,21}>"   # custom emoji
    r"|[\U0001F300-\U0001FFFF]"             # unicode emoji block
    r"|[\u2600-\u26FF\u2700-\u27BF]",       # misc symbols
)

_ZALGO_RE = re.compile(
    r"[\u0300-\u036F\u0489\u1DC0-\u1DFF\u20D0-\u20FF\uFE20-\uFE2F]{3,}"
)

_REPEATED_CHAR_RE = re.compile(r"(.)\1{4,}")  # 5+ of the same char in a row


# ── Invite detection ───────────────────────────────────────────

def has_invite(text: str) -> bool:
    """Return True if *text* contains a Discord invite link."""
    return bool(_INVITE_RE.search(text))


def extract_invites(text: str) -> list[str]:
    """Return all Discord invite URLs found in *text*."""
    return _INVITE_RE.findall(text)


# ── URL detection ──────────────────────────────────────────────

def has_url(text: str) -> bool:
    """Return True if *text* contains any HTTP/HTTPS URL."""
    return bool(_URL_RE.search(text))


def extract_urls(text: str) -> list[str]:
    """Return all URLs found in *text*."""
    return _URL_RE.findall(text)


def has_external_url(text: str) -> bool:
    """Return True if *text* contains a URL that is not a Discord invite."""
    return has_url(text) and not has_invite(text)


# ── Mention detection ──────────────────────────────────────────

def count_user_mentions(text: str) -> int:
    """Count ``<@id>`` / ``<@!id>`` mentions in *text*."""
    return len(_USER_MENTION_RE.findall(text))


def count_role_mentions(text: str) -> int:
    """Count ``<@&id>`` role mentions in *text*."""
    return len(_ROLE_MENTION_RE.findall(text))


def count_everyone_mentions(text: str) -> int:
    """Count ``@everyone`` / ``@here`` in *text*."""
    return len(_EVERYONE_RE.findall(text))


def count_total_mentions(text: str) -> int:
    """Total user + role + everyone/here mentions."""
    return (
        count_user_mentions(text)
        + count_role_mentions(text)
        + count_everyone_mentions(text)
    )


# ── Emoji detection ────────────────────────────────────────────

def count_emoji(text: str) -> int:
    """Count all emoji (custom and unicode) in *text*."""
    return len(_EMOJI_RE.findall(text))


# ── Caps detection ─────────────────────────────────────────────

def caps_ratio(text: str) -> float:
    """
    Return the fraction of alphabetic characters that are uppercase.
    Returns 0.0 if there are no alphabetic characters.
    """
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return 0.0
    return sum(1 for c in alpha if c.isupper()) / len(alpha)


def is_caps_spam(text: str, threshold: float = 0.7, min_length: int = 8) -> bool:
    """
    Return True if *text* is excessively capitalised.

    Only triggers if the message is at least *min_length* characters
    so short words like "OK" or "LOL" don't fire.
    """
    if len(text) < min_length:
        return False
    return caps_ratio(text) >= threshold


# ── Repeated character / zalgo ─────────────────────────────────

def has_repeated_chars(text: str) -> bool:
    """Return True if any character repeats 5+ times consecutively."""
    return bool(_REPEATED_CHAR_RE.search(text))


def has_zalgo(text: str) -> bool:
    """
    Return True if *text* contains zalgo-style combining character abuse
    (3+ stacked combining diacritics).
    """
    return bool(_ZALGO_RE.search(text))


# ── Newline flood ──────────────────────────────────────────────

def newline_count(text: str) -> int:
    """Return the number of newline characters in *text*."""
    return text.count("\n")


def is_newline_flood(text: str, threshold: int = 10) -> bool:
    """Return True if *text* has more than *threshold* newlines."""
    return newline_count(text) > threshold


# ── Duplicate message detection ────────────────────────────────

class DuplicateTracker:
    """
    Sliding-window duplicate message tracker per user.

    Stores the last *window* messages for each ``(guild_id, user_id)``
    pair and reports when the same content appears *threshold* or more
    times within that window.

    Usage:
        tracker = DuplicateTracker(window=5, threshold=3)

        # In on_message:
        if tracker.is_duplicate(guild_id, user_id, message.content):
            # user sent the same thing 3 times in the last 5 messages
    """

    def __init__(self, window: int = 5, threshold: int = 3) -> None:
        self._window    = window
        self._threshold = threshold
        # (guild_id, user_id) → list of last N message contents
        self._history: dict[tuple[int, int], list[str]] = {}

    def is_duplicate(
        self,
        guild_id: int,
        user_id:  int,
        content:  str,
    ) -> bool:
        """
        Record *content* and return True if it has been sent
        *threshold* or more times within the last *window* messages.
        """
        key     = (guild_id, user_id)
        history = self._history.setdefault(key, [])
        history.append(content.strip().lower())

        if len(history) > self._window:
            history.pop(0)

        counts = Counter(history)
        return counts[content.strip().lower()] >= self._threshold

    def clear_user(self, guild_id: int, user_id: int) -> None:
        """Reset history for a specific user."""
        self._history.pop((guild_id, user_id), None)

    def clear_guild(self, guild_id: int) -> None:
        """Reset history for all users in a guild."""
        keys = [k for k in self._history if k[0] == guild_id]
        for k in keys:
            del self._history[k]


# ── Convenience: full content scan ────────────────────────────

def scan(text: str) -> dict[str, int | float | bool]:
    """
    Run all detectors on *text* and return a summary dict.

    Useful for heat scoring — pass the result to your heat filter.

    Returns:
        {
            "has_invite":       bool,
            "has_url":          bool,
            "user_mentions":    int,
            "role_mentions":    int,
            "everyone":         int,
            "emoji_count":      int,
            "caps_ratio":       float,
            "is_caps_spam":     bool,
            "has_repeated":     bool,
            "has_zalgo":        bool,
            "newlines":         int,
            "is_newline_flood": bool,
        }
    """
    return {
        "has_invite":       has_invite(text),
        "has_url":          has_url(text),
        "user_mentions":    count_user_mentions(text),
        "role_mentions":    count_role_mentions(text),
        "everyone":         count_everyone_mentions(text),
        "emoji_count":      count_emoji(text),
        "caps_ratio":       round(caps_ratio(text), 3),
        "is_caps_spam":     is_caps_spam(text),
        "has_repeated":     has_repeated_chars(text),
        "has_zalgo":        has_zalgo(text),
        "newlines":         newline_count(text),
        "is_newline_flood": is_newline_flood(text),
    }