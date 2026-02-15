#!/usr/bin/env python3
"""
jellylink.py - watcher + media processor

Design notes:
- Fast-path regex parses common scene TV releases without calling guessit.
- If the file isn't stable (still being written), return "retry" so the
  scheduler handles backoff without blocking worker threads.
- SQLite fingerprint dedupe prevents repeated work.
"""

from __future__ import annotations

import configparser
import hashlib
import heapq
import logging
import os
import queue
import re
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

import requests
from guessit import guessit
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# ---------------- Logging ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-5s] %(message)s",
)
log = logging.getLogger("JellyLink")

# ---------------- Config ----------------
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "jellylink.conf"

cfg = configparser.ConfigParser()
cfg.read(DEFAULT_CONFIG)


def getbool(section: str, key: str, fallback: str = "false") -> bool:
    """Parse booleans from config safely."""
    value = cfg.get(section, key, fallback=fallback).strip().lower()
    return value in {"1", "true", "yes", "on"}


DRY_RUN = getbool("DEFAULT", "DRY_RUN", "true")

WATCH_FOLDER = Path(cfg.get("DEFAULT", "WATCH_FOLDER", fallback="/media/downloads")).resolve()
MEDIA_ROOT = Path(cfg.get("DEFAULT", "MEDIA_ROOT", fallback="/media")).resolve()
TV_ROOT = MEDIA_ROOT / cfg.get("DEFAULT", "TV_FOLDER", fallback="TV")
MOVIE_ROOT = MEDIA_ROOT / cfg.get("DEFAULT", "MOVIE_FOLDER", fallback="Movies")

DOWNLOAD_GRACE_SEC = cfg.getint("DEFAULT", "DOWNLOAD_GRACE_PERIOD", fallback=120)

MAX_WORKERS = int(os.getenv("JELLYWATCH_WORKERS", "3"))
DEDUPE_WINDOW_SEC = int(os.getenv("JELLYWATCH_DEDUPE_SEC", "30"))
RETRY_BASE_SEC = int(os.getenv("JELLYWATCH_RETRY_BASE", "45"))
RETRY_MAX_SEC = int(os.getenv("JELLYWATCH_RETRY_MAX", "20000"))
MAX_RETRIES = int(os.getenv("JELLYWATCH_MAX_RETRIES", "30"))

TELEGRAM_BOT_TOKEN = cfg.get("DEFAULT", "TELEGRAM_BOT_TOKEN", fallback="").strip()
TELEGRAM_CHAT_ID = cfg.get("DEFAULT", "TELEGRAM_CHAT_ID", fallback="").strip()
ENABLE_TELEGRAM = getbool("DEFAULT", "ENABLE_TELEGRAM", "false")

DB_PATH = SCRIPT_DIR / "jellylink.db"

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"}

# ---------------- Fast-Path Regex ----------------
TV_PATTERNS = [
    re.compile(
        r"(?i)^(?P<title>.+?)[ ._\-]+s(?P<season>\d{1,2})[ ._\-]*e(?P<episode>\d{1,2})\b"
    ),  # S01E01
    re.compile(
        r"(?i)^(?P<title>.+?)[ ._\-]+(?P<season>\d{1,2})x(?P<episode>\d{1,2})\b"
    ),  # 01x01
]

SCENE_JUNK_RE = re.compile(
    r"(?i)\b(1080p|720p|2160p|hdtv|h\.?264|x264|hevc|x265|web[- .]?dl|webrip|bluray|brrip)\b.*$"
)


def fast_parse_tv(filename: str) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    """Instant regex match to bypass guessit for standard TV releases."""
    for pattern in TV_PATTERNS:
        match = pattern.search(filename)
        if not match:
            continue

        raw_title = match.group("title")
        raw_title = raw_title.replace(".", " ").replace("_", " ").strip()

        # Strip common scene suffix junk that sometimes bleeds into title
        title = SCENE_JUNK_RE.sub("", raw_title).strip()

        try:
            season = int(match.group("season"))
            episode = int(match.group("episode"))
        except (ValueError, TypeError):
            continue

        return (title or raw_title), season, episode

    return None, None, None


def load_daily_titles() -> Set[str]:
    raw = cfg.get("DEFAULT", "DAILY_SHOW_TITLES", fallback="").strip()
    if not raw:
        return {
            "the daily show",
            "the tonight show starring jimmy fallon",
            "late night with seth meyers",
            "jimmy kimmel live",
            "the late show with stephen colbert",
            "last week tonight with john oliver",
        }
    titles: Set[str] = set()
    for part in raw.split(","):
        t = part.strip().lower()
        if t:
            titles.add(t)
    return titles


DAILY_SHOW_TITLES = load_daily_titles()

# ---------------- DB ----------------
def get_file_fingerprint(path: Path) -> str:
    """Stable-ish fingerprint: name + size + mtime, hashed."""
    try:
        s = path.stat()
        seed = f"{path.name}{s.st_size}{s.st_mtime}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()
    except Exception:
        return hashlib.sha256(str(path).encode("utf-8")).hexdigest()


