"""
Multi-server guild audio player.
Manages per-guild playback loop, queue, loop modes (off/track/queue),
filters, seek offsets, 24/7 mode, and interactive persistent control panel.
"""

import time
import random
import logging
import asyncio
from collections import deque
from typing import Optional, List, Dict, Any

import discord
from core.config import AUDIO_FILTERS, MAX_QUEUE
from core.ffmpeg_handler import find_ffmpeg
from core.extractor import Track, resolve_stream_url, get_autoplay_recommendation
from core.database import db
from core.ui import create_embed, format_clock, progress_bar, error_embed

logger = logging.getLogger("musicbot.player")


class LoopMode:
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


class GuildPlayer:
    """Per-guild playback state and queue controller."""
    def __init__(self, bot: discord.Client, guild: discord.Guild):
        self.bot = bot
        self.guild = guild
        self.queue: deque[Track] = deque()
        self.history: deque[Track] = deque(maxlen=30)
        
        self.current: Optional[Track] = None
        self.volume: float = 0.5
        self.loop_mode: str = LoopMode.OFF
        self.autoplay: bool = False
        self.mode_24_7: bool = False
        self.filter_name: str = "none"
        self.seek_offset: int = 0  # In seconds, applied on restart/seek

        # Timing tracking
        self.started_at: float = 0.0
        self.paused_at: float = 0.0
        self.paused_total: float = 0.0

        # Concurrency & lifecycle
        self.task: Optional[asyncio.Task] = None
        self.finished = asyncio.Event()
        self.generation: int = 0
        self.channel: Optional[discord.TextChannel] = None
        
        # Panel management
        self.panel_message: Optional[discord.Message] = None
        self.panel_lock = asyncio.Lock()
        self._loaded_settings = False

    async def ensure_settings_loaded(self):
        """Loads persistent guild settings from SQLite if not yet cached."""
        if not self._loaded_settings:
            settings = await db.get_guild_settings(self.guild.id)
            self.volume = settings.get("default_volume", 0.5)
            self.mode_24_7 = settings.get("mode_24_7", False)
            self.autoplay = settings.get("autoplay", False)
            self._loaded_settings = True

    def elapsed(self) -> int:
        """Returns elapsed playback duration in seconds for current track."""
        if not self.started_at or not self.current:
            return 0
        end = self.paused_at or time.monotonic()
        raw = end - self.started_at - self.paused_total
        return max(0, int(raw + self.seek_offset))

    def mark_pause(self):
        if not self.paused_at:
            self.paused_at = time.monotonic()

    def mark_resume(self):
        if self.paused_at:
            self.paused_total += time.monotonic() - self.paused_at
            self.paused_at = 0.0

    def start(self):
        """Spawns the worker loop task if not currently running."""
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.worker())

    async def worker(self):
        """Sequential queue processing worker for the guild."""
        await self.ensure_settings_loaded()
        try:
            while self.queue or (self.current and self.loop_mode == LoopMode.TRACK):
                generation = self.generation

                # If repeating single track and current exists, replay it
                if self.loop_mode == LoopMode.TRACK and self.current:
                    pass
                else:
                    self.current = self.queue.popleft()
                    self.history.appendleft(self.current)
                    # Record into SQLite history
                    await db.record_history(
                        guild_id=self.guild.id,
                        title=self.current.title,
                        query=self.current.query,
                        author=self.current.author,
                        requester=self.current.requester,
                        duration=self.current.duration
                    )

                try:
                    # 1. Ensure Voice Client is active
                    voice: Optional[discord.VoiceClient] = self.guild.voice_client
                    if not voice or not voice.is_connected():
                        logger.warning("Voice client disconnected for guild %s; worker pausing.", self.guild.id)
                        break

                    # 2. Freshly resolve media stream URL to prevent expired tokens
                    stream_url = await resolve_stream_url(self.current)
                    if generation != self.generation:
                        # Queue was skipped/stopped while resolving
                        self.current = None
                        continue

                    # 3. Locate FFmpeg binary
                    executable = find_ffmpeg()
                    if not executable:
                        raise RuntimeError("FFmpeg executable not found. Ensure FFmpeg is installed or FFMPEG_PATH is configured.")

                    # 4. Construct FFmpeg audio arguments
                    audio_filter = AUDIO_FILTERS.get(self.filter_name, "")
                    before_opts = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin"
                    if self.seek_offset > 0:
                        before_opts += f" -ss {self.seek_offset}"

                    ffmpeg_opts = "-vn"
                    if audio_filter:
                        ffmpeg_opts += f' -af "{audio_filter}"'

                    source = discord.FFmpegPCMAudio(
                        stream_url,
                        executable=executable,
                        before_options=before_opts,
                        options=ffmpeg_opts
                    )
                    source = discord.PCMVolumeTransformer(source, volume=self.volume)

                    # 5. Play audio via discord voice client
                    self.started_at = time.monotonic()
                    self.paused_at = 0.0
                    self.paused_total = 0.0
                    self.finished.clear()

                    loop = asyncio.get_running_loop()
                    def _after_playback(error):
                        if error:
                            logger.error("Discord voice playback error in guild %s: %s", self.guild.id, error)
                        loop.call_soon_threadsafe(self.finished.set)

                    if voice.is_playing() or voice.is_paused():
                        voice.stop()

                    voice.play(source, after=_after_playback)
                    logger.info("Started playback in guild %s: '%s'", self.guild.name, self.current.title)

                    # Update persistent panel
                    await self.update_panel()

                    # Wait for track completion or interrupt
                    await self.finished.wait()

                    # Reset seek offset
                    self.seek_offset = 0

                    # 6. Queue Loop Handling
                    if self.loop_mode == LoopMode.QUEUE and self.current:
                        self.queue.append(self.current)

                    # 7. Autoplay handling when queue becomes empty
                    if not self.queue and self.autoplay and self.loop_mode == LoopMode.OFF and generation == self.generation:
                        try:
                            recommendation = await get_autoplay_recommendation(self.current, list(self.history))
                            if recommendation and not self.queue and self.autoplay and generation == self.generation:
                                self.queue.append(recommendation)
                                if self.channel:
                                    await self.channel.send(
                                        embed=create_embed(description=f"♾️ **Autoplay added:** {recommendation.title}"),
                                        delete_after=10
                                    )
                        except Exception as auto_err:
                            logger.warning("Autoplay lookup failed: %s", auto_err)

                except Exception as play_err:
                    logger.exception("Error during playback of '%s': %s", self.current.title if self.current else "None", play_err)
                    if self.channel:
                        await self.channel.send(
                            embed=error_embed(f"Could not play **{self.current.title if self.current else 'track'}**: {str(play_err)[:150]}"),
                            delete_after=15
                        )
                    # Delay slightly before trying next song to prevent infinite error spin
                    await asyncio.sleep(1.5)

                self.current = None

        finally:
            self.current = None
            self.task = None
            await self.update_panel()
            
            # Auto-disconnect if 24/7 mode is disabled and queue is empty
            if not self.mode_24_7 and not self.queue:
                voice: Optional[discord.VoiceClient] = self.guild.voice_client
                if voice and voice.is_connected():
                    logger.info("Queue empty in guild %s (24/7 disabled); disconnecting.", self.guild.name)
                    if self.channel:
                        await self.channel.send(
                            embed=create_embed(description="⏹️ Queue finished. Left the voice channel. Enable `/247` to stay connected!"),
                            delete_after=15
                        )
                    await voice.disconnect()

    def build_panel_embed(self) -> discord.Embed:
        """Constructs the rich Now Playing / Music Panel embed."""
        title = self.current.title if self.current else "Nothing Playing"
        voice = self.guild.voice_client
        
        if voice and voice.is_paused():
            status = "⏸️ Paused"
        elif voice and voice.is_playing():
            status = "▶️ Playing"
        else:
            status = "⏹️ Idle"

        duration = self.current.duration if self.current else 0
        elapsed = self.elapsed() if self.current else 0
        bar = progress_bar(elapsed, duration)
        timeline = f"`{format_clock(elapsed)}` {bar} `{format_clock(duration) if duration else 'LIVE'}`"

        embed = create_embed(
            title="🎵 VECTOR MUSIC PANEL",
            description=f"**[{discord.utils.escape_markdown(title)}]({self.current.webpage_url if self.current and self.current.webpage_url else 'https://discord.com'})**\n{status} | {timeline}"
        )

        if self.current and self.current.thumbnail and self.current.thumbnail.startswith("http"):
            embed.set_thumbnail(url=self.current.thumbnail)

        if self.current:
            embed.add_field(name="Requested By", value=discord.utils.escape_markdown(self.current.requester)[:60], inline=True)
            if self.current.author:
                embed.add_field(name="Artist / Channel", value=discord.utils.escape_markdown(self.current.author)[:60], inline=True)

        loop_status = "🔂 Track" if self.loop_mode == LoopMode.TRACK else "🔁 Queue" if self.loop_mode == LoopMode.QUEUE else "Off"
        embed.add_field(name="🔁 Loop", value=loop_status, inline=True)
        embed.add_field(name="♾️ Autoplay", value="On ✅" if self.autoplay else "Off ⭕", inline=True)
        embed.add_field(name="🎛️ Filter", value=self.filter_name.title(), inline=True)
        embed.add_field(name="🔊 Volume", value=f"{round(self.volume * 100)}%", inline=True)
        embed.add_field(name="24/7 Mode", value="Enabled 🟢" if self.mode_24_7 else "Disabled ⚪", inline=True)
        embed.add_field(name="📜 Queue Size", value=f"{len(self.queue)} songs", inline=True)

        if self.queue:
            next_track = self.queue[0]
            embed.add_field(name="🎶 Up Next", value=discord.utils.escape_markdown(next_track.title)[:150], inline=False)

        return embed

    async def update_panel(self, move_to_bottom: bool = False):
        """Updates the active Music Panel message without spamming duplicate messages."""
        if not self.channel:
            return

        async with self.panel_lock:
            embed = self.build_panel_embed()
            view = PersistentMusicPanelView(self.bot)

            try:
                if move_to_bottom and self.panel_message:
                    try:
                        await self.panel_message.delete()
                    except discord.HTTPException:
                        pass
                    self.panel_message = None

                if self.panel_message:
                    await self.panel_message.edit(embed=embed, view=view)
                else:
                    self.panel_message = await self.channel.send(embed=embed, view=view)
            except discord.NotFound:
                # Message was deleted by a user or moderator; send a fresh one
                try:
                    self.panel_message = await self.channel.send(embed=embed, view=view)
                except discord.HTTPException:
                    self.panel_message = None
            except discord.Forbidden:
                logger.warning("Missing permissions to send or edit panel in %s", self.channel.name)
            except Exception as e:
                logger.debug("Failed updating panel message: %s", e)


