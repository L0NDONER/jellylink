#!/usr/bin/env python3

#######################################
#
#  Movie Folder Wrapper v1.0
#  Wraps orphan movie files in proper folders for Jellyfin
#
########################################

import os
import re
import shutil
from pathlib import Path

VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.mov', '.m4v'}

def detect_movie_info(filename):
    """
    Extract movie title and year from filename.
    Returns (title, year) or (title, None)
    """
    # Remove extension
    name = Path(filename).stem
    
    # Common patterns: Movie.Name.2024.1080p.BluRay.mkv
    # Try to find year (1900-2099)
    year_match = re.search(r'[\.\s](19\d{2}|20\d{2})', name)
    year = int(year_match.group(1)) if year_match else None
    
    if year:
        # Everything before the year is the title
        title = name[:year_match.start()]
    else:
        # No year found - take everything before quality markers
        quality_markers = r'(1080p|720p|480p|2160p|4K|BluRay|WEBRip|HDTV|x264|x265|HEVC)'
        quality_match = re.search(quality_markers, name, re.IGNORECASE)
        if quality_match:
            title = name[:quality_match.start()]
        else:
            title = name
    
    # Clean up title: replace dots/underscores with spaces
    title = re.sub(r'[._]', ' ', title)
    # Remove extra spaces
    title = re.sub(r'\s+', ' ', title).strip()
    # Title case
    title = title.title()
    
    return title, year

def wrap_orphan_movies(base_path, dry_run=True):
    """
    Find orphan movie files (not in folders) and wrap them in proper folders.
    
    Before: /media/Movies/Movie.Name.2024.mkv
    After:  /media/Movies/Movie Name (2024)/Movie.Name.2024.mkv
    """
    print(f"{'[DRY RUN] ' if dry_run else ''}Wrapping orphan movies in: {base_path}\n")
    
    orphans = []
    
    # Find all video files directly in base_path (not in subdirectories)
    for item in Path(base_path).iterdir():
        if item.is_file() and item.suffix.lower() in VIDEO_EXTS:
            orphans.append(item)
    
    if not orphans:
        print("✓ No orphan movies found - all files are already in folders!")
        return
    
    print(f"Found {len(orphans)} orphan movie files:\n")
    
    moves = []
    conflicts = []
    
    for orphan in orphans:
        title, year = detect_movie_info(orphan.name)
        
        # Create folder name
        folder_name = f"{title} ({year})" if year else title
        folder_path = Path(base_path) / folder_name
        dest_path = folder_path / orphan.name
        
        # Check if folder already exists with a different file
        if folder_path.exists():
            existing_files = list(folder_path.glob('*'))
            if existing_files and existing_files[0].name != orphan.name:
                conflicts.append((orphan, folder_path, existing_files))
                continue
        
        moves.append((orphan, folder_path, dest_path, title, year))
    
    # Report conflicts
    if conflicts:
        print("⚠️  CONFLICTS (folder exists with different file):\n")
        for orphan, folder, existing in conflicts:
            print(f"  {orphan.name}")
            print(f"    Would go to: {folder}")
            print(f"    But contains: {existing[0].name}")
            print(f"    → Manual review needed\n")
    
    # Report planned moves
    if not moves:
        if conflicts:
            print("No safe moves available - resolve conflicts first.")
        return
    
    print(f"Will wrap {len(moves)} movies:\n")
    for orphan, folder, dest, title, year in moves:
        year_str = f" ({year})" if year else ""
        print(f"  {orphan.name}")
        print(f"    → {title}{year_str}/")
        print(f"       {dest}\n")
    
    # Apply changes
    if not dry_run:
        print("=" * 60)
        print("WRAPPING MOVIES...")
        print("=" * 60 + "\n")
        
        success = 0
        failed = 0
        
        for orphan, folder, dest, title, year in moves:
            try:
                # Create folder
                folder.mkdir(exist_ok=True)
                # Move file into folder
                shutil.move(str(orphan), str(dest))
                year_str = f" ({year})" if year else ""
                print(f"✓ Wrapped: {title}{year_str}")
                success += 1
            except Exception as e:
                print(f"✗ Failed: {orphan.name}: {e}")
                failed += 1
        
        print(f"\n{'=' * 60}")
        print(f"Complete: {success} wrapped, {failed} failed")
        print(f"{'=' * 60}")
    else:
        print("=" * 60)
        print("[DRY RUN] No changes made.")
        print("Run with --apply to wrap these movies")
        print("=" * 60)

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        target_dir = sys.argv[1]
    else:
        target_dir = "/media/Movies"
    
    apply = "--apply" in sys.argv
    
    if not os.path.exists(target_dir):
        print(f"Error: '{target_dir}' does not exist.\n")
        print("Usage:")
        print(f"  {sys.argv[0]} [path] [--apply]\n")
        print("Examples:")
        print(f"  {sys.argv[0]}                    # Check /media/Movies")
        print(f"  {sys.argv[0]} /media/Movies      # Check specific path")
        print(f"  {sys.argv[0]} --apply            # Wrap orphans in /media/Movies")
        sys.exit(1)
    
    wrap_orphan_movies(target_dir, dry_run=not apply)
