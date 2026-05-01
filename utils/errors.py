"""
Custom exception hierarchy.

All bot-specific exceptions inherit from BotError so callers can
catch the entire family with a single `except BotError`.
"""

from __future__ import annotations


class BotError(Exception):
    """Base class for all bot-specific exceptions."""


# ── Permission / access ────────────────────────────────────────

class NotInGuild(BotError):
    """Command was invoked outside a guild."""


class MissingPermit(BotError):
    """
    Invoker does not hold the required permit level.

    Attributes:
        required: Minimum permit level needed.
        current:  Invoker's current permit level.
    """
    def __init__(self, required: int, current: int = 0) -> None:
        self.required = required
        self.current  = current
        super().__init__(
            f"Permit {required} required (you have {current})."
        )


class NotBotOwner(BotError):
    """Command restricted to bot owners."""


# ── Configuration ──────────────────────────────────────────────

class GuildNotConfigured(BotError):
    """
    A required guild configuration value is missing.

    Attributes:
        key: The config key that is not set.
    """
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"Guild configuration key `{key}` is not set.")


class FeatureDisabled(BotError):
    """A bot feature is disabled for this guild."""
    def __init__(self, feature: str) -> None:
        self.feature = feature
        super().__init__(f"Feature `{feature}` is disabled for this guild.")


# ── Database ───────────────────────────────────────────────────

class DatabaseError(BotError):
    """An unexpected database operation error."""


class RecordNotFound(DatabaseError):
    """Expected a database record but found none."""
    def __init__(self, table: str, identifier: int | str) -> None:
        self.table      = table
        self.identifier = identifier
        super().__init__(f"No record found in `{table}` for `{identifier}`.")


# ── Moderation ─────────────────────────────────────────────────

class ActionFailed(BotError):
    """
    A moderation action could not be applied.

    Attributes:
        action: The action that failed (e.g. "ban", "kick").
        reason: Human-readable failure cause.
    """
    def __init__(self, action: str, reason: str) -> None:
        self.action = action
        self.reason = reason
        super().__init__(f"Action `{action}` failed: {reason}")


class HierarchyError(ActionFailed):
    """Target member is too high in the role hierarchy."""
    def __init__(self, action: str) -> None:
        super().__init__(action, "Target is above me in the role hierarchy.")


class SelfTargetError(BotError):
    """Invoker attempted to target themselves."""


class BotTargetError(BotError):
    """Invoker attempted to target the bot itself."""


class OwnerTargetError(BotError):
    """Invoker attempted to target the guild owner."""


# ── Input / parsing ────────────────────────────────────────────

class ParseError(BotError):
    """Failed to parse user input."""
    def __init__(self, value: str, expected: str) -> None:
        self.value    = value
        self.expected = expected
        super().__init__(f"Could not parse `{value}` as {expected}.")


class DurationParseError(ParseError):
    """Failed to parse a duration string like `10m` or `2h`."""
    def __init__(self, value: str) -> None:
        super().__init__(value, "duration (e.g. `10m`, `2h`, `1d`)")


# ── Antinuke ───────────────────────────────────────────────────

class AntiNukeTriggered(BotError):
    """An antinuke threshold was breached."""
    def __init__(self, event: str, count: int, limit: int) -> None:
        self.event = event
        self.count = count
        self.limit = limit
        super().__init__(
            f"AntiNuke: `{event}` hit {count}/{limit}."
        )


# ── Premium ────────────────────────────────────────────────────

class PremiumRequired(BotError):
    """Feature requires a premium guild subscription."""
    def __init__(self, feature: str = "this feature") -> None:
        self.feature = feature
        super().__init__(
            f"`{feature}` requires a premium subscription."
        )


# ── Quarantine ─────────────────────────────────────────────────

class QuarantineRoleNotSet(GuildNotConfigured):
    """Quarantine role has not been configured."""
    def __init__(self) -> None:
        super().__init__("quarantine_role")


class AlreadyQuarantined(BotError):
    """Member is already quarantined."""


class NotQuarantined(BotError):
    """Member is not currently quarantined."""