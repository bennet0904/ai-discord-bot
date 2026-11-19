"""Core types shared by built-in tools and custom (self-programmed) tools."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import discord

if TYPE_CHECKING:
    from ..client import AIBot

_ID_RE = re.compile(r"(\d{15,21})")


class ToolError(Exception):
    """Raised by a tool to report a user-facing problem back to the LLM."""


Handler = Callable[["ToolContext", dict], Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Handler
    destructive: bool = False
    bot_owner_only: bool = False
    source: str = "builtin"  # "builtin" or "custom"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def params(required: list[str] | None = None, **properties: dict) -> dict:
    """Build a JSON schema object for tool parameters."""
    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def string(description: str, enum: list[str] | None = None) -> dict:
    out = {"type": "string", "description": description}
    if enum:
        out["enum"] = enum
    return out


def integer(description: str) -> dict:
    return {"type": "integer", "description": description}


def boolean(description: str) -> dict:
    return {"type": "boolean", "description": description}


def string_list(description: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": description}


@dataclass
class ToolContext:
    """Everything a tool needs to act on the server.

    Custom tools receive this object as `ctx`. Useful attributes:
      ctx.bot      the running discord.py bot (commands.Bot subclass)
      ctx.guild    the discord.Guild the console belongs to
      ctx.channel  the console channel the request came from
      ctx.author   the discord.Member who sent the request
      ctx.reason   a string to pass as `reason=` for the audit log
    Helpers: ctx.find_channel(ref), ctx.find_role(ref), await ctx.find_member(ref),
    await ctx.find_target(ref) (role or member).
    """

    bot: "AIBot"
    guild: discord.Guild
    channel: discord.abc.Messageable
    author: discord.Member
    extra: dict = field(default_factory=dict)

    @property
    def reason(self) -> str:
        return f"Requested by {self.author} ({self.author.id}) via AI console"

    # ---- resolvers ------------------------------------------------------

    @staticmethod
    def _extract_id(ref: Any) -> int | None:
        match = _ID_RE.search(str(ref))
        return int(match.group(1)) if match else None

    def find_channel(self, ref: Any) -> discord.abc.GuildChannel:
        if ref is None or str(ref).strip() == "":
            raise ToolError("No channel given.")
        channel_id = self._extract_id(ref)
        if channel_id:
            channel = self.guild.get_channel_or_thread(channel_id)
            if channel:
                return channel
        name = str(ref).strip().lstrip("#").lower()
        for channel in self.guild.channels:
            if channel.name.lower() == name:
                return channel
        raise ToolError(f"Channel not found: {ref!r}. Use list_channels to see what exists.")

    def find_category(self, ref: Any) -> discord.CategoryChannel | None:
        if ref is None or str(ref).strip() == "":
            return None
        channel = self.find_channel(ref)
        if not isinstance(channel, discord.CategoryChannel):
            raise ToolError(f"{channel.name!r} is not a category.")
        return channel

    def find_role(self, ref: Any) -> discord.Role:
        if ref is None or str(ref).strip() == "":
            raise ToolError("No role given.")
        text = str(ref).strip()
        if text.lower() in {"@everyone", "everyone"}:
            return self.guild.default_role
        role_id = self._extract_id(text)
        if role_id:
            role = self.guild.get_role(role_id)
            if role:
                return role
        name = text.lstrip("@").lower()
        for role in self.guild.roles:
            if role.name.lower() == name:
                return role
        raise ToolError(f"Role not found: {ref!r}. Use list_roles to see what exists.")

    async def find_member(self, ref: Any) -> discord.Member:
        if ref is None or str(ref).strip() == "":
            raise ToolError("No member given.")
        member_id = self._extract_id(ref)
        if member_id:
            member = self.guild.get_member(member_id)
            if member:
                return member
            try:
                return await self.guild.fetch_member(member_id)
            except discord.NotFound:
                raise ToolError(f"No member with id {member_id} on this server.") from None
        name = str(ref).strip().lstrip("@").lower()
        for member in self.guild.members:
            candidates = {member.name.lower(), member.display_name.lower()}
            if member.global_name:
                candidates.add(member.global_name.lower())
            if name in candidates:
                return member
        raise ToolError(f"Member not found: {ref!r}. Use list_members to search.")

    async def find_target(self, ref: Any) -> discord.Role | discord.Member:
        """Resolve a role or a member (roles take precedence for names)."""
        text = str(ref).strip()
        if text.startswith("<@&"):
            return self.find_role(text)
        if text.startswith("<@"):
            return await self.find_member(text)
        try:
            return self.find_role(text)
        except ToolError:
            pass
        try:
            return await self.find_member(text)
        except ToolError:
            raise ToolError(f"No role or member matches {ref!r}.") from None
