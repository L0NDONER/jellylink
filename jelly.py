#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import sys
import time
import argparse
import configparser
import logging
from pathlib import Path
from typing import Optional, Tuple
import datetime
import errno
import shutil

# Force UTF-8 for stdout/stderr
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

# ---------------------------------------------------------
# COMMAND-LINE ARGUMENTS
# ---------------------------------------------------------

parser = argparse.ArgumentParser(description="JellyLink Media Organizer")
parser.add_argument("--config", help="Path to config file", default=None)
parser.add_argument("--dry-run", help="Force dry-run mode (overrides config)", action="store_true")
args = parser.parse_args()

# ---------------------------------------------------------
# CONFIG LOADING
# ---------------------------------------------------------

def get_bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")

script_dir = os.path.dirname(os.path.abspath(__file__))
default_conf = os.path.join(script_dir, "jellylink.conf")
config_path = args.config if args.config else default_conf

config = configparser.ConfigParser()
config.read(config_path, encoding='utf-8')

DRY_RUN = get_bool(config.get("DEFAULT", "DRY_RUN", fallback="true"))
if args.dry_run:
    DRY_RUN = True

WATCH_FOLDER = os.path.expanduser(config.get("DEFAULT", "WATCH_FOLDER", fallback="~/Downloads"))
BASE_MEDIA_FOLDER = config.get("DEFAULT", "MEDIA_ROOT", fallback="/media")
TV_ROOT = os.path.join(BASE_MEDIA_FOLDER, config.get("DEFAULT", "TV_FOLDER", fallback="TV"))
MOVIE_ROOT = os.path.join(BASE_MEDIA_FOLDER, config.get("DEFAULT", "MOVIE_FOLDER", fallback="Movies"))

SKIP_SAMPLES = get_bool(config.get("DEFAULT", "SKIP_SAMPLES", fallback="true"))
LOG_FILE = config.get("DEFAULT", "LOG_FILE", fallback="").strip()

DOWNLOAD_GRACE_PERIOD = int(config.get("DEFAULT", "DOWNLOAD_GRACE_PERIOD", fallback="60"))
SCAN_INTERVAL = int(config.get("DEFAULT", "SCAN_INTERVAL", fallback="15"))

# New option: scan subfolders
SCAN_SUBFOLDERS = get_bool(config.get("DEFAULT", "SCAN_SUBFOLDERS", fallback="true"))
MAX_SUBFOLDER_DEPTH = int(config.get("DEFAULT", "MAX_SUBFOLDER_DEPTH", fallback="1"))

# ---------------------------------------------------------
# LOGGING
# ---------------------------------------------------------

log_kwargs = {
    "level": logging.INFO,
    "format": "%(asctime)s [%(levelname)s] %(message)s"
}
if LOG_FILE:
    log_kwargs["filename"] = LOG_FILE

logging.basicConfig(**log_kwargs)

def log(msg: str) -> None:
    print(msg)
    logging.info(msg)

# ---------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------

# Extensions considered "final" video file types
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v"}

# Common temporary/partial download suffixes to skip explicitly
TEMP_PARTIAL_SUFFIXES = (
    ".part",
    ".crdownload",
    ".!ut",
    ".!qb",
    ".aria2",
    ".partial",
)

def is_video_file(path: str) -> bool:
    ext = Path(path).suffix.lower()
    return ext in VIDEO_EXTS

def is_temporary_file(filename: str) -> bool:
    """
    Return True if the filename indicates a temporary/partial download.

    qBittorrent (and other clients) often append markers like ".!qB" to
    partially-downloaded files (e.g. "video.mkv.!qB" or "video.!qB.mkv").
    This function checks:
      - Known temporary suffixes (case-insensitive)
      - Presence of the qB-specific token ".!qB" anywhere in the name
    """
    ln = filename.lower()

    # Quick substring check for qBittorrent token anywhere in the name
    if ".!qb" in ln:
        return True

    # Exact suffix checks (handles .part, .crdownload, etc.)
    for s in TEMP_PARTIAL_SUFFIXES:
        if ln.endswith(s):
            return True

    return False

def is_recently_modified(path: str, grace_period: int) -> bool:
    try:
        mtime = os.path.getmtime(path)
    except FileNotFoundError:
        return True
    age = time.time() - mtime
    return age < grace_period

def safe_makedirs(path: str) -> None:
    if DRY_RUN:
        log(f"[DRY RUN] Would create folder: {path}")
        return
    os.makedirs(path, exist_ok=True)

