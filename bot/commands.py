"""Slash commands for managing the AI console."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .console import can_use_console, ensure_console, get_console

if TYPE_CHECKING:
    from .client import AIBot


class AdminCog(commands.Cog):
    ai = app_commands.Group(name="ai", description="Manage the AI admin console", guild_only=True)

    def __init__(self, bot: "AIBot"):
        self.bot = bot

    # ---- helpers ----------------------------------------------------------

    async def _is_manager(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        return bool(guild) and (interaction.user.id == guild.owner_id or await self.bot.is_owner(interaction.user))

    async def _require_manager(self, interaction: discord.Interaction) -> bool:
        if await self._is_manager(interaction):
            return True
        await interaction.response.send_message("Only the server owner can do this.", ephemeral=True)
        return False

    async def _require_user(self, interaction: discord.Interaction) -> bool:
        assert interaction.guild
        if can_use_console(self.bot, interaction.guild, interaction.user) or await self._is_manager(interaction):
            return True
        await interaction.response.send_message("You do not have access to the AI console.", ephemeral=True)
        return False

    # ---- commands ---------------------------------------------------------

    @ai.command(name="setup", description="Create or repair the private AI console channel")
    async def setup(self, interaction: discord.Interaction) -> None:
        if not await self._require_manager(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        channel = await ensure_console(self.bot, interaction.guild)
        await interaction.followup.send(f"AI console is ready: {channel.mention}", ephemeral=True)

    @ai.command(name="grant", description="Give a user access to the AI console")
    @app_commands.describe(user="The member who should get access")
    async def grant(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if not await self._require_manager(interaction):
            return
        if user.bot:
            await interaction.response.send_message("Bots cannot use the console.", ephemeral=True)
            return
        guild = interaction.guild
        settings = self.bot.storage.guild(guild.id)
        if user.id not in settings.allowed_user_ids:
            self.bot.storage.update_guild(guild.id, allowed_user_ids=[*settings.allowed_user_ids, user.id])
        await interaction.response.defer(ephemeral=True)
        channel = await ensure_console(self.bot, guild)
        await interaction.followup.send(f"{user.mention} can now use {channel.mention}.", ephemeral=True)

    @ai.command(name="revoke", description="Remove a user's access to the AI console")
    @app_commands.describe(user="The member to remove")
    async def revoke(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if not await self._require_manager(interaction):
            return
        guild = interaction.guild
        settings = self.bot.storage.guild(guild.id)
        if user.id == guild.owner_id:
            await interaction.response.send_message("The server owner always has access.", ephemeral=True)
            return
        self.bot.storage.update_guild(
            guild.id, allowed_user_ids=[uid for uid in settings.allowed_user_ids if uid != user.id]
        )
        await interaction.response.defer(ephemeral=True)
        await ensure_console(self.bot, guild)
        await interaction.followup.send(f"{user.mention} no longer has access.", ephemeral=True)

    @ai.command(name="users", description="Show who can use the AI console")
    async def users(self, interaction: discord.Interaction) -> None:
        if not await self._require_user(interaction):
            return
        guild = interaction.guild
        ids = [guild.owner_id, *self.bot.storage.guild(guild.id).allowed_user_ids]
        lines = [f"<@{uid}>" + (" (owner)" if uid == guild.owner_id else "") for uid in dict.fromkeys(ids)]
        await interaction.response.send_message(
            "**Console access**\n" + "\n".join(lines), ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @ai.command(name="reset", description="Clear the assistant's conversation memory")
    async def reset(self, interaction: discord.Interaction) -> None:
        if not await self._require_user(interaction):
            return
        self.bot.storage.clear_history(interaction.guild.id)
        await interaction.response.send_message("🧹 Conversation memory cleared.")

    @ai.command(name="model", description="Show or change the LLM used on this server")
    @app_commands.describe(name="LiteLLM model string like 'openai/gpt-4o', or 'default' to reset")
    async def model(self, interaction: discord.Interaction, name: str | None = None) -> None:
        guild = interaction.guild
        settings = self.bot.storage.guild(guild.id)
        if name is None:
            if not await self._require_user(interaction):
                return
            current = settings.model or f"{self.bot.config.llm_model} (default)"
            await interaction.response.send_message(f"Current model: `{current}`", ephemeral=True)
            return
        if not await self._require_manager(interaction):
            return
        new = None if name.strip().lower() == "default" else name.strip()
        self.bot.storage.update_guild(guild.id, model=new)
        await interaction.response.send_message(f"Model set to `{new or self.bot.config.llm_model}`.", ephemeral=True)

    @ai.command(name="status", description="Show the assistant's configuration")
    async def status(self, interaction: discord.Interaction) -> None:
        if not await self._require_user(interaction):
            return
        bot, cfg, guild = self.bot, self.bot.config, interaction.guild
        settings = bot.storage.guild(guild.id)
        console = get_console(bot, guild)
        custom = bot.custom_tools
        embed = discord.Embed(title="AI console status", colour=discord.Colour.blurple())
        embed.add_field(name="Model", value=f"`{settings.model or cfg.llm_model}`")
        embed.add_field(name="Console", value=console.mention if console else "missing, run /ai setup")
        embed.add_field(name="Confirmations", value="on" if cfg.require_confirmation else "off")
        embed.add_field(name="Built-in tools", value=str(len(bot.registry.builtin)))
        embed.add_field(
            name="Custom tools",
            value=(", ".join(f"`{n}`" for n in custom.tools) or "none") if cfg.enable_custom_tools else "disabled",
            inline=False,
        )
        if custom.errors:
            embed.add_field(
                name="Custom tools that failed to load",
                value="\n".join(f"`{n}`: {e[:150]}" for n, e in custom.errors.items())[:1000],
                inline=False,
            )
        embed.add_field(name="Stored messages", value=str(len(bot.storage.history(guild.id))))
        await interaction.response.send_message(embed=embed, ephemeral=True)
