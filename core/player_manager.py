"""
Player Manager for multi-server isolation.
Maintains a registry of GuildPlayer instances per guild_id.
"""

from typing import Dict
import discord
from core.player import GuildPlayer

_players: Dict[int, GuildPlayer] = {}


def get_player(bot: discord.Client, guild: discord.Guild) -> GuildPlayer:
    """Returns or creates the isolated player instance for a guild."""
    if guild.id not in _players:
        _players[guild.id] = GuildPlayer(bot, guild)
    return _players[guild.id]


def remove_player(guild_id: int):
    """Cleanly removes and discards the player instance for a guild."""
    if guild_id in _players:
        player = _players.pop(guild_id)
        if player.task and not player.task.done():
            player.task.cancel()


def all_players() -> Dict[int, GuildPlayer]:
    """Returns a copy of all active players."""
    return dict(_players)
