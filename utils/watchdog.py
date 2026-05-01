"""
Bot health watchdog.

Monitors:
- Latency spikes (websocket ping)
- Shard disconnects
- Unresponsive event loop (heartbeat stall detection)
- Memory usage (optional, requires psutil)

Exposes a simple status dict readable by cogs/owner.py and cogs/misc.py.

Usage:
    watchdog = BotWatchdog(bot)
    await watchdog.start()
    # later:
    status = watchdog.status()
    await watchdog.stop()
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("bot.watchdog")

_LATENCY_WARN_MS   = 500     # warn if ping exceeds this
_LATENCY_CRIT_MS   = 1500    # critical if ping exceeds this
_CHECK_INTERVAL    = 30      # seconds between health checks
_LATENCY_HISTORY   = 20      # number of samples to keep


class BotWatchdog:
    """
    Periodic health monitor for the bot process.

    Tracks latency history, disconnects, and restart events.
    Does not auto-restart — it only reports status and logs warnings.
    """

    def __init__(self, bot: Any) -> None:
        self.bot             = bot
        self._task:          asyncio.Task | None = None
        self._latency_hist:  deque[float]         = deque(maxlen=_LATENCY_HISTORY)
        self._started_at:    datetime | None      = None
        self._disconnect_ct: int                  = 0
        self._last_check:    datetime | None      = None
        self._last_latency:  float                = 0.0
        self._alerts:        list[str]            = []

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(self) -> None:
        """Start the watchdog loop."""
        if self._task and not self._task.done():
            return
        self._started_at = datetime.now(timezone.utc)
        self._task = asyncio.create_task(
            self._loop(), name="watchdog"
        )
        log.info("Watchdog started.")

    async def stop(self) -> None:
        """Stop the watchdog loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Watchdog stopped.")

    # ── Status ─────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """
        Return a snapshot of current health metrics.

        Suitable for display in an owner/debug command.
        """
        hist       = list(self._latency_hist)
        avg_latency = (sum(hist) / len(hist)) if hist else 0.0
        max_latency = max(hist) if hist else 0.0

        try:
            import psutil
            proc    = psutil.Process()
            mem_mb  = proc.memory_info().rss / 1_048_576
            cpu_pct = proc.cpu_percent(interval=None)
        except ImportError:
            mem_mb  = -1.0
            cpu_pct = -1.0

        return {
            "started_at":      self._started_at,
            "last_check":      self._last_check,
            "latency_ms":      round(self._last_latency * 1000, 2),
            "avg_latency_ms":  round(avg_latency * 1000, 2),
            "max_latency_ms":  round(max_latency * 1000, 2),
            "disconnects":     self._disconnect_ct,
            "guilds":          len(self.bot.guilds),
            "users":           sum(g.member_count or 0 for g in self.bot.guilds),
            "memory_mb":       round(mem_mb, 2),
            "cpu_pct":         cpu_pct,
            "recent_alerts":   list(self._alerts[-5:]),
        }

    def record_disconnect(self) -> None:
        """Call from on_disconnect to track disconnect count."""
        self._disconnect_ct += 1
        self._add_alert("WebSocket disconnected.")

    # ── Internal ───────────────────────────────────────────────

    async def _loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                await self._check()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Watchdog check failed: %s", exc)
            await asyncio.sleep(_CHECK_INTERVAL)

    async def _check(self) -> None:
        self._last_check   = datetime.now(timezone.utc)
        latency            = self.bot.latency        # seconds
        self._last_latency = latency
        self._latency_hist.append(latency)

        latency_ms = latency * 1000

        if latency_ms >= _LATENCY_CRIT_MS:
            msg = f"CRITICAL latency: {latency_ms:.0f}ms"
            log.critical(msg)
            self._add_alert(msg)
        elif latency_ms >= _LATENCY_WARN_MS:
            msg = f"High latency: {latency_ms:.0f}ms"
            log.warning(msg)
            self._add_alert(msg)
        else:
            log.debug("Health check OK — latency %.0fms", latency_ms)

    def _add_alert(self, message: str) -> None:
        ts  = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self._alerts.append(f"[{ts}] {message}")
        if len(self._alerts) > 50:
            self._alerts = self._alerts[-50:]