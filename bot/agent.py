"""The LLM agent loop: turns console messages into tool calls and replies."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

import discord

from . import llm
from .sandbox.host import SandboxError
from .tools import CustomToolRecord, Tool, ToolContext, ToolError
from .ui import ConfirmView, send_long

if TYPE_CHECKING:
    from .client import AIBot

log = logging.getLogger(__name__)

TOOL_TIMEOUT_SECONDS = 120
MAX_TOOL_RESULT_CHARS = 12000

SYSTEM_PROMPT = """You are the administrator assistant of the Discord server "{guild_name}" (id {guild_id}).
You talk to trusted server staff in a private console channel and you control the server through tools.
The bot you act through has administrator permissions, but it cannot touch roles at or above its own top role ("{bot_top_role}").

How to work:
- Act on requests directly with the tools. Look things up first (list_channels, list_roles, list_members) instead of guessing ids.
- When a request is ambiguous and the action is hard to undo, ask a short clarifying question first.
- Destructive tools may show the user an Approve/Deny prompt. If a tool returns that the user denied it, accept that and do not retry.
- If a tool fails, read the error, fix the arguments and try again, or explain the problem.
- Keep replies short and concrete. Report what you changed. Use Discord markdown.
{custom_tools_section}
The server owner is {owner}. The current speaker is given at the start of every user message.{extra}"""

CUSTOM_TOOLS_SECTION = """- You can extend yourself: when a task needs logic the built-in tools cannot do in one step, write a tool for this server with create_custom_tool, then call it to test it. Custom tools run in a sandbox and act on Discord by calling your built-in tools. This server's custom tools: {names}.
"""

NO_LLM_MESSAGE = (
    "⚙️ No LLM is connected to this server yet. The server owner has to run `/ai llm` and enter a model and "
    "their own API key. Supported: {examples}, any OpenAI-compatible server and more."
)


def _trim_history(messages: list[dict], max_messages: int) -> list[dict]:
    if len(messages) <= max_messages:
        return messages
    trimmed = messages[-max_messages:]
    # Never start in the middle of a tool exchange: drop until the first user message.
    while trimmed and trimmed[0].get("role") != "user":
        trimmed.pop(0)
    return trimmed


def _serialize_result(result: Any) -> str:
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(result)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + f"... [truncated, {len(text)} chars total]"
    return text or "(empty result)"


def _short_args(args: dict) -> str:
    shown = {k: v for k, v in args.items() if k != "code"}
    text = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in shown.items())
    return text if len(text) <= 160 else text[:157] + "..."


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, ToolError):
        return str(exc)
    if isinstance(exc, discord.Forbidden):
        return f"Discord refused (missing permissions): {exc.text}. The target may be above the bot's top role."
    if isinstance(exc, discord.HTTPException):
        return f"Discord API error {exc.status}: {exc.text}"
    if isinstance(exc, asyncio.TimeoutError):
        return f"timed out after {TOOL_TIMEOUT_SECONDS}s"
    if isinstance(exc, KeyError):
        return f"missing required argument {exc}"
    if isinstance(exc, SandboxError):
        return f"custom tool failed: {exc}"
    return f"{type(exc).__name__}: {exc}"


class Agent:
    def __init__(self, bot: "AIBot"):
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ---- settings ---------------------------------------------------------

    def llm_settings(self, guild_id: int) -> llm.LLMSettings | None:
        settings = self.bot.storage.guild(guild_id)
        if not settings.llm_model:
            return None
        api_key = None
        if settings.llm_api_key_encrypted:
            api_key = self.bot.keybox.decrypt(settings.llm_api_key_encrypted)
            if api_key is None:
                return None
        return llm.LLMSettings(model=settings.llm_model, api_key=api_key, api_base=settings.llm_api_base)

    # ---- prompt building --------------------------------------------------

    def _system_prompt(self, guild: discord.Guild) -> str:
        cfg = self.bot.config
        section = ""
        if cfg.enable_custom_tools:
            names = ", ".join(self.bot.custom_tools.records(guild.id)) or "none yet"
            section = CUSTOM_TOOLS_SECTION.format(names=names)
        extra = f"\n\n{cfg.system_prompt_extra}" if cfg.system_prompt_extra else ""
        owner = guild.owner or guild.get_member(guild.owner_id)
        return SYSTEM_PROMPT.format(
            guild_name=guild.name,
            guild_id=guild.id,
            bot_top_role=guild.me.top_role.name,
            custom_tools_section=section,
            owner=f"{owner} (id {guild.owner_id})" if owner else f"id {guild.owner_id}",
            extra=extra,
        )

    @staticmethod
    def _user_content(message: discord.Message) -> str:
        author = message.author
        role = "server owner" if message.guild and author.id == message.guild.owner_id else "staff"
        text = message.content
        if message.attachments:
            text += "\n\nAttachments:\n" + "\n".join(f"- {a.filename}: {a.url}" for a in message.attachments)
        return f"[{author.display_name} | @{author.name} | id {author.id} | {role}]\n{text}"

    # ---- confirmation -----------------------------------------------------

    async def confirm(self, ctx: ToolContext, tool: Tool, args: dict, note: str | None = None) -> bool:
        embed = discord.Embed(
            title=f"Confirm: {tool.name}",
            description=(note + "\n\n" if note else "") + tool.description[:300],
            colour=discord.Colour.orange(),
        )
        file = None
        if tool.name == "create_custom_tool":
            code = args.get("code", "")
            embed.add_field(name="Tool name", value=f"`{args.get('name')}`", inline=True)
            embed.add_field(name="Destructive", value=str(bool(args.get("destructive"))), inline=True)
            embed.add_field(name="Description", value=str(args.get("description", ""))[:1000] or "-", inline=False)
            preview = code if len(code) <= 900 else code[:900] + "\n# ... (full code attached)"
            embed.add_field(name="Code", value=f"```py\n{preview}\n```", inline=False)
            if len(code) > 900:
                file = discord.File(io.BytesIO(code.encode()), filename=f"{args.get('name', 'tool')}.py")
        else:
            for key, value in list(args.items())[:15]:
                embed.add_field(name=key[:100], value=str(value)[:300] or "-", inline=True)

        view = ConfirmView({ctx.author.id, ctx.guild.owner_id})
        kwargs: dict[str, Any] = {"embed": embed, "view": view}
        if file:
            kwargs["file"] = file
        view.message = await ctx.channel.send(**kwargs)
        await view.wait()
        return view.approved

    # ---- custom tools -----------------------------------------------------

    def make_custom_handler(self, record: CustomToolRecord):
        async def handler(ctx: ToolContext, args: dict) -> Any:
            return await self.run_custom_tool(ctx, record, args)

        return handler

    async def run_custom_tool(self, ctx: ToolContext, record: CustomToolRecord, args: dict) -> Any:
        bot = self.bot

        async def on_call(name: str, call_args: dict) -> Any:
            tool = bot.registry.discord_tools.get(name)
            if tool is None:
                raise ToolError(f"Unknown built-in tool {name!r}. Custom tools can only call built-in Discord tools.")
            if tool.destructive and bot.config.require_confirmation and not record.destructive:
                note = f"Requested by custom tool `{record.name}`."
                if not await self.confirm(ctx, tool, call_args, note=note):
                    raise ToolError("The user denied this action.")
            try:
                return await asyncio.wait_for(tool.handler(ctx, call_args), timeout=TOOL_TIMEOUT_SECONDS)
            except Exception as exc:  # noqa: BLE001
                raise ToolError(describe_error(exc)) from None

        context = {
            "guild_id": str(ctx.guild.id),
            "guild_name": ctx.guild.name,
            "author_id": str(ctx.author.id),
            "author_name": str(ctx.author),
        }
        out = await bot.sandbox.run(record.code, args, context, on_call)
        if out.get("logs"):
            return {"result": out.get("result"), "logs": out["logs"]}
        return out.get("result")

    # ---- tool execution ---------------------------------------------------

    async def _run_tool(self, ctx: ToolContext, allowed: dict[str, Tool], name: str, raw_args: str) -> tuple[str, bool, dict]:
        tool = allowed.get(name)
        if tool is None:
            return f"Error: unknown or unavailable tool {name!r}.", False, {}
        try:
            args = json.loads(raw_args) if raw_args and raw_args.strip() else {}
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as exc:
            return f"Error: could not parse arguments as JSON ({exc}).", False, {}

        if tool.destructive and self.bot.config.require_confirmation:
            if not await self.confirm(ctx, tool, args):
                return "The user denied this action. Do not retry it unless they ask again.", False, args

        # Custom tools have their own (longer) sandbox timeout.
        timeout = None if tool.source == "custom" else TOOL_TIMEOUT_SECONDS
        try:
            result = await asyncio.wait_for(tool.handler(ctx, args), timeout=timeout)
            return _serialize_result(result), True, args
        except Exception as exc:  # noqa: BLE001 - surface any failure to the model
            if not isinstance(exc, (ToolError, discord.HTTPException, SandboxError, KeyError, asyncio.TimeoutError)):
                log.exception("Tool %s crashed", name)
            return f"Error: {describe_error(exc)}", False, args

    # ---- main entry -------------------------------------------------------

    async def handle(self, message: discord.Message) -> None:
        guild = message.guild
        assert guild is not None
        async with self._locks[guild.id]:
            await self._handle_locked(message, guild)

    async def _handle_locked(self, message: discord.Message, guild: discord.Guild) -> None:
        bot = self.bot
        cfg = bot.config
        settings = self.llm_settings(guild.id)
        if settings is None:
            await message.channel.send(NO_LLM_MESSAGE.format(examples=llm.MODEL_EXAMPLES))
            return

        author = message.author if isinstance(message.author, discord.Member) else await guild.fetch_member(message.author.id)
        ctx = ToolContext(bot=bot, guild=guild, channel=message.channel, author=author)

        history = bot.storage.history(guild.id)
        new_messages: list[dict] = [{"role": "user", "content": self._user_content(message)}]

        for _round in range(cfg.max_tool_rounds):
            # Tools can be added mid-conversation (create_custom_tool), so rebuild every round.
            allowed = bot.registry.for_guild(guild.id)
            prompt = [{"role": "system", "content": self._system_prompt(guild)}, *history, *new_messages]
            try:
                async with message.channel.typing():
                    response = await llm.complete(
                        settings, prompt, [t.schema() for t in allowed.values()], cfg.llm_timeout_seconds
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("LLM request failed in guild %s: %s", guild.id, type(exc).__name__)
                await message.channel.send(f"⚠️ LLM request failed ({settings.model}): `{str(exc)[:1500]}`")
                return  # nothing from this turn is stored, so the history stays consistent

            choice = response.choices[0].message
            content = (choice.content or "").strip()
            tool_calls = list(choice.tool_calls or [])

            assistant: dict[str, Any] = {"role": "assistant", "content": content or None}
            if tool_calls:
                assistant["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.function.name, "arguments": call.function.arguments or "{}"},
                    }
                    for call in tool_calls
                ]
            new_messages.append(assistant)

            if content:
                await send_long(message.channel, content)

            if not tool_calls:
                break

            for call in tool_calls:
                result, ok, args = await self._run_tool(ctx, allowed, call.function.name, call.function.arguments)
                new_messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                if cfg.show_tool_calls:
                    mark = "✅" if ok else "❌"
                    line = f"-# {mark} `{call.function.name}` {_short_args(args)}"
                    if not ok:
                        line += f"\n-# {result[:300]}"
                    await message.channel.send(line[:1990], allowed_mentions=discord.AllowedMentions.none())
        else:
            await message.channel.send(
                f"⚠️ Stopped after {cfg.max_tool_rounds} tool rounds. Send another message to let me continue."
            )
            new_messages.append(
                {"role": "assistant", "content": f"(Stopped after {cfg.max_tool_rounds} tool rounds before finishing.)"}
            )

        bot.storage.set_history(guild.id, _trim_history(history + new_messages, cfg.max_history_messages))
