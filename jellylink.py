#!/usr/bin/env python3
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
import sqlite3

# WhatsApp notifications
try:
    from twilio.rest import Client
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False
    print("Warning: twilio not installed. Install with: pip install twilio --break-system-packages")

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
config.read(config_path)

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

# New config option for recursive scanning
RECURSIVE_SCAN = get_bool(config.get("DEFAULT", "RECURSIVE_SCAN", fallback="true"))

# WhatsApp notification settings
ENABLE_WHATSAPP = get_bool(config.get("DEFAULT", "ENABLE_WHATSAPP", fallback="false"))
TWILIO_ACCOUNT_SID = config.get("DEFAULT", "TWILIO_ACCOUNT_SID", fallback="")
TWILIO_AUTH_TOKEN = config.get("DEFAULT", "TWILIO_AUTH_TOKEN", fallback="")
TWILIO_WHATSAPP_FROM = config.get("DEFAULT", "TWILIO_WHATSAPP_FROM", fallback="+14155238886")
WHATSAPP_TO = config.get("DEFAULT", "WHATSAPP_TO", fallback="")

# Database for dashboard
DB_PATH = os.path.join(script_dir, "jellylink.db")

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
    name = re.sub(r"[._()]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name

def normalize_tv_title(title: str) -> str:
    """
    Normalize TV show titles by removing years and extra spacing.
    Examples:
      "Landman 2024" -> "Landman"
      "The Simpsons 1989" -> "The Simpsons"
    """
    # Remove 4-digit years (1900-2099) from the end of the title
    title = re.sub(r'\s+(19\d{2}|20\d{2})$', '', title)
    return title.strip()

# ---------------------------------------------------------
# WHATSAPP NOTIFICATIONS
# ---------------------------------------------------------

def send_whatsapp_notification(title: str, media_type: str, details: str) -> None:
    """Send WhatsApp notification when new media is processed"""
    if not ENABLE_WHATSAPP:
        return
    
    if not TWILIO_AVAILABLE:
        log("[WHATSAPP ERROR] Twilio library not installed")
        return
    
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, WHATSAPP_TO]):
        log("[WHATSAPP ERROR] Missing Twilio credentials in config")
        return
    
    if DRY_RUN:
        log(f"[DRY RUN] Would send WhatsApp: {media_type} - {title} - {details}")
        return
    
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        message_body = f"🎬 New {media_type} Added!\n\n📺 {title}\n📋 {details}\n\n✅ Ready to watch!"
        
        message = client.messages.create(
            from_=f'whatsapp:{TWILIO_WHATSAPP_FROM}',
            body=message_body,
            to=f'whatsapp:{WHATSAPP_TO}'
        )
        
        log(f"[WHATSAPP] Notification sent: {title} (SID: {message.sid})")
        log_notification(title, media_type, details, "sent")
    except Exception as e:
        log(f"[WHATSAPP ERROR] Failed to send notification: {e}")
        log_notification(title, media_type, details, "failed")

# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------

def init_db() -> None:
    """Initialize the SQLite database for tracking processed media"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Main media table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_filename TEXT NOT NULL,
                title TEXT NOT NULL,
                media_type TEXT NOT NULL,
                season INTEGER,
                episode INTEGER,
                year INTEGER,
                destination_path TEXT NOT NULL,
                processed_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Notifications table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                media_type TEXT NOT NULL,
                details TEXT NOT NULL,
                status TEXT NOT NULL,
                sent_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
    except Exception as e:
        log(f"[DB ERROR] Failed to initialize database: {e}")

def log_processed_media(original_filename: str, title: str, media_type: str, 
                       season: Optional[int], episode: Optional[int], 
                       year: Optional[int], destination_path: str) -> None:
    """Log processed media to database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO processed_media 
            (original_filename, title, media_type, season, episode, year, destination_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (original_filename, title, media_type, season, episode, year, destination_path))
        
        conn.commit()
        conn.close()
    except Exception as e:
        log(f"[DB ERROR] Failed to log processed media: {e}")

