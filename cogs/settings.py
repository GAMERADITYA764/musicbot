"""
Guild settings and administration cog.
Configures 24/7 mode, DJ roles, request channels, default volume,
and autoplay persistence per server.
"""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.database import db
from core.player_manager import get_player
from core.ui import create_embed, success_embed, error_embed
from utils.checks import verify_dj_or_admin, MusicCheckError

logger = logging.getLogger("musicbot.cogs.settings")


class SettingsCog(commands.Cog, name="Settings"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="247", description="Toggle 24/7 mode (keeps bot in voice channel indefinitely)")
    @app_commands.guild_only()
    async def mode_24_7_toggle(self, interaction: discord.Interaction):
        try:
            await verify_dj_or_admin(interaction)
            player = get_player(self.bot, interaction.guild)
            await player.ensure_settings_loaded()

            player.mode_24_7 = not player.mode_24_7
            await db.update_guild_setting(interaction.guild.id, mode_24_7=int(player.mode_24_7))

            status_str = "Enabled 🟢 (Bot will stay in voice 24/7)" if player.mode_24_7 else "Disabled ⚪ (Bot will leave when queue ends)"
            await interaction.response.send_message(embed=success_embed(f"24/7 Mode: **{status_str}**"))
            await player.update_panel()
        except MusicCheckError as mce:
            await interaction.response.send_message(embed=error_embed(str(mce)), ephemeral=True)

    @app_commands.command(name="autoplay", description="Toggle autoplay recommendations when queue finishes")
    @app_commands.guild_only()
    async def autoplay_toggle(self, interaction: discord.Interaction):
        try:
            player = get_player(self.bot, interaction.guild)
            await player.ensure_settings_loaded()

            player.autoplay = not player.autoplay
            await db.update_guild_setting(interaction.guild.id, autoplay=int(player.autoplay))

            status_str = "Enabled ✅ (Continuous playback)" if player.autoplay else "Disabled ⭕"
            await interaction.response.send_message(embed=success_embed(f"Autoplay: **{status_str}**"))
            await player.update_panel()
        except Exception as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @app_commands.command(name="set_dj", description="Set or clear the required DJ role for music commands")
    @app_commands.describe(role="Role to designate as DJ (leave empty to clear and allow everyone)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def set_dj_role(self, interaction: discord.Interaction, role: Optional[discord.Role] = None):
        if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(embed=error_embed("You need 'Manage Server' permissions to configure DJ role."), ephemeral=True)
            return

        role_id = role.id if role else None
        await db.update_guild_setting(interaction.guild.id, dj_role_id=role_id)

        if role:
            await interaction.response.send_message(embed=success_embed(f"DJ role successfully set to **@{role.name}**."))
        else:
            await interaction.response.send_message(embed=success_embed("DJ role cleared. All voice members can now control playback."))

    @app_commands.command(name="set_request_channel", description="Restrict music command interactions to a dedicated text channel")
    @app_commands.describe(channel="Designated text channel (leave empty to allow in all channels)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def set_request_channel(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(embed=error_embed("You need 'Manage Server' permissions to configure request channel."), ephemeral=True)
            return

        channel_id = channel.id if channel else None
        await db.update_guild_setting(interaction.guild.id, request_channel_id=channel_id)

        if channel:
            await interaction.response.send_message(embed=success_embed(f"Music commands are now bound to {channel.mention}."))
        else:
            await interaction.response.send_message(embed=success_embed("Request channel restriction removed. Commands allowed anywhere."))

    @app_commands.command(name="settings", description="View current guild configuration and preferences")
    @app_commands.guild_only()
    async def view_settings(self, interaction: discord.Interaction):
        settings = await db.get_guild_settings(interaction.guild.id)
        player = get_player(self.bot, interaction.guild)
        await player.ensure_settings_loaded()

        dj_role = interaction.guild.get_role(settings["dj_role_id"]) if settings["dj_role_id"] else None
        req_channel = interaction.guild.get_channel(settings["request_channel_id"]) if settings["request_channel_id"] else None

        embed = create_embed(
            title=f"⚙️ Settings for {interaction.guild.name}",
            description="Manage settings using `/set_dj`, `/set_request_channel`, and `/247`."
        )
        embed.add_field(name="DJ Role", value=dj_role.mention if dj_role else "None (Open to all)", inline=True)
        embed.add_field(name="Request Channel", value=req_channel.mention if req_channel else "Any Channel", inline=True)
        embed.add_field(name="24/7 Voice Mode", value="Enabled 🟢" if player.mode_24_7 else "Disabled ⚪", inline=True)
        embed.add_field(name="Autoplay", value="Enabled ✅" if player.autoplay else "Disabled ⭕", inline=True)
        embed.add_field(name="Current Volume", value=f"{round(player.volume * 100)}%", inline=True)
        embed.add_field(name="Active Audio Filter", value=player.filter_name.title(), inline=True)

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(SettingsCog(bot))
