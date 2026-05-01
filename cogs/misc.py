"""
Miscellaneous utility commands.

Commands:
    ping          — Latency and uptime
    uptime        — How long the bot has been online
    serverinfo    — Guild information embed
    userinfo      — User/member information embed
    roleinfo      — Role information embed
    avatar        — Show a user's avatar
    banner        — Show a user's banner
    membercount   — Current member count breakdown
    botstats      — Bot statistics summary
    invite        — Bot invite link (if configured)
"""

import platform
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils.checks import guild_only
from utils.embeds import make_embed, info

_START_TIME = datetime.now(timezone.utc)


class Misc(commands.Cog, name="Misc"):
    def __init__(self, bot):
        self.bot = bot

    # ── ping ───────────────────────────────────────────────────

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context) -> None:
        """Check bot latency and uptime."""
        latency_ms = round(self.bot.latency * 1000)
        uptime     = _format_uptime()

        colour = (
            discord.Colour.green()  if latency_ms < 100  else
            discord.Colour.orange() if latency_ms < 300  else
            discord.Colour.red()
        )
        embed = make_embed(
            title="🏓 Pong!",
            colour=colour,
            fields=[
                ("Websocket", f"`{latency_ms}ms`", True),
                ("Uptime",    uptime,              True),
            ],
        )
        await ctx.send(embed=embed)

    # ── uptime ─────────────────────────────────────────────────

    @commands.command(name="uptime")
    async def uptime(self, ctx: commands.Context) -> None:
        """Show how long the bot has been online."""
        await ctx.send(embed=info(f"Online for **{_format_uptime()}**."))

    # ── serverinfo ─────────────────────────────────────────────

    @commands.command(name="serverinfo", aliases=["guildinfo", "si"])
    @guild_only()
    async def serverinfo(self, ctx: commands.Context) -> None:
        """Display information about this server."""
        g       = ctx.guild
        created = discord.utils.format_dt(g.created_at, style="R")
        roles   = len(g.roles) - 1
        channels = (
            f"{sum(1 for c in g.channels if isinstance(c, discord.TextChannel))} text · "
            f"{sum(1 for c in g.channels if isinstance(c, discord.VoiceChannel))} voice"
        )
        boost_level = f"Level {g.premium_tier} ({g.premium_subscription_count} boosts)"

        embed = make_embed(
            title=g.name,
            colour=discord.Colour.blurple(),
            fields=[
                ("Owner",       f"<@{g.owner_id}>",      True),
                ("Created",     created,                  True),
                ("Members",     f"`{g.member_count}`",    True),
                ("Channels",    channels,                 True),
                ("Roles",       f"`{roles}`",             True),
                ("Boosts",      boost_level,              True),
                ("Verification",f"`{g.verification_level}`", True),
                ("ID",          f"`{g.id}`",              True),
            ],
            timestamp=True,
        )
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        if g.banner:
            embed.set_image(url=g.banner.url)
        await ctx.send(embed=embed)

    # ── userinfo ───────────────────────────────────────────────

    @commands.command(name="userinfo", aliases=["whois", "ui"])
    @guild_only()
    async def userinfo(
        self,
        ctx:    commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        """Display information about a member."""
        member  = member or ctx.author
        created = discord.utils.format_dt(member.created_at, style="R")
        joined  = discord.utils.format_dt(member.joined_at, style="R") \
                  if member.joined_at else "Unknown"
        roles   = [r.mention for r in reversed(member.roles) if not r.is_default()]
        roles_str = " ".join(roles[:20]) + (" …" if len(roles) > 20 else "") or "*(none)*"

        flags  = [f.name.replace("_", " ").title() for f, v in member.public_flags if v]
        badges = ", ".join(flags) if flags else "*(none)*"

        embed = make_embed(
            title=str(member),
            colour=member.colour if member.colour.value else discord.Colour.blurple(),
            fields=[
                ("Display Name", member.display_name,      True),
                ("ID",           f"`{member.id}`",          True),
                ("Bot",          "✅" if member.bot else "❌", True),
                ("Account Created", created,               True),
                ("Joined Server",   joined,                True),
                ("Badges",          badges,                True),
                ("Roles",           roles_str,             False),
            ],
            thumbnail=(member.avatar or member.default_avatar).url,
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── roleinfo ───────────────────────────────────────────────

    @commands.command(name="roleinfo", aliases=["ri"])
    @guild_only()
    async def roleinfo(
        self,
        ctx:  commands.Context,
        role: discord.Role,
    ) -> None:
        """Display information about a role."""
        created   = discord.utils.format_dt(role.created_at, style="R")
        key_perms = [
            p.replace("_", " ").title()
            for p, v in role.permissions
            if v and p in (
                "administrator", "manage_guild", "manage_roles",
                "manage_channels", "kick_members", "ban_members",
                "manage_messages", "mention_everyone",
            )
        ]
        perms_str = ", ".join(key_perms) if key_perms else "*(none notable)*"

        embed = make_embed(
            title=f"@{role.name}",
            colour=role.colour if role.colour.value else discord.Colour.blurple(),
            fields=[
                ("ID",          f"`{role.id}`",                           True),
                ("Colour",      f"`{role.colour}`",                       True),
                ("Position",    f"`{role.position}`",                     True),
                ("Mentionable", "✅" if role.mentionable else "❌",       True),
                ("Hoisted",     "✅" if role.hoist       else "❌",       True),
                ("Managed",     "✅" if role.managed     else "❌",       True),
                ("Members",     f"`{len(role.members)}`",                 True),
                ("Created",     created,                                   True),
                ("Key Perms",   perms_str,                                False),
            ],
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── avatar ─────────────────────────────────────────────────

    @commands.command(name="avatar", aliases=["av", "pfp"])
    async def avatar(
        self,
        ctx:  commands.Context,
        user: discord.User | None = None,
    ) -> None:
        """Show a user's avatar in full resolution."""
        user = user or ctx.author
        av   = user.avatar or user.default_avatar
        embed = make_embed(
            title=f"{user.display_name}'s Avatar",
            colour=discord.Colour.blurple(),
            image=av.url,
        )
        embed.description = (
            f"[PNG]({av.with_format('png').url}) · "
            f"[JPG]({av.with_format('jpg').url}) · "
            f"[WEBP]({av.with_format('webp').url})"
        )
        await ctx.send(embed=embed)

    # ── membercount ────────────────────────────────────────────

    @commands.command(name="membercount", aliases=["members", "mc"])
    @guild_only()
    async def membercount(self, ctx: commands.Context) -> None:
        """Show the current member count breakdown."""
        g       = ctx.guild
        humans  = sum(1 for m in g.members if not m.bot)
        bots    = sum(1 for m in g.members if m.bot)
        online  = sum(
            1 for m in g.members
            if m.status != discord.Status.offline
        )

        embed = make_embed(
            title=f"👥 {g.name} Members",
            colour=discord.Colour.blurple(),
            fields=[
                ("Total",   f"`{g.member_count}`", True),
                ("Humans",  f"`{humans}`",          True),
                ("Bots",    f"`{bots}`",            True),
                ("Online",  f"`{online}`",          True),
            ],
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── botstats ───────────────────────────────────────────────

    @commands.command(name="botstats", aliases=["stats"])
    async def botstats(self, ctx: commands.Context) -> None:
        """Show overall bot statistics."""
        guilds  = len(self.bot.guilds)
        users   = sum(g.member_count or 0 for g in self.bot.guilds)
        cogs    = len(self.bot.cogs)
        cmds    = len(self.bot.commands)

        embed = make_embed(
            title="📊 Statistics",
            colour=discord.Colour.blurple(),
            fields=[
                ("Servers",  f"`{guilds}`",                  True),
                ("Users",    f"`{users:,}`",                 True),
                ("Cogs",     f"`{cogs}`",                    True),
                ("Commands", f"`{cmds}`",                    True),
                ("Latency",  f"`{round(self.bot.latency*1000)}ms`", True),
                ("Uptime",   _format_uptime(),               True),
                ("Python",   f"`{platform.python_version()}`", True),
                ("discord.py", f"`{discord.__version__}`",   True),
            ],
            timestamp=True,
        )
        if self.bot.user.avatar:
            embed.set_thumbnail(url=self.bot.user.avatar.url)
        await ctx.send(embed=embed)


# ── Helpers ────────────────────────────────────────────────────

def _format_uptime() -> str:
    delta   = datetime.now(timezone.utc) - _START_TIME
    total_s = int(delta.total_seconds())
    days,   rem     = divmod(total_s, 86400)
    hours,  rem     = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:    parts.append(f"{days}d")
    if hours:   parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


async def setup(bot):
    await bot.add_cog(Misc(bot))