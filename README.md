# JellyLink

A Python-based media organizer that automatically sorts TV shows and movies from your downloads folder into a properly structured media library for Jellyfin, Plex, or Emby.

## Features

- **Automatic Detection**: Intelligently identifies TV shows and movies from filenames
- **Smart Linking**: Creates hardlinks to save disk space (falls back to copying across filesystems)
- **Graceful Handling**: Waits for downloads to complete before processing
- **Temporary File Filtering**: Skips partial downloads (.part, .!qB, etc.)
- **Sample File Detection**: Optionally ignores sample files
- **Dry-Run Mode**: Test your configuration without making changes
- **Continuous Monitoring**: Watches your downloads folder and processes new files automatically

## Requirements

- Python 3.6+
- No external dependencies (uses only Python standard library)

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/jellylink.git
cd jellylink
```

2. Make the script executable:
```bash
chmod +x jelly.py
```

3. Configure your settings:
```bash
cp jellylink.conf.example jellylink.conf
nano jellylink.conf
```

## Configuration

Edit `jellylink.conf` to match your setup:

```ini
[DEFAULT]

# Enable or disable dry-run mode (true = no changes made)
DRY_RUN = false

# Folder to watch for new downloads
WATCH_FOLDER = /home/martin/Downloads

# Root media directory
MEDIA_ROOT = /media

# Subfolders inside MEDIA_ROOT
TV_FOLDER = TV
MOVIE_FOLDER = Movies

# Skip sample files (true/false)
SKIP_SAMPLES = true

# Log file (leave blank to log only to console/journal)
LOG_FILE = /home/martin/python-scripts/jellylink.log

# Time (seconds) since last modification before processing a file
DOWNLOAD_GRACE_PERIOD = 60

# How often to scan for new files (seconds)
SCAN_INTERVAL = 15
```

### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `DRY_RUN` | Test mode - no actual changes made | `true` |
| `WATCH_FOLDER` | Directory to monitor for new files | `~/Downloads` |
| `MEDIA_ROOT` | Base directory for organized media | `/media` |
| `TV_FOLDER` | Subfolder for TV shows | `TV` |
| `MOVIE_FOLDER` | Subfolder for movies | `Movies` |
| `SKIP_SAMPLES` | Ignore files with "sample" in name | `true` |
| `LOG_FILE` | Path to log file (empty = console only) | `` |
| `DOWNLOAD_GRACE_PERIOD` | Seconds to wait after file modification | `60` |
| `SCAN_INTERVAL` | Seconds between folder scans | `15` |

## Usage

### Basic Usage

Run with default config file (`jellylink.conf` in script directory):
```bash
./jelly.py
```

### Using Custom Config

Specify a different config file:
```bash
./jelly.py --config /path/to/custom.conf
```

### Dry-Run Mode

Test without making changes:
```bash
./jelly.py --dry-run
```

### Running as a Service

Create a systemd service file `/etc/systemd/system/jellylink.service`:

```ini
[Unit]
Description=JellyLink Media Organizer
After=network.target

[Service]
Type=simple
User=martin
WorkingDirectory=/home/martin/python-scripts
ExecStart=/usr/bin/python3 /home/martin/python-scripts/jelly.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable jellylink
sudo systemctl start jellylink
sudo systemctl status jellylink
```

## Supported Formats

### TV Show Naming Patterns

JellyLink recognizes these common TV show formats:

- `Show.Name.S01E05.mkv` → Show Name, Season 1, Episode 5
- `Show Name S01E05.mkv`
- `Show.Name.1x05.mkv`
- `Show.Name.105.mkv` (Season 1, Episode 5)
- `Show.Name.S01E05E06.mkv` (multi-episode)

### Movie Naming Patterns

Movies are identified by year in the filename:

- `Movie.Name.2023.mkv` → Movie Name (2023)
- `Movie Name (2023).mkv`
- `Movie.Name.2023.1080p.mkv`

### Video File Extensions

Supported: `.mkv`, `.mp4`, `.avi`, `.mov`, `.m4v`

### Temporary Files (Automatically Skipped)

- `.part` (Firefox/Chrome)
- `.crdownload` (Chrome)
- `.!ut` (uTorrent)
- `.!qB` (qBittorrent)
- `.aria2` (Aria2)
- `.partial` (generic)

## Output Structure

### TV Shows
```
/media/TV/
├── Breaking Bad/
│   ├── Season 01/
│   │   ├── Breaking.Bad.S01E01.mkv
│   │   ├── Breaking.Bad.S01E02.mkv
│   │   └── ...
│   └── Season 02/
│       └── ...
```

### Movies
```
/media/Movies/
├── Inception (2010)/
│   └── Inception.2010.mkv
├── The Matrix (1999)/
│   └── The.Matrix.1999.mkv
```

## How It Works

1. **Monitors** your downloads folder every 15 seconds (configurable)
2. **Waits** for files to finish downloading (grace period)
3. **Parses** filenames to extract show/movie information
4. **Creates** organized folder structure
5. **Hardlinks** files to save space (or copies if on different filesystem)
6. **Logs** all operations for troubleshooting

## Troubleshooting

### Files aren't being processed

- Check that the file extension is supported
- Verify the filename matches a recognized pattern
- Ensure the grace period has elapsed
- Check log file for errors

### "Cross-device link" errors

This is normal when source and destination are on different filesystems. JellyLink automatically falls back to copying the file.

### Duplicate files

JellyLink checks if files are already linked (same inode) and skips them. If you see a "File exists (different)" message, a different file with that name already exists.

### Permission errors

Ensure the user running JellyLink has:
- Read access to `WATCH_FOLDER`
- Write access to `MEDIA_ROOT`

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - feel free to use and modify as needed.

## Acknowledgments

Built for seamless integration with Jellyfin, Plex, and Emby media servers.
