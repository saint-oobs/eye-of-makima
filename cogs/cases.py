"""
Moderation case records system.

Every moderation action (warn, mute, kick, ban, quarantine) creates
a numbered case entry in the database, queryable per-guild and per-user.

Commands:
    case <id>               — View a specific case by ID
    cases [member]          — List all cases, or cases for a member
    case edit <id> <reason> — Edit the reason on an existing case
    case delete <id>        — Delete a case (permit 4 only)
    case stats [member]     — Summary of action counts
    history [member]        — Alias for cases
"""

import logging

import discord
from discord.ext import commands

from utils.checks import require_permit, guild_only
from utils.embeds import make_embed, ok, fail, info

log = logging.getLogger("bot.cases")

_ACTION_COLOURS = {
    "warn":        discord.Colour.yellow(),
    "mute":        discord.Colour.orange(),
    "unmute":      discord.Colour.green(),
    "kick":        discord.Colour.orange(),
    "ban":         discord.Colour.red(),
    "unban":       discord.Colour.green(),
    "quarantine":  discord.Colour.dark_orange(),
    "unquarantine":discord.Colour.green(),
    "note":        discord.Colour.blurple(),
}
_ACTION_ICONS = {
    "warn":         "⚠️",
    "mute":         "🔇",
    "unmute":       "🔊",
    "kick":         "👢",
    "ban":          "🔨",
    "unban":        "🔓",
    "quarantine":   "🔒",
    "unquarantine": "🔓",
    "note":         "📝",
}


