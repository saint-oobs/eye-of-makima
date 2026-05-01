<div align="center">

<img src="https://img.shields.io/badge/discord.py-2.x-5865F2?style=for-the-badge&logo=discord&logoColor=white" />
<img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
<img src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge" />
<img src="https://img.shields.io/badge/status-active-22c55e?style=for-the-badge" />

# 🛡️ `[BOT NAME]`

**[ONE LINE DESCRIPTION — what the bot does and who it's for]**

[Invite `[BOT NAME]`](#) · [Support Server](#) · [Report a Bug](#)

</div>

---

## What `[BOT NAME]` Does

[2–3 sentence overview of the bot's purpose and value to a server. Describe the problem it solves and the type of communities it's built for.]

---

## Feature Overview

### 🔨 Moderation
Full moderation toolkit with case numbers on every action.

- `warn` / `warns` — issue and track warnings with escalation thresholds
- `mute` / `unmute` — role-based mute with optional duration
- `kick` / `ban` / `unban` — standard actions with reason logging
- `quarantine` — isolate a member without banning
- `tempban` — timed ban that auto-expires
- All actions create a numbered **case record** with moderator, reason, and timestamp

### 📋 Case System
Every moderation action creates a permanent, searchable record.

- `case <id>` — view any case by number
- `cases [member]` — paginated history for a member or the whole server
- `case edit <id> <reason>` — update the reason after the fact
- `case stats` — breakdown of actions by type
- Cases survive member leaves, bans, and bot restarts

### 🤖 Automod
Automatic rule enforcement that runs before moderators need to act.

| Rule | What it catches |
|------|----------------|
| Invite filter | Discord invite links from non-whitelisted servers |
| URL filter | External links in restricted channels |
| Caps spam | Messages exceeding a configurable caps ratio |
| Mention flood | Mass user/role/@everyone pings |
| Emoji spam | Excessive emoji in a single message |
| Duplicate spam | Same message repeated within a window |
| Zalgo | Character abuse / text corruption |
| Newline flood | Wall-of-newlines message spam |
| Wordlist | Global + per-server banned word/phrase matching |

Each rule is independently togglable and configurable per-server.

### 🏰 Anti-Nuke
Detection and automatic lockdown when a raid or internal attack is detected.

- Mass channel delete detection
- Mass role delete detection
- Ban flood detection
- Webhook creation abuse
- Role permission escalation detection
- Configurable thresholds and automatic action (kick/ban/lockdown)

### ✅ Verification
Gate new members before they can access the server.

- **Captcha mode** — solve a text captcha in DMs
- **Web mode** — verify via an external web endpoint
- **None mode** — auto-assign join role immediately
- Configurable verified role and log channel

### 📜 Logging
Detailed, channel-posted logs for everything that happens in your server.

- Message edits and deletes (with original content)
- Member joins, leaves, kicks, bans, unbans
- Role changes and nickname changes
- Channel creates, deletes, and updates
- Voice channel join/leave/move
- All moderation actions

### 🔒 Permit Levels
A 5-tier permission system separate from Discord roles.

| Level | Label | Example capabilities |
|-------|-------|----------------------|
| 0 | Everyone | Help, ping, userinfo |
| 1 | Trusted | View case history |
| 2 | Helper | Warn, view all cases |
| 3 | Moderator | Mute, kick, tempban |
| 4 | Admin | Ban, full config, anti-nuke |
| 5 | Bot Owner | Eval, SQL, shutdown |

Assign permit levels to specific roles via `setup`.

### ⚙️ Setup & Config
Every feature is configured per-server. Nothing is hardcoded.

```
setup prefix     — Change command prefix
setup logs       — Set mod-log channel
setup muterole   — Set the muted role
setup joinrole   — Set auto-join role for new members
setup modrole    — Assign moderator permit level to a role
setup adminrole  — Assign admin permit level to a role
setup view       — Show all current settings
setup reset      — Wipe config and start fresh
```

### ⭐ Premium
Optional per-guild premium tier unlocks:
- Extended case history
- Advanced automod rules
- Additional anti-nuke thresholds
- Priority support

---

## Command Reference

> Default prefix: `[PREFIX]` — configurable per server.

| Command | Level | Description |
|---------|-------|-------------|
| `help [cmd]` | 0 | Help menu or detail on a command |
| `ping` | 0 | Latency and uptime |
| `userinfo [member]` | 0 | Member info embed |
| `serverinfo` | 0 | Server info embed |
| `avatar [user]` | 0 | Full-resolution avatar |
| `warn <member> [reason]` | 2 | Issue a warning |
| `warns <member>` | 2 | View a member's warnings |
| `mute <member> [duration] [reason]` | 3 | Mute a member |
| `unmute <member>` | 3 | Remove a mute |
| `kick <member> [reason]` | 3 | Kick a member |
| `ban <member> [reason]` | 4 | Permanent ban |
| `tempban <member> <duration> [reason]` | 3 | Timed ban |
| `unban <user> [reason]` | 4 | Remove a ban |
| `quarantine <member>` | 3 | Quarantine a member |
| `case <id>` | 2 | View a case |
| `cases [member]` | 2 | Case history |
| `case edit <id> <reason>` | 3 | Edit a case reason |
| `case delete <id>` | 4 | Delete a case |
| `setup [subcommand]` | 4 | Configure the bot |

---

## Project Layout

```
[project-folder]/
├── main.py                  # Entrypoint
├── .env.example             # Config template
├── requirements.txt
│
├── cogs/                    # Feature modules
│   ├── automod.py
│   ├── antinuke.py
│   ├── cases.py
│   ├── errors.py
│   ├── events.py
│   ├── help.py
│   ├── logging.py
│   ├── misc.py
│   ├── moderation.py
│   ├── owner.py
│   ├── permits.py
│   ├── premium.py
│   ├── setup.py
│   ├── tempbans.py
│   ├── verification.py
│   ├── warnings.py
│   └── wordlist.py
│
├── database/
│   ├── db.py                # Async SQLite wrapper
│   ├── schema.sql           # Table definitions
│   └── migrations/
│
└── utils/
    ├── auditlog.py
    ├── backup.py
    ├── cache.py
    ├── checks.py
    ├── detectors.py
    ├── embeds.py
    ├── errors.py
    ├── parsers.py
    ├── taskman.py
    ├── views.py
    └── watchdog.py
```

---

## Required Bot Permissions

| Permission | Why |
|------------|-----|
| Manage Roles | Mute, quarantine, join role |
| Manage Channels | Anti-nuke channel restore |
| Kick Members | Kick command |
| Ban Members | Ban, tempban, anti-nuke |
| Manage Messages | Automod message deletion |
| View Audit Log | Detect who performed actions |
| Send Messages + Embed Links | All responses |
| Read Message History | Duplicate spam detection |

**Required Privileged Intents:** `MEMBERS` · `MESSAGE_CONTENT`

---

## License

MIT — see [LICENSE](LICENSE).

---

<div align="center">
Made with 🤍 — contributions welcome
</div>
