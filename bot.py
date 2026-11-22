"""
Main entry point for Discord Music Bot.
Handles lifecycle events, intents, cog registration, persistent view restoration,
global error handling, and clean asynchronous shutdown.
"""

import sys
import asyncio
import logging
import signal
from pathlib import Path

import discord
from discord.ext import commands

from core.config import (
    DISCORD_TOKEN,
    COMMAND_PREFIX,
    LOG_LEVEL,
)
from core.database import db
from core.ffmpeg_handler import find_ffmpeg, verify_ffmpeg
from core.player import PersistentMusicPanelView
from core.player_manager import all_players, remove_player

# Configure Root Logging
log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format=log_format,
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("musicbot.main")

# Required Intents:
# - guilds: required to receive guild updates, role checks, and slash commands.
# - voice_states: CRUCIAL for tracking voice channel joins, leaves, and bot connectivity.
# Note: Privileged Message Content Intent is NOT enabled or required since all commands are Slash Commands.
intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.message_content = False


class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=COMMAND_PREFIX,
            intents=intents,
            help_command=None,
        )
        self.persistent_views_added = False

    async def setup_hook(self):
        """Asynchronous initialization: DB connection, Cogs loading, View registration."""
        logger.info("Initializing SQLite database...")
        await db.connect()

        # Check FFmpeg
        ffmpeg_exe = find_ffmpeg()
        if ffmpeg_exe:
            if verify_ffmpeg():
                logger.info("FFmpeg verified and working: %s", ffmpeg_exe)
            else:
                logger.warning("FFmpeg executable located at %s but test synthesis failed. Playback may fail.", ffmpeg_exe)
        else:
            logger.error("FFmpeg was NOT found. Audio streaming requires FFmpeg. Check FFMPEG_PATH.")

        # Register persistent MusicPanel view for interactive buttons
        if not self.persistent_views_added:
            self.add_view(PersistentMusicPanelView(self))
            self.persistent_views_added = True
            logger.info("Registered persistent MusicPanel interaction view.")

        # Load Cogs
        cogs = [
            "cogs.music",
            "cogs.effects",
            "cogs.playlist",
            "cogs.settings",
            "cogs.general",
        ]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info("Loaded cog: %s", cog)
            except Exception as e:
                logger.exception("Failed to load cog %s: %s", cog, e)

    async def on_ready(self):
        logger.info("=" * 60)
        logger.info("Bot logged in successfully as: %s (ID: %s)", self.user.name, self.user.id)
        logger.info("Connected to %d servers.", len(self.guilds))
        logger.info("=" * 60)

        # Start background tasks now that bot is logged in and ready
        playlist_cog = self.get_cog("Playlists")
        if playlist_cog and hasattr(playlist_cog, "sync_loop") and not playlist_cog.sync_loop.is_running():
            playlist_cog.sync_loop.start()

        general_cog = self.get_cog("General")
        if general_cog and hasattr(general_cog, "panel_refresh_loop") and not general_cog.panel_refresh_loop.is_running():
            general_cog.panel_refresh_loop.start()

        # Set presence
        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name="/play | High-Fidelity Music"
        )
        await self.change_presence(status=discord.Status.online, activity=activity)

        # Sync application slash commands with Discord Gateway
        try:
            synced = await self.tree.sync()
            logger.info("Synchronized %d application (/) commands with Discord.", len(synced))
        except Exception as e:
            logger.exception("Failed syncing application commands: %s", e)

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Auto-cleanup when all users leave voice channel, or bot is disconnected."""
        voice: discord.VoiceClient = member.guild.voice_client if member.guild else None
        if not voice or not voice.channel:
            return

        # 1. If bot itself was disconnected or moved
        if member.id == self.user.id:
            if not after.channel:
                logger.info("Bot disconnected from voice in guild %s; cleaning player state.", member.guild.name)
                remove_player(member.guild.id)
            return

        # 2. If human members left voice channel
        from core.player_manager import get_player
        player = get_player(self, member.guild)
        await player.ensure_settings_loaded()

        # If 24/7 mode is enabled, bot stays in channel even when empty
        if player.mode_24_7:
            return

        # Count non-bot members remaining in bot's voice channel
        humans = [m for m in voice.channel.members if not m.bot]
        if len(humans) == 0:
            logger.info("All human users left voice channel %s in %s. Disconnecting.", voice.channel.name, member.guild.name)
            if player.channel:
                try:
                    embed = discord.Embed(
                        description="⏹️ Everyone left the voice channel. Disconnected to save VPS resources. (Use `/247` to disable auto-leave).",
                        color=discord.Color.blurple()
                    )
                    await player.channel.send(embed=embed, delete_after=15)
                except Exception:
                    pass
            player.generation += 1
            player.queue.clear()
            if voice.is_playing() or voice.is_paused():
                voice.stop()
            await voice.disconnect()
            await player.update_panel()

    async def close(self):
        """Graceful shutdown hook for cleanup."""
        logger.info("Shutting down bot. Disconnecting voice clients and database...")
        # Disconnect all voice connections
        for voice_client in list(self.voice_clients):
            try:
                await voice_client.disconnect(force=True)
            except Exception:
                pass
        
        # Close database connection
        await db.close()
        await super().close()
        logger.info("Shutdown completed successfully.")


async def main():
    if not DISCORD_TOKEN or DISCORD_TOKEN == "your_discord_bot_token_here":
        logger.critical("DISCORD_TOKEN is missing or not configured in .env file.")
        sys.exit(1)

    bot = MusicBot()

    # Handle OS termination signals for clean shutdown on Linux VPS (SIGTERM, SIGINT)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))
        except (NotImplementedError, AttributeError):
            # Windows may not implement add_signal_handler for all signals
            pass

    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Process terminated.")
