"""
UI and formatting helper module.
Builds Discord embeds, pagination views, and progress bars.
"""

import discord
from typing import List, Optional

FOOTER_TEXT = "🎧 Vector Nodes Music • Fun & High-Fidelity Audio"


def format_clock(seconds: Optional[int]) -> str:
    """Formats an integer seconds count to mm:ss or hh:mm:ss."""
    if not seconds or seconds < 0:
        return "00:00"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def progress_bar(elapsed: int, total: int, length: int = 14) -> str:
    """Generates a text progress bar: ▬▬▬▬🔘▬▬▬▬"""
    if total <= 0:
        return "▬" * length
    fraction = min(1.0, max(0.0, elapsed / total))
    filled = min(length, round(fraction * length))
    bar = "▬" * filled + "🔘" + "▬" * (length - filled)
    return bar


def create_embed(
    title: Optional[str] = None,
    description: Optional[str] = None,
    color: discord.Color = discord.Color.blurple(),
    footer: Optional[str] = FOOTER_TEXT
) -> discord.Embed:
    """Standardizes embed styling across all bot responses."""
    embed = discord.Embed(
        title=title,
        description=str(description)[:4096] if description else None,
        color=color
    )
    if footer:
        embed.set_footer(text=footer)
    return embed


def success_embed(text: str) -> discord.Embed:
    return create_embed(description=f"✅ {text}", color=discord.Color.green())


def error_embed(text: str) -> discord.Embed:
    return create_embed(description=f"❌ {text}", color=discord.Color.red())


def warning_embed(text: str) -> discord.Embed:
    return create_embed(description=f"⚠️ {text}", color=discord.Color.gold())


class PaginatorView(discord.ui.View):
    """Interactive paginator view for long lists (queue, favorites, history)."""
    def __init__(self, pages: List[discord.Embed], author_id: int, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.author_id = author_id
        self.current_page = 0
        self._update_buttons()

    def _update_buttons(self):
        self.first_page.disabled = (self.current_page == 0)
        self.prev_page.disabled = (self.current_page == 0)
        self.next_page.disabled = (self.current_page >= len(self.pages) - 1)
        self.last_page.disabled = (self.current_page >= len(self.pages) - 1)
        self.counter.label = f"Page {self.current_page + 1}/{len(self.pages)}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the command requester can flip pages.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⏮️", style=discord.ButtonStyle.secondary, row=0)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.primary, row=0)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="Page 1/1", style=discord.ButtonStyle.secondary, disabled=True, row=0)
    async def counter(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.primary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = len(self.pages) - 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        # Do not raise errors if message was deleted
