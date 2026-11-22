#!/usr/bin/env bash
# ==============================================================================
# Linux VPS Production Startup Script for Discord Music Bot
# Automatically checks venv, configuration, and launches bot.py with auto-restart
# ==============================================================================

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# 1. Check Virtual Environment
if [ -f ".venv/bin/python3" ]; then
    PYTHON_EXEC=".venv/bin/python3"
elif [ -f "venv/bin/python3" ]; then
    PYTHON_EXEC="venv/bin/python3"
elif command -v python3 &> /dev/null; then
    PYTHON_EXEC="python3"
else
    echo "Error: Python 3 executable not found! Run ./install.sh first."
    exit 1
fi

# 2. Check Configuration
if [ ! -f ".env" ]; then
    echo "Error: .env configuration file not found!"
    echo "Please copy .env.example to .env and configure your DISCORD_TOKEN."
    exit 1
fi

if grep -q "DISCORD_TOKEN=your_discord_bot_token_here" .env || grep -q "DISCORD_TOKEN=$" .env; then
    echo "Warning: DISCORD_TOKEN has not been set in .env!"
    echo "Please edit .env and insert your real Discord bot token."
    exit 1
fi

# 3. Run Pre-flight Healthcheck
echo "Running pre-flight checks..."
$PYTHON_EXEC healthcheck.py

echo "Starting Discord Music Bot (Press CTRL+C to stop)..."
exec $PYTHON_EXEC bot.py