class PersistentMusicPanelView(discord.ui.View):
    """
    Persistent Discord UI Button View for the Music Panel.
    Fully compatible with bot restarts (custom_id prefixed with 'music:').
    """
    def __init__(self, bot: discord.Client):
        super().__init__(timeout=None)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Enforces that the user is in the same voice channel as the bot."""
        voice = interaction.guild.voice_client if interaction.guild else None
        user_channel = interaction.user.voice.channel if interaction.user.voice else None

        if not voice:
            await interaction.response.send_message(embed=error_embed("I am not connected to any voice channel."), ephemeral=True)
            return False
        if not user_channel or user_channel.id != voice.channel.id:
            await interaction.response.send_message(embed=error_embed("Join my voice channel to use music controls."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Pause / Resume", emoji="⏯️", style=discord.ButtonStyle.primary, custom_id="music:toggle", row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice = interaction.guild.voice_client
        from core.player_manager import get_player
        player = get_player(self.bot, interaction.guild)

        if voice.is_paused():
            voice.resume()
            player.mark_resume()
            await interaction.response.send_message(embed=create_embed(description="▶️ Playback resumed."), ephemeral=True)
        elif voice.is_playing():
            voice.pause()
            player.mark_pause()
            await interaction.response.send_message(embed=create_embed(description="⏸️ Playback paused."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=create_embed(description="⏹️ Nothing is currently playing."), ephemeral=True)
        await player.update_panel()

    @discord.ui.button(label="Previous", emoji="⏮️", style=discord.ButtonStyle.secondary, custom_id="music:prev", row=0)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        from core.player_manager import get_player
        player = get_player(self.bot, interaction.guild)
        if len(player.history) < 2:
            await interaction.response.send_message(embed=error_embed("No previous track in history."), ephemeral=True)
            return

        # Pop current from history and prepend previous track
        prev_track = player.history[1]
        player.queue.appendleft(prev_track)
        voice = interaction.guild.voice_client
        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()
        await interaction.response.send_message(embed=create_embed(description=f"⏮️ Playing previous track: **{prev_track.title}**"), ephemeral=True)

    @discord.ui.button(label="Skip", emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="music:skip", row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice = interaction.guild.voice_client
        from core.player_manager import get_player
        player = get_player(self.bot, interaction.guild)
        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()
            await interaction.response.send_message(embed=create_embed(description="⏭️ Skipped current track."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed("Nothing is playing to skip."), ephemeral=True)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="music:stop", row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        from core.player_manager import get_player
        player = get_player(self.bot, interaction.guild)
        player.generation += 1
        player.queue.clear()
        voice = interaction.guild.voice_client
        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()
        await interaction.response.send_message(embed=create_embed(description="⏹️ Stopped playback and cleared queue."), ephemeral=True)
        await player.update_panel()

    @discord.ui.button(label="Loop", emoji="🔁", style=discord.ButtonStyle.secondary, custom_id="music:loop", row=1)
    async def loop_toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        from core.player_manager import get_player
        player = get_player(self.bot, interaction.guild)
        if player.loop_mode == LoopMode.OFF:
            player.loop_mode = LoopMode.TRACK
            msg = "🔂 Repeating current track."
        elif player.loop_mode == LoopMode.TRACK:
            player.loop_mode = LoopMode.QUEUE
            msg = "🔁 Repeating entire queue."
        else:
            player.loop_mode = LoopMode.OFF
            msg = "➡️ Repeat disabled."
        await interaction.response.send_message(embed=create_embed(description=msg), ephemeral=True)
        await player.update_panel()

    @discord.ui.button(label="Shuffle", emoji="🔀", style=discord.ButtonStyle.secondary, custom_id="music:shuffle", row=1)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        from core.player_manager import get_player
        player = get_player(self.bot, interaction.guild)
        if len(player.queue) < 2:
            await interaction.response.send_message(embed=error_embed("Need at least 2 tracks in queue to shuffle."), ephemeral=True)
            return
        items = list(player.queue)
        random.shuffle(items)
        player.queue = deque(items)
        await interaction.response.send_message(embed=create_embed(description="🔀 Queue shuffled."), ephemeral=True)
        await player.update_panel()

    @discord.ui.button(label="Vol -", emoji="🔉", style=discord.ButtonStyle.secondary, custom_id="music:voldown", row=1)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        from core.player_manager import get_player
        player = get_player(self.bot, interaction.guild)
        player.volume = max(0.0, round(player.volume - 0.1, 2))
        voice = interaction.guild.voice_client
        if voice and isinstance(voice.source, discord.PCMVolumeTransformer):
            voice.source.volume = player.volume
        await interaction.response.send_message(embed=create_embed(description=f"🔉 Volume set to {round(player.volume * 100)}%"), ephemeral=True)
        await player.update_panel()

    @discord.ui.button(label="Vol +", emoji="🔊", style=discord.ButtonStyle.secondary, custom_id="music:volup", row=1)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        from core.player_manager import get_player
        player = get_player(self.bot, interaction.guild)
        player.volume = min(2.0, round(player.volume + 0.1, 2))
        voice = interaction.guild.voice_client
        if voice and isinstance(voice.source, discord.PCMVolumeTransformer):
            voice.source.volume = player.volume
        await interaction.response.send_message(embed=create_embed(description=f"🔊 Volume set to {round(player.volume * 100)}%"), ephemeral=True)
        await player.update_panel()

    @discord.ui.button(label="Queue", emoji="📜", style=discord.ButtonStyle.primary, custom_id="music:queue_btn", row=1)
    async def queue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from core.player_manager import get_player
        player = get_player(self.bot, interaction.guild)
        if not player.queue:
            await interaction.response.send_message(embed=create_embed(description="Queue is currently empty."), ephemeral=True)
            return
        lines = [f"`#{i}` **{discord.utils.escape_markdown(t.title[:60])}** ({format_clock(t.duration)})" for i, t in enumerate(player.queue, 1)]
        await interaction.response.send_message(
            embed=create_embed(title=f"📜 Queue for {interaction.guild.name}", description="\n".join(lines[:15])),
            ephemeral=True
        )
