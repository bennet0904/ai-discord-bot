"""Built-in tools that let the LLM manage the Discord server."""

from __future__ import annotations

import datetime as dt
from typing import Any

import discord

from .base import Tool, ToolContext, ToolError, boolean, integer, params, string, string_list

BUILTIN_TOOLS: list[Tool] = []


def tool(name: str, description: str, parameters: dict | None = None, *, destructive: bool = False):
    def decorator(func):
        BUILTIN_TOOLS.append(
            Tool(
                name=name,
                description=description,
                parameters=parameters or params(),
                handler=func,
                destructive=destructive,
            )
        )
        return func

    return decorator


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

PERMISSION_NAMES = sorted(discord.Permissions.VALID_FLAGS)
PERMISSION_HELP = (
    "Permission names use discord.py naming, e.g. administrator, manage_guild, manage_roles, "
    "manage_channels, kick_members, ban_members, moderate_members, manage_messages, view_channel, "
    "send_messages, read_message_history, attach_files, embed_links, add_reactions, connect, speak, "
    "mention_everyone, manage_nicknames, manage_webhooks, manage_threads."
)


def _clean_perm_name(name: str) -> str:
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")


def build_permissions(names: list[str] | None) -> discord.Permissions:
    perms = discord.Permissions.none()
    for raw in names or []:
        name = _clean_perm_name(raw)
        if name not in discord.Permissions.VALID_FLAGS:
            raise ToolError(f"Unknown permission {raw!r}. Valid names: {', '.join(PERMISSION_NAMES)}")
        setattr(perms, name, True)
    return perms


def parse_color(value: str | None) -> discord.Colour | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().lower()
    if text.startswith("#"):
        text = text[1:]
    if text.startswith("0x"):
        text = text[2:]
    named = getattr(discord.Colour, text.replace(" ", "_"), None)
    if callable(named) and text not in {"from_rgb", "from_hsv", "from_str", "random"}:
        try:
            return named()
        except TypeError:
            pass
    try:
        return discord.Colour(int(text, 16))
    except ValueError:
        raise ToolError(f"Invalid color {value!r}. Use hex like '#ff8800'.") from None


def channel_info(channel: discord.abc.GuildChannel) -> dict:
    info: dict[str, Any] = {
        "id": str(channel.id),
        "name": channel.name,
        "type": str(channel.type),
        "position": channel.position,
    }
    if getattr(channel, "category", None):
        info["category"] = channel.category.name
    if getattr(channel, "topic", None):
        info["topic"] = channel.topic
    if isinstance(channel, discord.VoiceChannel) and channel.user_limit:
        info["user_limit"] = channel.user_limit
    if channel.overwrites:
        info["private"] = not channel.permissions_for(channel.guild.default_role).view_channel
    return info


def role_info(role: discord.Role) -> dict:
    perms = [name for name, value in role.permissions if value]
    if role.permissions.administrator:
        perms = ["administrator"]
    return {
        "id": str(role.id),
        "name": role.name,
        "color": str(role.colour),
        "position": role.position,
        "hoist": role.hoist,
        "mentionable": role.mentionable,
        "managed": role.managed,
        "members": len(role.members),
        "permissions": perms,
    }


def member_info(member: discord.Member) -> dict:
    return {
        "id": str(member.id),
        "username": member.name,
        "display_name": member.display_name,
        "bot": member.bot,
        "roles": [r.name for r in member.roles if not r.is_default()],
        "joined_at": member.joined_at.isoformat() if member.joined_at else None,
        "timed_out_until": member.timed_out_until.isoformat() if member.timed_out_until else None,
    }


def _console_guard(ctx: ToolContext, channel: discord.abc.GuildChannel) -> None:
    console_id = ctx.bot.storage.guild(ctx.guild.id).console_channel_id
    if channel.id == console_id:
        raise ToolError("Refusing to modify or delete the AI console channel itself. The owner can use /ai setup instead.")


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------


@tool("get_server_info", "Get an overview of the server: name, owner, member count, channel and role counts, features.")
async def get_server_info(ctx: ToolContext, args: dict) -> dict:
    g = ctx.guild
    return {
        "id": str(g.id),
        "name": g.name,
        "description": g.description,
        "owner": str(g.owner) if g.owner else str(g.owner_id),
        "member_count": g.member_count,
        "channels": len(g.channels),
        "roles": len(g.roles),
        "verification_level": str(g.verification_level),
        "system_channel": g.system_channel.name if g.system_channel else None,
        "rules_channel": g.rules_channel.name if g.rules_channel else None,
        "premium_tier": g.premium_tier,
        "features": g.features,
        "created_at": g.created_at.isoformat(),
        "bot_top_role": g.me.top_role.name,
    }