def log_notification(title: str, media_type: str, details: str, status: str) -> None:
    """Log notification attempt to database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO notifications (title, media_type, details, status)
            VALUES (?, ?, ?, ?)
        """, (title, media_type, details, status))
        
        conn.commit()
        conn.close()
    except Exception as e:
        log(f"[DB ERROR] Failed to log notification: {e}")

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
        # Explicit S##E## format (most reliable)
        re.compile(r'(?i)^(?P<title>.*?)[ ]+S(?P<season>\d{1,2})[ ]*E(?P<episode>\d{1,2})'),
        re.compile(r'(?i)^(?P<title>.*?)[ ]*S(?P<season>\d{1,2})[ ]*E(?P<episode>\d{1,2})'),
        # ##x## format (e.g., 2x05)
        re.compile(r'(?i)^(?P<title>.*?)[ ]+(?P<season>\d{1,2})x(?P<episode>\d{1,2})'),
        # ### format (e.g., 205) - but only match if there's a clear separator or after "Season" keyword
        # This prevents matching sequel numbers like "Greenland 2" followed by year "2026"
        re.compile(r'(?i)^(?P<title>.*?)(?:[ \-\._]+|season[ \-\._]+)(?P<season>\d)(?P<episode>\d{2})(?!\d)'),
        # Multi-episode format (S##E##-E##)
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
        title = normalize_tv_title(title)  # Remove years from TV titles
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
# QUALITY DETECTION
# ---------------------------------------------------------

def get_resolution(filename: str) -> int:
    """
    Extract resolution from filename and return as integer for comparison.
    Returns: 2160, 1080, 720, 480, or 0 if unknown
    """
    filename_lower = filename.lower()
    
    if '2160p' in filename_lower or '4k' in filename_lower or 'uhd' in filename_lower:
        return 2160
    elif '1080p' in filename_lower:
        return 1080
    elif '720p' in filename_lower:
        return 720
    elif '480p' in filename_lower:
        return 480
    
    return 0  # Unknown quality

# ---------------------------------------------------------
# PROCESSING
# ---------------------------------------------------------

def process_tv(src: str, title: str, season: int, episode: int) -> None:
    season_folder = f"Season {season:02d}"
    dest_dir = os.path.join(TV_ROOT, title, season_folder)
    safe_makedirs(dest_dir)
    
    # Get resolution of new file
    new_resolution = get_resolution(Path(src).name)
    
    # Check for existing episodes (any extension)
    existing_pattern = f"{title.replace(' ', '.')}.S{season:02d}E{episode:02d}.*"
    existing_files = []
    
    if os.path.exists(dest_dir):
        existing_files = [f for f in Path(dest_dir).glob(existing_pattern) if f.is_file()]
    
    # Compare quality with existing files
    should_process = True
    for existing in existing_files:
        existing_res = get_resolution(existing.name)
        
        if new_resolution > existing_res:
            # New file is better quality - delete old one
            log(f"[UPGRADE] {new_resolution}p > {existing_res}p, replacing {existing.name}")
            if not DRY_RUN:
                try:
                    os.remove(existing)
                    log(f"[DELETED] {existing}")
                except Exception as e:
                    log(f"[ERROR] Could not delete {existing}: {e}")
        elif new_resolution == existing_res:
            # Same quality - skip
            log(f"[SKIP] Already have {existing_res}p version of S{season:02d}E{episode:02d}")
            should_process = False
            break
        else:
            # Existing is better quality - skip new file
            log(f"[SKIP] Existing {existing_res}p is better than new {new_resolution}p")
            should_process = False
            break
    
    # Only process if we should (either new episode or upgrade)
    if not should_process:
        return
    
    dest_name = f"{title.replace(' ', '.')}.S{season:02d}E{episode:02d}{Path(src).suffix}"
    dest_path = os.path.join(dest_dir, dest_name)
    create_hardlink(src, dest_path)
    
    # Log to database
    log_processed_media(Path(src).name, title, "TV", season, episode, None, dest_path)
    
    # Send WhatsApp notification (with quality info if upgrade)
    quality_suffix = f" ({new_resolution}p)" if new_resolution > 0 else ""
    upgrade_suffix = " [UPGRADED]" if existing_files else ""
    details = f"S{season:02d}E{episode:02d}{quality_suffix}{upgrade_suffix}"
    send_whatsapp_notification(title, "TV Show", details)

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
    
    # Log to database
    log_processed_media(Path(src).name, title, "Movie", None, None, year, dest_path)
    
    # Send WhatsApp notification
    details = f"({year})" if year else "Year unknown"
    send_whatsapp_notification(title, "Movie", details)

