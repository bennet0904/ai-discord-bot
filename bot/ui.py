"""Discord UI helpers: confirmation buttons and message splitting."""

from __future__ import annotations

import discord


def split_message(text: str, limit: int = 1990) -> list[str]:
    """Split text into Discord-sized chunks, preferring line breaks and keeping code fences balanced."""
    chunks: list[str] = []
    remaining = text
    reopen = ""
    while remaining:
        prefix = f"{reopen}\n" if reopen else ""
        budget = limit - len(prefix) - 4
        if len(remaining) <= budget:
            chunks.append(prefix + remaining)
            break
        cut = remaining.rfind("\n", 0, budget)
        if cut < budget // 2:
            cut = remaining.rfind(" ", 0, budget)
        if cut < budget // 2:
            cut = budget
        body = prefix + remaining[:cut]
        remaining = remaining[cut:].lstrip("\n")

        inside, opening = False, "```"
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                inside, opening = (True, stripped) if not inside else (False, opening)
        reopen = opening if inside else ""
        if inside:
            body += "\n```"
        chunks.append(body)
    return [c for c in chunks if c.strip()]


async def send_long(channel: discord.abc.Messageable, text: str) -> None:
    for chunk in split_message(text):
        await channel.send(chunk, allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False))


class ConfirmView(discord.ui.View):
    """Approve / Deny buttons that only specific users may press."""

    def __init__(self, allowed_user_ids: set[int], timeout: float = 300):
        super().__init__(timeout=timeout)
        self.allowed_user_ids = allowed_user_ids
        self.approved = False
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.allowed_user_ids:
            return True
        await interaction.response.send_message("Only the person who made the request can decide this.", ephemeral=True)
        return False

    async def _finish(self, interaction: discord.Interaction, approved: bool) -> None:
        self.approved = approved
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        verdict = "Approved" if approved else "Denied"
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
        if embed:
            embed.colour = discord.Colour.green() if approved else discord.Colour.red()
            embed.set_footer(text=f"{verdict} by {interaction.user}")
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._finish(interaction, True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._finish(interaction, False)

    async def on_timeout(self) -> None:
        self.approved = False
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message:
            embed = self.message.embeds[0] if self.message.embeds else None
            if embed:
                embed.colour = discord.Colour.dark_grey()
                embed.set_footer(text="Timed out, treated as denied")
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass
