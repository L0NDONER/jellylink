🎬 JellyLink

Event-driven media ingestion engine with fast-path parsing, idempotent linking, and intelligent retry scheduling.

JellyLink watches a download directory, parses media files, and links them into a clean Jellyfin-ready library structure — efficiently, deterministically, and without unnecessary disk duplication.

✨ Features

👀 Recursive filesystem watcher (watchdog-based)

⚡ Fast-path regex parsing for common scene TV releases

🧠 Fallback parsing via guessit for edge cases

🔁 Exponential retry scheduler for incomplete downloads

🗃 SQLite fingerprint deduplication

🔗 Hardlink-first linking (instant when on same mount)

📦 Safe copy fallback when linking is not possible

🧹 Automatic cleanup of empty download folders

📲 Optional Telegram notifications

🧵 Multi-worker concurrent processing

🛡 Idempotent + crash-safe design

🧱 Architecture
Downloads
   ↓
Watchdog Event
   ↓
Scheduler (dedupe + retry)
   ↓
Worker Pool
   ↓
Parser (regex → guessit fallback)
   ↓
Hardlink Engine
   ↓
SQLite Log
   ↓
Telegram Notification (optional)


JellyLink is built as a long-running, resilient ingestion service — not a one-shot script.

⚡ Why Hardlinking?

If your download directory and media library are on the same filesystem:

🔗 Linking takes milliseconds

💾 No duplicate storage

🌱 Torrents continue seeding

📺 Jellyfin sees the file instantly

If hardlinking fails, JellyLink automatically falls back to copy2() safely.

🧠 Parsing Strategy

JellyLink uses a two-stage parsing approach:

Fast Regex Path

Handles 80–90% of common scene releases (S01E01, 01x01, etc.)

Avoids heavy parsing overhead.

Guessit Fallback

Catches edge cases and unusual naming.

Acts as the “receptor” for irregular files.

This keeps performance high while maintaining coverage.

🔁 Intelligent Retry System

Files still downloading?

JellyLink detects instability (size + mtime check) and:

Returns "retry"

Applies exponential backoff

Stops retrying after configurable max attempts

No blocking.
No spin loops.
No duplicate work.

🗃 Idempotency & Deduplication

Each file is fingerprinted and stored in SQLite.

Even if:

Watchdog fires multiple events

Multiple workers overlap

The service restarts

The same file will not be processed twice.

Destination existence is treated as success.

🐳 Docker Deployment
docker-compose up -d


Reproducible runtime

Volume-mounted media paths

Clean container lifecycle

Built from Git source of truth

🖥 Systemd Deployment

See SYSTEMD_GUIDE.md for full setup instructions.

Highlights:

Starts on boot

Auto-restarts on crash

Logs via journalctl

Runs as non-root user

📲 Telegram Notifications

Optional and configurable in jellylink.conf.

When enabled:

Sends notification when media is added

Displays show/movie name and episode/date

Silent when DRY_RUN is enabled

🧪 Configuration

Main configuration file:

jellylink.conf


Key settings:

WATCH_FOLDER

MEDIA_ROOT

TV_FOLDER

MOVIE_FOLDER

DRY_RUN

Retry/backoff tuning

Telegram credentials

📦 Requirements

Python 3.10+

Linux (tested on Mint / Debian-based systems)

Same filesystem mount for hardlink optimization

Jellyfin library pointed to MEDIA_ROOT

Dependencies are listed in requirements.txt.

🧘 Design Philosophy

JellyLink was built with the following principles:

Deterministic behavior

Safe iteration

Idempotent operations

Minimal disk churn

Observable logging

Long-running stability

It favors clarity and resilience over feature sprawl.

🏷 Versioning & Stability

Stable states are tagged in Git:

v0.x-stable-live


Running deployments map directly to tagged commits.

Rollback is instant and reproducible.

❓ Why Not Sonarr / Radarr?

JellyLink is not intended to replace full-feature media managers.

It exists for:

Full local control

Privacy

Minimal external dependencies

Deterministic ingestion

Learning and experimentation

Custom parsing logic

It is an ingestion engine, not a metadata manager.

🚧 Status

✅ Stable hardlink workflow

✅ Idempotent destination handling

✅ Concurrent scheduler verified

✅ Telegram integration working

🔄 Regex refinements ongoing

📜 License

Choose your preferred open-source license (MIT recommended for simplicity).
