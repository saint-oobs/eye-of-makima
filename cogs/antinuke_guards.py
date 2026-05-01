"""
Anti-Nuke guards — proactive permission and role monitoring.

Responsibilities:
- Detect when dangerous permissions are added to a role
- Detect when a bot is added to a dangerous role
- Detect when a member is given a role with dangerous permissions
  by someone without sufficient permit level
- Strip the dangerous permission or reverse the role grant
- Log all guard actions to the log channel
"""

import logging

import discord
from discord.ext import commands

from utils.helpers import make_embed, has_dangerous_perms, DANGEROUS_PERMISSIONS

log = logging.getLogger("bot.antinuke_guards")


class AntiNukeGuards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Role permission update ─────────────────────────────────

    async def on_role_update(
        self,
        before: discord.Role,
        after:  discord.Role,
    ) -> None:
        """
        Called by Events.on_guild_role_update.
        Checks if dangerous permissions were added to a role
        by someone without permit 4+.
        """
        guild = after.guild
        cfg   = self.bot.config.get(guild.id)

        if not cfg.get("antinuke", {}).get("enabled", True):
            return
        if not cfg.get("antinuke", {}).get("monitor_dangerous_role_perms", True):
            return

        # Check which dangerous perms were newly added
        newly_added = []
        for perm in DANGEROUS_PERMISSIONS:
            before_val = getattr(before.permissions, perm, False)
            after_val  = getattr(after.permissions, perm, False)
            if not before_val and after_val:
                newly_added.append(perm)

        if not newly_added:
            return

        # Find who made the change via audit log
        executor_id = await self._find_executor(
            guild,
            discord.AuditLogAction.role_update,
            target_id=after.id,
        )

        if executor_id is None or executor_id == self.bot.user.id:
            return

        # Check permit level of executor
        if await self._is_permitted(guild, executor_id):
            return

        log.warning(
            "Dangerous perms added to role %s in %s by user %d: %s",
            after.name, guild.name, executor_id, newly_added,
        )

        # Strip the newly added dangerous permissions
        new_perms = discord.Permissions(after.permissions.value)
        for perm in newly_added:
            setattr(new_perms, perm, False)

        try:
            await after.edit(
                permissions=new_perms,
                reason=f"[AntiNuke Guard] Dangerous permission removed: {', '.join(newly_added)}",
            )
            log.info(
                "Stripped dangerous perms from role %s in %s", after.name, guild.name
            )
        except discord.Forbidden:
            log.warning(
                "Cannot strip perms from role %s — missing permissions", after.name
            )

        await self._log_guard_action(
            guild,
            title="🛡️ Dangerous Permission Stripped",
            description=(
                f"Role **{after.name}** had dangerous permissions added by <@{executor_id}>.\n"
                f"Permissions have been **automatically removed**."
            ),
            fields=[
                ("Role",        after.mention,                                True),
                ("Executor",    f"<@{executor_id}> (`{executor_id}`)",        True),
                ("Perms Removed", "\n".join(f"`{p}`" for p in newly_added),  False),
            ],
            colour=discord.Colour.orange(),
        )

        # Trigger antinuke punishment for the executor
        antinuke_cog = self.bot.get_cog("AntiNuke")
        if antinuke_cog:
            await antinuke_cog._punish(
                guild,
                executor_id,
                reason=f"Added dangerous permissions to role {after.name}: {', '.join(newly_added)}",
                action_type="role_update",
            )

    # ── Member role add ────────────────────────────────────────

    async def on_member_role_add(
        self,
        guild:   discord.Guild,
        member:  discord.Member,
        roles:   list[discord.Role],
    ) -> None:
        """
        Called when a member receives a new role.
        Checks if any of the added roles carry dangerous permissions
        and if the executor is permitted to grant them.
        """
        cfg = self.bot.config.get(guild.id)
        if not cfg.get("antinuke", {}).get("enabled", True):
            return
        if not cfg.get("antinuke", {}).get("monitor_dangerous_role_perms", True):
            return

        dangerous_roles = [r for r in roles if has_dangerous_perms(r.permissions)]
        if not dangerous_roles:
            return

        executor_id = await self._find_executor(
            guild,
            discord.AuditLogAction.member_role_update,
            target_id=member.id,
        )

        if executor_id is None or executor_id == self.bot.user.id:
            return

        if await self._is_permitted(guild, executor_id):
            return

        if member.id == guild.owner_id:
            return

        log.warning(
            "Dangerous role(s) granted to %s in %s by %d: %s",
            member, guild.name, executor_id,
            [r.name for r in dangerous_roles],
        )

        # Reverse the role grants
        reverted = []
        for role in dangerous_roles:
            try:
                await member.remove_roles(
                    role,
                    reason=f"[AntiNuke Guard] Unauthorised dangerous role grant by {executor_id}",
                )
                reverted.append(role.name)
            except discord.Forbidden:
                pass

        if reverted:
            await self._log_guard_action(
                guild,
                title="🛡️ Dangerous Role Grant Reversed",
                description=(
                    f"<@{executor_id}> granted dangerous role(s) to {member.mention}.\n"
                    f"Role grant has been **automatically reversed**."
                ),
                fields=[
                    ("Target",        member.mention,                                  True),
                    ("Executor",      f"<@{executor_id}> (`{executor_id}`)",           True),
                    ("Roles Removed", "\n".join(f"`{n}`" for n in reverted),           False),
                ],
                colour=discord.Colour.orange(),
            )

        # Trigger antinuke punishment
        antinuke_cog = self.bot.get_cog("AntiNuke")
        if antinuke_cog:
            await antinuke_cog._punish(
                guild,
                executor_id,
                reason=f"Granted dangerous roles to {member}: {', '.join(reverted)}",
                action_type="role_update",
            )

    # ── Bot added to dangerous role ────────────────────────────

    async def on_bot_dangerous_role(
        self,
        guild:  discord.Guild,
        bot_member: discord.Member,
        roles:  list[discord.Role],
    ) -> None:
        """
        Check if a newly added bot was given dangerous permissions.
        Called by JoinGate when a new bot joins.
        """
        cfg = self.bot.config.get(guild.id)
        if not cfg.get("antinuke", {}).get("enabled", True):
            return

        dangerous = [r for r in roles if has_dangerous_perms(r.permissions)]
        if not dangerous:
            return

        log.warning(
            "Bot %s added with dangerous roles in %s: %s",
            bot_member, guild.name, [r.name for r in dangerous],
        )

        # Find who added the bot
        executor_id = await self._find_executor(
            guild,
            discord.AuditLogAction.bot_add,
            target_id=bot_member.id,
        )

        await self._log_guard_action(
            guild,
            title="⚠️ Bot Added With Dangerous Permissions",
            description=(
                f"Bot {bot_member.mention} was added with dangerous permissions.\n"
                f"This may indicate an unauthorised bot addition."
            ),
            fields=[
                ("Bot",          bot_member.mention,                                  True),
                ("Added By",     f"<@{executor_id}>" if executor_id else "Unknown",   True),
                ("Danger Roles", "\n".join(f"`{r.name}`" for r in dangerous),        False),
            ],
            colour=discord.Colour.red(),
        )

        # If the adder is not permitted, trigger antinuke
        if executor_id and not await self._is_permitted(guild, executor_id):
            antinuke_cog = self.bot.get_cog("AntiNuke")
            if antinuke_cog:
                await antinuke_cog._punish(
                    guild,
                    executor_id,
                    reason=f"Added bot {bot_member} with dangerous permissions",
                    action_type="webhook_create",
                )

    # ── Webhook guard ──────────────────────────────────────────

    async def on_suspicious_webhook(
        self,
        guild:       discord.Guild,
        webhook:     discord.Webhook,
        executor_id: int,
    ) -> None:
        """
        Called by antinuke when a webhook is created by a non-permitted user.
        Attempts to delete the webhook immediately.
        """
        cfg = self.bot.config.get(guild.id)
        if not cfg.get("antinuke", {}).get("enabled", True):
            return

        if await self._is_permitted(guild, executor_id):
            return

        try:
            await webhook.delete(reason="[AntiNuke Guard] Unauthorised webhook creation")
            log.info(
                "Deleted suspicious webhook '%s' in %s created by %d",
                webhook.name, guild.name, executor_id,
            )
            deleted = True
        except (discord.Forbidden, discord.HTTPException):
            deleted = False

        await self._log_guard_action(
            guild,
            title="🛡️ Suspicious Webhook Deleted" if deleted else "⚠️ Suspicious Webhook Detected",
            description=(
                f"<@{executor_id}> created a webhook without sufficient permissions.\n"
                + ("Webhook has been **automatically deleted**." if deleted
                   else "Could not delete webhook — missing permissions.")
            ),
            fields=[
                ("Webhook",  f"`{webhook.name}`",                          True),
                ("Executor", f"<@{executor_id}> (`{executor_id}`)",        True),
                ("Deleted",  "✅" if deleted else "❌",                    True),
            ],
            colour=discord.Colour.orange() if deleted else discord.Colour.red(),
        )

    # ── Utilities ──────────────────────────────────────────────

    async def _find_executor(
        self,
        guild:  discord.Guild,
        action: discord.AuditLogAction,
        *,
        target_id: int | None = None,
        limit: int = 5,
    ) -> int | None:
        """
        Scan recent audit log entries for the given action and target
        to identify who performed it. Returns the executor's ID or None.
        """
        try:
            async for entry in guild.audit_logs(action=action, limit=limit):
                if target_id is None:
                    return entry.user_id
                target = getattr(entry, "target", None)
                if target and getattr(target, "id", None) == target_id:
                    return entry.user_id
        except (discord.Forbidden, discord.HTTPException):
            pass
        return None

    async def _is_permitted(self, guild: discord.Guild, user_id: int) -> bool:
        if user_id == guild.owner_id:
            return True
        if await self.bot.is_owner(discord.Object(id=user_id)):
            return True
        cfg = self.bot.config.get(guild.id)
        return user_id in cfg.get("extra_owners", [])

    async def _log_guard_action(
        self,
        guild:       discord.Guild,
        title:       str,
        description: str,
        fields:      list[tuple[str, str, bool]],
        colour:      discord.Colour,
    ) -> None:
        cfg     = self.bot.config.get(guild.id)
        ch_id   = cfg.get("log_channel")
        if not ch_id:
            return
        channel = guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return
        embed = make_embed(
            title=title,
            description=description,
            colour=colour,
            fields=fields,
            timestamp=True,
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error("Guard log failed: %s", exc)


async def setup(bot):
    await bot.add_cog(AntiNukeGuards(bot))