"""
Moderation configuration commands.

Command group: g!modconfig (alias: g!mc)

Subcommands:
    status                          — Show moderation config
    threshold add <count> <action>  — Add a strike threshold punishment
    threshold remove <count>        — Remove a strike threshold
    threshold list                  — List all thresholds
    threshold duration <count> <m>  — Set mute duration for a threshold
    dmtargets <on|off>              — Toggle DM notifications to actioned members
    setpermit <level> <role>        — Assign a permit level to a role
    removepermit <level> <role>     — Remove a permit level from a role
    permitlist                      — Show all permit level assignments
    addowner <member>               — Add an extra owner (permit 4)
    removeowner <member>            — Remove an extra owner
    ownerlist                       — List all extra owners
"""

import logging

import discord
from discord.ext import commands

from utils.checks import require_permit, guild_only
from utils.helpers import make_embed, success_embed, error_embed, info_embed
from utils.paginator import send_paginated, build_pages

log = logging.getLogger("bot.moderation_config")

_THRESHOLD_ACTIONS = ("warn", "mute", "kick", "ban", "none")


class ModerationConfig(commands.Cog, name="ModerationConfig"):
    def __init__(self, bot):
        self.bot = bot

    # ── Group ──────────────────────────────────────────────────

    @commands.group(name="modconfig", aliases=["mc"], invoke_without_command=True)
    @guild_only()
    async def modconfig(self, ctx: commands.Context) -> None:
        """Moderation system configuration."""
        await ctx.send_help(ctx.command)

    # ── status ─────────────────────────────────────────────────

    @modconfig.command(name="status")
    @require_permit(2)
    async def mc_status(self, ctx: commands.Context) -> None:
        """Show the current moderation configuration."""
        cfg     = self.bot.config.get(ctx.guild.id)
        mod_cfg = cfg.get("moderation", {})

        thresholds    = mod_cfg.get("strike_thresholds", {})
        extra_owners  = cfg.get("extra_owners", [])
        permit_roles  = cfg.get("permit_roles", {})
        dm_targets    = cfg.get("misc", {}).get("dm_targets", True)

        log_ch_id = cfg.get("log_channel")
        log_ch    = ctx.guild.get_channel(log_ch_id) if log_ch_id else None

        # Format thresholds
        if thresholds:
            th_lines = []
            for count in sorted(thresholds.keys(), key=int):
                p = thresholds[count]
                dur = f" `{p.get('duration_minutes')}m`" if p.get("duration_minutes") else ""
                th_lines.append(f"**{count}** strikes → `{p.get('action', '?')}`{dur}")
            th_str = "\n".join(th_lines)
        else:
            th_str = "*(none configured)*"

        # Format permit roles
        permit_lines = []
        for lvl in range(1, 5):
            role_ids = permit_roles.get(str(lvl), [])
            roles    = [
                ctx.guild.get_role(rid)
                for rid in role_ids
                if ctx.guild.get_role(rid)
            ]
            if roles:
                permit_lines.append(
                    f"**Permit {lvl}:** {' '.join(r.mention for r in roles)}"
                )
        permit_str = "\n".join(permit_lines) or "*(none configured)*"

        embed = make_embed(
            title="⚙️ Moderation Configuration",
            colour=discord.Colour.blurple(),
            fields=[
                ("Log Channel",    log_ch.mention if log_ch else "*(not set)*",  True),
                ("DM Targets",     "✅" if dm_targets else "❌",                 True),
                ("Extra Owners",   f"`{len(extra_owners)}`",                     True),
                ("Strike Thresholds", th_str,                                    False),
                ("Permit Roles",      permit_str,                                False),
            ],
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── threshold group ────────────────────────────────────────

    @modconfig.group(name="threshold", invoke_without_command=True)
    @guild_only()
    async def mc_threshold(self, ctx: commands.Context) -> None:
        """Manage strike threshold auto-punishments."""
        await ctx.send_help(ctx.command)

    @mc_threshold.command(name="add")
    @require_permit(4)
    async def threshold_add(
        self,
        ctx:    commands.Context,
        count:  int,
        action: str,
    ) -> None:
        """
        Add or update a strike threshold punishment.

        Actions: mute | kick | ban | none

        Examples:
            g!mc threshold add 3 mute
            g!mc threshold add 5 kick
            g!mc threshold add 10 ban
        """
        action = action.lower()
        if action not in _THRESHOLD_ACTIONS:
            actions_fmt = ", ".join(f"`{a}`" for a in _THRESHOLD_ACTIONS)
            return await ctx.send(embed=error_embed(
                f"Invalid action `{action}`.\nValid actions: {actions_fmt}"
            ))
        if count < 1:
            return await ctx.send(embed=error_embed(
                "Strike count must be at least `1`."
            ))

        cfg = self.bot.config.get(ctx.guild.id)
        cfg["moderation"].setdefault("strike_thresholds", {})[str(count)] = {
            "action": action,
        }
        self.bot.config.save(ctx.guild.id)

        await ctx.send(embed=success_embed(
            f"Strike threshold: **{count}** strikes → **{action}**."
        ))

    @mc_threshold.command(name="remove", aliases=["rm"])
    @require_permit(4)
    async def threshold_remove(
        self,
        ctx:   commands.Context,
        count: int,
    ) -> None:
        """Remove a strike threshold."""
        cfg        = self.bot.config.get(ctx.guild.id)
        thresholds = cfg["moderation"].get("strike_thresholds", {})

        if str(count) not in thresholds:
            return await ctx.send(embed=error_embed(
                f"No threshold configured at **{count}** strikes."
            ))

        del thresholds[str(count)]
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed(
            f"Removed strike threshold at **{count}** strikes."
        ))

    @mc_threshold.command(name="list")
    @require_permit(2)
    async def threshold_list(self, ctx: commands.Context) -> None:
        """List all configured strike thresholds."""
        cfg        = self.bot.config.get(ctx.guild.id)
        thresholds = cfg.get("moderation", {}).get("strike_thresholds", {})

        if not thresholds:
            return await ctx.send(embed=info_embed(
                "No strike thresholds configured."
            ))

        lines = []
        for count in sorted(thresholds.keys(), key=int):
            p   = thresholds[count]
            dur = f" — mute for `{p.get('duration_minutes')}m`" \
                  if p.get("duration_minutes") else ""
            lines.append(f"**{count}** strikes → `{p.get('action', '?')}`{dur}")

        embed = make_embed(
            title="⚠️ Strike Thresholds",
            description="\n".join(lines),
            colour=discord.Colour.yellow(),
        )
        await ctx.send(embed=embed)

    @mc_threshold.command(name="duration")
    @require_permit(4)
    async def threshold_duration(
        self,
        ctx:     commands.Context,
        count:   int,
        minutes: int,
    ) -> None:
        """Set the mute duration (minutes) for a mute threshold."""
        cfg        = self.bot.config.get(ctx.guild.id)
        thresholds = cfg["moderation"].get("strike_thresholds", {})

        if str(count) not in thresholds:
            return await ctx.send(embed=error_embed(
                f"No threshold at **{count}** strikes. Add it first with "
                f"`g!mc threshold add {count} mute`."
            ))

        entry = thresholds[str(count)]
        if entry.get("action") != "mute":
            return await ctx.send(embed=error_embed(
                f"Threshold at **{count}** strikes is `{entry.get('action')}`, "
                f"not `mute`. Duration only applies to mute thresholds."
            ))

        if minutes < 1:
            return await ctx.send(embed=error_embed("Duration must be at least `1` minute."))

        entry["duration_minutes"] = minutes
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed(
            f"Mute threshold at **{count}** strikes → **{minutes}m** duration."
        ))

    # ── dmtargets ──────────────────────────────────────────────

    @modconfig.command(name="dmtargets")
    @require_permit(3)
    async def mc_dmtargets(self, ctx: commands.Context, toggle: str) -> None:
        """Toggle whether actioned members receive DM notifications."""
        val = toggle.lower() in ("on", "true", "yes", "enable", "1")
        self.bot.config.set(ctx.guild.id, ["misc", "dm_targets"], val)
        await ctx.send(embed=success_embed(
            f"DM notifications to actioned members **{'enabled' if val else 'disabled'}**."
        ))

    # ── permit roles ───────────────────────────────────────────

    @modconfig.command(name="setpermit")
    @require_permit(4)
    async def mc_setpermit(
        self,
        ctx:   commands.Context,
        level: int,
        role:  discord.Role,
    ) -> None:
        """
        Assign a permit level to a role.

        Permit levels:
            1 — View own records
            2 — View all records, use basic mod commands
            3 — Manage automod, joingate, verification settings
            4 — Full guild configuration (Extra Owner)

        Example:
            g!mc setpermit 2 @Moderator
            g!mc setpermit 3 @Admin
        """
        if not (1 <= level <= 4):
            return await ctx.send(embed=error_embed(
                "Permit level must be between `1` and `4`."
            ))

        # Prevent permit 4 being assigned via command — use g!addowner instead
        if level == 4:
            return await ctx.send(embed=error_embed(
                "Permit level 4 is reserved for Extra Owners.\n"
                "Use `g!mc addowner <member>` to grant level 4 access."
            ))

        cfg      = self.bot.config.get(ctx.guild.id)
        p_roles  = cfg.setdefault("permit_roles", {})
        key      = str(level)
        role_lst = p_roles.setdefault(key, [])

        if role.id in role_lst:
            return await ctx.send(embed=info_embed(
                f"{role.mention} already has permit level `{level}`."
            ))

        role_lst.append(role.id)
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed(
            f"Assigned permit level **{level}** to {role.mention}."
        ))

    @modconfig.command(name="removepermit")
    @require_permit(4)
    async def mc_removepermit(
        self,
        ctx:   commands.Context,
        level: int,
        role:  discord.Role,
    ) -> None:
        """Remove a permit level from a role."""
        if not (1 <= level <= 3):
            return await ctx.send(embed=error_embed(
                "Permit level must be between `1` and `3`."
            ))

        cfg     = self.bot.config.get(ctx.guild.id)
        p_roles = cfg.get("permit_roles", {})
        key     = str(level)
        lst     = p_roles.get(key, [])

        if role.id not in lst:
            return await ctx.send(embed=error_embed(
                f"{role.mention} does not have permit level `{level}`."
            ))

        lst.remove(role.id)
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed(
            f"Removed permit level **{level}** from {role.mention}."
        ))

    @modconfig.command(name="permitlist")
    @require_permit(2)
    async def mc_permitlist(self, ctx: commands.Context) -> None:
        """Show all permit level role assignments."""
        cfg     = self.bot.config.get(ctx.guild.id)
        p_roles = cfg.get("permit_roles", {})

        lines = []
        for lvl in range(1, 5):
            role_ids = p_roles.get(str(lvl), [])
            roles    = [ctx.guild.get_role(rid) for rid in role_ids if ctx.guild.get_role(rid)]
            if roles:
                lines.append(
                    f"**Permit {lvl}:** {' '.join(r.mention for r in roles)}"
                )
            else:
                lines.append(f"**Permit {lvl}:** *(none)*")

        embed = make_embed(
            title="🔑 Permit Level Assignments",
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
            footer=(
                "Permit 1: view • "
                "2: basic mod • "
                "3: config • "
                "4: extra owner"
            ),
        )
        await ctx.send(embed=embed)

    # ── extra owners ───────────────────────────────────────────

    @modconfig.command(name="addowner")
    @require_permit(4)
    async def mc_addowner(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> None:
        """
        Grant a member Extra Owner (permit 4) access.

        Extra Owners can:
        - Change all guild configuration
        - Manage permits and extra owners
        - Run all guard/antinuke/joingate commands

        Only the guild owner or bot owner can use this command.
        """
        # Only guild owner or bot owner
        if ctx.author.id != ctx.guild.owner_id:
            bot_owner = (await self.bot.application_info()).owner
            if ctx.author.id != bot_owner.id:
                return await ctx.send(embed=error_embed(
                    "Only the **server owner** can add Extra Owners."
                ))

        cfg    = self.bot.config.get(ctx.guild.id)
        owners = cfg.setdefault("extra_owners", [])

        if member.id == ctx.guild.owner_id:
            return await ctx.send(embed=info_embed(
                "The server owner already has full access."
            ))
        if member.bot:
            return await ctx.send(embed=error_embed("Bots cannot be Extra Owners."))
        if member.id in owners:
            return await ctx.send(embed=info_embed(
                f"**{member.display_name}** is already an Extra Owner."
            ))

        owners.append(member.id)
        self.bot.config.save(ctx.guild.id)

        await ctx.send(embed=success_embed(
            f"**{member.display_name}** is now an **Extra Owner** (permit 4)."
        ))
        log.info(
            "Extra owner added: %s (%d) in %s by %s",
            member, member.id, ctx.guild.name, ctx.author,
        )

    @modconfig.command(name="removeowner")
    @require_permit(4)
    async def mc_removeowner(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> None:
        """Remove a member's Extra Owner status."""
        if ctx.author.id != ctx.guild.owner_id:
            bot_owner = (await self.bot.application_info()).owner
            if ctx.author.id != bot_owner.id:
                return await ctx.send(embed=error_embed(
                    "Only the **server owner** can remove Extra Owners."
                ))

        cfg    = self.bot.config.get(ctx.guild.id)
        owners = cfg.get("extra_owners", [])

        if member.id not in owners:
            return await ctx.send(embed=error_embed(
                f"**{member.display_name}** is not an Extra Owner."
            ))

        owners.remove(member.id)
        self.bot.config.save(ctx.guild.id)
        await ctx.send(embed=success_embed(
            f"Removed **{member.display_name}** from Extra Owners."
        ))

    @modconfig.command(name="ownerlist")
    @require_permit(2)
    async def mc_ownerlist(self, ctx: commands.Context) -> None:
        """List all Extra Owners for this server."""
        cfg    = self.bot.config.get(ctx.guild.id)
        owners = cfg.get("extra_owners", [])

        lines = []

        # Always show guild owner first
        guild_owner = ctx.guild.owner
        if guild_owner:
            lines.append(
                f"👑 **{guild_owner.display_name}** (`{guild_owner.id}`) — *Server Owner*"
            )

        for uid in owners:
            m = ctx.guild.get_member(uid)
            if m:
                lines.append(f"⭐ **{m.display_name}** (`{uid}`) — Extra Owner")
            else:
                lines.append(f"⭐ *(left server)* (`{uid}`) — Extra Owner")

        if not lines:
            return await ctx.send(embed=info_embed("No Extra Owners configured."))

        embed = make_embed(
            title="👑 Server Owners & Extra Owners",
            description="\n".join(lines),
            colour=discord.Colour.gold(),
        )
        await ctx.send(embed=embed)

    # ── log channel ────────────────────────────────────────────

    @modconfig.command(name="setlog")
    @require_permit(4)
    async def mc_setlog(
        self,
        ctx:     commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        """Set the moderation log channel."""
        perms = channel.permissions_for(ctx.guild.me)
        if not perms.send_messages or not perms.embed_links:
            return await ctx.send(embed=error_embed(
                f"I need **Send Messages** and **Embed Links** permissions "
                f"in {channel.mention}."
            ))

        self.bot.config.set(ctx.guild.id, ["log_channel"], channel.id)
        await ctx.send(embed=success_embed(
            f"Log channel set to {channel.mention}."
        ))

    @modconfig.command(name="clearlog")
    @require_permit(4)
    async def mc_clearlog(self, ctx: commands.Context) -> None:
        """Clear the moderation log channel setting."""
        self.bot.config.set(ctx.guild.id, ["log_channel"], None)
        await ctx.send(embed=success_embed("Log channel cleared."))


async def setup(bot):
    await bot.add_cog(ModerationConfig(bot))