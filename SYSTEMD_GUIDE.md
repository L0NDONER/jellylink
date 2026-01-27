# JellyLink systemd Service Setup

## Quick Install

1. Copy the files to your JellyLink directory:
```bash
cd /home/martin/jellylink
# Copy jellylink.py and jellylink.service here
```

2. Run the installer:
```bash
chmod +x install-systemd-service.sh
./install-systemd-service.sh
```

That's it! JellyLink will now:
- ✅ Start automatically on boot
- ✅ Run in the background
- ✅ Restart automatically if it crashes
- ✅ Log to systemd journal

## Manual Installation (if you prefer)

If you don't want to use the script:

```bash
# Copy service file
sudo cp jellylink.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable (start on boot)
sudo systemctl enable jellylink

# Start now
sudo systemctl start jellylink

# Check status
sudo systemctl status jellylink
```

## Daily Usage Commands

### View Status
```bash
sudo systemctl status jellylink
```

### View Live Logs
```bash
sudo journalctl -u jellylink -f
```

### View Recent Logs
```bash
sudo journalctl -u jellylink -n 100
```

### Restart Service
```bash
sudo systemctl restart jellylink
```

### Stop Service
```bash
sudo systemctl stop jellylink
```

### Start Service
```bash
sudo systemctl start jellylink
```

### Disable Auto-Start
```bash
sudo systemctl disable jellylink
```

### Enable Auto-Start
```bash
sudo systemctl enable jellylink
```

## Troubleshooting

### Service won't start
Check the logs:
```bash
sudo journalctl -u jellylink -n 50 --no-pager
```

Common issues:
- **Path incorrect**: Edit `/etc/systemd/system/jellylink.service` and fix paths
- **Permissions**: Ensure martin user can read jellylink.py
- **Config file**: Make sure jellylink.conf exists in the same directory

### After making changes
If you edit jellylink.service:
```bash
sudo systemctl daemon-reload
sudo systemctl restart jellylink
```

### View service file
```bash
cat /etc/systemd/system/jellylink.service
```

### Edit service file
```bash
sudo nano /etc/systemd/system/jellylink.service
# After saving:
sudo systemctl daemon-reload
sudo systemctl restart jellylink
```

## What the Service Does

- **Runs as**: User `martin` (not root - safer)
- **Working directory**: `/home/martin/jellylink`
- **Starts**: Automatically on boot
- **Restarts**: Automatically if it crashes (after 10 seconds)
- **Logs**: To systemd journal (view with `journalctl`)

## Service File Location

The service file will be installed to:
```
/etc/systemd/system/jellylink.service
```

## Uninstallation

To remove the systemd service:

```bash
# Stop the service
sudo systemctl stop jellylink

# Disable auto-start
sudo systemctl disable jellylink

# Remove service file
sudo rm /etc/systemd/system/jellylink.service

# Reload systemd
sudo systemctl daemon-reload
```

## Customization

Edit `/etc/systemd/system/jellylink.service` to customize:

- **User/Group**: Change `martin` to your username
- **WorkingDirectory**: Change if jellylink is in a different location
- **ExecStart**: Change path to jellylink.py or add arguments
- **Restart policy**: Change `RestartSec=10` to a different value

After editing, always run:
```bash
sudo systemctl daemon-reload
sudo systemctl restart jellylink
```

## Checking If It's Running

Quick check:
```bash
systemctl is-active jellylink
# Should output: active
```

Or full status:
```bash
sudo systemctl status jellylink
```

Look for:
- **Active: active (running)** ✅
- **Active: inactive (dead)** ❌
- **Active: failed** ❌

## Log Files

JellyLink logs to both:
1. **Systemd journal**: `sudo journalctl -u jellylink -f`
2. **Log file** (if configured in jellylink.conf): Check `LOG_FILE` setting

---

**Note**: The install script assumes JellyLink is in `/home/martin/jellylink`. If yours is elsewhere, edit the paths in `jellylink.service` before installing.
