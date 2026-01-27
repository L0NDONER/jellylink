# JellyLink Media Organizer

Automatic media file organizer for Jellyfin/Plex. Monitors a download folder and automatically organizes TV shows and movies into a proper library structure.

## Features

- ✅ Automatic TV show detection (S##E##, #x##, ### formats)
- ✅ Movie detection with year parsing
- ✅ Smart sequel handling (Deadpool 2, Avatar 2, etc.)
- ✅ Quality-based upgrades (replaces 720p with 1080p)
- ✅ Recursive folder scanning
- ✅ WhatsApp notifications (optional)
- ✅ SQLite database tracking
- ✅ Systemd service support

## Installation

1. Clone the repository
2. Copy `jellylink.conf.example` to `jellylink.conf`
3. Edit `jellylink.conf` with your paths
4. Install as systemd service (optional)

## Usage

### Manual run
```bash
python3 jellylink.py
```

### Systemd service
```bash
sudo cp jellylink.service /etc/systemd/system/
sudo systemctl enable jellylink
sudo systemctl start jellylink
```

## Recent Fixes

- Fixed sequel movie detection (movies with years now prioritized over false TV matches)
- Added suspicious episode detection (episode >50 or in year digits)

## License

Personal project - use at your own risk