@tool(
    "edit_server",
    "Change server settings. Only pass the fields you want to change.",
    params(
        name=string("New server name."),
        description=string("New server description (community servers only)."),
        system_channel=string("Channel for join/boost messages (name or id)."),
        verification_level=string("Verification level.", ["none", "low", "medium", "high", "highest"]),
    ),
)
async def edit_server(ctx: ToolContext, args: dict) -> str:
    changes: dict[str, Any] = {}
    if args.get("name"):
        changes["name"] = args["name"]
    if "description" in args:
        changes["description"] = args["description"]
    if args.get("system_channel"):
        changes["system_channel"] = ctx.find_channel(args["system_channel"])
    if args.get("verification_level"):
        changes["verification_level"] = discord.VerificationLevel[args["verification_level"]]
    if not changes:
        raise ToolError("Nothing to change.")
    await ctx.guild.edit(reason=ctx.reason, **changes)
    return f"Server updated: {', '.join(changes)}"


# ---------------------------------------------------------------------------
# channels
# ---------------------------------------------------------------------------


@tool("list_channels", "List all channels and categories with their ids, types and categories.")
async def list_channels(ctx: ToolContext, args: dict) -> list[dict]:
    out = []
    for category, channels in ctx.guild.by_category():
        if category:
            out.append(channel_info(category))
        out.extend(channel_info(c) for c in channels)
    return out


CHANNEL_TYPES = ["text", "voice", "category", "forum", "stage", "announcement"]


@tool(
    "create_channel",
    "Create a channel or category.",
    params(
        ["name"],
        name=string("Channel name."),
        type=string("Channel type (default text).", CHANNEL_TYPES),
        category=string("Parent category name or id."),
        topic=string("Channel topic (text, announcement, forum)."),
        nsfw=boolean("Age-restricted channel."),
        slowmode_seconds=integer("Slowmode delay in seconds (text channels)."),
        user_limit=integer("Max users (voice channels, 0 = unlimited)."),
        private=boolean("If true, hide from @everyone. Use visible_to to grant access."),
        visible_to=string_list("Roles or members (names, mentions or ids) that can see a private channel."),
    ),
)
async def create_channel(ctx: ToolContext, args: dict) -> dict:
    kind = args.get("type") or "text"
    name = args["name"]
    category = ctx.find_category(args.get("category"))

    overwrites: dict = {}
    if args.get("private"):
        overwrites[ctx.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        overwrites[ctx.guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True)
        for ref in args.get("visible_to") or []:
            target = await ctx.find_target(ref)
            overwrites[target] = discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True)

    common: dict[str, Any] = {"name": name, "reason": ctx.reason}
    if overwrites:
        common["overwrites"] = overwrites

    if kind == "category":
        channel = await ctx.guild.create_category(**common)
    elif kind == "voice":
        extra = {"user_limit": args["user_limit"]} if args.get("user_limit") is not None else {}
        channel = await ctx.guild.create_voice_channel(category=category, **extra, **common)
    elif kind == "stage":
        channel = await ctx.guild.create_stage_channel(category=category, **common)
    elif kind == "forum":
        extra = {"topic": args["topic"]} if args.get("topic") else {}
        channel = await ctx.guild.create_forum(category=category, nsfw=bool(args.get("nsfw")), **extra, **common)
    elif kind in {"text", "announcement"}:
        extra: dict[str, Any] = {}
        if args.get("topic"):
            extra["topic"] = args["topic"]
        if args.get("slowmode_seconds") is not None:
            extra["slowmode_delay"] = args["slowmode_seconds"]
        channel = await ctx.guild.create_text_channel(
            category=category, nsfw=bool(args.get("nsfw")), news=kind == "announcement", **extra, **common
        )
    else:
        raise ToolError(f"Unknown channel type {kind!r}.")
    return {"created": channel_info(channel)}


