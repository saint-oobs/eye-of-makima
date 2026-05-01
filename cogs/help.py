"""
Custom help command.

Replaces discord.py's built-in HelpCommand with a paginated embed
that groups commands by cog, respects permit level visibility,
and provides per-command and per-cog detail pages.

Commands:
    help [command|cog]   — Show help for everything, a cog, or a command
"""

import logging
from typing import Any

import discord
from discord.ext import commands

from utils.embeds import make_embed
from utils.views  import PaginatorView

log = logging.getLogger("bot.help")

_HIDDEN_COGS = {"Owner", "ErrorHandler"}


class GuardBotHelp(commands.HelpCommand):
    """
    Paginated embeds with per-cog grouping.
    Falls back to permission-respecting filtering.
    """

    # ── Main index ────────────────────────────────────────────

    async def send_bot_help(
        self,
        mapping: dict[commands.Cog | None, list[commands.Command]],
    ) -> None:
        ctx    = self.context
        prefix = ctx.clean_prefix
        pages  = []

        # Cover page
        cover = make_embed(
            title="📖 Help Menu",
            description=(
                f"Use `{prefix}help <command>` for detailed help on a command.\n"
                f"Use `{prefix}help <category>` for all commands in a category.\n\n"
                f"Commands marked 🔒 require elevated permit levels."
            ),
            colour=discord.Colour.blurple(),
            footer=f"Prefix: {prefix}  •  {len(list(self.get_bot_mapping()))} categories",
            timestamp=True,
        )
        if ctx.bot.user.avatar:
            cover.set_thumbnail(url=ctx.bot.user.avatar.url)

        # Category list on cover
        lines = []
        for cog, cmds in mapping.items():
            if not cog:
                continue
            if cog.qualified_name in _HIDDEN_COGS:
                continue
            filtered = await self.filter_commands(cmds, sort=True)
            if filtered:
                lines.append(
                    f"**{cog.qualified_name}** — {len(filtered)} command(s)\n"
                    f"_{cog.description or 'No description.'}_"
                )
        cover.add_field(
            name="Categories",
            value="\n\n".join(lines) or "No categories available.",
            inline=False,
        )
        pages.append(cover)

        # One page per cog
        for cog, cmds in mapping.items():
            if not cog or cog.qualified_name in _HIDDEN_COGS:
                continue
            filtered = await self.filter_commands(cmds, sort=True)
            if not filtered:
                continue
            pages.append(self._cog_page(cog, filtered, prefix))

        await self._send_pages(pages)

    # ── Cog help page ─────────────────────────────────────────

    async def send_cog_help(self, cog: commands.Cog) -> None:
        prefix   = self.context.clean_prefix
        filtered = await self.filter_commands(cog.get_commands(), sort=True)
        if not filtered:
            await self.get_destination().send(
                embed=make_embed(
                    title=f"📂 {cog.qualified_name}",
                    description="No commands available.",
                    colour=discord.Colour.orange(),
                )
            )
            return
        await self._send_pages([self._cog_page(cog, filtered, prefix)])

    # ── Command help page ─────────────────────────────────────

    async def send_command_help(self, command: commands.Command) -> None:
        prefix = self.context.clean_prefix
        embed  = make_embed(
            title=f"`{prefix}{command.qualified_name} {command.signature}`",
            description=command.help or command.brief or "*No description provided.*",
            colour=discord.Colour.blurple(),
            timestamp=True,
        )

        if command.aliases:
            embed.add_field(
                name="Aliases",
                value=", ".join(f"`{a}`" for a in command.aliases),
                inline=False,
            )

        cd = command._buckets._cooldown  # type: ignore[attr-defined]
        if cd:
            embed.add_field(
                name="Cooldown",
                value=f"`{cd.rate}` use(s) per `{cd.per:.0f}s`",
                inline=True,
            )

        checks = [c.__qualname__.split(".")[0] for c in command.checks]
        if checks:
            embed.add_field(
                name="Requirements",
                value="\n".join(f"• `{c}`" for c in checks),
                inline=False,
            )

        await self.get_destination().send(embed=embed)

    # ── Group help page ───────────────────────────────────────

    async def send_group_help(self, group: commands.Group) -> None:
        prefix   = self.context.clean_prefix
        filtered = await self.filter_commands(group.commands, sort=True)
        lines    = [
            f"`{prefix}{group.qualified_name} {c.name}` — {c.brief or c.short_doc or '…'}"
            for c in filtered
        ]
        embed = make_embed(
            title=f"📂 {group.qualified_name}",
            description=(
                f"{group.help or '*No description.*'}\n\n"
                + ("\n".join(lines) if lines else "*No subcommands available.*")
            ),
            colour=discord.Colour.blurple(),
            footer=f"Use {prefix}help {group.qualified_name} <subcommand> for more detail.",
            timestamp=True,
        )
        await self.get_destination().send(embed=embed)

    # ── Error ─────────────────────────────────────────────────

    async def send_error_message(self, error: str) -> None:
        embed = make_embed(
            description=f"❌ {error}",
            colour=discord.Colour.red(),
        )
        await self.get_destination().send(embed=embed)

    # ── Helpers ───────────────────────────────────────────────

    def _cog_page(
        self,
        cog:      commands.Cog,
        commands: list[commands.Command],
        prefix:   str,
    ) -> discord.Embed:
        lines = []
        for cmd in commands:
            name  = f"`{prefix}{cmd.qualified_name}`"
            brief = cmd.brief or cmd.short_doc or "…"
            lines.append(f"{name} — {brief}")

        return make_embed(
            title=f"📂 {cog.qualified_name}",
            description=(
                f"_{cog.description or 'No description.'}_\n\n"
                + "\n".join(lines)
            ),
            colour=discord.Colour.blurple(),
            footer=f"{len(commands)} command(s)  •  {prefix}help <command> for detail",
            timestamp=True,
        )

    async def _send_pages(self, pages: list[discord.Embed]) -> None:
        dest = self.get_destination()
        if len(pages) == 1:
            await dest.send(embed=pages[0])
            return
        view = PaginatorView(pages, author=self.context.author)
        msg  = await dest.send(embed=pages[0], view=view)
        view.message = msg


class Help(commands.Cog, name="Help"):
    """Bot help and documentation."""

    def __init__(self, bot):
        self.bot = bot
        self._original_help = bot.help_command
        bot.help_command     = GuardBotHelp(
            command_attrs={
                "name":    "help",
                "aliases": ["h", "?"],
                "help":    "Show help for a command, category, or the full bot.",
            }
        )
        bot.help_command.cog = self

    def cog_unload(self):
        self.bot.help_command = self._original_help


async def setup(bot):
    await bot.add_cog(Help(bot))