def init_database() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_filename TEXT,
                source_fingerprint TEXT NOT NULL UNIQUE,
                title TEXT,
                media_type TEXT,
                season INTEGER,
                episode INTEGER,
                year INTEGER,
                destination_path TEXT,
                processed_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def already_processed(path: Path) -> bool:
    fp = get_file_fingerprint(path)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT 1 FROM processed_media WHERE source_fingerprint=? LIMIT 1",
            (fp,),
        )
        return cur.fetchone() is not None


def log_processed_media(
    src_path: Path,
    title: str,
    mtype: str,
    season: Optional[int],
    episode: Optional[int],
    year: Optional[int],
    dest: str,
) -> None:
    fingerprint = get_file_fingerprint(src_path)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO processed_media
                    (original_filename, source_fingerprint, title, media_type, season, episode, year, destination_path)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (src_path.name, fingerprint, str(title), mtype, season, episode, year, dest),
            )
    except sqlite3.IntegrityError:
        log.info("[SKIP] already in DB: %s", src_path.name)
    except Exception as err:
        log.error("[DB ERROR] %s", err)


# ---------------- IO helpers ----------------
def create_link(src: Path, dst: Path) -> bool:
    """
    Create a hardlink (fast) with safe/idempotent behavior.

    - If dst already exists, treat as success (prevents re-copying large files).
    - If hardlink fails for other reasons, fall back to copy2.
    """
    # If destination exists, avoid hardlink/copy churn.
    if dst.exists():
        try:
            src_size = src.stat().st_size
            dst_size = dst.stat().st_size
            if src_size == dst_size and src_size > 0:
                log.info("[SKIP] Destination already exists: %s", dst)
                return True
            log.warning("[SKIP] Destination exists but size differs: %s", dst)
            return False
        except Exception:
            log.info("[SKIP] Destination already exists (stat failed): %s", dst)
            return True

    if DRY_RUN:
        log.info("[DRY-RUN] Would link %s -> %s", src, dst)
        return True

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
        log.info("[LINK] %s -> %s", src.name, dst)
        return True
    except FileExistsError:
        # Race between workers or repeated events.
        log.info("[SKIP] Destination already exists (race): %s", dst)
        return True
    except OSError as e:
        # This is the 'Production' way to debug I/O
        log.warning(
            "[LINK FAIL] Could not hardlink (%s). Falling back to COPY. "
            "Check if src/dest are on same mount!",
            e,
        )
        try:
            shutil.copy2(src, dst)
            log.info("[COPY] %s -> %s", src.name, dst)
            return True
        except Exception as err:
            log.error("[ERROR] Copy failed: %s", err)
            return False


def cleanup_empty_dirs(path: Path) -> None:
    if not path.is_dir() or path == WATCH_FOLDER:
        return
    try:
        if not any(path.iterdir()):
            log.info("[CLEANUP] Removing empty dir: %s", path)
            path.rmdir()
            cleanup_empty_dirs(path.parent)
    except Exception as err:
        log.error("[CLEANUP ERROR] %s", err)


