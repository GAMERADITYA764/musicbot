"""
Audio effects and equalizer cog.
Provides FFmpeg audio DSP filters: Bass Boost, Nightcore, Slowed+Reverb,
Vaporwave, 8D Audio, Treble, Karaoke, Echo, and Soft Lowpass.
"""

import logging
import discord
from discord import app_commands
from discord.ext import commands

from core.config import AUDIO_FILTERS
from core.player_manager import get_player
from core.ui import create_embed, success_embed, error_embed
from utils.checks import verify_voice_connection, verify_dj_or_admin, MusicCheckError

logger = logging.getLogger("musicbot.cogs.effects")


class EffectsCog(commands.Cog, name="Effects"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _apply_filter_helper(self, interaction: discord.Interaction, filter_name: str):
        voice = await verify_voice_connection(interaction)
        await verify_dj_or_admin(interaction)
        player = get_player(self.bot, interaction.guild)

        player.filter_name = filter_name

        if player.current and (voice.is_playing() or voice.is_paused()):
            # Replay current song from beginning or current elapsed time with new filter
            player.seek_offset = player.elapsed()
            player.queue.appendleft(player.current)
            voice.stop()
            msg = f"Applied filter **{filter_name.title()}**! Resuming track with DSP effect."
        else:
            msg = f"Set default filter to **{filter_name.title()}**."

        await interaction.response.send_message(embed=success_embed(msg))
        await player.update_panel()

    @app_commands.command(name="filter", description="Apply an audio DSP effect / equalizer")
    @app_commands.describe(name="Filter effect to apply")
    @app_commands.choices(name=[
        app_commands.Choice(name="None (Reset Normal)", value="none"),
        app_commands.Choice(name="Bass Boost", value="bassboost"),
        app_commands.Choice(name="Nightcore (High Pitch + Fast)", value="nightcore"),
        app_commands.Choice(name="Slowed (Downtempo)", value="slow"),
        app_commands.Choice(name="Vaporwave (Chilled Pitch)", value="vaporwave"),
        app_commands.Choice(name="8D Audio (Surround Pulsator)", value="8d"),
        app_commands.Choice(name="Karaoke (Vocal Suppression)", value="karaoke"),
        app_commands.Choice(name="Echo / Reverb", value="echo"),
        app_commands.Choice(name="Treble Boost", value="treble"),
        app_commands.Choice(name="Soft Lowpass", value="soft"),
    ])
    @app_commands.guild_only()
    async def filter_command(self, interaction: discord.Interaction, name: app_commands.Choice[str]):
        try:
            await self._apply_filter_helper(interaction, name.value)
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)
        except Exception as e:
            logger.exception("Filter application failed")
            await interaction.response.send_message(embed=error_embed(f"Failed to apply effect: {str(e)[:150]}"), ephemeral=True)

    @app_commands.command(name="bassboost", description="Shortcut to toggle Bass Boost filter")
    @app_commands.guild_only()
    async def bassboost_shortcut(self, interaction: discord.Interaction):
        try:
            player = get_player(self.bot, interaction.guild)
            target = "none" if player.filter_name == "bassboost" else "bassboost"
            await self._apply_filter_helper(interaction, target)
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="nightcore", description="Shortcut to toggle Nightcore filter")
    @app_commands.guild_only()
    async def nightcore_shortcut(self, interaction: discord.Interaction):
        try:
            player = get_player(self.bot, interaction.guild)
            target = "none" if player.filter_name == "nightcore" else "nightcore"
            await self._apply_filter_helper(interaction, target)
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EffectsCog(bot))
