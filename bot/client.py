"""The discord.py bot wiring everything together."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from .agent import Agent
from .commands import AdminCog
from .config import Config
from .console import can_use_console, ensure_console
from .storage import Storage
from .tools import CustomToolManager, ToolRegistry

log = logging.getLogger(__name__)


class AIBot(commands.Bot):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        options = {"owner_ids": config.bot_owner_ids} if config.bot_owner_ids else {}
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
            **options,
        )
        self.config = config
        self.storage = Storage(config.data_dir)
        self.custom_tools = CustomToolManager(config.plugins_dir)
        self.registry = ToolRegistry(self.custom_tools, enable_custom_tools=config.enable_custom_tools)
        self.agent = Agent(self)

    async def setup_hook(self) -> None:
        await self.add_cog(AdminCog(self))
        synced = await self.tree.sync()
        log.info("Synced %d application commands", len(synced))

    async def on_ready(self) -> None:
        assert self.user
        invite = discord.utils.oauth_url(
            self.user.id,
            permissions=discord.Permissions(administrator=True),
            scopes=("bot", "applications.commands"),
        )
        log.info("Logged in as %s (%s) in %d guild(s)", self.user, self.user.id, len(self.guilds))
        log.info("Invite URL: %s", invite)
        log.info("Model: %s | tools: %d", self.config.llm_model, len(self.registry.all()))
        for guild in self.guilds:
            await self._setup_guild(guild)

    async def _setup_guild(self, guild: discord.Guild) -> None:
        if not guild.me.guild_permissions.administrator:
            log.warning("Missing administrator permission in %s; some tools will fail.", guild.name)
        try:
            await ensure_console(self, guild)
        except discord.HTTPException as exc:
            log.error("Could not set up console in %s: %s", guild.name, exc)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined guild %s (%s)", guild.name, guild.id)
        await self._setup_guild(guild)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info("Removed from guild %s (%s)", guild.name, guild.id)
        self.storage.forget_guild(guild.id)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        settings = self.storage.guild(channel.guild.id)
        if settings.console_channel_id == channel.id:
            log.info("Console channel in %s was deleted; run /ai setup to recreate it.", channel.guild.name)
            self.storage.update_guild(channel.guild.id, console_channel_id=None)

    async def on_member_remove(self, member: discord.Member) -> None:
        settings = self.storage.guild(member.guild.id)
        if member.id in settings.allowed_user_ids:
            self.storage.update_guild(
                member.guild.id, allowed_user_ids=[u for u in settings.allowed_user_ids if u != member.id]
            )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if self.storage.guild(message.guild.id).console_channel_id != message.channel.id:
            return
        if not message.content and not message.attachments:
            return
        if not can_use_console(self, message.guild, message.author):
            await message.reply(
                "-# You are not on this console's access list. Ask the server owner to run `/ai grant`.",
                mention_author=False,
            )
            return
        await self.agent.handle(message)
