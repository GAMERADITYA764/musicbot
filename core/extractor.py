"""
Audio extractor module using yt-dlp and Spotify Web API (client credentials).
Safe against blocking synchronous calls by delegating extraction to asyncio threadpool.
Resolves Spotify tracks/playlists/albums into metadata search queries.
"""

import os
import re
import random
import logging
import asyncio
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
import aiohttp
import yt_dlp

from core.config import (
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    MAX_QUEUE,
    YOUTUBE_COOKIES_PATH,
    ALLOWED_HOSTS,
)

logger = logging.getLogger("musicbot.extractor")

BASE_YDL_OPTIONS: Dict[str, Any] = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "default_search": "ytsearch",
    "socket_timeout": 15,
    "extractor_retries": 3,
    "restrictfilenames": True,
    "nocheckcertificate": False,
    "ignoreerrors": False,
    "logtostderr": False,
    # Optimize network buffers for streaming
    "buffersize": 1024 * 16,
    "http_chunk_size": 1048576,
}

if YOUTUBE_COOKIES_PATH and os.path.exists(YOUTUBE_COOKIES_PATH):
    BASE_YDL_OPTIONS["cookiefile"] = YOUTUBE_COOKIES_PATH


class Track:
    """Represents a playable or queued audio track."""
    def __init__(
        self,
        title: str,
        query: str,
        requester: str,
        url: str = "",
        duration: int = 0,
        author: str = "",
        thumbnail: str = "",
        webpage_url: str = ""
    ):
        self.title: str = title
        self.query: str = query
        self.requester: str = requester
        self.url: str = url  # Direct stream URL (resolved fresh right before playback)
        self.duration: int = duration
        self.author: str = author
        self.thumbnail: str = thumbnail
        self.webpage_url: str = webpage_url or (query if query.startswith("http") else "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "query": self.query,
            "requester": self.requester,
            "url": self.url,
            "duration": self.duration,
            "author": self.author,
            "thumbnail": self.thumbnail,
            "webpage_url": self.webpage_url,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], default_requester: str = "System") -> "Track":
        return cls(
            title=data.get("title", "Unknown"),
            query=data.get("query", ""),
            requester=data.get("requester", default_requester),
            url=data.get("url", ""),
            duration=int(data.get("duration", 0)),
            author=data.get("author", ""),
            thumbnail=data.get("thumbnail", ""),
            webpage_url=data.get("webpage_url", data.get("url", "")),
        )


def validate_query_url(query: str):
    """Ensures external URLs are only from trusted audio platforms."""
    query = query.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", query):
        parsed = urlparse(query)
        if parsed.scheme not in ("https", "http"):
            raise ValueError("Only standard http/https audio URLs are supported.")
        host = (parsed.hostname or "").lower()
        if not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
            raise ValueError(f"Host '{host}' is not allowed. Only YouTube, SoundCloud, and Spotify links are supported.")