@tool(
    "edit_channel",
    "Edit a channel or category. Only pass the fields you want to change.",
    params(
        ["channel"],
        channel=string("Channel name, mention or id."),
        name=string("New name."),
        topic=string("New topic."),
        category=string("Move into this category (name or id). Use 'none' to remove from category."),
        position=integer("New position in the list."),
        nsfw=boolean("Age-restricted."),
        slowmode_seconds=integer("Slowmode delay in seconds."),
        user_limit=integer("Voice user limit."),
    ),
)
async def edit_channel(ctx: ToolContext, args: dict) -> dict:
    channel = ctx.find_channel(args["channel"])
    _console_guard(ctx, channel)
    changes: dict[str, Any] = {}
    for key, target in [("name", "name"), ("topic", "topic"), ("position", "position"), ("nsfw", "nsfw"),
                        ("slowmode_seconds", "slowmode_delay"), ("user_limit", "user_limit")]:
        if args.get(key) is not None:
            changes[target] = args[key]
    if args.get("category") is not None:
        changes["category"] = None if str(args["category"]).lower() == "none" else ctx.find_category(args["category"])
    if not changes:
        raise ToolError("Nothing to change.")
    edited = await channel.edit(reason=ctx.reason, **changes)
    return {"updated": channel_info(edited or channel)}


@tool(
    "delete_channel",
    "Delete a channel or category (channels inside a category are kept).",
    params(["channel"], channel=string("Channel name, mention or id.")),
    destructive=True,
)
async def delete_channel(ctx: ToolContext, args: dict) -> str:
    channel = ctx.find_channel(args["channel"])
    _console_guard(ctx, channel)
    name = channel.name
    await channel.delete(reason=ctx.reason)
    return f"Deleted channel {name!r}."


@tool(
    "set_channel_permissions",
    "Set permission overwrites for a role or member on a channel. " + PERMISSION_HELP,
    params(
        ["channel", "target"],
        channel=string("Channel name, mention or id."),
        target=string("Role or member (name, mention or id). Use '@everyone' for the default role."),
        allow=string_list("Permissions to explicitly allow."),
        deny=string_list("Permissions to explicitly deny."),
        neutral=string_list("Permissions to reset to inherited."),
        clear=boolean("Remove the whole overwrite for this target."),
    ),
)
async def set_channel_permissions(ctx: ToolContext, args: dict) -> str:
    channel = ctx.find_channel(args["channel"])
    _console_guard(ctx, channel)
    target = await ctx.find_target(args["target"])
    if args.get("clear"):
        await channel.set_permissions(target, overwrite=None, reason=ctx.reason)
        return f"Cleared overwrites for {target} on #{channel.name}."
    overwrite = channel.overwrites_for(target)
    updates: dict[str, bool | None] = {}
    for key, value in (("allow", True), ("deny", False), ("neutral", None)):
        for raw in args.get(key) or []:
            name = _clean_perm_name(raw)
            if name not in discord.PermissionOverwrite.VALID_NAMES:
                raise ToolError(f"Unknown permission {raw!r}.")
            updates[name] = value
    if not updates:
        raise ToolError("Provide allow, deny, neutral or clear.")
    overwrite.update(**updates)
    await channel.set_permissions(target, overwrite=overwrite, reason=ctx.reason)
    return f"Updated permissions for {target} on #{channel.name}: {updates}"


# ---------------------------------------------------------------------------
# roles
# ---------------------------------------------------------------------------


@tool("list_roles", "List all roles (highest first) with ids, colors, permissions and member counts.")
async def list_roles(ctx: ToolContext, args: dict) -> list[dict]:
    return [role_info(r) for r in reversed(ctx.guild.roles)]


@tool(
    "create_role",
    "Create a role. " + PERMISSION_HELP,
    params(
        ["name"],
        name=string("Role name."),
        color=string("Hex color like '#3498db' or a name like 'blue'."),
        permissions=string_list("Permissions to grant."),
        hoist=boolean("Show members separately in the member list."),
        mentionable=boolean("Allow anyone to mention this role."),
    ),
)
async def create_role(ctx: ToolContext, args: dict) -> dict:
    kwargs: dict[str, Any] = {"name": args["name"], "reason": ctx.reason}
    colour = parse_color(args.get("color"))
    if colour is not None:
        kwargs["colour"] = colour
    if args.get("permissions"):
        kwargs["permissions"] = build_permissions(args["permissions"])
    for key in ("hoist", "mentionable"):
        if args.get(key) is not None:
            kwargs[key] = bool(args[key])
    role = await ctx.guild.create_role(**kwargs)
    return {"created": role_info(role)}


