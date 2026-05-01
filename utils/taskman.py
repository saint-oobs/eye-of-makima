"""
Background task manager.

Provides a central registry for recurring asyncio tasks so they can
be started, stopped, and inspected from anywhere in the bot without
each cog managing its own raw asyncio.Task handles.

Usage:
    # Register a coroutine as a recurring task (fires every 60s)
    taskman.register("prefix_sync", cog.sync_prefixes, interval=60)
    taskman.start("prefix_sync")

    # One-shot deferred task
    taskman.run_after("temp_unban_123", cog.do_unban, delay=3600)

    # Cancel
    taskman.cancel("prefix_sync")

    # On bot close
    await taskman.shutdown()
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("bot.taskman")


@dataclass
class _TaskEntry:
    name:     str
    coro_fn:  Callable[..., Coroutine[Any, Any, Any]]
    interval: float | None       # None = one-shot
    delay:    float              # initial delay before first run
    args:     tuple[Any, ...]
    kwargs:   dict[str, Any]
    task:     asyncio.Task | None = field(default=None, repr=False)


class TaskManager:
    """
    Central asyncio task registry.

    All tasks are tracked by name so duplicate registration raises
    immediately rather than silently leaking a task handle.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _TaskEntry] = {}

    # ── Registration ───────────────────────────────────────────

    def register(
        self,
        name:     str,
        coro_fn:  Callable[..., Coroutine[Any, Any, Any]],
        *,
        interval: float | None = None,
        delay:    float         = 0.0,
        args:     tuple[Any, ...] = (),
        kwargs:   dict[str, Any] | None = None,
        replace:  bool = False,
    ) -> None:
        """
        Register a coroutine function as a named task.

        Parameters
        ----------
        name:     Unique task name.
        coro_fn:  Async callable (not a coroutine — the factory).
        interval: Seconds between reruns. ``None`` = run once.
        delay:    Seconds before the first execution.
        args:     Positional args forwarded to *coro_fn*.
        kwargs:   Keyword args forwarded to *coro_fn*.
        replace:  If True, cancel and replace any existing task with
                  the same name. Otherwise raises ``KeyError``.
        """
        if name in self._entries:
            if replace:
                self.cancel(name)
            else:
                raise KeyError(f"Task '{name}' is already registered.")
        self._entries[name] = _TaskEntry(
            name=name,
            coro_fn=coro_fn,
            interval=interval,
            delay=delay,
            args=args,
            kwargs=kwargs or {},
        )

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self, name: str) -> asyncio.Task:
        """
        Start a previously registered task.

        Returns the underlying :class:`asyncio.Task`.
        Raises ``KeyError`` if *name* is not registered.
        """
        entry = self._entries[name]
        if entry.task and not entry.task.done():
            return entry.task

        entry.task = asyncio.create_task(
            self._runner(entry),
            name=f"taskman:{name}",
        )
        entry.task.add_done_callback(
            lambda t: self._on_done(name, t)
        )
        log.debug("Task started: %s", name)
        return entry.task

    def run_after(
        self,
        name:    str,
        coro_fn: Callable[..., Coroutine[Any, Any, Any]],
        *,
        delay:   float,
        args:    tuple[Any, ...] = (),
        kwargs:  dict[str, Any] | None = None,
    ) -> asyncio.Task:
        """
        Register and immediately start a one-shot deferred task.

        Shortcut for ``register(...) + start(...)``.
        """
        self.register(
            name, coro_fn,
            interval=None,
            delay=delay,
            args=args,
            kwargs=kwargs,
            replace=True,
        )
        return self.start(name)

    def cancel(self, name: str) -> bool:
        """
        Cancel a running task by name.

        Returns True if a task was cancelled, False if it was not running.
        """
        entry = self._entries.get(name)
        if entry and entry.task and not entry.task.done():
            entry.task.cancel()
            log.debug("Task cancelled: %s", name)
            return True
        return False

    def cancel_prefix(self, prefix: str) -> int:
        """
        Cancel all tasks whose names start with *prefix*.

        Useful for cleaning up per-guild tasks:
            ``taskman.cancel_prefix(f"tempban_{guild_id}_")``

        Returns the number of tasks cancelled.
        """
        cancelled = 0
        for name in list(self._entries):
            if name.startswith(prefix):
                if self.cancel(name):
                    cancelled += 1
        return cancelled

    def is_running(self, name: str) -> bool:
        """Return True if the task exists and is still running."""
        entry = self._entries.get(name)
        return bool(entry and entry.task and not entry.task.done())

    async def shutdown(self) -> None:
        """Cancel all running tasks and await their completion."""
        names = list(self._entries)
        for name in names:
            self.cancel(name)
        tasks = [
            e.task for e in self._entries.values()
            if e.task and not e.task.done()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        log.info("TaskManager: all tasks shut down.")

    # ── Internal ───────────────────────────────────────────────

    async def _runner(self, entry: _TaskEntry) -> None:
        if entry.delay:
            await asyncio.sleep(entry.delay)

        while True:
            try:
                await entry.coro_fn(*entry.args, **entry.kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception(
                    "Task '%s' raised an unhandled exception: %s",
                    entry.name, exc,
                )

            if entry.interval is None:
                break
            await asyncio.sleep(entry.interval)

    def _on_done(self, name: str, task: asyncio.Task) -> None:
        if task.cancelled():
            log.debug("Task finished (cancelled): %s", name)
            return
        exc = task.exception()
        if exc:
            log.error("Task finished with exception: %s — %s", name, exc)
        else:
            log.debug("Task finished: %s", name)