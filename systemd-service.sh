#!/bin/bash
#
# JellyLink systemd installation script
# This will set up JellyLink to run automatically as a system service
#

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}JellyLink systemd Service Installer${NC}\n"

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}Error: Do not run this script with sudo${NC}"
    echo "Run as your regular user. The script will ask for sudo when needed."
    exit 1
fi

# Variables - adjust these if your paths are different
JELLYLINK_DIR="/home/martin/jellylink"
SERVICE_FILE="jellylink.service"
SYSTEMD_DIR="/etc/systemd/system"

# Check if jellylink.py exists
if [ ! -f "$JELLYLINK_DIR/jellylink.py" ]; then
    echo -e "${RED}Error: jellylink.py not found in $JELLYLINK_DIR${NC}"
    echo "Please adjust JELLYLINK_DIR in this script or move jellylink.py to the correct location"
    exit 1
fi

# Check if service file exists
if [ ! -f "$SERVICE_FILE" ]; then
    echo -e "${RED}Error: $SERVICE_FILE not found in current directory${NC}"
    exit 1
fi

echo -e "${BLUE}Configuration:${NC}"
echo "  JellyLink directory: $JELLYLINK_DIR"
echo "  Service file: $SERVICE_FILE"
echo "  Install to: $SYSTEMD_DIR"
echo ""

read -p "Continue with installation? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Installation cancelled"
    exit 0
fi

# Install service file
echo -e "\n${BLUE}[1/5]${NC} Installing service file..."
sudo cp "$SERVICE_FILE" "$SYSTEMD_DIR/"
echo -e "${GREEN}✓${NC} Service file installed"

# Reload systemd
echo -e "\n${BLUE}[2/5]${NC} Reloading systemd daemon..."
sudo systemctl daemon-reload
echo -e "${GREEN}✓${NC} Systemd daemon reloaded"

# Enable service
echo -e "\n${BLUE}[3/5]${NC} Enabling JellyLink service..."
sudo systemctl enable jellylink.service
echo -e "${GREEN}✓${NC} Service enabled (will start on boot)"

# Start service
echo -e "\n${BLUE}[4/5]${NC} Starting JellyLink service..."
sudo systemctl start jellylink.service
sleep 2
echo -e "${GREEN}✓${NC} Service started"

# Check status
echo -e "\n${BLUE}[5/5]${NC} Checking service status..."
if sudo systemctl is-active --quiet jellylink.service; then
    echo -e "${GREEN}✓${NC} JellyLink is running!"
else
    echo -e "${RED}✗${NC} Service failed to start"
    echo -e "\n${YELLOW}Error details:${NC}"
    sudo systemctl status jellylink.service --no-pager
    exit 1
fi

# Show status
echo -e "\n${GREEN}════════════════════════════════════════${NC}"
echo -e "${GREEN}  JellyLink Service Installed Successfully!${NC}"
echo -e "${GREEN}════════════════════════════════════════${NC}\n"

echo -e "${BLUE}Useful commands:${NC}"
echo "  View status:       sudo systemctl status jellylink"
echo "  View logs:         sudo journalctl -u jellylink -f"
echo "  Stop service:      sudo systemctl stop jellylink"
echo "  Start service:     sudo systemctl start jellylink"
echo "  Restart service:   sudo systemctl restart jellylink"
echo "  Disable service:   sudo systemctl disable jellylink"
echo ""

echo -e "${BLUE}Current status:${NC}"
sudo systemctl status jellylink.service --no-pager | head -n 15

echo -e "\n${YELLOW}Tip:${NC} Use 'sudo journalctl -u jellylink -f' to watch live logs"
