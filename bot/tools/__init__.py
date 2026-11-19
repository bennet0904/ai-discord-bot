"""Tool registry combining built-in, meta and custom tools."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolError
from .builtin import BUILTIN_TOOLS
from .custom import CustomToolManager
from .meta import META_TOOLS

__all__ = ["Tool", "ToolContext", "ToolError", "ToolRegistry", "CustomToolManager"]


class ToolRegistry:
    def __init__(self, custom: CustomToolManager, enable_custom_tools: bool):
        self.custom = custom
        self.enable_custom_tools = enable_custom_tools
        self.builtin: dict[str, Tool] = {t.name: t for t in BUILTIN_TOOLS}
        if enable_custom_tools:
            self.builtin.update({t.name: t for t in META_TOOLS})
        self.reload_custom()

    @property
    def builtin_names(self) -> set[str]:
        # Meta tool names are always reserved, even when self-programming is disabled.
        return set(self.builtin) | {t.name for t in META_TOOLS}

    def reload_custom(self) -> None:
        if self.enable_custom_tools:
            self.custom.load_all(self.builtin_names)

    def all(self) -> list[Tool]:
        tools = list(self.builtin.values())
        if self.enable_custom_tools:
            tools.extend(self.custom.tools.values())
        return tools

    def available_for(self, *, is_bot_owner: bool) -> list[Tool]:
        return [t for t in self.all() if is_bot_owner or not t.bot_owner_only]

    def get(self, name: str) -> Tool | None:
        if name in self.builtin:
            return self.builtin[name]
        if self.enable_custom_tools:
            return self.custom.tools.get(name)
        return None
