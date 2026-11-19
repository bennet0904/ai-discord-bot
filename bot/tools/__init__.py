"""Tool registry: built-in tools for everyone, custom tools per server."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Tool, ToolContext, ToolError
from .builtin import BUILTIN_TOOLS
from .custom import CustomToolRecord, CustomToolStore
from .meta import META_TOOL_NAMES, build_meta_tools

if TYPE_CHECKING:
    from ..config import Config

__all__ = ["Tool", "ToolContext", "ToolError", "ToolRegistry", "CustomToolStore", "CustomToolRecord"]


class ToolRegistry:
    def __init__(self, config: "Config", custom: CustomToolStore, enable_custom_tools: bool):
        self.custom = custom
        self.enable_custom_tools = enable_custom_tools
        # Tools custom code may call through ctx.call(): the plain Discord tools.
        self.discord_tools: dict[str, Tool] = {t.name: t for t in BUILTIN_TOOLS}
        self.builtin: dict[str, Tool] = dict(self.discord_tools)
        if enable_custom_tools:
            self.builtin.update({t.name: t for t in build_meta_tools(config)})

    @property
    def reserved_names(self) -> set[str]:
        return set(self.discord_tools) | META_TOOL_NAMES

    def for_guild(self, guild_id: int) -> dict[str, Tool]:
        tools = dict(self.builtin)
        if self.enable_custom_tools:
            for tool in self.custom.tools(guild_id):
                tools.setdefault(tool.name, tool)
        return tools