@tool(
    "edit_role",
    "Edit a role. Only pass the fields you want to change. `permissions` replaces the full permission set. "
    + PERMISSION_HELP,
    params(
        ["role"],
        role=string("Role name, mention or id."),
        name=string("New name."),
        color=string("Hex color or color name."),
        permissions=string_list("Full new permission list (replaces existing)."),
        add_permissions=string_list("Permissions to add to the existing set."),
        remove_permissions=string_list("Permissions to remove from the existing set."),
        hoist=boolean("Show separately in member list."),
        mentionable=boolean("Mentionable by everyone."),
        position=integer("New position (higher = more powerful, must be below the bot's top role)."),
    ),
)
async def edit_role(ctx: ToolContext, args: dict) -> dict:
    role = ctx.find_role(args["role"])
    changes: dict[str, Any] = {}
    if args.get("name"):
        changes["name"] = args["name"]
    colour = parse_color(args.get("color"))
    if colour is not None:
        changes["colour"] = colour
    if args.get("permissions") is not None:
        changes["permissions"] = build_permissions(args["permissions"])
    if args.get("add_permissions") or args.get("remove_permissions"):
        perms = discord.Permissions(changes.get("permissions", role.permissions).value)
        perms.value |= build_permissions(args.get("add_permissions")).value
        perms.value &= ~build_permissions(args.get("remove_permissions")).value
        changes["permissions"] = perms
    for key in ("hoist", "mentionable", "position"):
        if args.get(key) is not None:
            changes[key] = args[key]
    if not changes:
        raise ToolError("Nothing to change.")
    edited = await role.edit(reason=ctx.reason, **changes)
    return {"updated": role_info(edited or role)}


@tool(
    "delete_role",
    "Delete a role.",
    params(["role"], role=string("Role name, mention or id.")),
    destructive=True,
)
async def delete_role(ctx: ToolContext, args: dict) -> str:
    role = ctx.find_role(args["role"])
    if role.is_default() or role.managed:
        raise ToolError(f"{role.name!r} cannot be deleted (default or managed by an integration).")
    name = role.name
    await role.delete(reason=ctx.reason)
    return f"Deleted role {name!r}."


# ---------------------------------------------------------------------------
# members
# ---------------------------------------------------------------------------


@tool(
    "list_members",
    "List server members, optionally filtered by role or name.",
    params(
        role=string("Only members with this role."),
        query=string("Case-insensitive substring of username or display name."),
        include_bots=boolean("Include bots (default true)."),
        limit=integer("Max results (default 50, max 200)."),
    ),
)
async def list_members(ctx: ToolContext, args: dict) -> dict:
    members = ctx.find_role(args["role"]).members if args.get("role") else list(ctx.guild.members)
    if args.get("query"):
        q = args["query"].lower()
        members = [m for m in members if q in m.name.lower() or q in m.display_name.lower()]
    if args.get("include_bots") is False:
        members = [m for m in members if not m.bot]
    limit = max(1, min(int(args.get("limit") or 50), 200))
    return {"total": len(members), "members": [member_info(m) for m in members[:limit]]}


@tool("get_member", "Get details about one member.", params(["member"], member=string("Member name, mention or id.")))
async def get_member(ctx: ToolContext, args: dict) -> dict:
    member = await ctx.find_member(args["member"])
    info = member_info(member)
    info["top_role"] = member.top_role.name
    info["guild_permissions"] = [n for n, v in member.guild_permissions if v]
    return info


@tool(
    "add_roles",
    "Give one or more roles to a member.",
    params(["member", "roles"], member=string("Member name, mention or id."), roles=string_list("Roles to add.")),
)
async def add_roles(ctx: ToolContext, args: dict) -> str:
    member = await ctx.find_member(args["member"])
    roles = [ctx.find_role(r) for r in args["roles"]]
    await member.add_roles(*roles, reason=ctx.reason)
    return f"Added {', '.join(r.name for r in roles)} to {member.display_name}."


@tool(
    "remove_roles",
    "Remove one or more roles from a member.",
    params(["member", "roles"], member=string("Member name, mention or id."), roles=string_list("Roles to remove.")),
)
async def remove_roles(ctx: ToolContext, args: dict) -> str:
    member = await ctx.find_member(args["member"])
    roles = [ctx.find_role(r) for r in args["roles"]]
    await member.remove_roles(*roles, reason=ctx.reason)
    return f"Removed {', '.join(r.name for r in roles)} from {member.display_name}."


