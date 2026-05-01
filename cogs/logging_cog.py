"""
Server event logging cog.

Logs the following events to a configured log channel (or per-category
channels if set via g!logconfig):

    Member Events:
        on_member_join           — member joined
        on_member_remove         — member left / was kicked
        on_member_ban            — member banned
        on_member_unban          — member unbanned
        on_member_update         — nickname / role changes

    Message Events:
        on_message_delete        — message deleted
        on_bulk_message_delete   — bulk delete (purge)
        on_message_edit          — message edited

    Channel Events:
        on_guild_channel_create  — channel created
        on_guild_channel_delete  — channel deleted
        on_guild_channel_update  — channel updated (name, perms, etc.)

    Role Events:
        on_guild_role_create     — role created
        on_guild_role_delete     — role deleted
        on_guild_role_update     — role updated

    Server Events:
        on_guild_update          — server settings changed
        on_invite_create         — invite created
        on_invite_delete         — invite deleted

    Voice Events:
        on_voice_state_update    — join / leave / move / mute / deafen

Configuration is per-guild via g!logconfig.
"""

import logging
from typing import Any

import discord
from discord.ext import commands

from utils.helpers import make_embed

log = logging.getLogger("bot.logging_cog")

# ── Category → config key mapping ─────────────────────────────
CATEGORIES = {
    "members":  "log_members_channel",
    "messages": "log_messages_channel",
    "channels": "log_channels_channel",
    "roles":    "log_roles_channel",
    "server":   "log_server_channel",
    "voice":    "log_voice_channel",
}