async def extract_info(query: str, download: bool = False, extra_opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Runs yt-dlp extract_info asynchronously without blocking the event loop."""
    opts = dict(BASE_YDL_OPTIONS)
    if extra_opts:
        opts.update(extra_opts)

    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(query, download=download)
            if data and "entries" in data:
                data = next((x for x in data["entries"] if x), None)
            return data

    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _extract)
    if not data:
        raise ValueError("Could not extract any audio information from source.")
    return data


async def resolve_stream_url(track: Track) -> str:
    """
    Resolves or refreshes the direct streamable audio URL right before feeding into FFmpeg.
    Crucial for long queues where raw media URLs expire after 4-6 hours.
    """
    query = track.webpage_url or track.query
    info = await extract_info(query)
    stream_url = info.get("url")
    if not stream_url:
        raise ValueError("Failed to retrieve direct stream URL for track.")
    
    # Update track metadata if missing
    track.url = stream_url
    if not track.duration:
        track.duration = int(info.get("duration") or 0)
    if not track.author:
        track.author = info.get("artist") or info.get("uploader") or info.get("channel") or ""
    if not track.thumbnail:
        track.thumbnail = info.get("thumbnail") or ""
    if not track.webpage_url:
        track.webpage_url = info.get("webpage_url") or query

    return stream_url


async def extract_tracks(query: str, requester: str) -> List[Track]:
    """
    Parses user input: either a Spotify link, YouTube playlist/track, SoundCloud link, or search string.
    Returns a list of Track objects.
    """
    query = query.strip()
    validate_query_url(query)

    parsed = urlparse(query)
    host = (parsed.hostname or "").lower()

    # Spotify Resolution
    if host == "spotify.com" or host.endswith(".spotify.com"):
        return await spotify_resolve(query, requester)

    # Check if it's a YouTube playlist URL
    if ("youtube.com" in host or "youtu.be" in host) and ("list=" in query or "/playlist" in parsed.path):
        return await extract_youtube_playlist(query, requester)

    # Single track or YouTube Search
    # If not a link, prefix with ytsearch1:
    search_query = query if query.startswith("http") else f"ytsearch1:{query}"
    info = await extract_info(search_query)

    title = info.get("title", query)
    author = info.get("artist") or info.get("uploader") or info.get("channel") or ""
    duration = int(info.get("duration") or 0)
    thumbnail = info.get("thumbnail") or ""
    webpage = info.get("webpage_url") or (query if query.startswith("http") else "")
    stream_url = info.get("url", "")

    return [Track(
        title=title,
        query=search_query,
        requester=requester,
        url=stream_url,
        duration=duration,
        author=author,
        thumbnail=thumbnail,
        webpage_url=webpage
    )]


async def extract_youtube_playlist(url: str, requester: str, max_items: int = MAX_QUEUE) -> List[Track]:
    """Extracts track items from a public YouTube playlist via flat extraction."""
    opts = {
        **BASE_YDL_OPTIONS,
        "noplaylist": False,
        "extract_flat": True,
        "playlistend": max_items,
        "skip_download": True,
    }

    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _extract)

    if not data or data.get("_type") not in ("playlist", "multi_video"):
        raise ValueError("Provided URL is not a valid or accessible YouTube playlist.")

    tracks: List[Track] = []
    for item in data.get("entries", []):
        if not item:
            continue
        vid = item.get("id")
        if not vid or not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            continue
        v_url = f"https://www.youtube.com/watch?v={vid}"
        title = item.get("title") or f"YouTube Video ({vid})"
        author = item.get("artist") or item.get("uploader") or item.get("channel") or ""
        duration = int(item.get("duration") or 0)
        thumb = item.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

        tracks.append(Track(
            title=title,
            query=v_url,
            requester=requester,
            url="",  # Stream URL resolved dynamically before playback
            duration=duration,
            author=author,
            thumbnail=thumb,
            webpage_url=v_url
        ))
    return tracks


async def spotify_resolve(url: str, requester: str) -> List[Track]:
    """
    Uses Spotify Web API Client Credentials to resolve tracks, albums, or playlists into
    metadata (artist, title).
    Protected audio is NOT downloaded from Spotify; each track is resolved to search queries.
    """
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise ValueError("Spotify metadata resolution requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env.")

    match = re.search(r"/(track|album|playlist)/([A-Za-z0-9]+)", urlparse(url).path)
    if not match:
        raise ValueError("Invalid Spotify URL. Please provide a track, album, or playlist link.")

    kind, item_id = match.groups()
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Obtain Spotify OAuth Bearer Token
        auth = aiohttp.BasicAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
        async with session.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            auth=auth
        ) as resp:
            if resp.status != 200:
                raise ValueError(f"Spotify authentication failed (HTTP {resp.status}). Check SPOTIFY credentials.")
            token = (await resp.json()).get("access_token")

        headers = {"Authorization": f"Bearer {token}"}
        endpoint = f"https://api.spotify.com/v1/{kind}s/{item_id}"

        async with session.get(endpoint, headers=headers) as resp:
            if resp.status != 200:
                raise ValueError(f"Spotify could not access this {kind} (HTTP {resp.status}). It may be private or invalid.")
            data = await resp.json()

        if kind == "track":
            items = [data]
        else:
            page = data.get("tracks", {})
            items = page.get("items", [])
            while page.get("next") and len(items) < MAX_QUEUE:
                next_url = page["next"]
                if urlparse(next_url).hostname != "api.spotify.com":
                    break
                async with session.get(next_url, headers=headers) as n_resp:
                    if n_resp.status != 200:
                        break
                    page = await n_resp.json()
                items.extend(page.get("items", []))

    result: List[Track] = []
    for item in items[:MAX_QUEUE]:
        track_data = item.get("track", item) if item else None
        if not track_data or not track_data.get("name") or not track_data.get("artists"):
            continue
        artist_names = ", ".join(a["name"] for a in track_data["artists"])
        title = track_data["name"]
        search_query = f"{title} - {artist_names}"
        images = (track_data.get("album") or {}).get("images") or (data.get("images", []) if kind == "album" else [])
        thumbnail = images[0].get("url", "") if images else ""
        duration_ms = track_data.get("duration_ms", 0)

        result.append(Track(
            title=title,
            query=f"ytsearch1:{search_query}",
            requester=requester,
            author=artist_names,
            duration=int(duration_ms / 1000) if duration_ms else 0,
            thumbnail=thumbnail,
            webpage_url=track_data.get("external_urls", {}).get("spotify", "")
        ))

    if not result:
        raise ValueError("No tracks could be found in the specified Spotify link.")

    return result


async def get_autoplay_recommendation(track: Track, history: List[Track]) -> Optional[Track]:
    """Finds related music tracks using yt-dlp search without repeating history."""
    query = f"{track.author} {track.title} music"[:200]
    opts = {
        **BASE_YDL_OPTIONS,
        "extract_flat": True,
        "noplaylist": False,
    }

    def _search():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(f"ytsearch15:{query}", download=False)

    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _search)
    if not data:
        return None

    recent_queries = {t.query for t in history if t.query}
    recent_titles = {t.title.casefold().strip() for t in history if t.title}

    candidates = []
    for item in data.get("entries") or []:
        if not item:
            continue
        vid = item.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            continue
        v_url = f"https://www.youtube.com/watch?v={vid}"
        title = item.get("title") or ""
        if not title or v_url in recent_queries or title.casefold().strip() in recent_titles:
            continue
        candidates.append(Track(
            title=title,
            query=v_url,
            requester="♾️ Autoplay",
            url="",
            duration=int(item.get("duration") or 0),
            author=item.get("artist") or item.get("uploader") or item.get("channel") or "",
            thumbnail=item.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            webpage_url=v_url
        ))

    return random.choice(candidates[:8]) if candidates else None
