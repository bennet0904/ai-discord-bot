"""Slash commands for managing the AI console."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from . import llm
from .console import can_use_console, ensure_console, get_console
from .tools import ToolError

if TYPE_CHECKING:
    from .client import AIBot


class LLMModal(discord.ui.Modal, title="Connect your LLM"):
    model = discord.ui.TextInput(
        label="Model (provider/model)",
        placeholder="e.g. anthropic/claude-sonnet-5-5 or openai/gpt-4o",
        max_length=200,
    )
    api_key = discord.ui.TextInput(
        label="API key",
        placeholder="Leave empty to keep the saved key",
        required=False,
        max_length=500,
    )
    api_base = discord.ui.TextInput(
        label="API base URL (optional)",
        placeholder="Only for OpenAI-compatible servers, Azure, Ollama ...",
        required=False,
        max_length=300,
    )

    def __init__(self, bot: "AIBot", guild_id: int):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        settings = bot.storage.guild(guild_id)
        if settings.llm_model:
            self.model.default = settings.llm_model
        if settings.llm_api_base:
            self.api_base.default = settings.llm_api_base

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        bot, cfg = self.bot, self.bot.config
        stored = bot.storage.guild(self.guild_id)
        try:
            model = llm.validate_model(self.model.value)
            api_base = self.api_base.value.strip() or None
            if api_base:
                api_base = await llm.validate_api_base(api_base, cfg.allow_private_api_base)
            api_key = self.api_key.value.strip() or None
            if api_key is None and stored.llm_api_key_encrypted:
                same_provider = stored.llm_model and stored.llm_model.split("/", 1)[0] == model.split("/", 1)[0]
                if same_provider:
                    api_key = bot.keybox.decrypt(stored.llm_api_key_encrypted)
            if api_key is None and api_base is None:
                raise llm.LLMConfigError("An API key is required (only self-hosted servers with an API base can skip it).")
            settings = llm.LLMSettings(model=model, api_key=api_key, api_base=api_base)
            warning = await llm.test_connection(settings)
        except llm.LLMConfigError as exc:
            await interaction.followup.send(f"❌ Not saved. {exc}", ephemeral=True)
            return

        bot.storage.update_guild(
            self.guild_id,
            llm_model=model,
            llm_api_key_encrypted=bot.keybox.encrypt(api_key) if api_key else None,
            llm_api_base=api_base,
        )
        text = f"✅ Connected to `{model}`. The test request worked."
        if warning:
            text += f"\n⚠️ {warning}"
        await interaction.followup.send(text, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        message = f"Something went wrong: {type(error).__name__}"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class AdminCog(commands.Cog):
    ai = app_commands.Group(name="ai", description="Manage the AI admin console", guild_only=True)

    def __init__(self, bot: "AIBot"):
        self.bot = bot

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _is_owner(interaction: discord.Interaction) -> bool:
        return bool(interaction.guild) and interaction.user.id == interaction.guild.owner_id

    async def _require_owner(self, interaction: discord.Interaction) -> bool:
        if self._is_owner(interaction):
            return True
        await interaction.response.send_message("Only the server owner can do this.", ephemeral=True)
        return False

    async def _require_user(self, interaction: discord.Interaction) -> bool:
        assert interaction.guild
        if can_use_console(self.bot, interaction.guild, interaction.user):
            return True
        await interaction.response.send_message("You do not have access to the AI console.", ephemeral=True)
        return False

    # ---- console ----------------------------------------------------------

    @ai.command(name="setup", description="Create or repair the private AI console channel")
    async def setup(self, interaction: discord.Interaction) -> None:
        if not await self._require_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        channel = await ensure_console(self.bot, interaction.guild)
        await interaction.followup.send(f"AI console is ready: {channel.mention}", ephemeral=True)

    @ai.command(name="grant", description="Give a user access to the AI console")
    @app_commands.describe(user="The member who should get access")
    async def grant(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if not await self._require_owner(interaction):
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
        if not await self._require_owner(interaction):
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

    @ai.command(name="stop", description="Cancel the request the assistant is working on")
    async def stop(self, interaction: discord.Interaction) -> None:
        if not await self._require_user(interaction):
            return
        if self.bot.agent.stop(interaction.guild.id):
            await interaction.response.send_message(
                "⏹️ Stopping. A running tool call finishes first, then the request ends.", ephemeral=True
            )
        else:
            await interaction.response.send_message("Nothing is running right now.", ephemeral=True)

    @ai.command(name="reset", description="Clear the assistant's conversation memory")
    async def reset(self, interaction: discord.Interaction) -> None:
        if not await self._require_user(interaction):
            return
        self.bot.storage.clear_history(interaction.guild.id)
        await interaction.response.send_message("🧹 Conversation memory cleared.")

    # ---- LLM --------------------------------------------------------------

    @ai.command(name="llm", description="Connect this server's own LLM (model and API key)")
    async def llm_setup(self, interaction: discord.Interaction) -> None:
        if not await self._require_owner(interaction):
            return
        await interaction.response.send_modal(LLMModal(self.bot, interaction.guild.id))

    @ai.command(name="llm-remove", description="Delete this server's saved LLM settings and API key")
    async def llm_remove(self, interaction: discord.Interaction) -> None:
        if not await self._require_owner(interaction):
            return
        self.bot.storage.update_guild(
            interaction.guild.id, llm_model=None, llm_api_key_encrypted=None, llm_api_base=None
        )
        await interaction.response.send_message("🗑️ LLM settings and API key deleted.", ephemeral=True)

    # ---- custom tools -----------------------------------------------------

    @ai.command(name="tools", description="List this server's custom tools")
    async def tools(self, interaction: discord.Interaction) -> None:
        if not await self._require_user(interaction):
            return
        if not self.bot.config.enable_custom_tools:
            await interaction.response.send_message("Custom tools are disabled on this bot.", ephemeral=True)
            return
        records = self.bot.custom_tools.records(interaction.guild.id)
        if not records:
            await interaction.response.send_message("This server has no custom tools yet.", ephemeral=True)
            return
        lines = [f"`{r.name}`{' ⚠️' if r.destructive else ''}: {r.description[:150]}" for r in records.values()]
        await interaction.response.send_message("\n".join(lines)[:1990], ephemeral=True)

    @ai.command(name="tool-delete", description="Delete one of this server's custom tools")
    @app_commands.describe(name="Name of the custom tool")
    async def tool_delete(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._require_owner(interaction):
            return
        try:
            self.bot.custom_tools.delete(interaction.guild.id, name)
        except ToolError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(f"Deleted custom tool `{name}`.", ephemeral=True)

    @tool_delete.autocomplete("name")
    async def _tool_name_autocomplete(self, interaction: discord.Interaction, current: str):
        names = self.bot.custom_tools.records(interaction.guild.id) if interaction.guild else {}
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n][:25]

    # ---- status -----------------------------------------------------------

    @ai.command(name="status", description="Show the assistant's configuration")
    async def status(self, interaction: discord.Interaction) -> None:
        if not await self._require_user(interaction):
            return
        bot, cfg, guild = self.bot, self.bot.config, interaction.guild
        settings = bot.storage.guild(guild.id)
        console = get_console(bot, guild)
        embed = discord.Embed(title="AI console status", colour=discord.Colour.blurple())
        embed.add_field(name="Model", value=f"`{settings.llm_model}`" if settings.llm_model else "not set, run `/ai llm`")
        embed.add_field(name="API key", value="saved (encrypted)" if settings.llm_api_key_encrypted else "none")
        if settings.llm_api_base:
            embed.add_field(name="API base", value=settings.llm_api_base, inline=False)
        embed.add_field(name="Console", value=console.mention if console else "missing, run /ai setup")
        embed.add_field(name="Confirmations", value="on" if cfg.require_confirmation else "off")
        embed.add_field(name="Built-in tools", value=str(len(bot.registry.discord_tools)))
        if cfg.enable_custom_tools:
            names = ", ".join(f"`{n}`" for n in bot.custom_tools.records(guild.id)) or "none"
            embed.add_field(name="Custom tools", value=names[:1000], inline=False)
        else:
            embed.add_field(name="Custom tools", value="disabled on this bot", inline=False)
        embed.add_field(name="Stored messages", value=str(len(bot.storage.history(guild.id))))
        await interaction.response.send_message(embed=embed, ephemeral=True)
