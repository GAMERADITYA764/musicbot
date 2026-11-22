"""
Permission and voice-state validation helpers.
Ensures callers are in the same voice channel, enforces DJ roles if configured,
and verifies request channel rules.
"""

from typing import Optional
import discord
from core.database import db


class MusicCheckError(Exception):
    """Custom exception raised when voice or permission checks fail."""
    pass


async def verify_voice_connection(interaction: discord.Interaction, require_same_channel: bool = True) -> discord.VoiceClient:
    """Verifies interaction user is in voice and connected to bot voice channel."""
    if not interaction.guild:
        raise MusicCheckError("This command can only be used in a Discord server.")

    user_voice = interaction.user.voice
    if not user_voice or not user_voice.channel:
        raise MusicCheckError("You must join a voice channel first.")

    bot_voice: Optional[discord.VoiceClient] = interaction.guild.voice_client

    if require_same_channel:
        if not bot_voice or not bot_voice.channel:
            raise MusicCheckError("I am not currently connected to any voice channel.")
        if bot_voice.channel.id != user_voice.channel.id:
            raise MusicCheckError(f"You must be in my voice channel ({bot_voice.channel.name}) to use this command.")

    return bot_voice


async def verify_dj_or_admin(interaction: discord.Interaction) -> bool:
    """
    Checks if the user has Administrator permissions, Manage Server permissions,
    or holds the designated DJ role (if set).
    """
    if not interaction.guild:
        return True

    # Guild administrators always have bypass
    if interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild:
        return True

    settings = await db.get_guild_settings(interaction.guild.id)
    dj_role_id = settings.get("dj_role_id")
    if not dj_role_id:
        # No DJ role configured, everyone in the voice channel can control music
        return True

    member_roles = {r.id for r in interaction.user.roles}
    if dj_role_id in member_roles:
        return True

    raise MusicCheckError("You need the configured DJ role or Administrator permissions to perform this action.")
