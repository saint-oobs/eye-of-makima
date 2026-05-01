import asyncio
import json
import logging
import os
import tempfile
from collections import defaultdict
from copy import deepcopy

log = logging.getLogger("bot.config")

GUILDS_DIR = os.path.join(os.path.dirname(__file__), "data", "guilds")


def _default_guild_config() -> dict:
    return {
        # ── Statics ───────────────────────────────────────────────
        "prefix":           None,       # None → falls back to BOT_PREFIX env var
        "quarantine_role":  None,       # slot 2
        "main_roles":       [],         # slot 5
        "log_channel":      None,       # slot 6
        "modlog_channel":   None,       # slot 7
        "partner_channels": [],         # slot 8
        "main_channel":     None,       # slot 9
        "trusted_admins":   [],         # slot 10 — Permit 3
        "extra_owners":     [],         # slot 11 — Permit 4
        "rescue_key":       None,

        # ── Internal runtime state ────────────────────────────────
        "_quarantined":     [],         # list of user IDs currently quarantined
        "_saved_roles":     {},         # user_id str → list of role IDs (saved on quarantine)
        "_warns":           {},         # user_id str → list of reason strings
        "_backups":         [],         # list of snapshot dicts (max 10)
        "_server_snapshot": None,       # latest snapshot dict

        # ── Misc settings ─────────────────────────────────────────
        "misc": {
            "dm_targets":            True,
            "auto_delete_mod_cmd":   False,
            "confirm_actions":       False,
            "default_timeout":       "1h",
            "days_purged_on_ban":    0,
            "ban_appeal_message":    "",
            "warn_max":              3,
            "warn_action_enabled":   False,
            "warn_action":           "timeout",
        },

        # ── Heat system ───────────────────────────────────────────
        "heat": {
            "enabled":               True,
            "max_heat":              85.0,
            "degradation_per_second": 0.5,
            "strikes_cap":           3,
            "timeout_per_strike":    "5m",
            "timeout_at_cap":        "1h",
            "multiplier":            False,   # Premium
            "reset_heat_on_timeout": True,
            "anti_spam":             True,
            "monitor_webhooks":      True,
            "auto_lockdown": {
                "enabled":           False,
                "mention_threshold": 5,
            },
            "panic_mode": {
                "enabled":              False,
                "raiders_to_trigger":   3,
                "duration_minutes":     10,
            },
            "filters": {
                "normal_message":     {"enabled": True,  "heat": 1.5},
                "similar_message":    {"enabled": True,  "heat": 10.0},
                "advertisement":      {"enabled": True,  "heat": 20.0, "action": "timeout"},
                "nsfw_websites":      {"enabled": True,  "heat": 30.0},
                "malicious_websites": {"enabled": True,  "heat": 80.0},
                "emojis":             {"enabled": True,  "heat_per_emoji": 2.0},
                "characters":         {"enabled": True,  "heat_per_100_chars": 0.5},
                "new_lines":          {"enabled": True,  "heat_per_line": 1.0},
                "inactivity":         {"enabled": True,  "heat": 5.0},
                "mentions":           {
                    "enabled":            True,
                    "heat_per_mention":   8.0,
                    "everyone_multiplier": 5,
                },
                "attachments":        {"enabled": True,  "heat": 3.0},
                "words_blacklist":    {
                    "enabled":        False,
                    "heat":           25.0,
                    "words":          [],       # local words added via g!heat blacklist addword
                    "remote_urls":    [],       # Premium: per-server URLs (list of dicts)
                    "remote_words":   [],       # words fetched from per-server URLs
                },
                "links_blacklist":    {"enabled": False, "links": []},
            },
        },

        # ── Anti-Nuke ─────────────────────────────────────────────
        "antinuke": {
            "enabled":                       True,
            "quarantine_hold":               True,
            "monitor_dangerous_role_perms":  True,
            "prune_detection":               False,
            "backups": {
                "enabled":   False,          # Premium
            },
            "panic_mode": {
                "enabled":              False,
                "lockdown_on_trigger":  True,
                "unlock_on_end":        True,
                "warned_roles":         [],
            },
            "minute_limit": {
                "ban":            3,
                "kick":           5,
                "channel_delete": 3,
                "channel_create": 5,
                "role_delete":    3,
                "role_create":    5,
                "webhook_create": 5,
                "webhook_delete": 3,
            },
            "hour_limit": {
                "ban":            10,
                "kick":           20,
                "channel_delete": 10,
                "channel_create": 15,
                "role_delete":    10,
                "role_create":    15,
                "webhook_create": 20,
                "webhook_delete": 10,
            },
        },

        # ── Join Gate ─────────────────────────────────────────────
        "joingate": {
            "no_pfp": {
                "enabled": False,
                "action":  "kick",
            },
            "account_age": {
                "enabled":    False,
                "min_days":   7,
                "action":     "kick",
                "expose_min": True,
            },
            "bot_addition": {
                "enabled": True,
                "action":  "kick",
            },
            "advertising_names": {
                "enabled": True,
                "action":  "kick",
            },
            "unverified_bots": {
                "enabled": True,
                "action":  "kick",
            },
            "suspicious": {
                "enabled": False,
                "action":  "log",
            },
            "username_filter": {
                "enabled":  False,
                "action":   "kick",
                "patterns": [],
            },
        },

        # ── Join Raid (Premium) ───────────────────────────────────
        "joinraid": {
            "enabled":                False,
            "trigger_count":          10,
            "trigger_period_minutes": 5,
            "action":                 "kick",
            "account_type":           "suspicious",
            "nopfp_flag":   {"enabled": True},
            "age_flag":     {"enabled": True, "min_days": 2},
            "warned_roles": [],
        },

        # ── Verification ──────────────────────────────────────────
        "verification": {
            "enabled":          False,
            "mode":             "captcha",   # captcha | none | web
            "target":           "suspicious", # all | suspicious
            "action":           "quarantine", # quarantine | kick | ban | none
            "duration_minutes": 10,
        },

        # ── Network ───────────────────────────────────────────────
        "network": {
            "notify_on_join":    True,
            "notify_on_present": False,
            "auto_heat":         False,   # Premium
            "auto_punish":       False,   # Premium
            "min_severity":      "timeout",
            "ignored_users":     [],
        },

        # ── Grace period ──────────────────────────────────────────
        "grace_period_days": 3,
    }


