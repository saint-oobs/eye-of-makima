-- ──────────────────────────────────────────────────────────────
--  schema.sql  —  applied on every startup via executescript()
--  All statements use IF NOT EXISTS / OR IGNORE — safe to re-run
-- ──────────────────────────────────────────────────────────────

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Premium ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS premium_guilds (
    guild_id    INTEGER PRIMARY KEY,
    added_by    INTEGER NOT NULL,
    added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP NULL  -- NULL = never expires
);

-- ── Moderation ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mod_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    target_id   INTEGER NOT NULL,
    mod_id      INTEGER NOT NULL,
    action      TEXT    NOT NULL,  -- ban | kick | timeout | warn | quarantine | note
    reason      TEXT,
    duration    TEXT,              -- human-readable: "1h", "7d", etc.
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mod_actions_guild
    ON mod_actions (guild_id, target_id);

CREATE INDEX IF NOT EXISTS idx_mod_actions_created
    ON mod_actions (guild_id, created_at);

-- ── Network (cross-server incident tracking) ──────────────────
CREATE TABLE IF NOT EXISTS network_incidents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    source_guild INTEGER NOT NULL,
    action       TEXT    NOT NULL,  -- ban | kick | timeout | quarantine
    severity     TEXT    NOT NULL,  -- low | medium | high | critical
    reason       TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_network_user
    ON network_incidents (user_id);

CREATE INDEX IF NOT EXISTS idx_network_created
    ON network_incidents (created_at);

-- Guilds that have been notified about a specific network incident
-- Prevents double-notifying the same guild for the same incident
CREATE TABLE IF NOT EXISTS network_notified (
    incident_id INTEGER NOT NULL,
    guild_id    INTEGER NOT NULL,
    notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (incident_id, guild_id),
    FOREIGN KEY (incident_id) REFERENCES network_incidents (id) ON DELETE CASCADE
);

-- ── Anti-Nuke action counters ──────────────────────────────────
-- Rolling per-guild-per-user action counts for nuke detection
CREATE TABLE IF NOT EXISTS antinuke_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    action_type TEXT    NOT NULL,  -- ban | kick | channel_delete | role_delete | etc.
    performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_antinuke_lookup
    ON antinuke_actions (guild_id, user_id, action_type, performed_at);

-- ── Join Raid log ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS joinraid_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    action      TEXT    NOT NULL,  -- kick | ban | quarantine
    joined_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_joinraid_guild
    ON joinraid_events (guild_id, joined_at);

-- ── Verification pending ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS verification_pending (
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    mode        TEXT    NOT NULL,   -- captcha | web
    token       TEXT    UNIQUE,     -- web mode verification token
    expires_at  TIMESTAMP NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_verification_token
    ON verification_pending (token);

CREATE INDEX IF NOT EXISTS idx_verification_expires
    ON verification_pending (expires_at);

-- ── Quarantine ─────────────────────────────────────────────────
-- Persistent quarantine record (supplements in-memory config state)
-- Used to restore quarantines after bot restarts
CREATE TABLE IF NOT EXISTS quarantine_records (
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    quarantined_by  INTEGER NOT NULL,
    reason          TEXT,
    saved_roles     TEXT    NOT NULL DEFAULT '[]',  -- JSON array of role IDs
    quarantined_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

-- ── Heat state persistence ────────────────────────────────────
-- Persists per-user heat across bot restarts
CREATE TABLE IF NOT EXISTS heat_state (
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    heat        REAL    NOT NULL DEFAULT 0.0,
    strikes     INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

-- ── Global wordlist URLs (bot-owner managed) ──────────────────
CREATE TABLE IF NOT EXISTS global_wordlist_urls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    url          TEXT    NOT NULL UNIQUE,
    added_by     INTEGER NOT NULL,
    added_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_fetched TIMESTAMP NULL,
    word_count   INTEGER NOT NULL DEFAULT 0,
    status       TEXT    NOT NULL DEFAULT 'pending'
    -- status: pending | ok | error
);

-- ── Global words (fetched from owner-managed URLs) ────────────
CREATE TABLE IF NOT EXISTS global_words (
    word        TEXT    PRIMARY KEY,
    source_url  TEXT    NOT NULL,
    added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_global_words_source
    ON global_words (source_url);

-- ── Per-server remote wordlist fetch cache ────────────────────
-- Stores words fetched from per-server Premium URLs
-- (complement to config.json remote_words field)
CREATE TABLE IF NOT EXISTS remote_words (
    guild_id    INTEGER NOT NULL,
    word        TEXT    NOT NULL,
    source_url  TEXT    NOT NULL,
    added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, word)
);

CREATE INDEX IF NOT EXISTS idx_remote_words_guild
    ON remote_words (guild_id);

-- ── Modlog message mapping ────────────────────────────────────
-- Maps mod action IDs to their modlog Discord message IDs
-- Allows editing modlog entries when cases are updated
CREATE TABLE IF NOT EXISTS modlog_messages (
    case_id     INTEGER PRIMARY KEY,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    FOREIGN KEY (case_id) REFERENCES mod_actions (id) ON DELETE CASCADE
);

-- ── Scheduled unmutes / untimeouts ───────────────────────────
-- Tracks active timeouts so they can be cancelled or listed
CREATE TABLE IF NOT EXISTS scheduled_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    action      TEXT    NOT NULL,  -- untimeout | unquarantine
    execute_at  TIMESTAMP NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scheduled_execute
    ON scheduled_actions (execute_at);

-- ── Rescue keys ───────────────────────────────────────────────
-- One-time emergency reset tokens per guild
CREATE TABLE IF NOT EXISTS rescue_keys (
    guild_id    INTEGER PRIMARY KEY,
    key_hash    TEXT    NOT NULL,  -- bcrypt hash of the key
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    used_at     TIMESTAMP NULL
);

-- ── Guild snapshots (Anti-Nuke backup — Premium) ──────────────
CREATE TABLE IF NOT EXISTS guild_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    snapshot    TEXT    NOT NULL,  -- JSON blob
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_snapshots_guild
    ON guild_snapshots (guild_id, created_at);