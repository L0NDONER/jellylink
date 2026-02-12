#!/usr/bin/env python3
# jellylink.py
# Purpose:    GitOps-friendly media organizer using hardlinks
#             TV / Movies → structured folders with quality-based deduplication

import os
import re
import sys
import time
import argparse
import configparser
import logging
import sqlite3
import shutil
import errno
from pathlib import Path
from typing import Optional, Tuple, List, Set
from datetime import datetime

# Optional WhatsApp dependency
try:
    from twilio.rest import Client
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False

########## 
# EXPLANATION: 
# Refined the Logging section to be "Docker-safe."
# It now checks if the directory for LOG_FILE exists before trying to open it.
# If it fails (e.g., due to a missing mount or path), it falls back to 
# standard console output so the container doesn't crash with FileNotFoundError.
##########

# ────────────────────────────────────────────────
# Argument parser
# ────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="JellyLink – declarative media organizer")
parser.add_argument("--config", default=None, help="Override default config location")
parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode")
parser.add_argument("--apply", action="store_true", help="Force apply mode (overrides dry-run)")
args = parser.parse_args()

# ────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────
script_dir = Path(__file__).resolve().parent
default_config = script_dir / "jellylink.conf"
config_path = Path(args.config) if args.config else default_config

if not config_path.is_file():
    print(f"ERROR: Config file not found: {config_path}", file=sys.stderr)
    sys.exit(2)

cfg = configparser.ConfigParser()
cfg.read(config_path)

def getbool(section: str, key: str, fallback: str = "false") -> bool:
    return cfg.get(section, key, fallback=fallback).strip().lower() in {"1", "true", "yes", "on"}

DRY_RUN           = getbool("DEFAULT", "DRY_RUN", "true")
RECURSIVE_SCAN    = getbool("DEFAULT", "RECURSIVE_SCAN", "true")
SKIP_SAMPLES      = getbool("DEFAULT", "SKIP_SAMPLES", "true")
ENABLE_WHATSAPP   = getbool("DEFAULT", "ENABLE_WHATSAPP", "false")

if args.apply:
    DRY_RUN = False
elif args.dry_run:
    DRY_RUN = True

WATCH_FOLDER = Path(cfg.get("DEFAULT", "WATCH_FOLDER", fallback="/data/downloads")).resolve()
MEDIA_ROOT        = Path(cfg.get("DEFAULT", "MEDIA_ROOT", fallback="/media")).resolve()
TV_ROOT           = MEDIA_ROOT / cfg.get("DEFAULT", "TV_FOLDER", fallback="TV")
MOVIE_ROOT        = MEDIA_ROOT / cfg.get("DEFAULT", "MOVIE_FOLDER", fallback="Movies")

DOWNLOAD_GRACE_SEC = cfg.getint("DEFAULT", "DOWNLOAD_GRACE_PERIOD", fallback=60)
SCAN_INTERVAL_SEC  = cfg.getint("DEFAULT", "SCAN_INTERVAL", fallback=15)
LOG_FILE_PATH      = cfg.get("DEFAULT", "LOG_FILE", fallback="").strip()

TWILIO_SID    = cfg.get("DEFAULT", "TWILIO_ACCOUNT_SID", fallback="")
TWILIO_TOKEN  = cfg.get("DEFAULT", "TWILIO_AUTH_TOKEN", fallback="")
WHATSAPP_FROM = cfg.get("DEFAULT", "TWILIO_WHATSAPP_FROM", fallback="+14155238886")
WHATSAPP_TO   = cfg.get("DEFAULT", "WHATSAPP_TO", fallback="")

DB_PATH = script_dir / "jellylink.db"

if ENABLE_WHATSAPP and TWILIO_AVAILABLE:
    if not all([TWILIO_SID, TWILIO_TOKEN, WHATSAPP_TO]):
        print("ERROR: WhatsApp enabled but missing Twilio credentials", file=sys.stderr)
        sys.exit(3)

# ────────────────────────────────────────────────
# Logging setup
# ────────────────────────────────────────────────
log_kwargs = {
    "level": logging.INFO,
    "format": "%(asctime)s [%(levelname)-5s] %(message)s",
    "datefmt": "%Y-%m-%d %H:%M:%S",
    "handlers": [logging.StreamHandler(sys.stdout)] # Always log to console for Docker
}