def create_hardlink(src: str, dst: str) -> None:
    if os.path.exists(dst):
        try:
            src_stat = os.stat(src)
            dst_stat = os.stat(dst)
            if src_stat.st_ino == dst_stat.st_ino and src_stat.st_dev == dst_stat.st_dev:
                log(f"[SKIP] Already linked: {dst}")
                return
            else:
                log(f"[SKIP] File exists (different): {dst}")
                return
        except FileNotFoundError:
            pass

    if DRY_RUN:
        log(f"[DRY RUN] Would link: {src} → {dst}")
        return

    try:
        os.link(src, dst)
        log(f"[LINK] {src} → {dst}")
    except OSError as e:
        # Cross-device link error -> fallback to copy
        if e.errno == errno.EXDEV:
            tmp_dst = dst + ".part"
            try:
                shutil.copy2(src, tmp_dst)
                os.replace(tmp_dst, dst)
                log(f"[COPY] {src} → {dst} (different filesystem)")
            except Exception as copy_err:
                try:
                    if os.path.exists(tmp_dst):
                        os.remove(tmp_dst)
                except Exception:
                    pass
                log(f"[ERROR] Copy fallback failed: {copy_err}")
        else:
            log(f"[ERROR] Linking failed: {e}")

def clean_title(name: str) -> str:
    """Clean title while preserving Unicode characters."""
    # Replace separators with spaces
    name = re.sub(r"[._]+", " ", name)
    
    # Remove filesystem-unsafe characters (Windows is strictest)
    # Keep: letters (any language), numbers, spaces, hyphens, parentheses
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    
    # Normalize whitespace
    name = re.sub(r"\s+", " ", name).strip()
    
    # Remove leading/trailing dots (Windows issue)
    name = name.strip('.')
    
    return name

# ---------------------------------------------------------
# PARSING (TV + MOVIES)
# ---------------------------------------------------------

# Only match realistic years (19xx or 20xx). We'll additionally validate bounds in code
year_pattern = re.compile(r"(19\d{2}|20\d{2})")

# Common tags to strip from filenames before matching
COMMON_TAGS_RE = re.compile(
    r'\b(?:2160p|1080p|720p|480p|2160|1080|720|4k|uhd|web[-_. ]?dl|web[-_. ]?rip|webrip|web|hdr|hevc|h\.?265|h\.?264|x264|x265|ddp5\.1|ddp5|aac|mp3|amzn|pmtp|pm_tp|nf|hdr10|10bit|8bit)\b',
    flags=re.I
)