# Per-guild asyncio locks to prevent concurrent config edits
_guild_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


class ConfigManager:
    """
    Per-guild JSON configuration manager.

    Features:
    - Atomic writes via temp file + os.replace (no partial writes on crash)
    - Per-guild asyncio.Lock via safe_edit() (no concurrent mutation)
    - Deep-merge on load (missing keys auto-filled from defaults)
    - In-memory cache (no disk read on every access)
    """

    def __init__(self):
        os.makedirs(GUILDS_DIR, exist_ok=True)
        self._cache: dict[int, dict] = {}

    # ── Internal ──────────────────────────────────────────────────
    def _path(self, guild_id: int) -> str:
        return os.path.join(GUILDS_DIR, f"{guild_id}.json")

    def _deep_merge(self, base: dict, saved: dict) -> dict:
        """
        Recursively fill any keys missing in `saved` from `base` defaults.
        This ensures new config keys added in updates are always present
        without wiping existing server settings.
        """
        result = deepcopy(base)
        for key, value in saved.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    # ── Public API ────────────────────────────────────────────────
    def get(self, guild_id: int) -> dict:
        """
        Get config for a guild. Loads from disk on first access,
        auto-fills missing keys from defaults via deep-merge.
        """
        if guild_id not in self._cache:
            path = self._path(guild_id)
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        saved = json.load(fh)
                    self._cache[guild_id] = self._deep_merge(
                        _default_guild_config(), saved
                    )
                except (json.JSONDecodeError, OSError) as exc:
                    log.error(
                        "Config load failed for guild %d: %s — using defaults",
                        guild_id, exc,
                    )
                    self._cache[guild_id] = _default_guild_config()
            else:
                self._cache[guild_id] = _default_guild_config()
        return self._cache[guild_id]

    def save(self, guild_id: int) -> None:
        """
        Atomic write: write to .tmp then os.replace().
        Guarantees the config file is never partially written,
        even if the process is killed mid-write.
        """
        path = self._path(guild_id)
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(self._cache.get(guild_id, {}), fh, indent=2)
            os.replace(tmp_path, path)  # Atomic on all POSIX systems
        except OSError as exc:
            log.error("Config save failed for guild %d: %s", guild_id, exc)
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def set(self, guild_id: int, keys: list[str], value) -> None:
        """
        Set a nested config value by key path and save immediately.
        Example: config.set(guild_id, ["heat", "enabled"], True)
        """
        cfg = self.get(guild_id)
        node = cfg
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value
        self.save(guild_id)

    def create_default(self, guild_id: int) -> None:
        """
        Create default config for a new guild.
        Only writes if no config file already exists.
        """
        if not os.path.exists(self._path(guild_id)):
            self._cache[guild_id] = _default_guild_config()
            self.save(guild_id)
            log.info("Created default config for guild %d", guild_id)

    def reset(self, guild_id: int) -> None:
        """
        Reset a guild's config to factory defaults.
        Used by the rescue system.
        """
        self._cache[guild_id] = _default_guild_config()
        self.save(guild_id)
        log.info("Config reset to defaults for guild %d", guild_id)

    async def safe_edit(self, guild_id: int, fn) -> None:
        """
        Edit a guild's config with a per-guild asyncio.Lock.
        Prevents race conditions when two cogs edit the same guild's
        config simultaneously (e.g. heat + antinuke firing at same time).

        Usage:
            async def edit(cfg):
                cfg["heat"]["enabled"] = False
            await bot.config.safe_edit(guild_id, edit)
        """
        async with _guild_locks[guild_id]:
            cfg = self.get(guild_id)
            if asyncio.iscoroutinefunction(fn):
                await fn(cfg)
            else:
                fn(cfg)
            self.save(guild_id)

    def invalidate(self, guild_id: int) -> None:
        """
        Remove a guild's config from the in-memory cache.
        Forces a fresh disk read on next access.
        """
        self._cache.pop(guild_id, None)