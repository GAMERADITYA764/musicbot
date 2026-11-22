"""
Utility and diagnostics cog.
Provides /ping, /status, /history, /lyrics, and background panel update loop.
"""

import sys
import platform
import logging
from typing import Optional
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.ffmpeg_handler import find_ffmpeg, verify_ffmpeg
from core.database import db
from core.player_manager import get_player, all_players
from core.ui import create_embed, error_embed, format_clock

logger = logging.getLogger("musicbot.cogs.general")


class GeneralCog(commands.Cog, name="General"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self):
        self.panel_refresh_loop.cancel()

    @tasks.loop(seconds=12)
    async def panel_refresh_loop(self):
        """Periodically refreshes active now-playing embeds with live elapsed time."""
        for player in list(all_players().values()):
            if player.current and player.panel_message:
                try:
                    await player.update_panel()
                except discord.HTTPException:
                    pass
                except Exception as e:
                    logger.debug("Panel periodic refresh failed: %s", e)

    @panel_refresh_loop.before_loop
    async def before_panels(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="ping", description="Check Gateway latency and WebSocket roundtrip")
    async def ping_command(self, interaction: discord.Interaction):
        latency_ms = round(self.bot.latency * 1000)
        embed = create_embed(
            title="🏓 Pong!",
            description=f"**Gateway WebSocket Latency:** `{latency_ms} ms`"
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="status", description="Check system health, FFmpeg availability, and VPS runtime")
    @app_commands.guild_only()
    async def status_command(self, interaction: discord.Interaction):
        ffmpeg_bin = find_ffmpeg()
        ffmpeg_healthy = verify_ffmpeg() if ffmpeg_bin else False
        ffmpeg_text = f"Available ✅ (`{ffmpeg_bin}`)" if ffmpeg_healthy else "Not found / Failing ❌"

        player = get_player(self.bot, interaction.guild)
        total_players = len(all_players())
        active_playing = sum(1 for p in all_players().values() if p.current is not None)

        embed = create_embed(
            title="📊 Bot Diagnostics & Host Status",
            description="Production Health & VPS Compatibility"
        )
        embed.add_field(name="Python Runtime", value=f"{sys.version.split()[0]} on {platform.system()} {platform.machine()}", inline=False)
        embed.add_field(name="FFmpeg Engine", value=ffmpeg_text, inline=False)
        embed.add_field(name="Bot Latency", value=f"{round(self.bot.latency * 1000)} ms", inline=True)
        embed.add_field(name="Guild Players", value=f"{active_playing} active / {total_players} total", inline=True)
        embed.add_field(name="Guild Voice Status", value="Connected 🟢" if interaction.guild.voice_client else "Idle ⚪", inline=True)
        embed.add_field(name="Current Guild Queue", value=f"{len(player.queue)} tracks", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="history", description="Show recently played tracks on this server")
    @app_commands.guild_only()
    async def history_command(self, interaction: discord.Interaction):
        records = await db.get_guild_history(interaction.guild.id, limit=15)
        if not records:
            await interaction.response.send_message(embed=create_embed(description="No song history recorded yet on this server."), ephemeral=True)
            return

        lines = []
        for i, r in enumerate(records, 1):
            dur_str = f"({format_clock(r['duration'])})" if r.get("duration") else ""
            req_str = f"requested by {r.get('requester', 'User')}" if r.get("requester") else ""
            lines.append(f"`#{i}` **{discord.utils.escape_markdown(r['title'][:60])}** {dur_str} {req_str}")

        embed = create_embed(
            title=f"📜 Song History for {interaction.guild.name}",
            description="\n".join(lines)
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="lyrics", description="Fetch synced or plain lyrics for a song or current track")
    @app_commands.describe(query="Song title and artist (defaults to currently playing song)")
    @app_commands.guild_only()
    async def lyrics_command(self, interaction: discord.Interaction, query: Optional[str] = None):
        await interaction.response.defer(thinking=True)
        player = get_player(self.bot, interaction.guild)

        target_query = query.strip() if query else (player.current.title if player.current else "")
        if not target_query:
            await interaction.followup.send(embed=error_embed("No song is currently playing. Please specify a song name: `/lyrics query:Song Title`."), ephemeral=True)
            return

        try:
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "VectorNodesMusicBot/2.0"}) as session:
                url = f"https://lrclib.net/api/search?q={quote(target_query[:150])}"
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise ValueError(f"LRCLIB service returned HTTP {resp.status}")
                    data = await resp.json()

            match = next((item for item in data if item.get("plainLyrics")), None)
            if not match or not match.get("plainLyrics"):
                await interaction.followup.send(embed=error_embed(f"No lyrics found for **{target_query}**."), ephemeral=True)
                return

            lyrics_text = match["plainLyrics"]
            if len(lyrics_text) > 4000:
                lyrics_text = lyrics_text[:3950] + "\n... [Lyrics truncated]"

            embed = create_embed(
                title=f"🎤 Lyrics: {match.get('trackName', target_query)} - {match.get('artistName', '')}",
                description=lyrics_text
            )
            embed.set_footer(text="Lyrics provided via LRCLIB")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.warning("Lyrics fetch error: %s", e)
            await interaction.followup.send(embed=error_embed("Lyrics service is temporarily unreachable. Try again later."), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCog(bot))
