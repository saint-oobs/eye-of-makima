import re

import discord
from discord.ext import commands

from utils.helpers import parse_duration


# ── Duration converter ─────────────────────────────────────────

class DurationConverter(commands.Converter):
    """
    Converts a duration string like "5m", "1h30m", "2d" into seconds (int).

    Usage in commands:
        async def timeout(self, ctx, member, duration: DurationConverter): ...

    Raises:
        commands.BadArgument — if the string cannot be parsed.
    """

    async def convert(self, ctx: commands.Context, value: str) -> int:
        seconds = parse_duration(value)
        if seconds is None or seconds <= 0:
            raise commands.BadArgument(
                f"`{value}` is not a valid duration. "
                f"Use formats like `30s`, `5m`, `2h`, `1d`, `1w`, or combined: `1h30m`."
            )
        return seconds


# ── Member or ID converter ─────────────────────────────────────

class MemberOrID(commands.Converter):
    """
    Resolves a Discord member by mention, username, display name, or raw ID.

    Falls back to a plain int (user ID) if the member is not in the server —
    useful for ban/history commands targeting users who already left.

    Returns:
        discord.Member  — if found in the guild
        int             — if a valid snowflake but not in guild
    """

    async def convert(
        self, ctx: commands.Context, value: str
    ) -> discord.Member | int:
        # 1. Try built-in MemberConverter first (handles mentions + cache)
        try:
            return await commands.MemberConverter().convert(ctx, value)
        except commands.MemberNotFound:
            pass

        # 2. Try raw integer ID
        value = value.strip().lstrip("@")
        if value.isdigit():
            member_id = int(value)
            # Try fetching from guild (may hit API)
            member = ctx.guild.get_member(member_id)
            if member:
                return member
            # Not in guild — return bare ID for cross-guild ops (ban by ID etc.)
            return member_id

        raise commands.BadArgument(
            f"Could not find member `{value}`. "
            f"Use a mention, username, or user ID."
        )


# ── Role converter (case-insensitive) ─────────────────────────

class CaseInsensitiveRole(commands.Converter):
    """
    Resolves a role by mention, ID, or name (case-insensitive).

    Raises:
        commands.RoleNotFound — if no role matches.
    """

    async def convert(self, ctx: commands.Context, value: str) -> discord.Role:
        # 1. Standard converter handles mentions and IDs
        try:
            return await commands.RoleConverter().convert(ctx, value)
        except commands.RoleNotFound:
            pass

        # 2. Case-insensitive name match
        value_lower = value.strip().lower()
        for role in ctx.guild.roles:
            if role.name.lower() == value_lower:
                return role

        # 3. Partial name match (prefix)
        matches = [r for r in ctx.guild.roles if r.name.lower().startswith(value_lower)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(f"`{r.name}`" for r in matches[:5])
            raise commands.RoleNotFound(
                f"Multiple roles match `{value}`: {names}. Be more specific."
            )

        raise commands.RoleNotFound(value)


# ── Channel converter (text channels only) ────────────────────

class TextChannelOrID(commands.Converter):
    """
    Resolves a text channel by mention, ID, or name.
    Returns discord.TextChannel or raises BadArgument.
    """

    async def convert(self, ctx: commands.Context, value: str) -> discord.TextChannel:
        try:
            return await commands.TextChannelConverter().convert(ctx, value)
        except commands.ChannelNotFound:
            pass

        value_lower = value.strip().lstrip("#").lower()
        for ch in ctx.guild.text_channels:
            if ch.name.lower() == value_lower:
                return ch

        raise commands.BadArgument(
            f"Could not find text channel `{value}`."
        )


# ── Bounded integer ────────────────────────────────────────────

def bounded_int(min_val: int, max_val: int):
    """
    Returns a converter that parses an integer within [min_val, max_val].

    Usage:
        async def set_limit(self, ctx, count: bounded_int(1, 100)): ...
    """

    class BoundedIntConverter(commands.Converter):
        async def convert(self, ctx: commands.Context, value: str) -> int:
            if not value.lstrip("-").isdigit():
                raise commands.BadArgument(
                    f"`{value}` is not a valid integer."
                )
            n = int(value)
            if not (min_val <= n <= max_val):
                raise commands.BadArgument(
                    f"`{n}` is out of range. Must be between {min_val} and {max_val}."
                )
            return n

    BoundedIntConverter.__name__ = f"BoundedInt({min_val}-{max_val})"
    return BoundedIntConverter


# ── URL validator ──────────────────────────────────────────────

_URL_RE = re.compile(
    r"^https?://"                       # scheme
    r"(?:[a-zA-Z0-9\-]+\.)+[a-zA-Z]{2,}"  # domain
    r"(?::\d+)?"                        # optional port
    r"(?:/[^\s]*)?$"                    # optional path
)


class HttpUrl(commands.Converter):
    """
    Validates that the argument is a well-formed http/https URL.

    Raises:
        commands.BadArgument — if the URL is malformed.
    """

    async def convert(self, ctx: commands.Context, value: str) -> str:
        value = value.strip().rstrip("/")
        if not _URL_RE.match(value):
            raise commands.BadArgument(
                f"`{value}` is not a valid URL. "
                f"Must start with `http://` or `https://`."
            )
        return value


# ── Emoji or unicode ───────────────────────────────────────────

class EmojiOrUnicode(commands.Converter):
    """
    Accepts a custom guild emoji (by mention or ID) or a raw unicode emoji.

    Returns:
        discord.Emoji   — for custom guild emoji
        str             — for unicode emoji
    """

    async def convert(
        self, ctx: commands.Context, value: str
    ) -> discord.Emoji | str:
        # Try custom emoji converter first
        try:
            return await commands.EmojiConverter().convert(ctx, value)
        except commands.EmojiNotFound:
            pass

        # Accept raw unicode (1–2 chars, or emoji sequences up to 10 chars)
        value = value.strip()
        if 1 <= len(value) <= 10:
            return value

        raise commands.BadArgument(
            f"`{value}` is not a recognised emoji."
        )