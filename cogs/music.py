"""
Core music commands cog.
Implements /play, /pause, /resume, /skip, /previous, /stop, /leave,
/queue, /nowplaying, /volume, /seek, /loop, /shuffle, /clear, /remove, /move, /join.
"""

import random
import logging
from collections import deque
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.player import LoopMode
from core.player_manager import get_player
from core.extractor import extract_tracks, Track
from core.ui import create_embed, success_embed, error_embed, format_clock, PaginatorView
from core.config import MAX_QUEUE
from utils.checks import verify_voice_connection, verify_dj_or_admin, MusicCheckError

logger = logging.getLogger("musicbot.cogs.music")


class MusicCog(commands.Cog, name="Music"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="join", description="Connect the bot to your current voice channel")
    @app_commands.guild_only()
    async def join_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            if not interaction.user.voice or not interaction.user.voice.channel:
                raise MusicCheckError("You must join a voice channel first.")

            channel = interaction.user.voice.channel
            voice: Optional[discord.VoiceClient] = interaction.guild.voice_client

            if voice and voice.channel.id != channel.id:
                raise MusicCheckError(f"I am already active in {voice.channel.name}.")

            if not voice:
                await channel.connect(timeout=25.0, self_deaf=True)
            elif not voice.is_connected():
                await voice.disconnect(force=True)
                await channel.connect(timeout=25.0, self_deaf=True)

            await interaction.followup.send(embed=success_embed(f"Connected to **{channel.name}**!"))
        except MusicCheckError as mce:
            await interaction.followup.send(embed=error_embed(str(mce)))
        except Exception as e:
            logger.exception("Join command error")
            await interaction.followup.send(embed=error_embed(f"Connection failed: {str(e)[:150]}"))

    @app_commands.command(name="play", description="Play a song by name or link (YouTube, Spotify, SoundCloud)")
    @app_commands.describe(query="Song title, artist, or YouTube/Spotify/SoundCloud link")
    @app_commands.guild_only()
    async def play_command(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True)
        try:
            if not interaction.user.voice or not interaction.user.voice.channel:
                raise MusicCheckError("You must join a voice channel first.")

            channel = interaction.user.voice.channel
            voice: Optional[discord.VoiceClient] = interaction.guild.voice_client

            if voice and voice.channel.id != channel.id:
                raise MusicCheckError(f"I am already playing in {voice.channel.name}. Join there or use /leave.")

            player = get_player(self.bot, interaction.guild)
            await player.ensure_settings_loaded()

            # Extract tracks safely
            tracks = await extract_tracks(query, interaction.user.display_name)
            if not tracks:
                raise MusicCheckError("No playable audio tracks found for that search.")

            # Queue capacity check
            available_slots = MAX_QUEUE - len(player.queue) - (1 if player.current else 0)
            if available_slots <= 0:
                raise MusicCheckError(f"Queue is full (max {MAX_QUEUE} songs).")

            queued_tracks = tracks[:available_slots]

            # Connect to voice if not connected
            if not voice or not voice.is_connected():
                await channel.connect(timeout=25.0, self_deaf=True)

            player.channel = interaction.channel
            starts_immediately = (player.current is None and not player.queue)
            position = len(player.queue) + 1

            player.queue.extend(queued_tracks)

            # Response Embed
            first = queued_tracks[0]
            embed = create_embed(
                title="🎶 Added to Queue",
                description=f"**[{discord.utils.escape_markdown(first.title)}]({first.webpage_url or 'https://discord.com'})**"
            )
            if first.author:
                embed.add_field(name="Artist / Channel", value=discord.utils.escape_markdown(first.author)[:60], inline=True)
            
            queue_pos_text = "Playing now ▶️" if starts_immediately else f"#{position} in queue"
            embed.add_field(name="Queue Position", value=queue_pos_text, inline=True)

            if len(queued_tracks) > 1:
                embed.add_field(name="Batch Size", value=f"{len(queued_tracks)} tracks added", inline=True)

            if first.thumbnail and first.thumbnail.startswith("http"):
                embed.set_thumbnail(url=first.thumbnail)

            await interaction.followup.send(embed=embed)
            await player.update_panel(move_to_bottom=True)
            player.start()

        except MusicCheckError as mce:
            await interaction.followup.send(embed=error_embed(str(mce)), ephemeral=True)
        except Exception as e:
            logger.exception("Play command exception")
            await interaction.followup.send(embed=error_embed(f"Could not load track: {str(e)[:200]}"), ephemeral=True)

    @app_commands.command(name="pause", description="Pause the currently playing track")
    @app_commands.guild_only()
    async def pause_command(self, interaction: discord.Interaction):
        try:
            voice = await verify_voice_connection(interaction)
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)

            if not voice.is_playing():
                raise MusicCheckError("Playback is already paused or nothing is playing.")

            voice.pause()
            player.mark_pause()
            await interaction.response.send_message(embed=success_embed("Playback paused ⏸️"))
            await player.update_panel()
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="resume", description="Resume paused audio playback")
    @app_commands.guild_only()
    async def resume_command(self, interaction: discord.Interaction):
        try:
            voice = await verify_voice_connection(interaction)
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)

            if not voice.is_paused():
                raise MusicCheckError("Playback is not currently paused.")

            voice.resume()
            player.mark_resume()
            await interaction.response.send_message(embed=success_embed("Playback resumed ▶️"))
            await player.update_panel()
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="skip", description="Skip the current track")
    @app_commands.guild_only()
    async def skip_command(self, interaction: discord.Interaction):
        try:
            voice = await verify_voice_connection(interaction)
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)

            if not voice.is_playing() and not voice.is_paused():
                raise MusicCheckError("Nothing is currently playing to skip.")

            # Interrupt current track
            voice.stop()
            await interaction.response.send_message(embed=success_embed("Skipped to next track ⏭️"))
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="previous", description="Replay the previously played track")
    @app_commands.guild_only()
    async def previous_command(self, interaction: discord.Interaction):
        try:
            voice = await verify_voice_connection(interaction)
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)

            if len(player.history) < 2:
                raise MusicCheckError("No previous track found in history.")

            prev_track = player.history[1]
            player.queue.appendleft(prev_track)
            if voice.is_playing() or voice.is_paused():
                voice.stop()
            await interaction.response.send_message(embed=success_embed(f"Replaying previous: **{prev_track.title}**"))
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="stop", description="Stop music and clear the entire queue")
    @app_commands.guild_only()
    async def stop_command(self, interaction: discord.Interaction):
        try:
            voice = await verify_voice_connection(interaction)
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)

            player.generation += 1
            player.queue.clear()
            player.loop_mode = LoopMode.OFF

            if voice.is_playing() or voice.is_paused():
                voice.stop()

            await interaction.response.send_message(embed=success_embed("Stopped playback and cleared the queue ⏹️"))
            await player.update_panel()
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="leave", description="Disconnect the bot from voice channel")
    @app_commands.guild_only()
    async def leave_command(self, interaction: discord.Interaction):
        try:
            voice = await verify_voice_connection(interaction)
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)

            player.generation += 1
            player.queue.clear()
            if voice.is_playing() or voice.is_paused():
                voice.stop()

            await voice.disconnect()
            await interaction.response.send_message(embed=success_embed("Disconnected from voice channel 👋"))
            await player.update_panel()
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="nowplaying", description="Show detailed status of the currently playing song")
    @app_commands.guild_only()
    async def nowplaying_command(self, interaction: discord.Interaction):
        player = get_player(self.bot, interaction.guild)
        if not player.current:
            await interaction.response.send_message(embed=create_embed(description="⏹️ Nothing is currently playing."), ephemeral=True)
            return
        embed = player.build_panel_embed()
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="queue", description="Show upcoming songs in the queue with interactive pagination")
    @app_commands.guild_only()
    async def queue_command(self, interaction: discord.Interaction):
        player = get_player(self.bot, interaction.guild)
        if not player.queue:
            current_info = f"\n\n**Currently Playing:** {player.current.title}" if player.current else ""
            await interaction.response.send_message(embed=create_embed(title="📜 Server Queue", description=f"The queue is empty.{current_info}"))
            return

        # Build paginated pages of 10 items
        items_per_page = 10
        total_songs = len(player.queue)
        pages = []
        queue_list = list(player.queue)

        for i in range(0, total_songs, items_per_page):
            chunk = queue_list[i:i + items_per_page]
            lines = []
            for idx, track in enumerate(chunk, start=i + 1):
                lines.append(f"`#{idx}` **[{discord.utils.escape_markdown(track.title[:60])}]({track.webpage_url or 'https://discord.com'})** | `{format_clock(track.duration)}` (by {discord.utils.escape_markdown(track.requester)})")
            
            page_embed = create_embed(
                title=f"📜 Queue for {interaction.guild.name} ({total_songs} songs)",
                description="\n".join(lines)
            )
            if player.current:
                page_embed.set_footer(text=f"Now Playing: {player.current.title[:50]} • {format_clock(player.current.duration)}")
            pages.append(page_embed)

        if len(pages) == 1:
            await interaction.response.send_message(embed=pages[0])
        else:
            view = PaginatorView(pages, author_id=interaction.user.id)
            await interaction.response.send_message(embed=pages[0], view=view)

    @app_commands.command(name="volume", description="Adjust playback volume (0% to 200%)")
    @app_commands.describe(percent="Volume percentage between 0 and 200")
    @app_commands.guild_only()
    async def volume_command(self, interaction: discord.Interaction, percent: app_commands.Range[int, 0, 200]):
        try:
            voice = await verify_voice_connection(interaction)
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)

            player.volume = percent / 100.0
            if voice and isinstance(voice.source, discord.PCMVolumeTransformer):
                voice.source.volume = player.volume

            await interaction.response.send_message(embed=success_embed(f"Volume adjusted to **{percent}%** 🔊"))
            await player.update_panel()
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="seek", description="Seek to a specific timestamp in the current track")
    @app_commands.describe(timestamp="Target time in seconds or format mm:ss (e.g. 1:30 or 90)")
    @app_commands.guild_only()
    async def seek_command(self, interaction: discord.Interaction, timestamp: str):
        try:
            voice = await verify_voice_connection(interaction)
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)

            if not player.current:
                raise MusicCheckError("No track is currently playing.")

            # Parse timestamp
            seconds = 0
            if ":" in timestamp:
                parts = timestamp.split(":")
                if len(parts) == 2:
                    seconds = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            else:
                seconds = int(timestamp)

            if seconds < 0 or (player.current.duration and seconds > player.current.duration):
                raise MusicCheckError(f"Timestamp must be between 0 and {format_clock(player.current.duration)}.")

            player.seek_offset = seconds
            # Prepend current track and trigger immediate restart at offset
            player.queue.appendleft(player.current)
            if voice.is_playing() or voice.is_paused():
                voice.stop()

            await interaction.response.send_message(embed=success_embed(f"Seeked to `{format_clock(seconds)}` ⏩"))
        except ValueError:
            await interaction.response.send_message(embed=error_embed("Invalid timestamp format. Use seconds or mm:ss."), ephemeral=True)
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="loop", description="Set repeat mode: off, track, or queue")
    @app_commands.describe(mode="Repeat mode selection")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Disable Loop", value="off"),
        app_commands.Choice(name="Repeat Single Track", value="track"),
        app_commands.Choice(name="Repeat Entire Queue", value="queue")
    ])
    @app_commands.guild_only()
    async def loop_command(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        try:
            await verify_voice_connection(interaction)
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)

            player.loop_mode = mode.value
            names = {"off": "Disabled", "track": "Track Repeat (🔂)", "queue": "Queue Repeat (🔁)"}
            await interaction.response.send_message(embed=success_embed(f"Loop mode set to: **{names[mode.value]}**"))
            await player.update_panel()
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="shuffle", description="Randomize the order of tracks in the queue")
    @app_commands.guild_only()
    async def shuffle_command(self, interaction: discord.Interaction):
        try:
            await verify_voice_connection(interaction)
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)

            if len(player.queue) < 2:
                raise MusicCheckError("Need at least 2 tracks in queue to shuffle.")

            items = list(player.queue)
            random.shuffle(items)
            player.queue = deque(items)

            await interaction.response.send_message(embed=success_embed(f"Shuffled **{len(items)}** tracks in queue 🔀"))
            await player.update_panel()
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="clear", description="Remove all tracks from queue without stopping current song")
    @app_commands.guild_only()
    async def clear_command(self, interaction: discord.Interaction):
        try:
            await verify_voice_connection(interaction)
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)

            count = len(player.queue)
            player.queue.clear()
            await interaction.response.send_message(embed=success_embed(f"Cleared **{count}** songs from queue."))
            await player.update_panel()
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="remove", description="Remove a specific track from the queue by its position")
    @app_commands.describe(position="Track number in the queue (e.g. 1, 2, 3)")
    @app_commands.guild_only()
    async def remove_command(self, interaction: discord.Interaction, position: int):
        try:
            await verify_voice_connection(interaction)
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)

            if position < 1 or position > len(player.queue):
                raise MusicCheckError(f"Invalid position. Enter a number between 1 and {len(player.queue)}.")

            removed = player.queue[position - 1]
            del player.queue[position - 1]

            await interaction.response.send_message(embed=success_embed(f"Removed `#{position}`: **{removed.title}**"))
            await player.update_panel()
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="move", description="Move a track to a new position in the queue")
    @app_commands.describe(from_pos="Current queue position", to_pos="Desired queue position")
    @app_commands.guild_only()
    async def move_command(self, interaction: discord.Interaction, from_pos: int, to_pos: int):
        try:
            await verify_voice_connection(interaction)
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)

            q_len = len(player.queue)
            if from_pos < 1 or from_pos > q_len or to_pos < 1 or to_pos > q_len:
                raise MusicCheckError(f"Positions must be between 1 and {q_len}.")

            track = player.queue[from_pos - 1]
            del player.queue[from_pos - 1]
            player.queue.insert(to_pos - 1, track)

            await interaction.response.send_message(embed=success_embed(f"Moved **{track.title}** from `#{from_pos}` to `#{to_pos}`."))
            await player.update_panel()
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
