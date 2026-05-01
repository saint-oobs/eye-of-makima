import re
import time
from datetime import timedelta
from typing import Any

import discord
from discord.ext import commands

# ── Duration parsing ───────────────────────────────────────────

_DURATION_RE = re.compile(
    r"(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?",
    re.IGNORECASE,
)

_UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}


def parse_duration(value: str) -> int | None:
    """
    Parse a human-readable duration string into total seconds.

    Accepts:
        "30s", "5m", "2h", "1d", "1w"
        "1h30m", "2d12h", "1w3d6h30m"

    Returns total seconds as int, or None if unparseable.

    Examples:
        parse_duration("5m")      → 300
        parse_duration("1h30m")   → 5400
        parse_duration("2d")      → 172800
        parse_duration("abc")     → None
    """
    value = value.strip().lower()
    # Try simple single-unit shorthand: "300" → 300s
    if value.isdigit():
        return int(value)

    total = 0
    # Manual scan to handle concatenated units: "1h30m10s"
    pattern = re.compile(r"(\d+)\s*([wdhms])")
    matches = pattern.findall(value)
    if not matches:
        return None
    for amount, unit in matches:
        total += int(amount) * _UNIT_SECONDS[unit]
    return total if total > 0 else None


def format_duration(seconds: int) -> str:
    """
    Format a number of seconds into a compact human-readable string.

    Examples:
        format_duration(90)      → "1m 30s"
        format_duration(3661)    → "1h 1m 1s"
        format_duration(86400)   → "1d"
        format_duration(0)       → "0s"
    """
    if seconds <= 0:
        return "0s"

    parts = []
    units = [("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]
    remaining = int(seconds)

    for label, size in units:
        if remaining >= size:
            value, remaining = divmod(remaining, size)
            parts.append(f"{value}{label}")

    return " ".join(parts)


def duration_to_timedelta(value: str) -> timedelta | None:
    """
    Parse a duration string and return a timedelta, or None.
    """
    seconds = parse_duration(value)
    if seconds is None:
        return None
    return timedelta(seconds=seconds)


# ── Permission helpers ─────────────────────────────────────────

#: Permissions considered "dangerous" — their presence on a role
#: should trigger Anti-Nuke monitoring.
DANGEROUS_PERMISSIONS = (
    "administrator",
    "ban_members",
    "kick_members",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_webhooks",
    "manage_expressions",
    "mention_everyone",
)


def has_dangerous_perms(permissions: discord.Permissions) -> bool:
    """Return True if any dangerous permission is granted."""
    return any(getattr(permissions, perm, False) for perm in DANGEROUS_PERMISSIONS)


def get_permit_level(
    member: discord.Member,
    cfg: dict,
) -> int:
    """
    Determine a member's permission level within the bot's hierarchy.

    Levels:
        0 — Regular member (no special access)
        1 — Has a "main role" (trusted community member)
        2 — Server administrator (has Discord Administrator perm or Manage Guild)
        3 — Trusted admin (listed in cfg["trusted_admins"])
        4 — Extra owner (listed in cfg["extra_owners"])
        5 — Guild owner
    """
    if member.guild.owner_id == member.id:
        return 5

    if member.id in cfg.get("extra_owners", []):
        return 4

    if member.id in cfg.get("trusted_admins", []):
        return 3

    if (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
    ):
        return 2

    main_roles = set(cfg.get("main_roles", []))
    if main_roles and any(r.id in main_roles for r in member.roles):
        return 1

    return 0


def require_permit(level: int):
    """
    Command check decorator: requires a minimum permit level.

    Usage:
        @require_permit(3)
        async def my_command(ctx): ...
    """
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild:
            raise commands.NoPrivateMessage()
        cfg = ctx.bot.config.get(ctx.guild.id)
        permit = get_permit_level(ctx.author, cfg)
        if permit < level:
            await ctx.send(
                f"❌ You need permission level **{level}** to use this. "
                f"You have level **{permit}**.",
                delete_after=8,
            )
            return False
        return True
    return commands.check(predicate)


# ── Embed helpers ──────────────────────────────────────────────

def make_embed(
    title: str = "",
    description: str = "",
    colour: discord.Colour = discord.Colour.blurple(),
    *,
    fields: list[tuple[str, str, bool]] | None = None,
    footer: str | None = None,
    thumbnail: str | None = None,
    timestamp: bool = False,
) -> discord.Embed:
    """
    Build a consistently styled embed.

    Args:
        fields:    List of (name, value, inline) tuples.
        footer:    Footer text.
        thumbnail: URL for embed thumbnail.
        timestamp: If True, set embed timestamp to now.
    """
    embed = discord.Embed(
        title=title,
        description=description,
        colour=colour,
    )
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    if footer:
        embed.set_footer(text=footer)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if timestamp:
        embed.timestamp = discord.utils.utcnow()
    return embed


def success_embed(description: str, title: str = "✅ Success") -> discord.Embed:
    return make_embed(title=title, description=description, colour=discord.Colour.green())


def error_embed(description: str, title: str = "❌ Error") -> discord.Embed:
    return make_embed(title=title, description=description, colour=discord.Colour.red())


def warn_embed(description: str, title: str = "⚠️ Warning") -> discord.Embed:
    return make_embed(title=title, description=description, colour=discord.Colour.orange())


def info_embed(description: str, title: str = "ℹ️ Info") -> discord.Embed:
    return make_embed(title=title, description=description, colour=discord.Colour.blurple())


# ── String / text helpers ──────────────────────────────────────

def truncate(text: str, max_len: int = 1024, suffix: str = "…") -> str:
    """Truncate text to max_len, appending suffix if cut."""
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix


def clean_mentions(text: str) -> str:
    """Strip all Discord mention syntax from a string."""
    return re.sub(r"<@[!&]?\d+>|<#\d+>|<@&\d+>", "", text).strip()


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """
    Return singular or plural form based on count.

    Examples:
        plural(1, "strike")           → "1 strike"
        plural(3, "strike")           → "3 strikes"
        plural(1, "match", "matches") → "1 match"
        plural(2, "match", "matches") → "2 matches"
    """
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count} {word}"


def code_block(text: str, lang: str = "") -> str:
    """Wrap text in a Discord code block."""
    return f"```{lang}\n{text}\n```"


def inline_code(text: str) -> str:
    """Wrap text in Discord inline code."""
    return f"`{text}`"


# ── Member / user helpers ──────────────────────────────────────

def display_name(user: discord.User | discord.Member) -> str:
    """Return display name with discriminator if non-zero."""
    if isinstance(user, discord.Member):
        name = user.display_name
    else:
        name = user.name
    if user.discriminator and user.discriminator != "0":
        return f"{name}#{user.discriminator}"
    return name


def avatar_url(user: discord.User | discord.Member) -> str:
    """Return the user's avatar URL, falling back to default avatar."""
    return (user.avatar or user.default_avatar).url


def account_age_days(user: discord.User | discord.Member) -> int:
    """Return how many days old the user's Discord account is."""
    delta = discord.utils.utcnow() - user.created_at
    return delta.days


def is_account_new(user: discord.User | discord.Member, min_days: int = 7) -> bool:
    """Return True if the account is younger than min_days."""
    return account_age_days(user) < min_days


def has_default_avatar(user: discord.User | discord.Member) -> bool:
    """Return True if the user has no custom avatar set."""
    return user.avatar is None


# ── Snowflake / ID helpers ─────────────────────────────────────

def snowflake_time_ms(snowflake: int) -> int:
    """Return the creation timestamp (ms since epoch) of a Discord snowflake."""
    return (snowflake >> 22) + 1420070400000


def snowflake_age_days(snowflake: int) -> int:
    """Return how many days ago a Discord snowflake was created."""
    created_ms = snowflake_time_ms(snowflake)
    now_ms = int(time.time() * 1000)
    return (now_ms - created_ms) // 86_400_000


# ── Confirmation helper ────────────────────────────────────────

async def confirm(
    ctx: commands.Context,
    prompt: str,
    *,
    timeout: float = 30.0,
) -> bool:
    """
    Send a yes/no confirmation prompt and wait for a response.

    Returns True if the user confirms, False if they cancel or time out.
    """
    await ctx.send(
        f"{prompt}\n\nType `yes` to confirm or `no` to cancel.",
        delete_after=timeout + 2,
    )

    def check(m):
        return (
            m.author == ctx.author
            and m.channel == ctx.channel
            and m.content.lower() in ("yes", "no", "y", "n")
        )

    try:
        msg = await ctx.bot.wait_for("message", check=check, timeout=timeout)
        return msg.content.lower() in ("yes", "y")
    except TimeoutError:
        await ctx.send("⏱️ Confirmation timed out.", delete_after=5)
        return False


# ── Paginator ──────────────────────────────────────────────────

def paginate(items: list[Any], page_size: int = 10) -> list[list[Any]]:
    """
    Split a list into pages of page_size items.

    Example:
        paginate([1,2,3,4,5], 2) → [[1,2], [3,4], [5]]
    """
    return [items[i : i + page_size] for i in range(0, len(items), page_size)]