@tool(
    "set_nickname",
    "Set or clear a member's nickname.",
    params(["member"], member=string("Member name, mention or id."), nickname=string("New nickname, empty to clear.")),
)
async def set_nickname(ctx: ToolContext, args: dict) -> str:
    member = await ctx.find_member(args["member"])
    nick = args.get("nickname") or None
    await member.edit(nick=nick, reason=ctx.reason)
    return f"Nickname of {member.name} set to {nick!r}."


@tool(
    "timeout_member",
    "Time out a member (they cannot chat or join voice). Use minutes=0 to remove a timeout.",
    params(
        ["member", "minutes"],
        member=string("Member name, mention or id."),
        minutes=integer("Duration in minutes (max 40320 = 28 days). 0 removes the timeout."),
        reason=string("Reason for the audit log."),
    ),
    destructive=True,
)
async def timeout_member(ctx: ToolContext, args: dict) -> str:
    member = await ctx.find_member(args["member"])
    minutes = int(args["minutes"])
    reason = f"{args.get('reason') or 'No reason'} | {ctx.reason}"
    if minutes <= 0:
        await member.timeout(None, reason=reason)
        return f"Removed timeout from {member.display_name}."
    await member.timeout(dt.timedelta(minutes=min(minutes, 40320)), reason=reason)
    return f"Timed out {member.display_name} for {minutes} minutes."


@tool(
    "kick_member",
    "Kick a member from the server.",
    params(["member"], member=string("Member name, mention or id."), reason=string("Reason.")),
    destructive=True,
)
async def kick_member(ctx: ToolContext, args: dict) -> str:
    member = await ctx.find_member(args["member"])
    if member.id == ctx.guild.owner_id:
        raise ToolError("The server owner cannot be kicked.")
    await member.kick(reason=f"{args.get('reason') or 'No reason'} | {ctx.reason}")
    return f"Kicked {member}."


@tool(
    "ban_user",
    "Ban a user (by member reference or raw user id).",
    params(
        ["user"],
        user=string("Member name, mention or user id."),
        reason=string("Reason."),
        delete_message_days=integer("Delete this many days of their messages (0-7)."),
    ),
    destructive=True,
)
async def ban_user(ctx: ToolContext, args: dict) -> str:
    try:
        target: discord.abc.Snowflake = await ctx.find_member(args["user"])
    except ToolError:
        user_id = ctx._extract_id(args["user"])
        if not user_id:
            raise
        target = discord.Object(id=user_id)
    if target.id == ctx.guild.owner_id:
        raise ToolError("The server owner cannot be banned.")
    days = max(0, min(int(args.get("delete_message_days") or 0), 7))
    await ctx.guild.ban(
        target, reason=f"{args.get('reason') or 'No reason'} | {ctx.reason}", delete_message_seconds=days * 86400
    )
    return f"Banned {target}."


@tool(
    "unban_user",
    "Unban a user by id.",
    params(["user_id"], user_id=string("The banned user's id.")),
)
async def unban_user(ctx: ToolContext, args: dict) -> str:
    user_id = ctx._extract_id(args["user_id"])
    if not user_id:
        raise ToolError("Need a numeric user id.")
    await ctx.guild.unban(discord.Object(id=user_id), reason=ctx.reason)
    return f"Unbanned {user_id}."


@tool("list_bans", "List banned users.", params(limit=integer("Max results (default 50).")))
async def list_bans(ctx: ToolContext, args: dict) -> list[dict]:
    limit = max(1, min(int(args.get("limit") or 50), 500))
    return [
        {"id": str(entry.user.id), "user": str(entry.user), "reason": entry.reason}
        async for entry in ctx.guild.bans(limit=limit)
    ]


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------


@tool(
    "send_message",
    "Send a message (optionally with an embed) to a channel.",
    params(
        ["channel"],
        channel=string("Channel name, mention or id."),
        content=string("Plain message text."),
        embed_title=string("Embed title."),
        embed_description=string("Embed body text (markdown allowed)."),
        embed_color=string("Embed color (hex)."),
    ),
)
async def send_message(ctx: ToolContext, args: dict) -> str:
    channel = ctx.find_channel(args["channel"])
    if not isinstance(channel, discord.abc.Messageable):
        raise ToolError(f"#{channel.name} cannot receive messages.")
    embed = None
    if args.get("embed_title") or args.get("embed_description"):
        embed = discord.Embed(
            title=args.get("embed_title"),
            description=args.get("embed_description"),
            colour=parse_color(args.get("embed_color")) or discord.Colour.blurple(),
        )
    if not args.get("content") and embed is None:
        raise ToolError("Need content or an embed.")
    msg = await channel.send(content=args.get("content"), embed=embed)
    return f"Sent message {msg.id} to #{channel.name}."