def sanitize_for_matching(name: str) -> str:
    """
    Remove common tags and replace separators with spaces so patterns match reliably.
    """
    s = re.sub(r'[\._\-]+', ' ', name)
    s = COMMON_TAGS_RE.sub('', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def detect_tv(filename: str) -> Optional[Tuple[str, int, int]]:
    """
    Try multiple common TV filename patterns and return (title, season, episode)
    """
    base = Path(filename).stem
    clean_base = sanitize_for_matching(base)

    patterns = [
        re.compile(r'(?i)^(?P<title>.*?)[ ]+S(?P<season>\d{1,2})[ ]*E(?P<episode>\d{1,2})'),
        re.compile(r'(?i)^(?P<title>.*?)[ ]*S(?P<season>\d{1,2})[ ]*E(?P<episode>\d{1,2})'),
        re.compile(r'(?i)^(?P<title>.*?)[ ]+(?P<season>\d{1,2})x(?P<episode>\d{1,2})'),
        re.compile(r'(?i)^(?P<title>.*?)[ ]+(?P<season>\d)(?P<episode>\d{2})(?!\d)'),
        re.compile(r'(?i)^(?P<title>.*?)[ ]*S(?P<season>\d{1,2})[ ]*E(?P<episode>\d{1,2})(?:[ \-]*E?(?P<episode2>\d{1,2}))'),
    ]

    for p in patterns:
        m = p.search(clean_base)
        if not m:
            continue

        gd = m.groupdict()
        ep = gd.get('episode')
        try:
            season = int(gd.get('season'))
            episode = int(ep) if ep else None
        except (TypeError, ValueError):
            continue

        title_raw = gd.get('title') if gd.get('title') is not None else clean_base[:m.start()]
        title = clean_title(title_raw)
        if not title or episode is None:
            continue

        return (title, season, episode)

    return None

def detect_movie(filename: str) -> Optional[Tuple[str, Optional[int]]]:
    base = Path(filename).stem
    clean_base = sanitize_for_matching(base)
    m = year_pattern.search(clean_base)
    year = None
    title_part = clean_base
    if m:
        try:
            year = int(m.group(1))
            current_year = datetime.datetime.now().year
            if not (1900 <= year <= current_year + 1):
                year = None
            else:
                title_part = clean_base[:m.start()]
        except ValueError:
            year = None

    title = clean_title(title_part)
    if not title:
        return None
    return (title, year)

# ---------------------------------------------------------
# PROCESSING
# ---------------------------------------------------------

def process_tv(src: str, title: str, season: int, episode: int) -> None:
    season_folder = f"Season {season:02d}"
    dest_dir = os.path.join(TV_ROOT, title, season_folder)
    safe_makedirs(dest_dir)
    dest_name = f"{title.replace(' ', '.')}.S{season:02d}E{episode:02d}{Path(src).suffix}"
    dest_path = os.path.join(dest_dir, dest_name)
    create_hardlink(src, dest_path)

def process_movie(src: str, title: str, year: Optional[int]) -> None:
    folder_name = f"{title} ({year})" if year else title
    dest_dir = os.path.join(MOVIE_ROOT, folder_name)
    safe_makedirs(dest_dir)
    base_name = f"{title.replace(' ', '.')}"
    if year:
        base_name += f".{year}"
    dest_name = base_name + Path(src).suffix
    dest_path = os.path.join(dest_dir, dest_name)
    create_hardlink(src, dest_path)

def process_file(path: str) -> None:
    filename = Path(path).name

    # Explicitly skip temporary/partial download files
    if is_temporary_file(filename):
        log(f"[SKIP] Temporary/partial file: {filename}")
        return

    # Only consider known video file extensions
    if not is_video_file(filename):
        return

    if SKIP_SAMPLES and "sample" in filename.lower():
        log(f"[SKIP] Sample file: {filename}")
        return

    if is_recently_modified(path, DOWNLOAD_GRACE_PERIOD):
        log(f"[WAIT] Recently modified: {filename}")
        return

    # TV ALWAYS wins over movie
    tv_info = detect_tv(filename)
    if tv_info:
        title, season, episode = tv_info
        log(f"[TV] {filename} → {title} S{season:02d}E{episode:02d}")
        process_tv(path, title, season, episode)
        return

    # Only detect movie if NOT TV
    movie_info = detect_movie(filename)
    if movie_info:
        title, year = movie_info
        log(f"[MOVIE] {filename} → {title} ({year})" if year else f"[MOVIE] {filename} → {title}")
        process_movie(path, title, year)
        return

    log(f"[UNMATCHED] {filename}")

# ---------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------

def main() -> None:
    log(f"Watching: {WATCH_FOLDER}")
    log(f"Media root: {BASE_MEDIA_FOLDER}")
    log(f"DRY RUN: {DRY_RUN}")
    log(f"Skip samples: {SKIP_SAMPLES}")
    log(f"Scan subfolders: {SCAN_SUBFOLDERS} (max depth: {MAX_SUBFOLDER_DEPTH})")

    if not os.path.isdir(WATCH_FOLDER):
        log(f"[ERROR] Watch folder missing: {WATCH_FOLDER}")
        sys.exit(1)

    seen = set()

    log("Monitoring started.")

    try:
        while True:
            if SCAN_SUBFOLDERS:
                # Scan WATCH_FOLDER and subfolders up to MAX_SUBFOLDER_DEPTH
                for root, dirs, files in os.walk(WATCH_FOLDER):
                    # Calculate depth relative to WATCH_FOLDER
                    depth = root.replace(WATCH_FOLDER, '').count(os.sep)
                    
                    # Skip if too deep
                    if depth > MAX_SUBFOLDER_DEPTH:
                        # Don't descend further into subdirectories
                        dirs.clear()
                        continue
                    
                    for filename in files:
                        full_path = os.path.join(root, filename)
                        if full_path not in seen:
                            process_file(full_path)
                            seen.add(full_path)
            else:
                # Original behavior: only scan root of WATCH_FOLDER
                for filename in os.listdir(WATCH_FOLDER):
                    full_path = os.path.join(WATCH_FOLDER, filename)
                    if os.path.isfile(full_path) and full_path not in seen:
                        process_file(full_path)
                        seen.add(full_path)

            time.sleep(SCAN_INTERVAL)
    except KeyboardInterrupt:
        log("Stopped by user.")

if __name__ == "__main__":
    main()
