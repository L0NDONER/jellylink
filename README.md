# JellyLink Media Organizer

Automatic media file organizer for Jellyfin / Plex.  
Monitors a download folder and automatically organizes TV shows and movies into a proper library structure using **hardlinks**.

## Features

- 🐳 **Dockerized Deployment**  
  Runs in an isolated container with `unless-stopped` restart policy

- 📺 **Automatic TV Show Detection**  
  Reliably parses S##E##, #x## and complex scene / P2P release strings

- 🎬 **Movie Detection with Year Parsing**  
  Smart sequel handling (e.g. Deadpool 2, Batman Begins / The Dark Knight)

- ⬆️ **Quality-based Upgrades**  
  Automatically replaces lower quality files with better versions (e.g. 2160p > 1080p > WEBRip > HDTV)

- 🔄 **Cross-Device / Cross-Filesystem Support**  
  Falls back to safe copying when hardlinking is not possible

- 🗄️ **SQLite Database Tracking**  
  Prevents redundant processing of already handled files

- 📱 **WhatsApp Notifications**  
  Real-time updates via Twilio when new media is successfully organized

## Prerequisites

- Docker + Docker Compose
- A properly configured `jellylink.conf` (start with `jellylink.conf.example`)

## Deployment (Recommended – Docker)

```bash
# 1. Clone repository
git clone <repository-url>
cd jellylink

# 2. Create and edit configuration
cp jellylink.conf.example jellylink.conf
# → edit jellylink.conf with your paths & Twilio credentials

# 3. Start (builds image if needed)
docker compose up -d --build
