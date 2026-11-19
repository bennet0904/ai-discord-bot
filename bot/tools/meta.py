"""Tools that let the LLM extend itself by writing new tools in Python."""

from __future__ import annotations

from .base import Tool, ToolContext, boolean, params, string

CTX_DOC = (
    "The code must define `async def run(ctx, args)` and return a JSON-serialisable result (usually a string). "
    "`args` is a dict matching your parameters schema. `ctx` provides: ctx.bot (discord.py commands.Bot), "
    "ctx.guild (discord.Guild), ctx.channel (the console channel), ctx.author (discord.Member who asked), "
    "ctx.reason (string for audit-log `reason=`), ctx.find_channel(ref), ctx.find_role(ref), "
    "await ctx.find_member(ref), await ctx.find_target(ref). Raise bot.tools.base.ToolError (or any exception) "
    "to report a failure. You may import any installed module (discord, aiohttp, asyncio, json, ...). "
    "Use discord.py 2.x APIs. Keep it async, do not block the event loop."
)


async def create_custom_tool(ctx: ToolContext, args: dict) -> str:
    manager = ctx.bot.custom_tools
    name = args["name"]
    existed = name in manager.tools
    tool = manager.save(
        name=name,
        description=args["description"],
        parameters=args.get("parameters") or {"type": "object", "properties": {}},
        destructive=bool(args.get("destructive", False)),
        code=args["code"],
        reserved=ctx.bot.registry.builtin_names,
    )
    verb = "Updated" if existed else "Created"
    return f"{verb} custom tool {tool.name!r}. It is available from your next step on."


async def list_custom_tools(ctx: ToolContext, args: dict) -> dict:
    manager = ctx.bot.custom_tools
    return {
        "tools": [{"name": t.name, "description": t.description, "destructive": t.destructive} for t in manager.tools.values()],
        "failed_to_load": manager.errors,
    }


async def view_custom_tool(ctx: ToolContext, args: dict) -> str:
    return ctx.bot.custom_tools.source(args["name"])


async def delete_custom_tool(ctx: ToolContext, args: dict) -> str:
    ctx.bot.custom_tools.delete(args["name"])
    return f"Deleted custom tool {args['name']!r}."


async def reload_custom_tools(ctx: ToolContext, args: dict) -> dict:
    ctx.bot.registry.reload_custom()
    return await list_custom_tools(ctx, args)


def _code_tool(name: str, description: str, parameters: dict, handler, destructive: bool = True) -> Tool:
    return Tool(name, description, parameters, handler, destructive=destructive, bot_owner_only=True)


META_TOOLS: list[Tool] = [
    _code_tool(
        "create_custom_tool",
        "Program a new tool for yourself (or overwrite an existing custom tool) in Python. Use this when the user "
        "wants a capability the built-in tools lack, or asks you to build a feature. The tool is saved to disk, "
        "survives restarts and becomes callable right away. " + CTX_DOC,
        params(
            ["name", "description", "code"],
            name=string("snake_case tool name, e.g. 'create_welcome_embed'."),
            description=string("What the tool does, written for your future self."),
            parameters={
                "type": "object",
                "description": "JSON schema (type 'object') for the tool's arguments.",
            },
            code=string("Python source defining `async def run(ctx, args)` plus any imports and helpers."),
            destructive=boolean("True if calls should require user confirmation."),
        ),
        create_custom_tool,
    ),
    _code_tool(
        "list_custom_tools",
        "List custom tools you have written, including ones that failed to load.",
        params(),
        list_custom_tools,
        destructive=False,
    ),
    _code_tool(
        "view_custom_tool",
        "Show the source code of a custom tool.",
        params(["name"], name=string("Custom tool name.")),
        view_custom_tool,
        destructive=False,
    ),
    _code_tool(
        "delete_custom_tool",
        "Delete a custom tool permanently.",
        params(["name"], name=string("Custom tool name.")),
        delete_custom_tool,
    ),
    _code_tool(
        "reload_custom_tools",
        "Reload all custom tools from disk (after manual edits).",
        params(),
        reload_custom_tools,
        destructive=False,
    ),
]