if LOG_FILE_PATH:
    try:
        p = Path(LOG_FILE_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Add file handler if path is valid
        log_kwargs["handlers"].append(logging.FileHandler(p, mode='a'))
    except Exception as e:
        print(f"WARNING: Could not initialize log file at {LOG_FILE_PATH}: {e}")
        print("Falling back to console-only logging.")

logging.basicConfig(**log_kwargs)

def log(msg: str) -> None:
    logging.info(msg)

# ────────────────────────────────────────────────
# Constants & Patterns
# ────────────────────────────────────────────────
VIDEO_EXTENSIONS  = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"}
TEMPORARY_SUFFIXES = {".part", ".crdownload", ".!ut", ".!qb", ".aria2", ".partial", ".tmp"}

COMMON_RELEASE_TAGS = re.compile(
    r'\b(?:2160p|1080p|720p|480p|4k|uhd|webdl|webrip|web|web-?dl|web-?rip|'
    r'hdr|hevc|h265|h264|x265|x264|ddp|dd\+|aac|mp3|amzn|nf|pmtp|10bit|8bit)\b',
    flags=re.IGNORECASE
)

YEAR_PATTERN = re.compile(r"(19[0-9]{2}|20[0-9]{2})")

# ────────────────────────────────────────────────
# Utilities
# ────────────────────────────────────────────────
def is_video_file(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXTENSIONS

def looks_like_temporary_file(name: str) -> bool:
    ln = name.lower()
    return any(ln.endswith(s) for s in TEMPORARY_SUFFIXES) or ".!qb" in ln or ".!ut" in ln

def file_is_still_downloading(p: Path, grace_seconds: int) -> bool:
    try:
        return (time.time() - p.stat().st_mtime) < grace_seconds
    except (FileNotFoundError, PermissionError):
        return True

def safe_mkdir(d: Path) -> None:
    if DRY_RUN:
        log(f"[DRY] Would mkdir: {d}")
        return
    d.mkdir(parents=True, exist_ok=True)

def already_linked(src: Path, dst: Path) -> bool:
    if not dst.exists(): return False
    try:
        s_stat, d_stat = src.stat(), dst.stat()
        return s_stat.st_ino == d_stat.st_ino and s_stat.st_dev == d_stat.st_dev
    except Exception: return False

def create_or_copy_link(src: Path, dst: Path) -> bool:
    if already_linked(src, dst):
        log(f"[SKIP] Already hardlinked: {dst.name}")
        return True
    if DRY_RUN:
        log(f"[DRY] Would link/copy: {src.name} → {dst}")
        return True

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".jl-tmp")
    try:
        os.link(src, tmp)
        tmp.rename(dst)
        log(f"[LINK] {src.name} → {dst}")
        return True
    except OSError as e:
        if e.errno != errno.EXDEV:
            log(f"[ERROR] Hardlink failed: {e}")
            return False
    try:
        shutil.copy2(src, tmp)
        tmp.rename(dst)
        log(f"[COPY] {src.name} → {dst} (cross-device)")
        return True
    except Exception as e:
        log(f"[ERROR] Copy failed: {e}")
        if tmp.exists(): tmp.unlink()
        return False

# ────────────────────────────────────────────────
# Parsing logic
# ────────────────────────────────────────────────
def sanitize_name_for_parsing(name: str) -> str:
    s = re.sub(r'[._-]+', ' ', name)
    s = COMMON_RELEASE_TAGS.sub(' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def parse_tv_show(name: str) -> Optional[Tuple[str, int, int]]:
    base = Path(name).stem
    clean = sanitize_name_for_parsing(base)
    patterns = [
        (r'(?i)(.*?)[ .]*\bs(\d{1,2})[ .]*e(\d{1,2})\b', 2, 3),
        (r'(?i)(.*?)[ .]*\b(\d{1,2})x(\d{1,2})\b', 2, 3),
    ]
    for pat, s_idx, e_idx in patterns:
        m = re.search(pat, clean)
        if m:
            title_part = m.group(1).strip()
            try:
                s, e = int(m.group(s_idx)), int(m.group(e_idx))
                if 1 <= s <= 250 and 1 <= e <= 250:
                    title_part = re.sub(r'\s+(19\d{2}|20\d{2})$', '', title_part).strip()
                    if not title_part: continue
                    return ' '.join(title_part.split()), s, e
            except: continue
    return None

def parse_movie(name: str) -> Optional[Tuple[str, Optional[int]]]:
    base = Path(name).stem
    clean = sanitize_name_for_parsing(base)
    m = YEAR_PATTERN.search(clean)
    if not m: return clean.strip() or None, None
    year = int(m.group(0))
    if not (1900 <= year <= datetime.now().year + 2): return clean.strip(), None
    return clean[:m.start()].strip() or None, year

def get_quality_score(filename: str) -> int:
    s = filename.lower()
    for q in [2160, 1080, 720, 480]:
        if str(q) in s or (q == 2160 and "4k" in s): return q
    return 0

# ────────────────────────────────────────────────
# Core processing logic
# ────────────────────────────────────────────────
def handle_tv_show(src: Path, title: str, season: int, episode: int) -> bool:
    target_dir = TV_ROOT / title / f"Season {season:02d}"
    safe_mkdir(target_dir)
    dest_path = target_dir / f"{title.replace(' ', '.')}.S{season:02d}E{episode:02d}{src.suffix}"
    
    existing = list(target_dir.glob(f"*.S{season:02d}E{episode:02d}.*"))
    new_q = get_quality_score(src.name)
    for old in existing:
        if new_q <= get_quality_score(old.name):
            log(f"[SKIP TV] Existing quality better/equal: {old.name}")
            return True
        if not DRY_RUN: old.unlink()

    if create_or_copy_link(src, dest_path):
        log_processed_media(src.name, title, "TV", season, episode, None, str(dest_path))
        send_notification(title, "TV Show", f"S{season:02d}E{episode:02d} ({new_q}p)")
        return True
    return False

def handle_movie(src: Path, title: str, year: Optional[int]) -> bool:
    target_dir = MOVIE_ROOT / (f"{title} ({year})" if year else title)
    safe_mkdir(target_dir)
    dest_path = target_dir / (f"{title.replace(' ', '.')}{f'.{year}' if year else ''}{src.suffix}")
    
    if dest_path.exists() and already_linked(src, dest_path): return True
    if create_or_copy_link(src, dest_path):
        log_processed_media(src.name, title, "Movie", None, None, year, str(dest_path))
        send_notification(title, "Movie", f"({year})" if year else "")
        return True
    return False

def log_processed_media(orig, title, mtype, s, e, y, dest):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO processed_media (original_filename, title, media_type, season, episode, year, destination_path) VALUES (?,?,?,?,?,?,?)", 
                         (orig, title, mtype, s, e, y, dest))
    except Exception as err: log(f"[DB ERROR] {err}")

def send_notification(title, mtype, details):
    if ENABLE_WHATSAPP and TWILIO_AVAILABLE and not DRY_RUN:
        try:
            Client(TWILIO_SID, TWILIO_TOKEN).messages.create(
                from_=f"whatsapp:{WHATSAPP_FROM}", to=f"whatsapp:{WHATSAPP_TO}",
                body=f"🎬 {mtype} Added\n\n{title}\n{details}")
        except Exception as err: log(f"[WA ERROR] {err}")

# ────────────────────────────────────────────────
# Main logic
# ────────────────────────────────────────────────
def init_database():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS processed_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT, original_filename TEXT, title TEXT, 
            media_type TEXT, season INTEGER, episode INTEGER, year INTEGER, 
            destination_path TEXT, processed_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

def collect_video_files():
    files = []
    if RECURSIVE_SCAN:
        for r, d, fs in os.walk(WATCH_FOLDER):
            d[:] = [dn for dn in d if dn.lower() != "sample"]
            files.extend([Path(r) / f for f in fs if is_video_file(Path(f))])
    else:
        files = [p for p in WATCH_FOLDER.iterdir() if p.is_file() and is_video_file(p)]
    return files

def try_process_file(path: Path) -> bool:
    if looks_like_temporary_file(path.name): return False
    if SKIP_SAMPLES and "sample" in path.name.lower(): return True
    if file_is_still_downloading(path, DOWNLOAD_GRACE_SEC): return False

    tv    = parse_tv_show(path.name)
    movie = parse_movie(path.name)

    if tv:
        return handle_tv_show(path, *tv)
    
    if movie[0]:
        return handle_movie(path, *movie)

    log(f"[UNMATCHED] {path.name}")
    return True

def main():
    init_database()
    log(f"JellyLink Started | Watch: {WATCH_FOLDER} | Dry-run: {DRY_RUN}")
    processed = set()
    try:
        while True:
            for p in collect_video_files():
                if p not in processed and try_process_file(p):
                    processed.add(p)
            time.sleep(SCAN_INTERVAL_SEC)
    except KeyboardInterrupt:
        log("Stopped by user")

if __name__ == "__main__":
    main()
