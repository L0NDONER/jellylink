#!/usr/bin/env python3

#######################################
#
#  JellyLink Folder Normalizer v2.0
#  Normalize folders to match JellyLink standard:
#  - Title Case capitalization
#  - Spaces (not periods) in folder names
#
########################################

import os
import re
from pathlib import Path

def is_season_folder(name):
    """Check if this is a Season folder (should stay as-is)"""
    return bool(re.match(r'^Season \d{2}$', name))

def smart_title_case(name):
    """
    Convert to title case with smart handling of special cases.
    """
    # First, convert periods to spaces (scene -> readable)
    name = name.replace('.', ' ')
    
    # Remove multiple spaces
    name = re.sub(r'\s+', ' ', name).strip()
    
    # Basic title case
    result = name.title()
    
    # Fix common small words (unless first/last word)
    small_words = ['Of', 'The', 'A', 'An', 'And', 'But', 'Or', 'For', 'Nor', 'On', 'At', 'To', 'By', 'In', 'Out', 'Does']
    words = result.split()
    
    if len(words) > 1:
        for i in range(1, len(words) - 1):
            if words[i] in small_words:
                words[i] = words[i].lower()
        result = ' '.join(words)
    
    return result

def needs_normalization(name):
    """Check if a folder name needs normalization"""
    # Skip Season folders - they're already correct
    if is_season_folder(name):
        return False
    
    # Check if it has periods (scene style)
    if '.' in name:
        return True
    
    # Check if it's not title case
    if name != smart_title_case(name):
        return True
    
    return False

def normalize_folders(base_path, dry_run=True):
    """
    Normalize all folders to JellyLink standard:
    - Convert periods to spaces
    - Apply Title Case
    """
    print(f"{'[DRY RUN] ' if dry_run else ''}Normalizing to JellyLink standard: {base_path}\n")
    
    changes = []
    conflicts = []
    skipped = []
    
    # Walk bottom-up to avoid path invalidation
    for root, dirs, _ in os.walk(base_path, topdown=False):
        for dir_name in dirs:
            original_path = os.path.join(root, dir_name)
            
            # Check if normalization needed
            if not needs_normalization(dir_name):
                skipped.append(dir_name)
                continue
            
            # Normalize the name
            normalized_name = smart_title_case(dir_name)
            
            # Skip if somehow no change
            if normalized_name == dir_name:
                skipped.append(dir_name)
                continue
            
            normalized_path = os.path.join(root, normalized_name)
            
            # Check for conflicts
            if os.path.exists(normalized_path) and original_path.lower() != normalized_path.lower():
                conflicts.append((original_path, normalized_path))
            else:
                changes.append((original_path, normalized_path, dir_name, normalized_name))
    
    # Report skipped
    if skipped and dry_run:
        print(f"✓ Skipped {len(skipped)} folders (already correct)\n")
    
    # Report conflicts
    if conflicts:
        print(f"⚠️  Found {len(conflicts)} CONFLICTS:\n")
        for old, new in conflicts:
            print(f"  {os.path.basename(old)}")
            print(f"  → {os.path.basename(new)}")
            print(f"    {old}")
            print(f"    {new} (ALREADY EXISTS)\n")
    
    # Report changes
    if not changes:
        if not conflicts:
            print("✓ All folders already match JellyLink standard!")
        else:
            print("⚠️  No safe changes due to conflicts.")
        return
    
    print(f"Found {len(changes)} folders to normalize:\n")
    for old_path, new_path, old_name, new_name in changes:
        print(f"  {old_name}")
        print(f"  → {new_name}")
        print(f"    {old_path}\n")
    
    # Apply changes
    if not dry_run:
        print("=" * 60)
        print("NORMALIZING TO JELLYLINK STANDARD...")
        print("=" * 60 + "\n")
        
        success = 0
        failed = 0
        
        for old_path, new_path, old_name, new_name in changes:
            try:
                # Check if this is a case-only change (same path, different case)
                if old_path.lower() == new_path.lower():
                    # Two-step rename to handle case-sensitivity issues
                    temp_path = old_path + ".tmp_rename"
                    os.rename(old_path, temp_path)
                    os.rename(temp_path, new_path)
                else:
                    # Direct rename for different paths
                    os.rename(old_path, new_path)
                
                print(f"✓ {old_name}")
                print(f"  → {new_name}")
                success += 1
            except Exception as e:
                print(f"✗ Failed: {old_name}: {e}")
                failed += 1
        
        print(f"\n{'=' * 60}")
        print(f"Complete: {success} normalized, {failed} failed")
        print(f"{'=' * 60}")
    else:
        print("=" * 60)
        print("[DRY RUN] No changes made.")
        print("Run with --apply to normalize these folders")
        print("=" * 60)

if __name__ == "__main__":
    import sys
    
    # Parse arguments
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        target_dir = sys.argv[1]
    else:
        target_dir = "/media/TV"
    
    apply = "--apply" in sys.argv
    
    # Validate
    if not os.path.exists(target_dir):
        print(f"Error: '{target_dir}' does not exist.\n")
        print("Usage:")
        print(f"  {sys.argv[0]} [path] [--apply]\n")
        print("Examples:")
        print(f"  {sys.argv[0]}                  # Check /media/TV")
        print(f"  {sys.argv[0]} /media/Movies    # Check /media/Movies")
        print(f"  {sys.argv[0]} --apply          # Normalize /media/TV")
        print(f"  {sys.argv[0]} /media/Movies --apply  # Normalize Movies")
        sys.exit(1)
    
    normalize_folders(target_dir, dry_run=not apply)