def send_notification(title: str, mtype: str, details: str) -> None:
    if DRY_RUN or not (ENABLE_TELEGRAM and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return

    msg = f"🎬 {mtype} Added\n\n{title}\n{details}"
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as err:
        log.error("[TG ERROR] %s", err)


# ---------------- Non-Blocking Stability Check ----------------
def check_stability_instant(path: Path) -> bool:
    """Check if file is currently being written to by comparing mtime/size briefly."""
    try:
        s1 = path.stat()
        time.sleep(0.5)
        s2 = path.stat()
        return (s1.st_size == s2.st_size) and (s1.st_mtime == s2.st_mtime) and (s1.st_size > 0)
    except FileNotFoundError:
        return False


# ---------------- Processing ----------------
def process_file(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return "ignored"

    name_l = path.name.lower()
    if ".sample." in name_l or name_l.endswith("sample.mkv") or name_l.endswith("sample.mp4") or "-sample" in name_l:
        return "ignored"

    try:
        if path.stat().st_size < 50 * 1024 * 1024:
            return "ignored"
    except Exception:
        return "ignored"

    if already_processed(path):
        return "skip"

    # If not stable, return 'retry' so scheduler handles backoff.
    if not check_stability_instant(path):
        return "retry"

    # --- PHASE 1: Fast-Path Regex ---
    title, season, ep_num = fast_parse_tv(path.name)
    mtype: Optional[str] = "episode" if title else None
    year: Optional[int] = None
    date_info = None

    # --- PHASE 2: Guessit Fallback ---
    if not title:
        info = guessit(path.name)
        title = info.get("title")
        if not title:
            log.warning("[PARSE FAIL] %s", path.name)
            return "done"

        mtype = info.get("type")
        date_info = info.get("date")
        season = info.get("season", 1)
        ep_num = info.get("episode")
        year = info.get("year")

        # Daily show date-based override
        if mtype == "movie" and date_info and str(title).strip().lower() in DAILY_SHOW_TITLES:
            mtype = "episode"

    # --- PHASE 3: Pathing and IO ---
    if mtype == "episode":
        # Prefer episode number if present, else date, else "1"
        if ep_num is not None:
            ep_display = str(ep_num)
        elif date_info is not None:
            ep_display = date_info.strftime("%Y-%m-%d")
        else:
            ep_display = "1"

        season_num = int(season) if season is not None else 1
        dest = TV_ROOT / str(title) / f"Season {season_num}" / f"{title} - {ep_display}{path.suffix}"

        if create_link(path, dest):
            logged_year = date_info.year if date_info else None
            log_processed_media(path, str(title), "TV", season_num, ep_num, logged_year, str(dest))
            log.info("[ADDED] %s -> %s", path.name, dest)
            send_notification(str(title), "TV Show", f"Date/Ep: {ep_display}")
            cleanup_empty_dirs(path.parent)
            return "added"

    # Default: movie
    folder_name = f"{title} ({year})" if year else str(title)
    dest = MOVIE_ROOT / folder_name / f"{title}{path.suffix}"
    if create_link(path, dest):
        log_processed_media(path, str(title), "Movie", None, None, year, str(dest))
        log.info("[ADDED] %s -> %s", path.name, dest)
        send_notification(str(title), "Movie", f"({year})" if year else "")
        cleanup_empty_dirs(path.parent)
        return "added"

    return "done"


# ---------------- Scheduler ----------------
@dataclass(order=True)
class RetryItem:
    run_at: float
    path: Path = field(compare=False)
    tries: int = field(compare=False, default=0)


class Scheduler:
    def __init__(self) -> None:
        self.work_q: "queue.Queue[Tuple[Path, int]]" = queue.Queue()
        self.retry_heap: list[RetryItem] = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.inflight: Set[Path] = set()
        self.last_enqueued: Dict[Path, float] = {}

    def enqueue(self, path: Path, tries: int = 0) -> None:
        now = time.time()
        if tries == 0:
            if now - self.last_enqueued.get(path, 0.0) < DEDUPE_WINDOW_SEC:
                return
            self.last_enqueued[path] = now

        with self.lock:
            if tries == 0 and path in self.inflight:
                return
            self.inflight.add(path)

        self.work_q.put((path, tries))

    def schedule_retry(self, path: Path, tries: int) -> None:
        if tries >= MAX_RETRIES:
            log.warning("[GIVE UP] %s", path.name)
            with self.lock:
                self.inflight.discard(path)
            return

        delay = min(RETRY_MAX_SEC, RETRY_BASE_SEC * (2 ** min(tries, 8)))
        with self.lock:
            heapq.heappush(self.retry_heap, RetryItem(time.time() + delay, path, tries))

    def retry_loop(self) -> None:
        while not self.stop.is_set():
            item: Optional[RetryItem] = None
            with self.lock:
                if self.retry_heap and self.retry_heap[0].run_at <= time.time():
                    item = heapq.heappop(self.retry_heap)

            if item:
                self.enqueue(item.path, item.tries)
            else:
                time.sleep(1)

    def mark_done(self, path: Path) -> None:
        with self.lock:
            self.inflight.discard(path)


def worker_thread(sched: Scheduler, idx: int) -> None:
    while not sched.stop.is_set():
        try:
            path, tries = sched.work_q.get(timeout=1)
        except queue.Empty:
            continue

        try:
            res = process_file(path)
            if res == "retry":
                sched.schedule_retry(path, tries + 1)
            else:
                sched.mark_done(path)

            if res in {"ignored", "skip", "missing", "done"}:
                log.info("[%s] %s", res.upper(), path.name)
        except Exception:
            log.exception("Worker %s processing crash", idx)
            sched.schedule_retry(path, tries + 1)
        finally:
            sched.work_q.task_done()


class Handler(FileSystemEventHandler):
    def __init__(self, sched: Scheduler) -> None:
        self.sched = sched

    def on_created(self, event) -> None:
        if not event.is_directory:
            self.sched.enqueue(Path(event.src_path))

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self.sched.enqueue(Path(event.dest_path))

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self.sched.enqueue(Path(event.src_path))


def main() -> None:
    init_database()
    log.warning("DRY_RUN is %s", "ON" if DRY_RUN else "OFF")

    if not WATCH_FOLDER.exists():
        log.error("WATCH_FOLDER missing: %s", WATCH_FOLDER)
        raise SystemExit(2)

    sched = Scheduler()

    threading.Thread(target=sched.retry_loop, daemon=True).start()
    for i in range(MAX_WORKERS):
        threading.Thread(target=worker_thread, args=(sched, i), daemon=True).start()

    obs = Observer()
    obs.schedule(Handler(sched), str(WATCH_FOLDER), recursive=True)
    obs.start()

    log.info("Watching: %s", WATCH_FOLDER)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        obs.stop()
    finally:
        sched.stop.set()
        obs.join()


if __name__ == "__main__":
    main()