class LoggingCog(commands.Cog, name="Logging"):
    def __init__(self, bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════

    def _get_log_channel(
        self,
        guild:    discord.Guild,
        category: str,
    ) -> discord.TextChannel | None:
        """
        Return the log channel for a category.
        Falls back to the global log_channel if a category-specific one
        is not set.
        """
        cfg    = self.bot.config.get(guild.id)
        key    = CATEGORIES.get(category)
        ch_id  = (cfg.get(key) or cfg.get("log_channel")) if key else cfg.get("log_channel")
        if not ch_id:
            return None
        ch = guild.get_channel(ch_id)
        return ch if isinstance(ch, discord.TextChannel) else None

    def _logging_enabled(self, guild: discord.Guild, category: str) -> bool:
        cfg      = self.bot.config.get(guild.id)
        disabled = cfg.get("log_disabled_categories", [])
        return category not in disabled

    async def _send_log(
        self,
        guild:    discord.Guild,
        category: str,
        embed:    discord.Embed,
    ) -> None:
        if not self._logging_enabled(guild, category):
            return
        channel = self._get_log_channel(guild, category)
        if not channel:
            return
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.warning("Log send failed in %s (%s): %s", guild.name, category, exc)

    @staticmethod
    def _avatar_url(user: discord.User | discord.Member) -> str:
        av = getattr(user, "avatar", None) or getattr(user, "default_avatar", None)
        return av.url if av else ""

    # ══════════════════════════════════════════════════════════
    # Member Events
    # ══════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild   = member.guild
        created = discord.utils.format_dt(member.created_at, style="R")
        embed   = make_embed(
            title="📥 Member Joined",
            description=f"{member.mention} ({member})",
            colour=discord.Colour.green(),
            fields=[
                ("Account Created", created,            True),
                ("User ID",         f"`{member.id}`",   True),
                ("Member Count",    f"`{guild.member_count}`", True),
            ],
            timestamp=True,
        )
        embed.set_thumbnail(url=self._avatar_url(member))
        await self._send_log(guild, "members", embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild  = member.guild
        joined = discord.utils.format_dt(member.joined_at, style="R") \
                 if member.joined_at else "Unknown"
        roles  = [r.mention for r in member.roles if not r.is_default()]
        embed  = make_embed(
            title="📤 Member Left",
            description=f"{member.mention} ({member})",
            colour=discord.Colour.red(),
            fields=[
                ("Joined",   joined,                         True),
                ("User ID",  f"`{member.id}`",               True),
                ("Roles",    " ".join(roles) or "*(none)*",  False),
            ],
            timestamp=True,
        )
        embed.set_thumbnail(url=self._avatar_url(member))
        await self._send_log(guild, "members", embed)

    @commands.Cog.listener()
    async def on_member_ban(
        self,
        guild:  discord.Guild,
        user:   discord.User,
    ) -> None:
        embed = make_embed(
            title="🔨 Member Banned",
            description=f"{user.mention} ({user})",
            colour=discord.Colour.dark_red(),
            fields=[("User ID", f"`{user.id}`", True)],
            timestamp=True,
        )
        embed.set_thumbnail(url=self._avatar_url(user))
        await self._send_log(guild, "members", embed)

    @commands.Cog.listener()
    async def on_member_unban(
        self,
        guild: discord.Guild,
        user:  discord.User,
    ) -> None:
        embed = make_embed(
            title="🔓 Member Unbanned",
            description=f"{user.mention} ({user})",
            colour=discord.Colour.green(),
            fields=[("User ID", f"`{user.id}`", True)],
            timestamp=True,
        )
        embed.set_thumbnail(url=self._avatar_url(user))
        await self._send_log(guild, "members", embed)

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after:  discord.Member,
    ) -> None:
        guild   = after.guild
        changes = []

        # Nickname change
        if before.nick != after.nick:
            changes.append((
                "Nickname",
                f"`{before.nick or '(none)'}` → `{after.nick or '(none)'}`",
                False,
            ))

        # Role changes
        added   = [r for r in after.roles  if r not in before.roles and not r.is_default()]
        removed = [r for r in before.roles if r not in after.roles  and not r.is_default()]
        if added:
            changes.append(("Roles Added",   " ".join(r.mention for r in added),   False))
        if removed:
            changes.append(("Roles Removed", " ".join(r.mention for r in removed), False))

        # Timeout change
        if before.timed_out_until != after.timed_out_until:
            if after.timed_out_until:
                until = discord.utils.format_dt(after.timed_out_until, style="R")
                changes.append(("Timed Out Until", until, True))
            else:
                changes.append(("Timeout", "Removed", True))

        if not changes:
            return

        embed = make_embed(
            title="✏️ Member Updated",
            description=f"{after.mention} ({after})",
            colour=discord.Colour.blurple(),
            fields=changes,
            timestamp=True,
        )
        embed.set_thumbnail(url=self._avatar_url(after))
        await self._send_log(guild, "members", embed)

    # ══════════════════════════════════════════════════════════
    # Message Events
    # ══════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if not message.guild:
            return
        if message.author.bot:
            return

        content = message.content or "*(no text content)*"
        if len(content) > 1024:
            content = content[:1021] + "..."

        embed = make_embed(
            title="🗑️ Message Deleted",
            colour=discord.Colour.red(),
            fields=[
                ("Author",  f"{message.author.mention} ({message.author})", True),
                ("Channel", message.channel.mention,                        True),
                ("Content", content,                                        False),
            ],
            timestamp=True,
        )
        embed.set_thumbnail(url=self._avatar_url(message.author))
        await self._send_log(message.guild, "messages", embed)

    @commands.Cog.listener()
    async def on_bulk_message_delete(
        self,
        messages: list[discord.Message],
    ) -> None:
        if not messages:
            return
        guild = messages[0].guild
        if not guild:
            return

        channel = messages[0].channel
        embed   = make_embed(
            title="🗑️ Bulk Message Delete",
            colour=discord.Colour.dark_red(),
            fields=[
                ("Channel",  channel.mention,            True),
                ("Deleted",  f"`{len(messages)}` messages", True),
            ],
            timestamp=True,
        )
        await self._send_log(guild, "messages", embed)

    @commands.Cog.listener()
    async def on_message_edit(
        self,
        before: discord.Message,
        after:  discord.Message,
    ) -> None:
        if not after.guild:
            return
        if after.author.bot:
            return
        if before.content == after.content:
            return

        b_content = before.content[:512] + ("..." if len(before.content) > 512 else "")
        a_content = after.content[:512]  + ("..." if len(after.content)  > 512 else "")

        embed = make_embed(
            title="✏️ Message Edited",
            colour=discord.Colour.blurple(),
            fields=[
                ("Author",  f"{after.author.mention} ({after.author})", True),
                ("Channel", after.channel.mention,                      True),
                ("Jump",    f"[View Message]({after.jump_url})",        True),
                ("Before",  b_content or "*(empty)*",                   False),
                ("After",   a_content or "*(empty)*",                   False),
            ],
            timestamp=True,
        )
        embed.set_thumbnail(url=self._avatar_url(after.author))
        await self._send_log(after.guild, "messages", embed)

    # ══════════════════════════════════════════════════════════
    # Channel Events
    # ══════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_guild_channel_create(
        self,
        channel: discord.abc.GuildChannel,
    ) -> None:
        embed = make_embed(
            title="➕ Channel Created",
            colour=discord.Colour.green(),
            fields=[
                ("Name",     f"`{channel.name}`",             True),
                ("Type",     f"`{channel.type}`",             True),
                ("Category", f"`{channel.category or '—'}`",  True),
                ("ID",       f"`{channel.id}`",               True),
            ],
            timestamp=True,
        )
        await self._send_log(channel.guild, "channels", embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(
        self,
        channel: discord.abc.GuildChannel,
    ) -> None:
        embed = make_embed(
            title="➖ Channel Deleted",
            colour=discord.Colour.red(),
            fields=[
                ("Name", f"`{channel.name}`", True),
                ("Type", f"`{channel.type}`", True),
                ("ID",   f"`{channel.id}`",   True),
            ],
            timestamp=True,
        )
        await self._send_log(channel.guild, "channels", embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after:  discord.abc.GuildChannel,
    ) -> None:
        changes = []
        if before.name != after.name:
            changes.append(("Name", f"`{before.name}` → `{after.name}`", True))
        if hasattr(before, "topic") and before.topic != after.topic:  # type: ignore
            changes.append(("Topic", f"`{before.topic}` → `{after.topic}`", False))  # type: ignore
        if before.category != after.category:
            changes.append(("Category", f"`{before.category}` → `{after.category}`", True))
        if not changes:
            return

        embed = make_embed(
            title="✏️ Channel Updated",
            description=f"`{after.name}` (`{after.id}`)",
            colour=discord.Colour.blurple(),
            fields=changes,
            timestamp=True,
        )
        await self._send_log(after.guild, "channels", embed)

    # ══════════════════════════════════════════════════════════
    # Role Events
    # ══════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        embed = make_embed(
            title="➕ Role Created",
            colour=role.colour if role.colour.value else discord.Colour.green(),
            fields=[
                ("Name",  f"`{role.name}`",                         True),
                ("ID",    f"`{role.id}`",                           True),
                ("Colour",f"`{str(role.colour)}`",                  True),
                ("Mentionable", "✅" if role.mentionable else "❌", True),
                ("Hoisted",     "✅" if role.hoist       else "❌", True),
            ],
            timestamp=True,
        )
        await self._send_log(role.guild, "roles", embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        embed = make_embed(
            title="➖ Role Deleted",
            colour=discord.Colour.red(),
            fields=[
                ("Name", f"`{role.name}`", True),
                ("ID",   f"`{role.id}`",   True),
            ],
            timestamp=True,
        )
        await self._send_log(role.guild, "roles", embed)

    @commands.Cog.listener()
    async def on_guild_role_update(
        self,
        before: discord.Role,
        after:  discord.Role,
    ) -> None:
        changes = []
        if before.name != after.name:
            changes.append(("Name", f"`{before.name}` → `{after.name}`", True))
        if before.colour != after.colour:
            changes.append(("Colour", f"`{before.colour}` → `{after.colour}`", True))
        if before.hoist != after.hoist:
            changes.append(("Hoisted", f"`{before.hoist}` → `{after.hoist}`", True))
        if before.mentionable != after.mentionable:
            changes.append(("Mentionable", f"`{before.mentionable}` → `{after.mentionable}`", True))
        if before.permissions != after.permissions:
            changes.append(("Permissions", "*(changed)*", True))
        if not changes:
            return

        embed = make_embed(
            title="✏️ Role Updated",
            description=f"{after.mention} (`{after.id}`)",
            colour=after.colour if after.colour.value else discord.Colour.blurple(),
            fields=changes,
            timestamp=True,
        )
        await self._send_log(after.guild, "roles", embed)

    # ══════════════════════════════════════════════════════════
    # Server Events
    # ══════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_guild_update(
        self,
        before: discord.Guild,
        after:  discord.Guild,
    ) -> None:
        changes = []
        if before.name != after.name:
            changes.append(("Name", f"`{before.name}` → `{after.name}`", True))
        if before.icon != after.icon:
            changes.append(("Icon", "*(changed)*", True))
        if before.owner_id != after.owner_id:
            changes.append(("Owner", f"<@{before.owner_id}> → <@{after.owner_id}>", True))
        if before.verification_level != after.verification_level:
            changes.append((
                "Verification Level",
                f"`{before.verification_level}` → `{after.verification_level}`",
                True,
            ))
        if not changes:
            return

        embed = make_embed(
            title="✏️ Server Updated",
            colour=discord.Colour.blurple(),
            fields=changes,
            timestamp=True,
        )
        await self._send_log(after, "server", embed)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        embed = make_embed(
            title="🔗 Invite Created",
            colour=discord.Colour.green(),
            fields=[
                ("Code",     f"`{invite.code}`",                              True),
                ("Channel",  invite.channel.mention if invite.channel else "—", True),
                ("Creator",  str(invite.inviter) if invite.inviter else "—",  True),
                ("Max Uses", f"`{invite.max_uses or '∞'}`",                   True),
                ("Expires",  discord.utils.format_dt(invite.expires_at, "R")
                              if invite.expires_at else "Never",               True),
            ],
            timestamp=True,
        )
        await self._send_log(invite.guild, "server", embed)  # type: ignore

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        embed = make_embed(
            title="🔗 Invite Deleted",
            colour=discord.Colour.red(),
            fields=[
                ("Code",    f"`{invite.code}`",                                 True),
                ("Channel", invite.channel.mention if invite.channel else "—",  True),
            ],
            timestamp=True,
        )
        await self._send_log(invite.guild, "server", embed)  # type: ignore

    # ══════════════════════════════════════════════════════════
    # Voice Events
    # ══════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after:  discord.VoiceState,
    ) -> None:
        guild   = member.guild
        changes = []

        # Channel join / leave / move
        if before.channel != after.channel:
            if before.channel is None:
                changes.append(("Joined", f"`{after.channel}`", True))
            elif after.channel is None:
                changes.append(("Left", f"`{before.channel}`", True))
            else:
                changes.append((
                    "Moved",
                    f"`{before.channel}` → `{after.channel}`",
                    True,
                ))

        # Mute / deafen / stream / video
        if before.self_mute != after.self_mute:
            changes.append(("Self Mute", "🔇" if after.self_mute else "🔊", True))
        if before.self_deaf != after.self_deaf:
            changes.append(("Self Deaf", "🔕" if after.self_deaf else "🔔", True))
        if before.mute != after.mute:
            changes.append(("Server Mute", "🔇" if after.mute else "🔊", True))
        if before.deaf != after.deaf:
            changes.append(("Server Deaf", "🔕" if after.deaf else "🔔", True))
        if before.self_stream != after.self_stream:
            changes.append(("Streaming", "✅" if after.self_stream else "❌", True))
        if before.self_video != after.self_video:
            changes.append(("Video", "✅" if after.self_video else "❌", True))

        if not changes:
            return

        embed = make_embed(
            title="🔊 Voice State Update",
            description=f"{member.mention} ({member})",
            colour=discord.Colour.blurple(),
            fields=changes,
            timestamp=True,
        )
        embed.set_thumbnail(url=self._avatar_url(member))
        await self._send_log(guild, "voice", embed)


async def setup(bot):
    await bot.add_cog(LoggingCog(bot))