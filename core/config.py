"""
Configuration module for Discord Music Bot.
Loads environment variables safely and provides structured, validated settings.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Discord Bot
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "").strip().strip('"')
COMMAND_PREFIX: str = os.getenv("COMMAND_PREFIX", "!").strip()

# Spotify API credentials
SPOTIFY_CLIENT_ID: str = os.getenv("SPOTIFY_CLIENT_ID", "").strip().strip('"')
SPOTIFY_CLIENT_SECRET: str = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip().strip('"')

# Paths & Storage
DATABASE_PATH: str = os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "musicbot.db")).strip().strip('"')
LEGACY_DATA_FILE: str = os.getenv("DATA_FILE", str(BASE_DIR / "data" / "playlists.json")).strip().strip('"')
FFMPEG_PATH_OVERRIDE: str = os.getenv("FFMPEG_PATH", "").strip().strip('"')
YOUTUBE_COOKIES_PATH: str = os.getenv("YOUTUBE_COOKIES_PATH", "").strip().strip('"')

# Operational Parameters
MAX_QUEUE: int = max(10, min(1000, int(os.getenv("MAX_QUEUE_SIZE", "200"))))
SYNC_MINUTES: int = max(5, int(os.getenv("SYNC_MINUTES", "30")))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# Supported Filter Definitions (FFmpeg audio filter chains)
AUDIO_FILTERS = {
    "none": "",
    "bassboost": "bass=g=10:f=110:w=0.6",
    "treble": "treble=g=8",
    "nightcore": "asetrate=48000*1.25,aresample=48000,atempo=1.0",
    "slow": "asetrate=48000*0.85,aresample=48000,atempo=1.0",
    "vaporwave": "asetrate=48000*0.8,aresample=48000,atempo=1.0",
    "8d": "apulsator=hz=0.125",
    "karaoke": "stereotools=mlev=0.03",
    "echo": "aecho=0.8:0.88:60:0.4",
    "tremolo": "tremolo=f=5:d=0.7",
    "vibrato": "vibrato=f=6:d=0.5",
    "soft": "lowpass=f=5000",
}

# Allowed URL Hosts for extraction safety
ALLOWED_HOSTS = (
    "youtube.com",
    "youtu.be",
    "music.youtube.com",
    "soundcloud.com",
    "spotify.com",
    "open.spotify.com",
)
