"""
Production Healthcheck and Diagnostics Script for Linux VPS / Windows.
Validates:
1. Python version >= 3.10
2. Essential Python packages and versions
3. FFmpeg binary availability and audio tone synthesis
4. Opus library readiness for discord.py voice
5. Database connectivity and schema
6. Configuration and token availability
"""

import os
import sys
import shutil
import platform
import subprocess
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def print_status(component: str, ok: bool, details: str = ""):
    symbol = f"{GREEN}[PASSED]{RESET}" if ok else f"{RED}[FAILED]{RESET}"
    print(f" {symbol} {component:<25} {details}")


def main():
    print("=" * 65)
    print("    DISCORD MUSIC BOT - VPS HEALTHCHECK & DIAGNOSTICS")
    print("=" * 65)

    all_passed = True

    # 1. Python Version Check
    py_ver = sys.version_info
    py_ok = (py_ver.major == 3 and py_ver.minor >= 10)
    print_status(
        "Python Version",
        py_ok,
        f"{sys.version.split()[0]} ({platform.system()} {platform.machine()})"
    )
    if not py_ok:
        all_passed = False

    # 2. Package Dependency Checks
    packages = [
        ("discord.py", "discord"),
        ("PyNaCl", "nacl"),
        ("yt-dlp", "yt_dlp"),
        ("aiohttp", "aiohttp"),
        ("aiosqlite", "aiosqlite"),
        ("python-dotenv", "dotenv"),
    ]

    for pkg_name, mod_name in packages:
        try:
            mod = __import__(mod_name)
            ver = getattr(mod, "__version__", "installed")
            print_status(f"Package: {pkg_name}", True, f"v{ver}")
        except ImportError as e:
            print_status(f"Package: {pkg_name}", False, f"Missing ({e})")
            all_passed = False

    # 3. FFmpeg Verification
    from core.ffmpeg_handler import find_ffmpeg, verify_ffmpeg
    ffmpeg_bin = find_ffmpeg()
    if ffmpeg_bin:
        can_run = verify_ffmpeg()
        print_status("FFmpeg Binary", can_run, f"{ffmpeg_bin}")
        if not can_run:
            all_passed = False
    else:
        print_status("FFmpeg Binary", False, "Not found in PATH or project. Install via apt-get or set FFMPEG_PATH.")
        all_passed = False

    # 4. Opus Library
    try:
        import discord.opus
        opus_loaded = discord.opus.is_loaded()
        if not opus_loaded:
            import ctypes.util
            opus_found = ctypes.util.find_library("opus")
            opus_loaded = bool(opus_found)
        print_status("Opus Audio Library", opus_loaded, "Ready for Voice UDP" if opus_loaded else "libopus may be needed on some Linux distros (apt install libopus0)")
    except Exception as e:
        print_status("Opus Audio Library", False, str(e))

    # 5. Database Connection Test
    try:
        import asyncio
        from core.database import db
        async def _test_db():
            await db.connect()
            await db.close()
        asyncio.run(_test_db())
        print_status("Database Connection", True, f"SQLite schema verified at {db.db_path}")
    except Exception as e:
        print_status("Database Connection", False, f"Error initializing database: {e}")
        all_passed = False

    # 6. Configuration & Token Check
    token = os.getenv("DISCORD_TOKEN", "").strip()
    token_valid = bool(token and token != "your_discord_bot_token_here")
    print_status("Discord Bot Token", token_valid, "Token configured in environment/.env" if token_valid else "Missing or placeholder in .env")
    if not token_valid:
        print(f" {YELLOW}[WARNING]{RESET} Bot will not start without a valid DISCORD_TOKEN in .env.")

    # 7. Spotify API Config (Optional)
    spot_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    spot_sec = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    spot_ready = bool(spot_id and spot_sec)
    print_status("Spotify Metadata API", True if spot_ready else True, "Configured (Ready)" if spot_ready else "Optional (Spotify links will not resolve without it)")

    print("=" * 65)
    if all_passed and token_valid:
        print(f"{GREEN}All critical diagnostics passed! The bot is ready for production 24/7 hosting.{RESET}")
        sys.exit(0)
    elif all_passed and not token_valid:
        print(f"{YELLOW}System dependencies and audio engine are ready! Add your DISCORD_TOKEN to .env before starting.{RESET}")
        sys.exit(0)
    else:
        print(f"{RED}Some diagnostics failed. Please resolve the errors above before launching.{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
