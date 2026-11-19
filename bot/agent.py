"""The LLM agent loop: turns console messages into tool calls and replies."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

import discord
import litellm

from .tools import Tool, ToolContext, ToolError
from .ui import ConfirmView, send_long

if TYPE_CHECKING:
    from .client import AIBot

log = logging.getLogger(__name__)

litellm.drop_params = True  # silently drop params a provider does not support
litellm.suppress_debug_info = True

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

CUSTOM_TOOLS_SECTION = """- You can extend yourself: when a task needs something the built-in tools cannot do (scheduled behaviour aside), write a new tool with create_custom_tool. Test it by calling it afterwards. Prefer small, focused tools. Existing custom tools: {names}.
"""


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


def _short_args(name: str, args: dict) -> str:
    shown = {k: v for k, v in args.items() if k != "code"}
    text = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in shown.items())
    return text if len(text) <= 160 else text[:157] + "..."


class Agent:
    def __init__(self, bot: "AIBot"):
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ---- prompt building --------------------------------------------------

    def _system_prompt(self, guild: discord.Guild) -> str:
        cfg = self.bot.config
        section = ""
        if cfg.enable_custom_tools:
            names = ", ".join(self.bot.custom_tools.tools) or "none yet"
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

    # ---- LLM call ---------------------------------------------------------

    async def _complete(self, model: str, messages: list[dict], tools: list[Tool]) -> Any:
        cfg = self.bot.config
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = [t.schema() for t in tools]
            kwargs["tool_choice"] = "auto"
        if cfg.llm_api_key:
            kwargs["api_key"] = cfg.llm_api_key
        if cfg.llm_api_base:
            kwargs["api_base"] = cfg.llm_api_base
        if cfg.llm_temperature is not None:
            kwargs["temperature"] = cfg.llm_temperature
        if cfg.llm_max_tokens:
            kwargs["max_tokens"] = cfg.llm_max_tokens
        return await litellm.acompletion(**kwargs)

    # ---- confirmation -----------------------------------------------------

    async def _confirm(self, ctx: ToolContext, tool: Tool, args: dict) -> bool:
        embed = discord.Embed(
            title=f"Confirm: {tool.name}",
            description=tool.description[:300],
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

        allowed = {ctx.author.id, ctx.guild.owner_id}
        view = ConfirmView(allowed)
        kwargs: dict[str, Any] = {"embed": embed, "view": view}
        if file:
            kwargs["file"] = file
        view.message = await ctx.channel.send(**kwargs)
        await view.wait()
        return view.approved

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
            if not await self._confirm(ctx, tool, args):
                return "The user denied this action. Do not retry it unless they ask again.", False, args

        try:
            result = await asyncio.wait_for(tool.handler(ctx, args), timeout=TOOL_TIMEOUT_SECONDS)
            return _serialize_result(result), True, args
        except ToolError as exc:
            return f"Error: {exc}", False, args
        except discord.Forbidden as exc:
            return (
                f"Error: Discord refused (missing permissions): {exc.text}. The target may be above the bot's top role.",
                False,
                args,
            )
        except discord.HTTPException as exc:
            return f"Error: Discord API error {exc.status}: {exc.text}", False, args
        except asyncio.TimeoutError:
            return f"Error: tool timed out after {TOOL_TIMEOUT_SECONDS}s.", False, args
        except KeyError as exc:
            return f"Error: missing required argument {exc}.", False, args
        except Exception as exc:  # noqa: BLE001 - surface any failure to the model
            log.exception("Tool %s crashed", name)
            return f"Error: {type(exc).__name__}: {exc}", False, args

    # ---- main entry -------------------------------------------------------

    async def handle(self, message: discord.Message) -> None:
        guild = message.guild
        assert guild is not None
        async with self._locks[guild.id]:
            await self._handle_locked(message, guild)

    async def _handle_locked(self, message: discord.Message, guild: discord.Guild) -> None:
        bot = self.bot
        cfg = bot.config
        settings = bot.storage.guild(guild.id)
        model = settings.model or cfg.llm_model
        author = message.author if isinstance(message.author, discord.Member) else await guild.fetch_member(message.author.id)

        is_bot_owner = await bot.is_owner(author)
        ctx = ToolContext(bot=bot, guild=guild, channel=message.channel, author=author)

        history = bot.storage.history(guild.id)
        new_messages: list[dict] = [{"role": "user", "content": self._user_content(message)}]

        for _round in range(cfg.max_tool_rounds):
            # Tools can be added mid-conversation (create_custom_tool), so rebuild every round.
            tools = bot.registry.available_for(is_bot_owner=is_bot_owner)
            allowed = {t.name: t for t in tools}
            prompt = [{"role": "system", "content": self._system_prompt(guild)}, *history, *new_messages]
            try:
                async with message.channel.typing():
                    response = await self._complete(model, prompt, tools)
            except Exception as exc:  # noqa: BLE001
                log.exception("LLM request failed")
                await message.channel.send(f"⚠️ LLM request failed ({model}): `{str(exc)[:1500]}`")
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
                    line = f"-# {mark} `{call.function.name}` {_short_args(call.function.name, args)}"
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