def process_file(path: str) -> bool:
    """
    Process a file and return True if successfully handled.
    Returns False if file should be retried later.
    """
    filename = Path(path).name

    # Explicitly skip temporary/partial download files
    if is_temporary_file(filename):
        log(f"[SKIP] Temporary/partial file: {filename}")
        return False

    # Only consider known video file extensions
    if not is_video_file(filename):
        return False

    if SKIP_SAMPLES and "sample" in filename.lower():
        log(f"[SKIP] Sample file: {filename}")
        return True  # Don't retry samples

    if is_recently_modified(path, DOWNLOAD_GRACE_PERIOD):
        log(f"[WAIT] Recently modified: {filename}")
        return False  # Retry later when grace period expires

    # Smart detection: If a clear year (19xx or 20xx) is present AND would make a valid movie,
    # check movie first to avoid misidentifying sequels (like "Greenland 2 Migration 2026")
    # as TV episodes (S02E26)
    
    movie_info = detect_movie(filename)
    tv_info = detect_tv(filename)
    
    # If we have a movie with a valid year, prefer it over TV detection
    if movie_info:
        title, year = movie_info
        if year is not None:  # Valid year found
            # Double-check: does the TV detection look suspicious?
            # If TV was detected but movie has a year, it's likely a sequel being misread
            if tv_info:
                tv_title, tv_season, tv_episode = tv_info
                # If the "episode" number is suspiciously high (>50) or matches part of the year,
                # it's probably a false positive
                year_str = str(year)
                if tv_episode > 50 or str(tv_episode) in year_str:
                    log(f"[MOVIE] {filename} → {title} ({year}) [TV detection ignored: suspicious S{tv_season:02d}E{tv_episode:02d}]")
                    process_movie(path, title, year)
                    return True
            
            # Normal movie with year
            log(f"[MOVIE] {filename} → {title} ({year})")
            process_movie(path, title, year)
            return True
    
    # If no movie with year, check TV
    if tv_info:
        title, season, episode = tv_info
        log(f"[TV] {filename} → {title} S{season:02d}E{episode:02d}")
        process_tv(path, title, season, episode)
        return True
    
    # If we detected a movie but no year, use it as fallback
    if movie_info:
        title, year = movie_info
        log(f"[MOVIE] {filename} → {title}" + (f" ({year})" if year else ""))
        process_movie(path, title, year)
        return True

    log(f"[UNMATCHED] {filename}")
    return True  # Don't retry unmatched files

def find_video_files(root_dir: str) -> list:
    """
    Recursively find all video files in the watch folder.
    Returns a list of full paths to video files.
    """
    video_files = []
    
    if not RECURSIVE_SCAN:
        # Original behavior - only scan files directly in root_dir
        for filename in os.listdir(root_dir):
            full_path = os.path.join(root_dir, filename)
            if os.path.isfile(full_path):
                video_files.append(full_path)
    else:
        # New behavior - recursively scan subfolders
        for dirpath, dirnames, filenames in os.walk(root_dir):
            # Skip Sample folders (common in torrent releases)
            dirnames[:] = [d for d in dirnames if d.lower() != 'sample']
            
            for filename in filenames:
                full_path = os.path.join(dirpath, filename)
                video_files.append(full_path)
    
    return video_files

# ---------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------

def main() -> None:
    # Initialize database
    init_db()
    
    log(f"Watching: {WATCH_FOLDER}")
    log(f"Media root: {BASE_MEDIA_FOLDER}")
    log(f"DRY RUN: {DRY_RUN}")
    log(f"Skip samples: {SKIP_SAMPLES}")
    log(f"Recursive scan: {RECURSIVE_SCAN}")
    log(f"WhatsApp notifications: {ENABLE_WHATSAPP}")
    log(f"Dashboard database: {DB_PATH}")

    if not os.path.isdir(WATCH_FOLDER):
        log(f"[ERROR] Watch folder missing: {WATCH_FOLDER}")
        sys.exit(1)

    processed = set()  # Track successfully processed files

    log("Monitoring started.")

    try:
        while True:
            # Find all video files (recursively if enabled)
            video_files = find_video_files(WATCH_FOLDER)
            
            for full_path in video_files:
                # Skip if already successfully processed
                if full_path in processed:
                    continue
                
                # Try to process the file
                success = process_file(full_path)
                
                # Only mark as processed if successfully handled
                # Files waiting for grace period will be retried next scan
                if success:
                    processed.add(full_path)

            time.sleep(SCAN_INTERVAL)
    except KeyboardInterrupt:
        log("Stopped by user.")

if __name__ == "__main__":
    main()
