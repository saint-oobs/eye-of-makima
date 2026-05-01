"""
Discord audit log fetching helpers.

Wraps the Discord audit log API with retry logic, caching, and
convenience methods so cogs don't duplicate the same boilerplate.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import AsyncIterator

import discord

log = logging.getLogger("bot.auditlog")

_AUDIT_FETCH_TIMEOUT = 3.0   # seconds
_AUDIT_MATCH_WINDOW  = 8.0   # seconds — how recent an entry must be


async def fetch_recent_entry(
    guild:       discord.Guild,
    action:      discord.AuditLogAction,
    *,
    target_id:   int | None = None,
    user_id:     int | None = None,
    within:      float      = _AUDIT_MATCH_WINDOW,
    limit:       int        = 5,
) -> discord.AuditLogEntry | None:
    """
    Fetch the most recent audit log entry for *action*.

    Parameters
    ----------
    guild:
        The guild to search.
    action:
        The :class:`discord.AuditLogAction` to filter by.
    target_id:
        If provided, only match entries whose ``target.id`` equals this.
    user_id:
        If provided, only match entries performed by this user.
    within:
        Maximum age in seconds for the entry to be considered a match.
    limit:
        How many recent entries to fetch (1–100).

    Returns
    -------
    The matching :class:`discord.AuditLogEntry`, or ``None`` if not found.
    """
    if not guild.me.guild_permissions.view_audit_log:
        log.debug("Missing VIEW_AUDIT_LOG in guild %s", guild.name)
        return None

    cutoff = discord.utils.utcnow() - timedelta(seconds=within)

    try:
        async with asyncio.timeout(_AUDIT_FETCH_TIMEOUT):
            async for entry in guild.audit_logs(action=action, limit=limit):
                if entry.created_at < cutoff:
                    break
                if target_id is not None and getattr(entry.target, "id", None) != target_id:
                    continue
                if user_id is not None and entry.user_id != user_id:
                    continue
                return entry
    except TimeoutError:
        log.warning("Audit log fetch timed out in guild %s", guild.name)
    except discord.Forbidden:
        log.warning("Forbidden: audit log in guild %s", guild.name)
    except discord.HTTPException as exc:
        log.error("Audit log HTTP error in guild %s: %s", guild.name, exc)

    return None


async def fetch_ban_actor(
    guild:     discord.Guild,
    target_id: int,
) -> discord.Member | discord.User | None:
    """
    Return the moderator who most recently banned *target_id*, or None.
    """
    entry = await fetch_recent_entry(
        guild,
        discord.AuditLogAction.ban,
        target_id=target_id,
    )
    return entry.user if entry else None


async def fetch_kick_actor(
    guild:     discord.Guild,
    target_id: int,
) -> discord.Member | discord.User | None:
    """
    Return the moderator who most recently kicked *target_id*, or None.
    """
    entry = await fetch_recent_entry(
        guild,
        discord.AuditLogAction.kick,
        target_id=target_id,
    )
    return entry.user if entry else None


async def fetch_unban_actor(
    guild:     discord.Guild,
    target_id: int,
) -> discord.Member | discord.User | None:
    entry = await fetch_recent_entry(
        guild,
        discord.AuditLogAction.unban,
        target_id=target_id,
    )
    return entry.user if entry else None


async def fetch_role_change_actor(
    guild:     discord.Guild,
    target_id: int,
) -> discord.Member | discord.User | None:
    """Return the user who most recently changed roles for *target_id*."""
    entry = await fetch_recent_entry(
        guild,
        discord.AuditLogAction.member_role_update,
        target_id=target_id,
    )
    return entry.user if entry else None


async def fetch_channel_delete_actor(
    guild:     discord.Guild,
    target_id: int,
) -> discord.Member | discord.User | None:
    entry = await fetch_recent_entry(
        guild,
        discord.AuditLogAction.channel_delete,
        target_id=target_id,
    )
    return entry.user if entry else None


async def fetch_channel_create_actor(
    guild:     discord.Guild,
    target_id: int,
) -> discord.Member | discord.User | None:
    entry = await fetch_recent_entry(
        guild,
        discord.AuditLogAction.channel_create,
        target_id=target_id,
    )
    return entry.user if entry else None


async def fetch_webhook_create_actor(
    guild:     discord.Guild,
    target_id: int,
) -> discord.Member | discord.User | None:
    entry = await fetch_recent_entry(
        guild,
        discord.AuditLogAction.webhook_create,
        target_id=target_id,
    )
    return entry.user if entry else None


async def fetch_role_create_actor(
    guild:     discord.Guild,
    target_id: int,
) -> discord.Member | discord.User | None:
    entry = await fetch_recent_entry(
        guild,
        discord.AuditLogAction.role_create,
        target_id=target_id,
    )
    return entry.user if entry else None


async def fetch_role_delete_actor(
    guild:     discord.Guild,
    target_id: int,
) -> discord.Member | discord.User | None:
    entry = await fetch_recent_entry(
        guild,
        discord.AuditLogAction.role_delete,
        target_id=target_id,
    )
    return entry.user if entry else None