import logging
import logging.handlers
import os
import sys
from typing import Literal

# ── ANSI colour codes ──────────────────────────────────────────
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"

_LEVEL_COLOURS: dict[int, str] = {
    logging.DEBUG:    "\033[36m",   # Cyan
    logging.INFO:     "\033[32m",   # Green
    logging.WARNING:  "\033[33m",   # Yellow
    logging.ERROR:    "\033[31m",   # Red
    logging.CRITICAL: "\033[35m",   # Magenta
}

_LEVEL_LABELS: dict[int, str] = {
    logging.DEBUG:    "DBG",
    logging.INFO:     "INF",
    logging.WARNING:  "WRN",
    logging.ERROR:    "ERR",
    logging.CRITICAL: "CRT",
}


class _ColourFormatter(logging.Formatter):
    """
    Compact, colour-coded log formatter for TTY output.

    Format:
        HH:MM:SS  INF  bot.heat        Message text here
        HH:MM:SS  ERR  bot.antinuke    Error message
    """

    def format(self, record: logging.LogRecord) -> str:
        colour = _LEVEL_COLOURS.get(record.levelno, "")
        label  = _LEVEL_LABELS.get(record.levelno, record.levelname[:3])

        time_str = self.formatTime(record, datefmt="%H:%M:%S")
        name     = record.name[:24].ljust(24)

        # Format exception info if present
        msg = record.getMessage()
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            msg = f"{msg}\n{record.exc_text}"

        return (
            f"{_DIM}{time_str}{_RESET}  "
            f"{colour}{_BOLD}{label}{_RESET}  "
            f"{_DIM}{name}{_RESET}  "
            f"{msg}"
        )


class _PlainFormatter(logging.Formatter):
    """
    Plain formatter for file output (no ANSI codes).

    Format:
        2025-01-15 14:23:01  INFO     bot.heat        Message text here
    """

    def format(self, record: logging.LogRecord) -> str:
        time_str = self.formatTime(record, datefmt="%Y-%m-%d %H:%M:%S")
        name     = record.name[:28].ljust(28)
        level    = record.levelname[:8].ljust(8)

        msg = record.getMessage()
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            msg = f"{msg}\n{record.exc_text}"

        return f"{time_str}  {level}  {name}  {msg}"


def setup_logging(
    level: str | int = "INFO",
    *,
    log_file: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,   # 10 MB
    backup_count: int = 5,
) -> None:
    """
    Configure the root logger and discord.py / aiosqlite noise suppression.

    Args:
        level:        Log level string ("DEBUG", "INFO", etc.) or int.
        log_file:     Optional path to a rotating log file.
                      Defaults to LOG_FILE env var, or None (no file logging).
        max_bytes:    Max size before log rotation (default 10 MB).
        backup_count: Number of rotated backup files to keep.
    """
    # Resolve level
    if isinstance(level, str):
        numeric = getattr(logging, level.upper(), logging.INFO)
    else:
        numeric = level

    log_file = log_file or os.getenv("LOG_FILE")

    # ── Root logger ───────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(numeric)

    # Remove any handlers added by discord.py's default setup
    root.handlers.clear()

    # ── Stdout handler ────────────────────────────────────────
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(numeric)
    # Use colour formatter only when stdout is a real TTY
    if sys.stdout.isatty():
        stdout_handler.setFormatter(_ColourFormatter())
    else:
        stdout_handler.setFormatter(_PlainFormatter())
    root.addHandler(stdout_handler)

    # ── File handler (optional) ───────────────────────────────
    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric)
        file_handler.setFormatter(_PlainFormatter())
        root.addHandler(file_handler)

    # ── Suppress noisy third-party loggers ────────────────────
    _SUPPRESS: list[tuple[str, int]] = [
        # discord.py internal noise — only show WARNING+
        ("discord",                logging.WARNING),
        ("discord.http",           logging.WARNING),
        ("discord.gateway",        logging.WARNING),
        ("discord.client",         logging.WARNING),
        ("discord.state",          logging.WARNING),
        ("discord.ext.commands",   logging.WARNING),
        # aiosqlite — only show ERROR+
        ("aiosqlite",              logging.ERROR),
        # asyncio — only show ERROR+
        ("asyncio",                logging.ERROR),
        # aiohttp — only show ERROR+
        ("aiohttp",                logging.ERROR),
        ("aiohttp.access",         logging.ERROR),
    ]

    for name, suppress_level in _SUPPRESS:
        logging.getLogger(name).setLevel(suppress_level)

    # Initial log line
    log = logging.getLogger("bot.logger")
    log.info(
        "Logging initialised — level=%s%s",
        logging.getLevelName(numeric),
        f", file={log_file}" if log_file else "",
    )