@tool(
    "read_messages",
    "Read the most recent messages of a channel.",
    params(["channel"], channel=string("Channel name, mention or id."), limit=integer("How many (default 20, max 100).")),
)
async def read_messages(ctx: ToolContext, args: dict) -> list[dict]:
    channel = ctx.find_channel(args["channel"])
    if not hasattr(channel, "history"):
        raise ToolError(f"#{channel.name} has no message history.")
    limit = max(1, min(int(args.get("limit") or 20), 100))
    out = []
    async for msg in channel.history(limit=limit):
        out.append(
            {
                "id": str(msg.id),
                "author": str(msg.author),
                "content": msg.content[:1500],
                "created_at": msg.created_at.isoformat(),
                "attachments": [a.url for a in msg.attachments],
            }
        )
    return list(reversed(out))


@tool(
    "purge_messages",
    "Bulk delete recent messages in a channel.",
    params(
        ["channel", "limit"],
        channel=string("Channel name, mention or id."),
        limit=integer("How many recent messages to scan (max 500)."),
        from_user=string("Only delete messages from this member."),
    ),
    destructive=True,
)
async def purge_messages(ctx: ToolContext, args: dict) -> str:
    channel = ctx.find_channel(args["channel"])
    _console_guard(ctx, channel)
    if not hasattr(channel, "purge"):
        raise ToolError(f"#{channel.name} does not support purging.")
    check = None
    if args.get("from_user"):
        member = await ctx.find_member(args["from_user"])
        check = lambda m: m.author.id == member.id  # noqa: E731
    limit = max(1, min(int(args["limit"]), 500))
    deleted = await channel.purge(limit=limit, check=check, reason=ctx.reason) if check else await channel.purge(
        limit=limit, reason=ctx.reason
    )
    return f"Deleted {len(deleted)} messages in #{channel.name}."


@tool(
    "create_invite",
    "Create an invite link for a channel.",
    params(
        channel=string("Channel (default: first text channel)."),
        max_age_hours=integer("Expiry in hours, 0 = never (default 24)."),
        max_uses=integer("Max uses, 0 = unlimited."),
    ),
)
async def create_invite(ctx: ToolContext, args: dict) -> str:
    if args.get("channel"):
        channel = ctx.find_channel(args["channel"])
    else:
        channel = next((c for c in ctx.guild.text_channels if c.permissions_for(ctx.guild.me).create_instant_invite), None)
        if channel is None:
            raise ToolError("No channel available for invites.")
    hours = args.get("max_age_hours")
    invite = await channel.create_invite(
        max_age=int(hours if hours is not None else 24) * 3600, max_uses=int(args.get("max_uses") or 0), reason=ctx.reason
    )
    return invite.url


@tool(
    "create_thread",
    "Create a thread in a text channel (or a post in a forum channel).",
    params(
        ["channel", "name"],
        channel=string("Parent channel."),
        name=string("Thread name / post title."),
        content=string("First message (required for forum posts)."),
    ),
)
async def create_thread(ctx: ToolContext, args: dict) -> str:
    channel = ctx.find_channel(args["channel"])
    if isinstance(channel, discord.ForumChannel):
        if not args.get("content"):
            raise ToolError("Forum posts need content.")
        result = await channel.create_thread(name=args["name"], content=args["content"], reason=ctx.reason)
        return f"Created forum post {result.thread.name!r} ({result.thread.id})."
    if not isinstance(channel, discord.TextChannel):
        raise ToolError("Threads can only be created in text or forum channels.")
    thread = await channel.create_thread(
        name=args["name"], type=discord.ChannelType.public_thread, reason=ctx.reason
    )
    if args.get("content"):
        await thread.send(args["content"])
    return f"Created thread {thread.name!r} ({thread.id})."


@tool(
    "list_emojis",
    "List the server's custom emojis.",
)
async def list_emojis(ctx: ToolContext, args: dict) -> list[dict]:
    return [{"id": str(e.id), "name": e.name, "animated": e.animated} for e in ctx.guild.emojis]


@tool("list_permission_names", "List every valid permission name for roles and channel overwrites.")
async def list_permission_names(ctx: ToolContext, args: dict) -> list[str]:
    return PERMISSION_NAMES
