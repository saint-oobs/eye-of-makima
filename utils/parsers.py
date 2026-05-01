"""
Input parsing utilities.

Provides:
    parse_duration    — "10m", "2h30m", "1d" → timedelta
    parse_duration_s  — same but returns total seconds (int)
    parse_snowflake   — extract Discord ID from mention or raw int string
    parse_bool        — "yes"/"no"/"on"/"off"/"true"/"false" → bool
    MemberOrID        — discord.ext.commands converter (member or bare ID)
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from utils.errors import DurationParseError, ParseError

if TYPE_CHECKING:
    pass

# ── Duration parser ────────────────────────────────────────────

_DURATION_RE = re.compile(
    r"(?:(\d+)\s*w(?:eeks?)?)?"     # weeks
    r"(?:(\d+)\s*d(?:ays?)?)?"      # days
    r"(?:(\d+)\s*h(?:ours?)?)?"     # hours
    r"(?:(\d+)\s*m(?:in(?:utes?)?)?)?"  # minutes
    r"(?:(\d+)\s*s(?:ec(?:onds?)?)?)?", # seconds
    re.IGNORECASE,
)

_MAX_DURATION = timedelta(days=28)  # sensible upper cap for mute/timeout


def parse_duration(value: str) -> timedelta:
    """
    Parse a human duration string into a :class:`datetime.timedelta`.

    Accepted formats:
        ``10m``  ``2h``  ``1d``  ``1w``  ``2h30m``  ``1d12h``
        ``30s``  ``1w2d3h4m5s``

    Raises :class:`~utils.errors.DurationParseError` if the string
    contains no recognisable duration components.

    Raises :class:`ValueError` if the resulting duration exceeds 28 days.
    """
    value = value.strip()
    match = _DURATION_RE.fullmatch(value)
    if not match or not any(match.groups()):
        raise DurationParseError(value)

    weeks, days, hours, minutes, seconds = (
        int(g) if g else 0 for g in match.groups()
    )
    delta = timedelta(
        weeks=weeks,
        days=days,
        hours=hours,
        minutes=minutes,
        seconds=seconds,
    )
    if delta.total_seconds() <= 0:
        raise DurationParseError(value)
    if delta > _MAX_DURATION:
        raise ValueError(
            f"Duration `{value}` exceeds maximum of {_MAX_DURATION.days} days."
        )
    return delta


def parse_duration_s(value: str) -> int:
    """
    Same as :func:`parse_duration` but returns total seconds as an int.
    """
    return int(parse_duration(value).total_seconds())


def human_duration(delta: timedelta) -> str:
    """
    Format a :class:`datetime.timedelta` into a readable string.

    Examples:
        timedelta(seconds=90)    → "1 minute 30 seconds"
        timedelta(hours=2)       → "2 hours"
        timedelta(days=1, hours=6) → "1 day 6 hours"
    """
    total   = int(delta.total_seconds())
    periods = [
        ("week",   60 * 60 * 24 * 7),
        ("day",    60 * 60 * 24),
        ("hour",   60 * 60),
        ("minute", 60),
        ("second", 1),
    ]
    parts = []
    for name, secs in periods:
        value, total = divmod(total, secs)
        if value:
            parts.append(f"{value} {name}{'s' if value != 1 else ''}")
    return " ".join(parts) if parts else "0 seconds"


# ── Snowflake / mention parser ─────────────────────────────────

_MENTION_RE = re.compile(r"<[@#!&]{0,2}(\d{15,21})>")


def parse_snowflake(value: str) -> int:
    """
    Extract a Discord snowflake ID from:
    - A raw integer string: ``"123456789012345678"``
    - A user mention:       ``"<@123456789012345678>"``
    - A role mention:       ``"<@&123456789012345678>"``
    - A channel mention:    ``"<#123456789012345678>"``

    Raises :class:`~utils.errors.ParseError` if nothing valid is found.
    """
    value = value.strip()
    mention_match = _MENTION_RE.fullmatch(value)
    if mention_match:
        return int(mention_match.group(1))
    if value.isdigit() and 15 <= len(value) <= 21:
        return int(value)
    raise ParseError(value, "Discord snowflake or mention")


# ── Bool parser ────────────────────────────────────────────────

_TRUTHY  = {"yes", "y", "on",  "true",  "enable",  "enabled",  "1"}
_FALSY   = {"no",  "n", "off", "false", "disable", "disabled", "0"}


def parse_bool(value: str) -> bool:
    """
    Parse a loose boolean string.

    Raises :class:`~utils.errors.ParseError` if the value is ambiguous.
    """
    v = value.strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    raise ParseError(value, "boolean (yes/no, on/off, true/false)")


# ── discord.py converters ──────────────────────────────────────

class DurationConverter(commands.Converter):
    """
    ``discord.ext.commands`` converter for duration strings.

    Usage in command signature:
        async def mute(self, ctx, member, duration: DurationConverter): ...
    """
    async def convert(self, ctx: commands.Context, value: str) -> timedelta:
        try:
            return parse_duration(value)
        except (DurationParseError, ValueError) as exc:
            raise commands.BadArgument(str(exc)) from exc


class SnowflakeConverter(commands.Converter):
    """Converter that accepts a member mention or raw user ID."""

    async def convert(self, ctx: commands.Context, value: str) -> int:
        try:
            return parse_snowflake(value)
        except ParseError as exc:
            raise commands.BadArgument(str(exc)) from exc


class MemberOrID(commands.Converter):
    """
    Accepts a :class:`discord.Member` (by mention/name/ID) **or** a bare
    integer user ID for users no longer in the guild (e.g. ban lookup).

    Returns either a :class:`discord.Member` or an :class:`int`.
    """

    async def convert(
        self,
        ctx:   commands.Context,
        value: str,
    ) -> discord.Member | int:
        # Try standard member lookup first
        try:
            return await commands.MemberConverter().convert(ctx, value)
        except commands.BadArgument:
            pass
        # Fall back to raw snowflake
        try:
            return parse_snowflake(value)
        except ParseError:
            pass
        raise commands.BadArgument(
            f"`{value}` is not a valid member or user ID."
        )