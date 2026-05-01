"""
Centralised embed factory.

All user-facing embeds should be built through these helpers so
colour, footer, and timestamp styling stays consistent across the
entire bot without repeating the same boilerplate in every cog.

Usage:
    from utils.embeds import ok, fail, info, warn, make_embed

    await ctx.send(embed=ok("Member banned."))
    await ctx.send(embed=fail("You lack permission."))
"""

from __future__ import annotations

import discord

# ── Brand colours ──────────────────────────────────────────────
_COLOUR_OK      = discord.Colour(0x2ECC71)   # green
_COLOUR_FAIL    = discord.Colour(0xE74C3C)   # red
_COLOUR_WARN    = discord.Colour(0xF39C12)   # amber
_COLOUR_INFO    = discord.Colour(0x3498DB)   # blue
_COLOUR_NEUTRAL = discord.Colour(0x95A5A6)   # grey


def make_embed(
    *,
    title:       str | None                                = None,
    description: str | None                                = None,
    colour:      discord.Colour | int                      = _COLOUR_NEUTRAL,
    fields:      list[tuple[str, str, bool]] | None        = None,
    footer:      str | None                                = None,
    thumbnail:   str | None                                = None,
    image:       str | None                                = None,
    url:         str | None                                = None,
    timestamp:   bool                                      = False,
) -> discord.Embed:
    """
    Build a :class:`discord.Embed` from keyword arguments.

    Parameters
    ----------
    title:       Embed title text.
    description: Embed description / body text.
    colour:      Embed left-border colour.
    fields:      List of ``(name, value, inline)`` tuples.
    footer:      Footer text (no icon).
    thumbnail:   URL for the small top-right thumbnail image.
    image:       URL for the large bottom image.
    url:         Hyperlink applied to the title.
    timestamp:   If True, sets ``embed.timestamp`` to UTC now.
    """
    embed = discord.Embed(
        title       = title,
        description = description,
        colour      = colour,
        url         = url,
    )

    if timestamp:
        embed.timestamp = discord.utils.utcnow()

    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value or "\u200b", inline=inline)

    if footer:
        embed.set_footer(text=footer)

    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    if image:
        embed.set_image(url=image)

    return embed


# ── Semantic shortcuts ─────────────────────────────────────────

def ok(
    description: str,
    *,
    title:  str | None = None,
    fields: list[tuple[str, str, bool]] | None = None,
    **kwargs,
) -> discord.Embed:
    """Green success embed."""
    return make_embed(
        title       = title or "✅ Success",
        description = description,
        colour      = _COLOUR_OK,
        fields      = fields,
        timestamp   = True,
        **kwargs,
    )


def fail(
    description: str,
    *,
    title:  str | None = None,
    fields: list[tuple[str, str, bool]] | None = None,
    **kwargs,
) -> discord.Embed:
    """Red error / failure embed."""
    return make_embed(
        title       = title or "❌ Error",
        description = description,
        colour      = _COLOUR_FAIL,
        fields      = fields,
        timestamp   = True,
        **kwargs,
    )


def warn(
    description: str,
    *,
    title:  str | None = None,
    fields: list[tuple[str, str, bool]] | None = None,
    **kwargs,
) -> discord.Embed:
    """Amber warning embed."""
    return make_embed(
        title       = title or "⚠️ Warning",
        description = description,
        colour      = _COLOUR_WARN,
        fields      = fields,
        timestamp   = True,
        **kwargs,
    )


def info(
    description: str,
    *,
    title:  str | None = None,
    fields: list[tuple[str, str, bool]] | None = None,
    **kwargs,
) -> discord.Embed:
    """Blue informational embed."""
    return make_embed(
        title       = title or "ℹ️ Info",
        description = description,
        colour      = _COLOUR_INFO,
        fields      = fields,
        timestamp   = True,
        **kwargs,
    )


def loading(description: str = "Please wait…") -> discord.Embed:
    """Neutral grey embed for async loading states."""
    return make_embed(
        title       = "⏳ Loading",
        description = description,
        colour      = _COLOUR_NEUTRAL,
    )


# ── Alias map (backwards-compat with older cog code) ──────────
# Older cogs may call success_embed / error_embed / info_embed
# from utils.helpers — these aliases prevent ImportError if they
# are imported from here instead.
success_embed = ok
error_embed   = fail
info_embed    = info
warn_embed    = warn