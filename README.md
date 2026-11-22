# 🎧 High-Fidelity Discord Music Bot (Production Edition)

A stable, asynchronous, multi-server Discord music bot engineered for 24/7 reliability on Linux KVM VPS and Windows environments. Built with **discord.py 2.5+**, **yt-dlp**, **FFmpeg**, and **aiosqlite**.

---

## ✨ Features

- **Multi-Source Audio Extraction**:
  - Full support for YouTube videos, searches, and public playlists.
  - SoundCloud audio streaming.
  - Spotify tracks, albums, and playlists (metadata resolved safely into playable audio).
- **Persistent UI Controls**:
  - Interactive Discord button panel (Pause/Resume, Skip, Stop, Previous, Loop, Shuffle, Volume, Queue).
  - Works across bot restarts without losing button listeners.
- **Advanced Audio Effects (FFmpeg DSP Filters)**:
  - Bass Boost, Nightcore, Slowed (Downtempo), Vaporwave, 8D Audio, Karaoke, Echo/Reverb, Treble Boost, Soft Lowpass.
- **Server Autoplay & Smart Recommendations**:
  - Continues playing similar songs automatically when the queue finishes.
- **24/7 Voice Mode**:
  - Keeps the bot connected in voice channels indefinitely (configurable per server).
- **Multi-Server Isolation**:
  - Each Discord guild operates completely independently with its own queue, volume, loop settings, and audio player.
- **DJ Role & Permission Enforcement**:
  - Restrict playback control to users with a configured DJ role or server administrators.
- **Custom Playlists & Favorites**:
  - Server-wide auto-syncing playlists (YouTube/Spotify) that periodically refresh.
  - Personal favorite bookmarks (`/favorite_add`, `/favorites`).
- **Synchronized Lyrics**:
  - Integrated LRCLIB lyrics search via `/lyrics`.

---

## 🛠️ Slash Commands Reference

| Command | Description |
| :--- | :--- |
| `/play <query>` | Play a song by title or YouTube/Spotify/SoundCloud link |
| `/pause` | Pause currently playing track |
| `/resume` | Resume paused audio playback |
| `/skip` | Skip to the next track in queue |
| `/previous` | Replay the previous track from history |
| `/stop` | Stop playback and clear the queue |
| `/leave` | Disconnect the bot from the voice channel |
| `/join` | Connect the bot to your current voice channel |
| `/nowplaying` | Show real-time playback embed and progress bar |
| `/queue` | Display the interactive paginated queue |
| `/volume <0-200>` | Adjust volume percentage |
| `/seek <timestamp>` | Jump to a timestamp (e.g. `1:30` or `90`) |
| `/loop <mode>` | Repeat mode: `off`, `track`, or `queue` |
| `/shuffle` | Randomize the upcoming queue |
| `/clear` | Remove upcoming tracks without stopping playback |
| `/remove <position>` | Delete a specific track from the queue |
| `/move <from> <to>` | Reorder a track in the queue |
| `/filter <name>` | Apply audio DSP effect (Bass Boost, Nightcore, 8D, etc.) |
| `/247` | Toggle 24/7 voice channel persistence |
| `/autoplay` | Toggle smart recommendations on queue end |
| `/playlist_add <name> <url>` | Save a YouTube/Spotify playlist to auto-sync |
| `/playlist_play <name>` | Queue all tracks from a saved server playlist |
| `/playlist_list` | Show all saved server playlists |
| `/playlist_remove <name>` | Delete a saved server playlist |
| `/favorite_add [query]` | Bookmark current track or query to personal favorites |
| `/favorites` | View your saved personal favorite tracks |
| `/set_dj [role]` | Designate a DJ role for playback controls |
| `/set_request_channel [ch]` | Restrict music commands to a specific text channel |
| `/history` | Show recently played songs on the server |
| `/lyrics [query]` | Search lyrics for current song or query |
| `/ping` | Check Discord Gateway latency |
| `/status` | View system health, FFmpeg path, and active players |

---

## 🚀 Quick Setup on Linux KVM VPS

### 1. Prerequisites (Ubuntu / Debian)
```bash
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv ffmpeg libopus0 git curl
```

### 2. Clone and Run Automated Installer
```bash
git clone [https://github.com/GAMERADITYA764/musicbot] musicbot
cd musicbot

chmod +x install.sh start.sh
./install.sh
```

### 3. Configure Credentials
Copy `.env.example` to `.env` (if not already done) and open it:
```bash
nano .env
```
Add your credentials:
```env
DISCORD_TOKEN=your_bot_token_here
SPOTIFY_CLIENT_ID=your_spotify_id_optional
SPOTIFY_CLIENT_SECRET=your_spotify_secret_optional
```

### 4. Run Pre-flight Healthcheck
```bash
python3 healthcheck.py
```

### 5. Start the Bot
```bash
./start.sh
```

---

## ⚙️ Running 24/7 via Systemd

To run the bot as a background system service that starts automatically on boot:

1. Copy the service unit:
```bash
sudo cp musicbot.service /etc/systemd/system/
```
2. Edit `/etc/systemd/system/musicbot.service` to reflect your user and folder path.
3. Reload and enable:
```bash
sudo systemctl daemon-reload
sudo systemctl enable musicbot
sudo systemctl start musicbot
```
4. View live logs:
```bash
sudo journalctl -u musicbot -f
```

---

## 🧪 Testing
Run automated unit and integration tests:
```bash
python -m unittest discover tests
```
