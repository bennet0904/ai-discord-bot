"""Tools that let the LLM write, inspect and delete its own per-server tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Tool, ToolContext, boolean, params, string
from .custom import CustomToolRecord

if TYPE_CHECKING:
    from ..config import Config

ALLOWED_MODULES_TEXT = (
    "asyncio, base64, bisect, calendar, collections, copy, dataclasses, datetime, decimal, enum, fractions, "
    "functools, hashlib, heapq, html, itertools, json, math, operator, pprint, random, re, statistics, string, "
    "textwrap, time, typing, unicodedata, uuid"
)


def sandbox_doc(cfg: "Config") -> str:
    return (
        "The code must define `async def run(ctx, args)` and return a JSON-serialisable result. `args` is a dict "
        "matching your parameters schema. The code runs in an isolated sandbox: no files, no network, no "
        f"subprocesses, and only these modules: {ALLOWED_MODULES_TEXT}. It acts on Discord only through "
        "`await ctx.call(tool_name, {...args})`, which runs one of your built-in tools (create_role, list_members, "
        "send_message, ...) on this server and returns its result as JSON data; a failure raises ToolCallError "
        "(available without import). Also available: ctx.guild_id, ctx.guild_name, ctx.author_id, ctx.author_name. "
        "print() output is returned as logs. Destructive built-in calls ask the user for approval unless the "
        f"custom tool itself is marked destructive. Limits per run: {cfg.sandbox_cpu_seconds}s CPU, "
        f"{cfg.sandbox_memory_mb} MB memory, {cfg.sandbox_timeout_seconds}s total, {cfg.sandbox_max_calls} tool calls."
    )


async def create_custom_tool(ctx: ToolContext, args: dict) -> str:
    bot = ctx.bot
    cfg = bot.config
    record = CustomToolRecord(
        name=args["name"],
        description=args["description"],
        parameters=args.get("parameters") or {"type": "object", "properties": {}},
        destructive=bool(args.get("destructive", False)),
        code=args["code"],
        author_id=ctx.author.id,
    )
    replaced = bot.custom_tools.save(
        ctx.guild.id,
        record,
        reserved=bot.registry.reserved_names,
        max_tools=cfg.max_custom_tools,
        max_code_chars=cfg.max_custom_tool_code_chars,
    )
    verb = "Updated" if replaced else "Created"
    return f"{verb} custom tool {record.name!r}. It can be called from your next step on. Test it now."


async def list_custom_tools(ctx: ToolContext, args: dict) -> list[dict]:
    return [
        {"name": r.name, "description": r.description, "destructive": r.destructive, "updated_at": r.updated_at}
        for r in ctx.bot.custom_tools.records(ctx.guild.id).values()
    ]


async def view_custom_tool(ctx: ToolContext, args: dict) -> dict:
    record = ctx.bot.custom_tools.get(ctx.guild.id, args["name"])
    return {"name": record.name, "description": record.description, "parameters": record.parameters,
            "destructive": record.destructive, "code": record.code}


async def delete_custom_tool(ctx: ToolContext, args: dict) -> str:
    ctx.bot.custom_tools.delete(ctx.guild.id, args["name"])
    return f"Deleted custom tool {args['name']!r}."


def build_meta_tools(cfg: "Config") -> list[Tool]:
    return [
        Tool(
            "create_custom_tool",
            "Program a new tool for this server (or overwrite one of its custom tools) in Python. Use it when the "
            "user wants something the built-in tools cannot do in one step, or asks you to build a feature. "
            "Custom tools are stored per server and survive restarts. " + sandbox_doc(cfg),
            params(
                ["name", "description", "code"],
                name=string("snake_case tool name, e.g. 'setup_gaming_section'."),
                description=string("What the tool does, written for your future self."),
                parameters={"type": "object", "description": "JSON schema (type 'object') for the tool's arguments."},
                code=string("Python source defining `async def run(ctx, args)`."),
                destructive=boolean("True if every run should be approved by the user first."),
            ),
            create_custom_tool,
            destructive=True,
        ),
        Tool("list_custom_tools", "List this server's custom tools.", params(), list_custom_tools),
        Tool(
            "view_custom_tool",
            "Show the definition and code of one of this server's custom tools.",
            params(["name"], name=string("Custom tool name.")),
            view_custom_tool,
        ),
        Tool(
            "delete_custom_tool",
            "Delete one of this server's custom tools.",
            params(["name"], name=string("Custom tool name.")),
            delete_custom_tool,
            destructive=True,
        ),
    ]


META_TOOL_NAMES = {"create_custom_tool", "list_custom_tools", "view_custom_tool", "delete_custom_tool"}
