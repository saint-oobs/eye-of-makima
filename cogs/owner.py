"""
Bot owner-only commands.

These commands require the invoker's ID to be in bot.owner_ids.
They are intentionally not listed in the public help menu.

Commands:
    reload <cog>        — Reload a cog by name
    reloadall           — Reload all loaded cogs
    load <cog>          — Load a cog
    unload <cog>        — Unload a cog
    coglist             — List all loaded cogs
    shutdown            — Gracefully shut down the bot
    restart             — Restart the bot process
    eval <code>         — Evaluate a Python expression
    sql <query>         — Run a raw SQL query
    setstatus <text>    — Change bot presence status text
    setactivity <type> <text> — Change bot activity
    guilds              — List all guilds the bot is in
    leaveguild <id>     — Leave a guild by ID
    watchdog            — Show watchdog health report
    clearcache [guild]  — Clear config/premium cache
"""

import asyncio
import io
import logging
import os
import sys
import textwrap
import traceback
from contextlib import redirect_stdout

import discord
from discord.ext import commands

from utils.checks import bot_owner_only
from utils.embeds import make_embed, ok, fail, info

log = logging.getLogger("bot.owner")


class Owner(commands.Cog, name="Owner"):
    def __init__(self, bot):
        self.bot    = bot
        self._last_eval_result = None

    def cog_check(self, ctx: commands.Context) -> bool:
        return ctx.bot.is_owner(ctx.author) or (
            hasattr(ctx.bot, "owner_ids")
            and ctx.author.id in (ctx.bot.owner_ids or set())
        )

    # ── Cog management ─────────────────────────────────────────

    @commands.command(name="reload", hidden=True)
    async def reload_cog(self, ctx: commands.Context, *, cog: str) -> None:
        """Reload a single cog."""
        try:
            await self.bot.reload_extension(f"cogs.{cog}")
            await ctx.send(embed=ok(f"Reloaded `cogs.{cog}`."))
        except Exception as exc:
            await ctx.send(embed=fail(f"```\n{exc}\n```"))

    @commands.command(name="reloadall", hidden=True)
    async def reload_all(self, ctx: commands.Context) -> None:
        """Reload every loaded cog."""
        failed  = []
        success = 0
        for ext in list(self.bot.extensions):
            try:
                await self.bot.reload_extension(ext)
                success += 1
            except Exception as exc:
                failed.append(f"`{ext}`: {exc}")

        if failed:
            await ctx.send(embed=make_embed(
                title="🔄 Reload All",
                description=f"✅ {success} reloaded\n❌ {len(failed)} failed\n\n" + "\n".join(failed),
                colour=discord.Colour.orange(),
            ))
        else:
            await ctx.send(embed=ok(f"All {success} cogs reloaded."))

    @commands.command(name="load", hidden=True)
    async def load_cog(self, ctx: commands.Context, *, cog: str) -> None:
        """Load a cog that isn't currently loaded."""
        try:
            await self.bot.load_extension(f"cogs.{cog}")
            await ctx.send(embed=ok(f"Loaded `cogs.{cog}`."))
        except Exception as exc:
            await ctx.send(embed=fail(f"```\n{exc}\n```"))

    @commands.command(name="unload", hidden=True)
    async def unload_cog(self, ctx: commands.Context, *, cog: str) -> None:
        """Unload a cog."""
        if cog in ("owner", "errors"):
            return await ctx.send(embed=fail("Cannot unload critical cogs."))
        try:
            await self.bot.unload_extension(f"cogs.{cog}")
            await ctx.send(embed=ok(f"Unloaded `cogs.{cog}`."))
        except Exception as exc:
            await ctx.send(embed=fail(f"```\n{exc}\n```"))

    @commands.command(name="coglist", hidden=True)
    async def cog_list(self, ctx: commands.Context) -> None:
        """List all currently loaded cogs."""
        exts = sorted(self.bot.extensions.keys())
        lines = [f"`{e}`" for e in exts]
        await ctx.send(embed=make_embed(
            title=f"🔧 Loaded Cogs ({len(exts)})",
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
        ))

    # ── Process control ────────────────────────────────────────

    @commands.command(name="shutdown", hidden=True)
    async def shutdown(self, ctx: commands.Context) -> None:
        """Gracefully shut down the bot."""
        await ctx.send(embed=ok("Shutting down…"))
        log.warning("Shutdown requested by %s", ctx.author)
        await self.bot.close()

    @commands.command(name="restart", hidden=True)
    async def restart(self, ctx: commands.Context) -> None:
        """Restart the bot process."""
        await ctx.send(embed=ok("Restarting…"))
        log.warning("Restart requested by %s", ctx.author)
        await self.bot.close()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ── Eval ───────────────────────────────────────────────────

    @commands.command(name="eval", hidden=True)
    async def eval_cmd(
        self,
        ctx:  commands.Context,
        *,
        code: str,
    ) -> None:
        """Evaluate Python code. Unsafe — owner only."""
        env = {
            "bot":   self.bot,
            "ctx":   ctx,
            "guild": ctx.guild,
            "ch":    ctx.channel,
            "me":    ctx.author,
            "_":     self._last_eval_result,
            "discord": discord,
            "commands": commands,
        }
        env.update(globals())

        # Strip code blocks
        if code.startswith("```"):
            code = "\n".join(code.split("\n")[1:])
        if code.startswith("`"):
            code = code.strip("`")
        code = code.rstrip("`")

        body = f"async def _eval_fn():\n{textwrap.indent(code, '    ')}"
        stdout = io.StringIO()

        try:
            exec(compile(body, "<eval>", "exec"), env)  # noqa: S102
            fn  = env["_eval_fn"]
            with redirect_stdout(stdout):
                ret = await fn()
        except Exception:
            output = stdout.getvalue()
            tb     = traceback.format_exc()
            await ctx.send(embed=fail(
                f"```py\n{output}{tb}\n```"[:3990]
            ))
            return

        output = stdout.getvalue()
        if ret is not None:
            self._last_eval_result = ret
            output += str(ret)

        if output:
            await ctx.send(embed=make_embed(
                title="✅ Eval",
                description=f"```py\n{output[:3900]}\n```",
                colour=discord.Colour.green(),
            ))
        else:
            await ctx.message.add_reaction("✅")

    # ── SQL ────────────────────────────────────────────────────

    @commands.command(name="sql", hidden=True)
    async def sql_cmd(
        self,
        ctx:   commands.Context,
        *,
        query: str,
    ) -> None:
        """Run a raw SQL query against the bot database."""
        query = query.strip().strip("`")
        try:
            if query.lower().startswith("select"):
                rows = await self.bot.db.fetchall(query)
                if not rows:
                    return await ctx.send(embed=info("No rows returned."))
                keys   = list(rows.keys())
                lines  = [" | ".join(keys)]
                lines += ["-" * len(lines)]
                for row in rows[:20]:
                    lines.append(" | ".join(str(row[k]) for k in keys))
                output = "\n".join(lines)
                await ctx.send(embed=make_embed(
                    title=f"SQL — {len(rows)} row(s)",
                    description=f"```\n{output[:3900]}\n```",
                    colour=discord.Colour.blurple(),
                ))
            else:
                await self.bot.db.execute(query)
                await ctx.send(embed=ok("Query executed."))
        except Exception as exc:
            await ctx.send(embed=fail(f"```\n{exc}\n```"))

    # ── Presence ───────────────────────────────────────────────

    @commands.command(name="setstatus", hidden=True)
    async def set_status(
        self,
        ctx:    commands.Context,
        status: str,
        *,
        text:   str = "",
    ) -> None:
        """
        Set bot status and optional activity text.

        Usage: setstatus online | idle | dnd | invisible [text]
        """
        status_map = {
            "online":    discord.Status.online,
            "idle":      discord.Status.idle,
            "dnd":       discord.Status.dnd,
            "invisible": discord.Status.invisible,
        }
        ds = status_map.get(status.lower())
        if not ds:
            return await ctx.send(embed=fail(
                f"Invalid status. Choose: {', '.join(status_map)}"
            ))
        activity = discord.Game(name=text) if text else None
        await self.bot.change_presence(status=ds, activity=activity)
        await ctx.send(embed=ok(f"Status set to `{status}`."))

    # ── Guild management ───────────────────────────────────────

    @commands.command(name="guilds", hidden=True)
    async def guilds_list(self, ctx: commands.Context) -> None:
        """List all guilds the bot is in."""
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count or 0, reverse=True)
        lines  = [
            f"`{g.id}` **{g.name}** — {g.member_count} members"
            for g in guilds[:30]
        ]
        if len(guilds) > 30:
            lines.append(f"*…and {len(guilds) - 30} more*")
        await ctx.send(embed=make_embed(
            title=f"🏠 Guilds ({len(guilds)})",
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
        ))

    @commands.command(name="leaveguild", hidden=True)
    async def leave_guild(
        self,
        ctx:      commands.Context,
        guild_id: int,
    ) -> None:
        """Force the bot to leave a guild by ID."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await ctx.send(embed=fail(f"Guild `{guild_id}` not found."))
        await guild.leave()
        await ctx.send(embed=ok(f"Left guild `{guild.name}` (`{guild_id}`)."))

    # ── Watchdog ───────────────────────────────────────────────

    @commands.command(name="watchdog", hidden=True)
    async def watchdog_cmd(self, ctx: commands.Context) -> None:
        """Show the bot health watchdog report."""
        wd = getattr(self.bot, "watchdog", None)
        if not wd:
            return await ctx.send(embed=info("Watchdog is not running."))

        s = wd.status()

        def fmt_dt(dt):
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "—"

        fields = [
            ("Started",       fmt_dt(s["started_at"]),       True),
            ("Last Check",    fmt_dt(s["last_check"]),        True),
            ("Latency",       f"`{s['latency_ms']}ms`",       True),
            ("Avg Latency",   f"`{s['avg_latency_ms']}ms`",   True),
            ("Peak Latency",  f"`{s['max_latency_ms']}ms`",   True),
            ("Disconnects",   f"`{s['disconnects']}`",        True),
            ("Guilds",        f"`{s['guilds']}`",             True),
            ("Users",         f"`{s['users']}`",              True),
            ("Memory",        f"`{s['memory_mb']} MB`" if s["memory_mb"] >= 0 else "N/A", True),
            ("CPU",           f"`{s['cpu_pct']}%`" if s["cpu_pct"] >= 0 else "N/A", True),
        ]

        alerts = s.get("recent_alerts", [])
        if alerts:
            fields.append(("Recent Alerts", "\n".join(alerts[-5:]), False))

        await ctx.send(embed=make_embed(
            title="🩺 Health Report",
            colour=discord.Colour.blurple(),
            fields=fields,
            timestamp=True,
        ))

    # ── Cache ──────────────────────────────────────────────────

    @commands.command(name="clearcache", hidden=True)
    async def clear_cache(
        self,
        ctx:      commands.Context,
        guild_id: int | None = None,
    ) -> None:
        """Clear the in-memory config cache for a guild or all guilds."""
        cache = getattr(self.bot, "config_cache", None)
        if not cache:
            return await ctx.send(embed=info("No cache module attached."))
        if guild_id:
            cache.clear_guild(guild_id)
            await ctx.send(embed=ok(f"Cache cleared for guild `{guild_id}`."))
        else:
            cache.clear()
            await ctx.send(embed=ok("Entire cache cleared."))


async def setup(bot):
    await bot.add_cog(Owner(bot))