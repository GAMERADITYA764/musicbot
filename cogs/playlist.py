"""
Custom synced playlists and personal user favorites cog.
Allows servers to sync YouTube / Spotify playlists continuously,
and users to save their favorite tracks to replay anywhere.
"""

import re
import time
import logging
from typing import List, Dict, Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.database import db
from core.extractor import extract_youtube_playlist, spotify_resolve, Track
from core.player_manager import get_player
from core.config import SYNC_MINUTES, MAX_QUEUE
from core.ui import create_embed, success_embed, error_embed, format_clock, PaginatorView
from utils.checks import verify_voice_connection, verify_dj_or_admin, MusicCheckError

logger = logging.getLogger("musicbot.cogs.playlist")


class PlaylistCog(commands.Cog, name="Playlists"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self):
        self.sync_loop.cancel()

    async def _fetch_playlist_tracks(self, url: str) -> List[Dict[str, Any]]:
        """Parses Spotify or YouTube playlist into serialized dictionary format."""
        url_lower = url.lower()
        if "spotify.com" in url_lower:
            tracks = await spotify_resolve(url, requester="Synced Playlist")
        elif "youtube.com" in url_lower or "youtu.be" in url_lower:
            tracks = await extract_youtube_playlist(url, requester="Synced Playlist")
        else:
            raise ValueError("Only YouTube and Spotify playlists are supported.")

        return [t.to_dict() for t in tracks]

    @tasks.loop(minutes=SYNC_MINUTES)
    async def sync_loop(self):
        """Periodically refreshes all saved guild playlists from YouTube/Spotify."""
        logger.info("Starting background playlist synchronization...")
        try:
            playlists = await db.get_all_syncable_playlists()
            for entry in playlists:
                guild_id = entry["guild_id"]
                name = entry["name"]
                source_url = entry["source_url"]

                try:
                    updated_tracks = await self._fetch_playlist_tracks(source_url)
                    await db.save_playlist(guild_id, name, source_url, updated_tracks, error=None)
                except Exception as err:
                    logger.warning("Failed syncing playlist '%s' for guild %s: %s", name, guild_id, err)
                    await db.save_playlist(guild_id, name, source_url, entry["tracks"], error=str(err)[:150])
            logger.info("Playlist synchronization complete for %d playlists.", len(playlists))
        except Exception as e:
            logger.exception("Error in playlist sync loop: %s", e)

    @sync_loop.before_loop
    async def before_sync(self):
        await self.bot.wait_until_ready()

    # -------------------------------------------------------------
    # Server Playlists
    # -------------------------------------------------------------
    @app_commands.command(name="playlist_add", description="Save a public YouTube or Spotify playlist for auto-syncing")
    @app_commands.describe(name="Unique identifier name for this playlist", url="YouTube or Spotify playlist URL")
    @app_commands.guild_only()
    async def playlist_add(self, interaction: discord.Interaction, name: str, url: str):
        await interaction.response.defer(ephemeral=True)
        try:
            await verify_dj_or_admin(interaction)
            name_clean = name.strip().lower()
            if not re.fullmatch(r"[a-z0-9_-]{1,32}", name_clean):
                raise MusicCheckError("Playlist name must be 1-32 alphanumeric characters, dashes, or underscores.")

            existing = await db.get_all_guild_playlists(interaction.guild.id)
            if name_clean not in existing and len(existing) >= 20:
                raise MusicCheckError("Server has reached the limit of 20 saved playlists.")

            tracks = await self._fetch_playlist_tracks(url)
            if not tracks:
                raise MusicCheckError("No tracks could be retrieved from that playlist link.")

            await db.save_playlist(interaction.guild.id, name_clean, url, tracks)
            await interaction.followup.send(
                embed=success_embed(f"Saved playlist **{name_clean}** with **{len(tracks)}** tracks! Auto-syncs every {SYNC_MINUTES} mins.")
            )
        except MusicCheckError as mce:
            await interaction.followup.send(embed=error_embed(str(mce)))
        except Exception as e:
            logger.exception("Playlist add failure")
            await interaction.followup.send(embed=error_embed(f"Could not save playlist: {str(e)[:180]}"))

    @app_commands.command(name="playlist_play", description="Queue all tracks from a saved server playlist")
    @app_commands.describe(name="Name of the saved playlist")
    @app_commands.guild_only()
    async def playlist_play(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(thinking=True)
        try:
            if not interaction.user.voice or not interaction.user.voice.channel:
                raise MusicCheckError("You must join a voice channel first.")

            channel = interaction.user.voice.channel
            voice: Optional[discord.VoiceClient] = interaction.guild.voice_client

            if voice and voice.channel.id != channel.id:
                raise MusicCheckError("I am already in another voice channel.")

            playlist_data = await db.get_playlist(interaction.guild.id, name)
            if not playlist_data or not playlist_data["tracks"]:
                raise MusicCheckError(f"Playlist **{name}** was not found or is empty. Use `/playlist_list`.")

            player = get_player(self.bot, interaction.guild)
            available = MAX_QUEUE - len(player.queue) - (1 if player.current else 0)
            if available <= 0:
                raise MusicCheckError(f"Queue is full (max {MAX_QUEUE} songs).")

            raw_tracks = playlist_data["tracks"][:available]
            tracks_to_queue = [
                Track.from_dict(t, default_requester=interaction.user.display_name)
                for t in raw_tracks
            ]

            if not voice or not voice.is_connected():
                await channel.connect(timeout=25.0, self_deaf=True)

            player.channel = interaction.channel
            player.queue.extend(tracks_to_queue)

            await interaction.followup.send(embed=success_embed(f"Queued **{len(tracks_to_queue)}** songs from playlist **{name}**!"))
            await player.update_panel(move_to_bottom=True)
            player.start()
        except MusicCheckError as mce:
            await interaction.followup.send(embed=error_embed(str(mce)), ephemeral=True)
        except Exception as e:
            logger.exception("Playlist play failed")
            await interaction.followup.send(embed=error_embed(f"Failed to load playlist: {str(e)[:150]}"), ephemeral=True)

    @app_commands.command(name="playlist_list", description="Show all saved playlists on this server")
    @app_commands.guild_only()
    async def playlist_list(self, interaction: discord.Interaction):
        playlists = await db.get_all_guild_playlists(interaction.guild.id)
        if not playlists:
            await interaction.response.send_message(embed=create_embed(description="No playlists saved on this server. Use `/playlist_add` to create one."), ephemeral=True)
            return

        lines = []
        for name, data in playlists.items():
            count = len(data.get("tracks", []))
            synced_at = data.get("synced_at", 0)
            time_str = f"<t:{synced_at}:R>" if synced_at else "Never"
            err_indicator = " ⚠️ (Last sync error)" if data.get("error") else ""
            lines.append(f"• **{name}** — `{count}` tracks (Synced: {time_str}){err_indicator}")

        embed = create_embed(
            title=f"📁 Saved Playlists ({len(playlists)})",
            description="\n".join(lines)
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="playlist_remove", description="Delete a saved server playlist")
    @app_commands.describe(name="Name of the playlist to delete")
    @app_commands.guild_only()
    async def playlist_remove(self, interaction: discord.Interaction, name: str):
        try:
            await verify_dj_or_admin(interaction)
            deleted = await db.delete_playlist(interaction.guild.id, name)
            if deleted:
                await interaction.response.send_message(embed=success_embed(f"Deleted playlist **{name}**."))
            else:
                await interaction.response.send_message(embed=error_embed(f"Playlist **{name}** does not exist."), ephemeral=True)
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    # -------------------------------------------------------------
    # User Personal Favorites
    # -------------------------------------------------------------
    @app_commands.command(name="favorite_add", description="Save current playing song or search query to personal favorites")
    @app_commands.describe(query="Optional song name or URL (defaults to currently playing track)")
    async def favorite_add(self, interaction: discord.Interaction, query: Optional[str] = None):
        user_id = interaction.user.id
        player = get_player(self.bot, interaction.guild) if interaction.guild else None

        if not query:
            if not player or not player.current:
                await interaction.response.send_message(embed=error_embed("Nothing is currently playing. Provide a song name or link."), ephemeral=True)
                return
            t = player.current
            title, q, url, author, duration, thumb = t.title, t.query, t.url, t.author, t.duration, t.thumbnail
        else:
            title = query
            q = query
            url = query if query.startswith("http") else ""
            author, duration, thumb = "", 0, ""

        success = await db.add_favorite(user_id, title, q, url, author, duration, thumb)
        if success:
            await interaction.response.send_message(embed=success_embed(f"Added **{title}** to your personal favorites! ⭐"), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed("Failed to add to favorites."), ephemeral=True)

    @app_commands.command(name="favorites", description="View your saved favorite tracks")
    async def favorites_list(self, interaction: discord.Interaction):
        favs = await db.get_user_favorites(interaction.user.id)
        if not favs:
            await interaction.response.send_message(embed=create_embed(description="You have no saved favorites. Use `/favorite_add` to bookmark songs! ⭐"), ephemeral=True)
            return

        lines = [f"`#{i}` **{discord.utils.escape_markdown(f['title'][:60])}** ({format_clock(f['duration'])})" for i, f in enumerate(favs, 1)]
        embed = create_embed(
            title=f"⭐ {interaction.user.display_name}'s Favorites",
            description="\n".join(lines)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="favorite_remove", description="Remove a track from your favorites")
    @app_commands.describe(query_or_title="Track title or query to remove")
    async def favorite_remove(self, interaction: discord.Interaction, query_or_title: str):
        removed = await db.remove_favorite(interaction.user.id, query_or_title)
        if removed:
            await interaction.response.send_message(embed=success_embed(f"Removed from favorites: **{query_or_title}**"), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"Could not find **{query_or_title}** in your favorites."), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PlaylistCog(bot))
