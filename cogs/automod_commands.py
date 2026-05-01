"""
AutoMod command group.

Command group: g!automod (alias: g!am)

Subcommands:
    status                          — Show full automod configuration
    enable / disable                — Toggle automod system
    exempt channel <add|remove>     — Exempt a channel from automod
    exempt role <add|remove>        — Exempt a role from automod
    exempt list                     — Show all exemptions
    invites <enable|disable>        — Toggle invite filter
    invites action <action>         — Set invite filter action
    invites whitelist <add|remove>  — Manage invite code whitelist
    links <enable|disable>          — Toggle link filter
    links mode <whitelist|blacklist>— Set link filter mode
    links action <action>           — Set link filter action
    links domain <add|remove>       — Manage domain whitelist/blacklist
    words <enable|disable>          — Toggle word filter
    words action <action>           — Set word filter action
    words add <word>                — Add a word to the filter
    words remove <word>             — Remove a word from the filter
    words list                      — List filtered words
    words regex <on|off>            — Toggle regex mode for word filter
    spam <enable|disable>           — Toggle spam filter
    spam set count <n>              — Set message count limit
    spam set window <s>             — Set window in seconds
    spam action <action>            — Set spam filter action
    mentions <enable|disable>       — Toggle mention spam filter
    mentions set <n>                — Set max mentions per message
    mentions action <action>        — Set action
    emoji <enable|disable>          — Toggle emoji spam filter
    emoji set <n>                   — Set max emojis per message
    emoji action <action>           — Set action
    caps <enable|disable>           — Toggle caps filter
    caps set <pct>                  — Set uppercase threshold (%)
    caps action <action>            — Set action
    duplicate <enable|disable>      — Toggle duplicate filter
    duplicate set <n>               — Set max duplicate count
    duplicate action <action>       — Set action
"""

import logging

import discord
from discord.ext import commands

from utils.checks import require_permit, guild_only
from utils.helpers import make_embed, success_embed, error_embed, info_embed
from utils.paginator import send_paginated, build_pages

log = logging.getLogger("bot.automod_commands")

_VALID_ACTIONS = (
    "delete", "warn", "mute", "kick", "ban",
    "delete_warn", "delete_mute", "delete_kick", "delete_ban", "log",
)