class Cases(commands.Cog, name="Cases"):
    def __init__(self, bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════════
    # Public API — called by other cogs to log actions
    # ══════════════════════════════════════════════════════════

    async def create_case(
        self,
        *,
        guild_id:     int,
        user_id:      int,
        moderator_id: int,
        action:       str,
        reason:       str,
        duration:     int | None = None,
    ) -> int:
        """
        Insert a new case record and return its case ID.

        Parameters
        ----------
        guild_id:     Guild where the action occurred.
        user_id:      Target user's ID.
        moderator_id: Moderator's ID.
        action:       Action string (warn/mute/kick/ban/etc.).
        reason:       Human-readable reason.
        duration:     Optional duration in seconds (for mutes/timeouts).
        """
        row = await self.bot.db.fetchone(
            """
            INSERT INTO cases
                (guild_id, user_id, moderator_id, action, reason, duration)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING case_id
            """,
            (guild_id, user_id, moderator_id, action, reason, duration),
        )
        case_id = row["case_id"] if row else -1
        log.debug(
            "Case #%d created: guild=%d user=%d action=%s",
            case_id, guild_id, user_id, action,
        )
        return case_id

    # ══════════════════════════════════════════════════════════
    # Commands
    # ══════════════════════════════════════════════════════════

    @commands.group(name="case", invoke_without_command=True)
    @guild_only()
    @require_permit(2)
    async def case_cmd(
        self,
        ctx:     commands.Context,
        case_id: int,
    ) -> None:
        """View a specific moderation case by its ID."""
        row = await self.bot.db.fetchone(
            """
            SELECT * FROM cases
            WHERE guild_id = ? AND case_id = ?
            """,
            (ctx.guild.id, case_id),
        )
        if not row:
            return await ctx.send(embed=fail(
                f"Case `#{case_id}` not found in this server."
            ))
        await ctx.send(embed=await self._case_embed(ctx.guild, row))

    @case_cmd.command(name="edit")
    @require_permit(3)
    async def case_edit(
        self,
        ctx:     commands.Context,
        case_id: int,
        *,
        reason:  str,
    ) -> None:
        """Edit the reason on an existing case."""
        row = await self.bot.db.fetchone(
            "SELECT case_id FROM cases WHERE guild_id = ? AND case_id = ?",
            (ctx.guild.id, case_id),
        )
        if not row:
            return await ctx.send(embed=fail(
                f"Case `#{case_id}` not found."
            ))

        await self.bot.db.execute(
            """
            UPDATE cases SET reason = ?, edited_by = ?, edited_at = CURRENT_TIMESTAMP
            WHERE guild_id = ? AND case_id = ?
            """,
            (reason, ctx.author.id, ctx.guild.id, case_id),
        )
        await ctx.send(embed=ok(f"Case `#{case_id}` reason updated."))

    @case_cmd.command(name="delete", aliases=["del", "remove"])
    @require_permit(4)
    async def case_delete(
        self,
        ctx:     commands.Context,
        case_id: int,
    ) -> None:
        """Permanently delete a case record."""
        row = await self.bot.db.fetchone(
            "SELECT case_id FROM cases WHERE guild_id = ? AND case_id = ?",
            (ctx.guild.id, case_id),
        )
        if not row:
            return await ctx.send(embed=fail(
                f"Case `#{case_id}` not found."
            ))

        await self.bot.db.execute(
            "DELETE FROM cases WHERE guild_id = ? AND case_id = ?",
            (ctx.guild.id, case_id),
        )
        await ctx.send(embed=ok(f"Case `#{case_id}` deleted."))

    @case_cmd.command(name="stats")
    @require_permit(2)
    async def case_stats(
        self,
        ctx:    commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        """Show a breakdown of moderation action counts."""
        if member:
            rows = await self.bot.db.fetchall(
                """
                SELECT action, COUNT(*) AS cnt
                FROM cases
                WHERE guild_id = ? AND user_id = ?
                GROUP BY action
                ORDER BY cnt DESC
                """,
                (ctx.guild.id, member.id),
            )
            title = f"📊 Case Stats — {member.display_name}"
        else:
            rows = await self.bot.db.fetchall(
                """
                SELECT action, COUNT(*) AS cnt
                FROM cases
                WHERE guild_id = ?
                GROUP BY action
                ORDER BY cnt DESC
                """,
                (ctx.guild.id,),
            )
            title = "📊 Case Stats — Server"

        if not rows:
            return await ctx.send(embed=info("No cases recorded yet."))

        lines = [f"`{r['action']:15s}` **{r['cnt']}**" for r in rows]
        total = sum(r["cnt"] for r in rows)
        lines.append(f"\n`{'TOTAL':15s}` **{total}**")

        embed = make_embed(
            title=title,
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
            timestamp=True,
        )
        if member:
            av = member.avatar or member.default_avatar
            embed.set_thumbnail(url=av.url)
        await ctx.send(embed=embed)

    @commands.command(name="cases", aliases=["history", "modlogs"])
    @guild_only()
    @require_permit(2)
    async def cases_list(
        self,
        ctx:    commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        """
        List all cases in this server, or filter by member.

        Paginated — shows 10 cases per page.
        """
        if member:
            rows = await self.bot.db.fetchall(
                """
                SELECT * FROM cases
                WHERE guild_id = ? AND user_id = ?
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (ctx.guild.id, member.id),
            )
            title = f"📋 Cases — {member.display_name} ({len(rows)})"
        else:
            rows = await self.bot.db.fetchall(
                """
                SELECT * FROM cases
                WHERE guild_id = ?
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (ctx.guild.id,),
            )
            title = f"📋 All Cases — {ctx.guild.name} ({len(rows)})"

        if not rows:
            return await ctx.send(embed=info(
                "No cases found."
                if not member else
                f"No cases found for **{member.display_name}**."
            ))

        # Build paginated embeds (10 per page)
        pages    = []
        per_page = 10
        chunks   = [rows[i:i+per_page] for i in range(0, len(rows), per_page)]

        for i, chunk in enumerate(chunks):
            lines = []
            for row in chunk:
                icon    = _ACTION_ICONS.get(row["action"], "•")
                ts      = str(row["created_at"])[:10]
                reason  = (row["reason"] or "*(no reason)*")[:40]
                lines.append(
                    f"`#{row['case_id']:04d}` {icon} `{row['action']:12s}` "
                    f"<@{row['user_id']}> — {reason} `{ts}`"
                )

            embed = make_embed(
                title       = title,
                description = "\n".join(lines),
                colour      = discord.Colour.blurple(),
                footer      = f"Page {i+1}/{len(chunks)}",
                timestamp   = True,
            )
            pages.append(embed)

        if len(pages) == 1:
            return await ctx.send(embed=pages[0])

        from utils.views import PaginatorView
        view = PaginatorView(pages, author=ctx.author)
        msg  = await ctx.send(embed=pages[0], view=view)
        view.message = msg

    # ══════════════════════════════════════════════════════════
    # Internal
    # ══════════════════════════════════════════════════════════

    async def _case_embed(
        self,
        guild: discord.Guild,
        row:   dict,
    ) -> discord.Embed:
        action  = row["action"]
        icon    = _ACTION_ICONS.get(action, "•")
        colour  = _ACTION_COLOURS.get(action, discord.Colour.blurple())

        user = guild.get_member(row["user_id"])
        mod  = guild.get_member(row["moderator_id"])

        user_str = user.mention if user else f"<@{row['user_id']}> *(left)*"
        mod_str  = mod.mention  if mod  else f"<@{row['moderator_id']}>"

        fields = [
            ("User",      user_str, True),
            ("Moderator", mod_str,  True),
            ("Action",    f"`{action}`", True),
            ("Reason",    row["reason"] or "*(no reason)*", False),
        ]

        if row.get("duration"):
            from utils.parsers import human_duration
            from datetime import timedelta
            fields.append((
                "Duration",
                human_duration(timedelta(seconds=row["duration"])),
                True,
            ))

        if row.get("edited_at"):
            edited_by = guild.get_member(row["edited_by"])
            fields.append((
                "Edited",
                f"by {edited_by.mention if edited_by else f'<@{row[\"edited_by\"]}>'} "
                f"at `{str(row['edited_at'])[:16]}`",
                False,
            ))

        embed = make_embed(
            title   = f"{icon} Case #{row['case_id']} — {action.title()}",
            colour  = colour,
            fields  = fields,
            footer  = f"Case ID: {row['case_id']}  •  {str(row['created_at'])[:16]}",
            timestamp=False,
        )

        if user:
            av = user.avatar or user.default_avatar
            embed.set_thumbnail(url=av.url)

        return embed


async def setup(bot):
    await bot.add_cog(Cases(bot))