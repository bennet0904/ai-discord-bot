"""Creating and maintaining the private AI console channel per guild."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from .client import AIBot

log = logging.getLogger(__name__)

USER_ACCESS = discord.PermissionOverwrite(
    view_channel=True,
    send_messages=True,
    read_message_history=True,
    attach_files=True,
    embed_links=True,
    use_application_commands=True,
)
BOT_ACCESS = discord.PermissionOverwrite(
    view_channel=True,
    send_messages=True,
    read_message_history=True,
    attach_files=True,
    embed_links=True,
    manage_messages=True,
    manage_channels=True,
)

WELCOME = """👋 Hi {owner}, this is your private **AI console** for **{guild}**.

Every message you write here goes to an AI assistant that can manage this server for you: \
create and delete channels and roles, set permissions, moderate members, post announcements and more. \
{custom}

**First step:** connect your own LLM with `/ai llm`. You enter a model (for example `anthropic/claude-sonnet-5-5`, \
`openai/gpt-4o` or `gemini/gemini-2.5-pro`) and your own API key. The key is stored encrypted and only used for this server.

**Commands**
`/ai llm` connect or change your LLM (owner only)
`/ai llm-remove` delete your LLM settings and key (owner only)
`/ai grant @user` / `/ai revoke @user` manage who can use this console (owner only)
`/ai users` show who has access
`/ai tools` / `/ai tool-delete` view or remove custom tools
`/ai stop` cancel the running request
`/ai reset` clear the conversation memory
`/ai status` show the current configuration
`/ai setup` recreate or repair this channel (owner only)

{confirm}"""


async def _resolve_member(guild: discord.Guild, user_id: int) -> discord.Member | None:
    member = guild.get_member(user_id)
    if member:
        return member
    try:
        return await guild.fetch_member(user_id)
    except discord.HTTPException:
        return None


async def build_overwrites(bot: "AIBot", guild: discord.Guild) -> dict:
    settings = bot.storage.guild(guild.id)
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: BOT_ACCESS,
    }
    for user_id in {guild.owner_id, *settings.allowed_user_ids}:
        member = await _resolve_member(guild, user_id)
        if member:
            overwrites[member] = USER_ACCESS
    return overwrites


def get_console(bot: "AIBot", guild: discord.Guild) -> discord.TextChannel | None:
    channel_id = bot.storage.guild(guild.id).console_channel_id
    channel = guild.get_channel(channel_id) if channel_id else None
    return channel if isinstance(channel, discord.TextChannel) else None


async def ensure_console(bot: "AIBot", guild: discord.Guild) -> discord.TextChannel:
    """Return the console channel, creating it if missing and fixing its permissions."""
    overwrites = await build_overwrites(bot, guild)
    channel = get_console(bot, guild)
    if channel is not None:
        if channel.overwrites != overwrites:
            await channel.edit(overwrites=overwrites, reason="AI console: sync access list")
        return channel

    channel = await guild.create_text_channel(
        bot.config.console_channel_name,
        overwrites=overwrites,
        topic="Private AI admin console. Every message here is sent to the AI assistant.",
        position=0,
        reason="AI console setup",
    )
    bot.storage.update_guild(guild.id, console_channel_id=channel.id)
    log.info("Created console channel #%s in %s", channel.name, guild.name)

    owner = guild.owner or await _resolve_member(guild, guild.owner_id)
    cfg = bot.config
    await channel.send(
        WELCOME.format(
            owner=owner.mention if owner else "there",
            guild=guild.name,
            custom=(
                "When you ask for something it cannot do yet, it can **write its own tools** for this server. "
                "They run in an isolated sandbox."
                if cfg.enable_custom_tools
                else ""
            ),
            confirm=(
                "Destructive actions (deleting, kicking, banning, writing tools) need your approval via buttons."
                if cfg.require_confirmation
                else "⚠️ Confirmations are disabled on this bot: the assistant acts immediately, including destructive actions."
            ),
        ),
        allowed_mentions=discord.AllowedMentions(users=True),
    )
    return channel


def can_use_console(bot: "AIBot", guild: discord.Guild, user: discord.abc.User) -> bool:
    if user.id == guild.owner_id:
        return True
    return user.id in bot.storage.guild(guild.id).allowed_user_ids
