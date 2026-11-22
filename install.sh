#!/usr/bin/env bash
# ==============================================================================
# Linux KVM VPS Installation Script for Discord Music Bot
# Tested on Debian 11/12, Ubuntu 20.04/22.04/24.04, AlmaLinux/Rocky Linux 8/9
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}===================================================================${NC}"
echo -e "${BLUE}     DISCORD MUSIC BOT - LINUX VPS ENVIRONMENT SETUP               ${NC}"
echo -e "${BLUE}===================================================================${NC}"

# 1. Root / Sudo Check
if [ "$EUID" -ne 0 ]; then
    SUDO="sudo"
else
    SUDO=""
fi

# 2. Package Manager Detection & System Package Installation
echo -e "\n${YELLOW}[1/6] Installing necessary system packages & multimedia libraries...${NC}"
if command -v apt-get &> /dev/null; then
    echo "Detected Debian/Ubuntu system."
    $SUDO apt-get update -y
    $SUDO apt-get install -y python3 python3-pip python3-venv ffmpeg libopus0 libopus-dev libffi-dev build-essential curl git
elif command -v dnf &> /dev/null; then
    echo "Detected RHEL/Fedora/Rocky/AlmaLinux system."
    $SUDO dnf install -y epel-release || true
    $SUDO dnf install -y python3 python3-pip ffmpeg opus opus-devel libffi-devel gcc curl git
elif command -v pacman &> /dev/null; then
    echo "Detected Arch Linux system."
    $SUDO pacman -Sy --noconfirm python python-pip ffmpeg opus
else
    echo -e "${YELLOW}Warning: Unknown package manager. Please ensure Python 3.10+, pip, FFmpeg, and libopus are installed.${NC}"
fi

# 3. Verify Python 3 & Pip
echo -e "\n${YELLOW}[2/6] Verifying Python 3 version...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 was not found!${NC}"
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "Python Version Detected: ${GREEN}$PY_VER${NC}"

# 4. Create Virtual Environment
echo -e "\n${YELLOW}[3/6] Setting up isolated Python virtual environment (.venv)...${NC}"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo -e "${GREEN}Virtual environment created in .venv${NC}"
else
    echo "Virtual environment already exists in .venv."
fi

# 5. Install Dependencies inside .venv
echo -e "\n${YELLOW}[4/6] Installing and upgrading Python dependencies...${NC}"
.venv/bin/python3 -m pip install --upgrade pip setuptools wheel
.venv/bin/python3 -m pip install -r requirements.txt

# 6. Verify FFmpeg
echo -e "\n${YELLOW}[5/6] Verifying FFmpeg installation...${NC}"
if command -v ffmpeg &> /dev/null; then
    FFMPEG_LOC=$(which ffmpeg)
    echo -e "${GREEN}FFmpeg detected at: $FFMPEG_LOC${NC}"
else
    echo -e "${RED}FFmpeg not found in PATH! Audio streaming may fail unless FFMPEG_PATH is set in .env.${NC}"
fi

# 7. Environment File Setup
echo -e "\n${YELLOW}[6/6] Verifying configuration file (.env)...${NC}"
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${GREEN}Created .env from .env.example.${NC}"
        echo -e "${YELLOW}IMPORTANT: Open .env and insert your DISCORD_TOKEN before starting!${NC}"
    fi
else
    echo ".env file already exists."
fi

# Ensure data directory exists
mkdir -p data

echo -e "\n${BLUE}===================================================================${NC}"
echo -e "${GREEN}   INSTALLATION COMPLETE! Running health diagnostics...             ${NC}"
echo -e "${BLUE}===================================================================${NC}"
.venv/bin/python3 healthcheck.py || true

echo -e "\nTo start your bot:"
echo -e "  ${GREEN}./start.sh${NC}  or  ${GREEN}source .venv/bin/activate && python3 bot.py${NC}\n"
