import asyncio
import logging
import os
import time

import discord
from discord.ext import commands
from dotenv import load_dotenv

from config import ConfigManager
from database.db import Database
from utils.logger import setup_logging

load_dotenv()
setup_logging(os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("bot.main")

COGS = [
    "cogs.events",
    "cogs.heat",
    "cogs.heat_state",
    "cogs.heat_filters",
    "cogs.heat_commands",
    "cogs.heat_breach",
    "cogs.antinuke",
    "cogs.antinuke_panic",
    "cogs.antinuke_guards",
    "cogs.antinuke_commands",
    "cogs.joingate",
    "cogs.joingate_commands",
    "cogs.joinraid",
    "cogs.joinraid_commands",
    "cogs.verification",
    "cogs.verification_commands",
    "cogs.moderation",
    "cogs.modlog",
    "cogs.moderation_history",
    "cogs.network",
    "cogs.network_incidents",
    "cogs.network_notify",
    "cogs.network_autoheat",
    "cogs.network_autopunish",
    "cogs.network_grace",
    "cogs.network_commands",
    "cogs.setup",
    "cogs.statics",
    "cogs.rescue",
    "cogs.whitelist",
    "cogs.misc",
    "cogs.tshoot",
    "cogs.info",
    "cogs.owner",
    "cogs.wordlist",
    "cogs.help",
]


async def get_prefix(bot: "Bot", message: discord.Message):
    if message.guild:
        cfg = bot.config.get(message.guild.id)
        guild_prefix = cfg.get("prefix")
        if guild_prefix:
            return commands.when_mentioned_or(guild_prefix)(bot, message)
    default = os.getenv("BOT_PREFIX", "!")
    return commands.when_mentioned_or(default)(bot, message)


class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.moderation = True  # Required for on_audit_log_entry_create

        owner_ids = {
            int(x.strip())
            for x in os.getenv("OWNER_IDS", "").split(",")
            if x.strip().isdigit()
        }

        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            owner_ids=owner_ids,
            help_command=None,
            case_insensitive=True,
            strip_after_prefix=True,
        )

        self.config = ConfigManager()
        self.db = Database()
        self.bot_name: str = os.getenv("BOT_NAME", "SecurityBot")
        self.start_time: float = time.time()

        # Premium LRU cache: guild_id → (is_premium: bool, cached_at: float)
        self._premium_cache: dict[int, tuple[bool, float]] = {}
        self._premium_ttl: int = 60  # seconds

    # ── Premium check ──────────────────────────────────────────────
    async def is_premium(self, guild_id: int) -> bool:
        cached = self._premium_cache.get(guild_id)
        if cached and (time.time() - cached[1]) < self._premium_ttl:
            return cached[0]
        row = await self.db.fetchone(
            "SELECT 1 FROM premium_guilds "
            "WHERE guild_id = ? "
            "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)",
            (guild_id,),
        )
        result = row is not None
        self._premium_cache[guild_id] = (result, time.time())
        return result

    def premium_cache_invalidate(self, guild_id: int) -> None:
        self._premium_cache.pop(guild_id, None)

    # ── Startup ────────────────────────────────────────────────────
    async def setup_hook(self) -> None:
        await self.db.init()
        failed = []
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("Loaded: %s", cog)
            except Exception as exc:
                log.error("Failed to load %s: %s", cog, exc, exc_info=True)
                failed.append(cog)
        if failed:
            log.warning("Failed cogs: %s", ", ".join(failed))

    # ── Events ─────────────────────────────────────────────────────
    async def on_ready(self) -> None:
        log.info(
            "Ready — %s (ID: %d) — %d guilds",
            self.user, self.user.id, len(self.guilds),
        )
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.guilds)} servers | {os.getenv('BOT_PREFIX', '!')}help",
            )
        )

    # ── Error handler ──────────────────────────────────────────────
    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"⏱️ Slow down. Try again in **{error.retry_after:.1f}s**.",
                delete_after=5,
            )
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission to use this command.")
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.send(
                f"❌ I'm missing permissions: "
                f"{', '.join(error.missing_permissions)}"
            )
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send(f"❌ Member `{error.argument}` not found.")
        elif isinstance(error, commands.UserNotFound):
            await ctx.send(f"❌ User `{error.argument}` not found.")
        elif isinstance(error, commands.RoleNotFound):
            await ctx.send(f"❌ Role `{error.argument}` not found.")
        elif isinstance(error, commands.ChannelNotFound):
            await ctx.send(f"❌ Channel `{error.argument}` not found.")
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"❌ Invalid argument: {error}")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                f"❌ Missing argument: `{error.param.name}`\n"
                f"Use `{ctx.prefix}help {ctx.command.qualified_name}` for usage."
            )
        elif isinstance(error, commands.CommandNotFound):
            pass  # Silently ignore unknown commands
        elif isinstance(error, commands.CheckFailure):
            pass  # Permit-level checks handle their own messages
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("❌ This command can only be used in a server.")
        else:
            log.error(
                "Unhandled error in command '%s': %s",
                ctx.command, error, exc_info=True,
            )
            await ctx.send("❌ An unexpected error occurred. Please try again.")


# ── Entry point ────────────────────────────────────────────────────
async def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN not set in environment.")
    async with Bot() as bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())