class AutoModCommands(commands.Cog, name="AutoMod"):
    def __init__(self, bot):
        self.bot = bot

    # ── Helpers ────────────────────────────────────────────────

    def _valid_action(self, action: str) -> bool:
        return action.lower() in _VALID_ACTIONS

    def _fmt_check(self, check: dict, extras: str = "") -> str:
        enabled = "✅" if check.get("enabled") else "❌"
        action  = check.get("action", "—")
        return f"{enabled} `{action}`{extras}"

    # ── Group ──────────────────────────────────────────────────

    @commands.group(name="automod", aliases=["am"], invoke_without_command=True)
    @guild_only()
    async def automod(self, ctx: commands.Context) -> None:
        """AutoMod system management."""
        await ctx.send_help(ctx.command)

    # ── status ─────────────────────────────────────────────────

    @automod.command(name="status")
    @require_permit(2)
    async def am_status(self, ctx: commands.Context) -> None:
        """Show the full automod configuration."""
        cfg = self.bot.config.get(ctx.guild.id)
        am  = cfg.get("automod", {})

        def _row(key: str, label: str, extra: str = "") -> tuple:
            check = am.get(key, {})
            return (label, self._fmt_check(check, extra), True)

        spam       = am.get("spam_filter", {})
        caps       = am.get("caps_filter", {})
        dup        = am.get("duplicate_filter", {})
        mentions   = am.get("mention_spam", {})
        emoji      = am.get("emoji_spam", {})
        links      = am.get("link_filter", {})

        ex_channels = am.get("exempt_channels", [])
        ex_roles    = am.get("exempt_roles", [])

        embed = make_embed(
            title="🤖 AutoMod Configuration",
            colour=discord.Colour.blurple(),
            fields=[
                ("Enabled",         "✅" if am.get("enabled", True) else "❌", True),
                ("Exempt Permit",   f"`{am.get('exempt_permit_level', 3)}+`",  True),
                ("Exemptions",      f"`{len(ex_channels)}` ch · `{len(ex_roles)}` roles", True),
                _row("invite_filter",   "Invite Filter"),
                _row("link_filter",     "Link Filter",
                     f" · mode: `{links.get('mode', 'blacklist')}`"),
                _row("word_filter",     "Word Filter"),
                _row("spam_filter",     "Spam Filter",
                     f" · `{spam.get('message_count', 5)}`msg/`{spam.get('window_seconds', 5)}`s"),
                _row("mention_spam",    "Mention Spam",
                     f" · max `{mentions.get('max_mentions', 5)}`"),
                _row("emoji_spam",      "Emoji Spam",
                     f" · max `{emoji.get('max_emojis', 10)}`"),
                _row("caps_filter",     "Caps Filter",
                     f" · `{caps.get('threshold_pct', 70)}`%"),
                _row("duplicate_filter","Duplicate Filter",
                     f" · max `{dup.get('max_duplicates', 3)}`"),
            ],
            timestamp=True,
        )
        await ctx.send(embed=embed)

    # ── enable / disable ───────────────────────────────────────

    @automod.command(name="enable")
    @require_permit(3)
    async def am_enable(self, ctx: commands.Context) -> None:
        """Enable the AutoMod system."""
        self.bot.config.set(ctx.guild.id, ["automod", "enabled"], True)
        await ctx.send(embed=success_embed("AutoMod system **enabled**."))

    @automod.command(name="disable")
    @require_permit(3)
    async def am_disable(self, ctx: commands.Context) -> None:
        """Disable the AutoMod system."""
        self.bot.config.set(ctx.guild.id, ["automod", "enabled"], False)
        await ctx.send(embed=success_embed("AutoMod system **disabled**."))

    # ── exempt group ───────────────────────────────────────────

    @automod.group(name="exempt", invoke_without_command=True)
    @guild_only()
    async def am_exempt(self, ctx: commands.Context) -> None:
        """Manage automod exemptions."""
        await ctx.send_help(ctx.command)

    @am_exempt.command(name="channel")
    @require_permit(3)
    async def exempt_channel(
        self,
        ctx:     commands.Context,
        toggle:  str,
        channel: discord.TextChannel,
    ) -> None:
        """Add or remove a channel from the automod exemption list."""
        toggle = toggle.lower()
        cfg    = self.bot.config.get(ctx.guild.id)
        lst    = cfg["automod"].setdefault("exempt_channels", [])

        if toggle == "add":
            if channel.id in lst:
                return await ctx.send(embed=info_embed(
                    f"{channel.mention} is already exempt."
                ))
            lst.append(channel.id)
            self.bot.config.save(ctx.guild.id)
            await ctx.send(embed=success_embed(f"Exempted {channel.mention} from AutoMod."))
        elif toggle == "remove":
            if channel.id not in lst:
                return await ctx.send(embed=error_embed(
                    f"{channel.mention} is not in the exempt list."
                ))
            lst.remove(channel.id)
            self.bot.config.save(ctx.guild.id)
            await ctx.send(embed=success_embed(f"Removed {channel.mention} from AutoMod exemptions."))
        else:
            await ctx.send(embed=error_embed("Use `add` or `remove`."))

    @am_exempt.command(name="role")
    @require_permit(3)
    async def exempt_role(
        self,
        ctx:    commands.Context,
        toggle: str,
        role:   discord.Role,
    ) -> None:
        """Add or remove a role from the automod exemption list."""
        toggle = toggle.lower()
        cfg    = self.bot.config.get(ctx.guild.id)
        lst    = cfg["automod"].setdefault("exempt_roles", [])

        if toggle == "add":
            if role.id in lst:
                return await ctx.send(embed=info_embed(
                    f"{role.mention} is already exempt."
                ))
            lst.append(role.id)
            self.bot.config.save(ctx.guild.id)
            await ctx.send(embed=success_embed(f"Exempted {role.mention} from AutoMod."))
        elif toggle == "remove":
            if role.id not in lst:
                return await ctx.send(embed=error_embed(
                    f"{role.mention} is not in the exempt list."
                ))
            lst.remove(role.id)
            self.bot.config.save(ctx.guild.id)
            await ctx.send(embed=success_embed(f"Removed {role.mention} from AutoMod exemptions."))
        else:
            await ctx.send(embed=error_embed("Use `add` or `remove`."))

    @am_exempt.command(name="list")
    @require_permit(2)
    async def exempt_list(self, ctx: commands.Context) -> None:
        """Show all automod exemptions."""
        cfg      = self.bot.config.get(ctx.guild.id)
        am       = cfg.get("automod", {})
        channels = am.get("exempt_channels", [])
        roles    = am.get("exempt_roles", [])

        ch_mentions   = [f"<#{cid}>" for cid in channels] or ["*(none)*"]
        role_mentions = [f"<@&{rid}>" for rid in roles]   or ["*(none)*"]

        embed = make_embed(
            title="🤖 AutoMod Exemptions",
            colour=discord.Colour.blurple(),
            fields=[
                ("Channels", " ".join(ch_mentions),   False),
                ("Roles",    " ".join(role_mentions),  False),
            ],
        )
        await ctx.send(embed=embed)

    # ── invites ────────────────────────────────────────────────

    @automod.group(name="invites", invoke_without_command=True)
    @guild_only()
    async def am_invites(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle or configure the invite filter."""
        if toggle:
            await self._toggle_check(ctx, "invite_filter", toggle)
        else:
            await ctx.send_help(ctx.command)

    @am_invites.command(name="action")
    @require_permit(3)
    async def invites_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the invite filter."""
        await self._set_action(ctx, "invite_filter", action)

    @am_invites.command(name="whitelist")
    @require_permit(3)
    async def invites_whitelist(
        self,
        ctx:    commands.Context,
        toggle: str,
        code:   str,
    ) -> None:
        """Add or remove an invite code from the whitelist."""
        await self._list_op(
            ctx, ["automod", "invite_filter", "whitelist_codes"],
            toggle, code, "invite code",
        )

    # ── links ──────────────────────────────────────────────────

    @automod.group(name="links", invoke_without_command=True)
    @guild_only()
    async def am_links(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle or configure the link filter."""
        if toggle:
            await self._toggle_check(ctx, "link_filter", toggle)
        else:
            await ctx.send_help(ctx.command)

    @am_links.command(name="mode")
    @require_permit(3)
    async def links_mode(self, ctx: commands.Context, mode: str) -> None:
        """Set link filter mode: whitelist | blacklist"""
        mode = mode.lower()
        if mode not in ("whitelist", "blacklist"):
            return await ctx.send(embed=error_embed(
                "Mode must be `whitelist` or `blacklist`."
            ))
        self.bot.config.set(ctx.guild.id, ["automod", "link_filter", "mode"], mode)
        await ctx.send(embed=success_embed(f"Link filter mode set to **{mode}**."))

    @am_links.command(name="action")
    @require_permit(3)
    async def links_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the link filter."""
        await self._set_action(ctx, "link_filter", action)

    @am_links.command(name="domain")
    @require_permit(3)
    async def links_domain(
        self,
        ctx:    commands.Context,
        toggle: str,
        domain: str,
    ) -> None:
        """
        Add or remove a domain from the whitelist or blacklist.
        The active list is determined by the current link filter mode.
        """
        cfg  = self.bot.config.get(ctx.guild.id)
        mode = cfg.get("automod", {}).get("link_filter", {}).get("mode", "blacklist")
        key  = f"{mode}_domains"
        await self._list_op(
            ctx, ["automod", "link_filter", key],
            toggle, domain.lower(), f"{mode} domain",
        )

    # ── words ──────────────────────────────────────────────────

    @automod.group(name="words", invoke_without_command=True)
    @guild_only()
    async def am_words(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle or configure the word filter."""
        if toggle:
            await self._toggle_check(ctx, "word_filter", toggle)
        else:
            await ctx.send_help(ctx.command)

    @am_words.command(name="action")
    @require_permit(3)
    async def words_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the word filter."""
        await self._set_action(ctx, "word_filter", action)

    @am_words.command(name="add")
    @require_permit(3)
    async def words_add(self, ctx: commands.Context, *, word: str) -> None:
        """Add a word or phrase to the word filter."""
        await self._list_op(
            ctx, ["automod", "word_filter", "words"],
            "add", word.lower(), "word/phrase",
        )

    @am_words.command(name="remove", aliases=["rm"])
    @require_permit(3)
    async def words_remove(self, ctx: commands.Context, *, word: str) -> None:
        """Remove a word or phrase from the word filter."""
        await self._list_op(
            ctx, ["automod", "word_filter", "words"],
            "remove", word.lower(), "word/phrase",
        )

    @am_words.command(name="list")
    @require_permit(2)
    async def words_list(self, ctx: commands.Context) -> None:
        """List all words in the word filter."""
        cfg   = self.bot.config.get(ctx.guild.id)
        words = cfg.get("automod", {}).get("word_filter", {}).get("words", [])

        if not words:
            return await ctx.send(embed=info_embed("No words in the word filter."))

        pages = build_pages(
            [f"`{w}`" for w in words],
            title=f"🤖 Word Filter ({len(words)} entries)",
            colour=discord.Colour.blurple(),
            per_page=20,
            numbered=True,
        )
        await send_paginated(ctx, pages)

    @am_words.command(name="regex")
    @require_permit(3)
    async def words_regex(self, ctx: commands.Context, toggle: str) -> None:
        """Toggle regex mode for the word filter."""
        val = toggle.lower() in ("on", "enable", "true", "yes", "1")
        self.bot.config.set(ctx.guild.id, ["automod", "word_filter", "use_regex"], val)
        await ctx.send(embed=success_embed(
            f"Word filter regex mode **{'enabled' if val else 'disabled'}**."
        ))

    # ── spam ───────────────────────────────────────────────────

    @automod.group(name="spam", invoke_without_command=True)
    @guild_only()
    async def am_spam(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle or configure the spam filter."""
        if toggle:
            await self._toggle_check(ctx, "spam_filter", toggle)
        else:
            await ctx.send_help(ctx.command)

    @am_spam.command(name="action")
    @require_permit(3)
    async def spam_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the spam filter."""
        await self._set_action(ctx, "spam_filter", action)

    @am_spam.group(name="set", invoke_without_command=True)
    @guild_only()
    async def spam_set(self, ctx: commands.Context) -> None:
        """Update spam filter thresholds."""
        await ctx.send_help(ctx.command)

    @spam_set.command(name="count")
    @require_permit(3)
    async def spam_set_count(self, ctx: commands.Context, count: int) -> None:
        """Set the number of messages that triggers spam detection."""
        if count < 2:
            return await ctx.send(embed=error_embed("Count must be at least `2`."))
        self.bot.config.set(ctx.guild.id, ["automod", "spam_filter", "message_count"], count)
        await ctx.send(embed=success_embed(f"Spam message count set to **{count}**."))

    @spam_set.command(name="window")
    @require_permit(3)
    async def spam_set_window(self, ctx: commands.Context, seconds: int) -> None:
        """Set the rolling window (seconds) for spam detection."""
        if not (1 <= seconds <= 60):
            return await ctx.send(embed=error_embed("Window must be between `1` and `60` seconds."))
        self.bot.config.set(ctx.guild.id, ["automod", "spam_filter", "window_seconds"], seconds)
        await ctx.send(embed=success_embed(f"Spam window set to **{seconds}s**."))

    # ── mentions ───────────────────────────────────────────────

    @automod.group(name="mentions", invoke_without_command=True)
    @guild_only()
    async def am_mentions(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle or configure the mention spam filter."""
        if toggle:
            await self._toggle_check(ctx, "mention_spam", toggle)
        else:
            await ctx.send_help(ctx.command)

    @am_mentions.command(name="set")
    @require_permit(3)
    async def mentions_set(self, ctx: commands.Context, count: int) -> None:
        """Set the maximum mentions allowed per message."""
        if count < 1:
            return await ctx.send(embed=error_embed("Count must be at least `1`."))
        self.bot.config.set(ctx.guild.id, ["automod", "mention_spam", "max_mentions"], count)
        await ctx.send(embed=success_embed(f"Max mentions per message set to **{count}**."))

    @am_mentions.command(name="action")
    @require_permit(3)
    async def mentions_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the mention spam filter."""
        await self._set_action(ctx, "mention_spam", action)

    # ── emoji ──────────────────────────────────────────────────

    @automod.group(name="emoji", invoke_without_command=True)
    @guild_only()
    async def am_emoji(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle or configure the emoji spam filter."""
        if toggle:
            await self._toggle_check(ctx, "emoji_spam", toggle)
        else:
            await ctx.send_help(ctx.command)

    @am_emoji.command(name="set")
    @require_permit(3)
    async def emoji_set(self, ctx: commands.Context, count: int) -> None:
        """Set the maximum emojis allowed per message."""
        if count < 1:
            return await ctx.send(embed=error_embed("Count must be at least `1`."))
        self.bot.config.set(ctx.guild.id, ["automod", "emoji_spam", "max_emojis"], count)
        await ctx.send(embed=success_embed(f"Max emojis per message set to **{count}**."))

    @am_emoji.command(name="action")
    @require_permit(3)
    async def emoji_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the emoji spam filter."""
        await self._set_action(ctx, "emoji_spam", action)

    # ── caps ───────────────────────────────────────────────────

    @automod.group(name="caps", invoke_without_command=True)
    @guild_only()
    async def am_caps(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle or configure the caps filter."""
        if toggle:
            await self._toggle_check(ctx, "caps_filter", toggle)
        else:
            await ctx.send_help(ctx.command)

    @am_caps.command(name="set")
    @require_permit(3)
    async def caps_set(self, ctx: commands.Context, percent: int) -> None:
        """Set the uppercase threshold percentage (e.g. 70 = 70% caps triggers)."""
        if not (10 <= percent <= 100):
            return await ctx.send(embed=error_embed("Threshold must be between `10` and `100`."))
        self.bot.config.set(ctx.guild.id, ["automod", "caps_filter", "threshold_pct"], percent)
        await ctx.send(embed=success_embed(f"Caps threshold set to **{percent}%**."))

    @am_caps.command(name="action")
    @require_permit(3)
    async def caps_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the caps filter."""
        await self._set_action(ctx, "caps_filter", action)

    # ── duplicate ──────────────────────────────────────────────

    @automod.group(name="duplicate", invoke_without_command=True)
    @guild_only()
    async def am_duplicate(self, ctx: commands.Context, toggle: str | None = None) -> None:
        """Toggle or configure the duplicate message filter."""
        if toggle:
            await self._toggle_check(ctx, "duplicate_filter", toggle)
        else:
            await ctx.send_help(ctx.command)

    @am_duplicate.command(name="set")
    @require_permit(3)
    async def duplicate_set(self, ctx: commands.Context, count: int) -> None:
        """Set how many duplicate messages trigger the filter."""
        if count < 2:
            return await ctx.send(embed=error_embed("Count must be at least `2`."))
        self.bot.config.set(ctx.guild.id, ["automod", "duplicate_filter", "max_duplicates"], count)
        await ctx.send(embed=success_embed(f"Duplicate threshold set to **{count}**."))

    @am_duplicate.command(name="action")
    @require_permit(3)
    async def duplicate_action(self, ctx: commands.Context, action: str) -> None:
        """Set the action for the duplicate filter."""
        await self._set_action(ctx, "duplicate_filter", action)

    # ── Shared helpers ─────────────────────────────────────────

    async def _toggle_check(
        self,
        ctx:       commands.Context,
        check_key: str,
        toggle:    str,
    ) -> None:
        """Enable or disable a named automod check."""
        perm_check = require_permit(3)
        try:
            await perm_check.predicate(ctx)
        except Exception:
            return

        val = toggle.lower() in ("enable", "on", "true", "yes", "1")
        if toggle.lower() not in (
            "enable", "disable", "on", "off", "true", "false", "yes", "no", "1", "0"
        ):
            return await ctx.send(embed=error_embed("Use `enable` or `disable`."))

        self.bot.config.set(ctx.guild.id, ["automod", check_key, "enabled"], val)
        label = check_key.replace("_", " ").title()
        await ctx.send(embed=success_embed(
            f"**{label}** **{'enabled' if val else 'disabled'}**."
        ))

    async def _set_action(
        self,
        ctx:       commands.Context,
        check_key: str,
        action:    str,
    ) -> None:
        """Set the action for a named automod check."""
        action = action.lower()
        if not self._valid_action(action):
            actions_fmt = ", ".join(f"`{a}`" for a in _VALID_ACTIONS)
            return await ctx.send(embed=error_embed(
                f"Invalid action `{action}`.\nValid actions: {actions_fmt}"
            ))
        self.bot.config.set(ctx.guild.id, ["automod", check_key, "action"], action)
        label = check_key.replace("_", " ").title()
        await ctx.send(embed=success_embed(
            f"**{label}** action set to `{action}`."
        ))

    async def _list_op(
        self,
        ctx:   commands.Context,
        path:  list[str],
        op:    str,
        value: str,
        label: str,
    ) -> None:
        """Generic add/remove operation on a list config value."""
        op = op.lower()
        if op not in ("add", "remove"):
            return await ctx.send(embed=error_embed("Use `add` or `remove`."))

        cfg  = self.bot.config.get(ctx.guild.id)
        node = cfg
        for key in path[:-1]:
            node = node.setdefault(key, {})
        lst  = node.setdefault(path[-1], [])

        if op == "add":
            if value in lst:
                return await ctx.send(embed=info_embed(
                    f"`{value}` is already in the {label} list."
                ))
            lst.append(value)
            self.bot.config.save(ctx.guild.id)
            await ctx.send(embed=success_embed(f"Added `{value}` to {label} list."))
        else:
            if value not in lst:
                return await ctx.send(embed=error_embed(
                    f"`{value}` was not found in the {label} list."
                ))
            lst.remove(value)
            self.bot.config.save(ctx.guild.id)
            await ctx.send(embed=success_embed(f"Removed `{value}` from {label} list."))


async def setup(bot):
    await bot.add_cog(AutoModCommands(bot))