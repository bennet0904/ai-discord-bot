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

Every message you write here goes to the AI assistant (`{model}`), which can manage this server for you: \
create and delete channels and roles, set permissions, moderate members, post announcements and more. \
{custom}

**Commands**
`/ai grant @user` give someone else access to this console (owner only)
`/ai revoke @user` remove access (owner only)
`/ai users` show who has access
`/ai reset` clear the conversation memory
`/ai model` show or change the LLM for this server (owner only)
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
            model=bot.storage.guild(guild.id).model or cfg.llm_model,
            custom=(
                "It can also **program new tools for itself** in Python when you ask for something it cannot do yet "
                "(only the bot's owner can trigger this)."
                if cfg.enable_custom_tools
                else ""
            ),
            confirm=(
                "Destructive actions (deleting, kicking, banning, writing code) need your approval via buttons."
                if cfg.require_confirmation
                else "⚠️ Confirmations are disabled: the assistant acts immediately, including destructive actions."
            ),
        ),
        allowed_mentions=discord.AllowedMentions(users=True),
    )
    return channel


def can_use_console(bot: "AIBot", guild: discord.Guild, user: discord.abc.User) -> bool:
    if user.id == guild.owner_id:
        return True
    return user.id in bot.storage.guild(guild.id).allowed_user_ids
