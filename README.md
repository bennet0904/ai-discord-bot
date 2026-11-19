# AI Discord Admin Bot

A Discord bot that lets you run your server by chatting with an LLM.

When the bot joins a server it creates a private channel (`#ai-console` by default) that only the server owner can see. Every message written there goes to the LLM, which can then act on the server through tools. You can ask it things like "create a category called Gaming with two text channels and one voice channel", "make a Moderator role in blue that can kick and time out members" or "delete all channels that start with test". The owner can give other people access to the console.

The assistant can also write new tools for itself in Python. If you ask for something the built-in tools can't do, it writes a tool, saves it to `plugins/` and uses it right away. Those tools are kept across restarts.

It works with any LLM that [LiteLLM](https://docs.litellm.ai/docs/providers) supports and that can do tool calling. That includes Anthropic, OpenAI, Google Gemini, Mistral, Groq, DeepSeek, xAI, OpenRouter, Azure, AWS Bedrock, Ollama and any OpenAI-compatible server.

## What it can do

Built-in tools:

| Area | Tools |
| --- | --- |
| Server | `get_server_info`, `edit_server` |
| Channels | `list_channels`, `create_channel` (text, voice, category, forum, stage, announcement, private), `edit_channel`, `delete_channel`, `set_channel_permissions`, `create_thread` |
| Roles | `list_roles`, `create_role`, `edit_role`, `delete_role`, `list_permission_names` |
| Members | `list_members`, `get_member`, `add_roles`, `remove_roles`, `set_nickname`, `timeout_member`, `kick_member`, `ban_user`, `unban_user`, `list_bans` |
| Messages | `send_message` (with embeds), `read_messages`, `purge_messages`, `create_invite`, `list_emojis` |
| Self-programming | `create_custom_tool`, `list_custom_tools`, `view_custom_tool`, `delete_custom_tool`, `reload_custom_tools` |

Destructive tools (deleting channels or roles, kick, ban, timeout, purge, writing code) post an Approve / Deny prompt in the console first. Only the person who asked and the server owner can press the buttons. You can turn this off with `REQUIRE_CONFIRMATION=false`.

Every action shows up in the Discord audit log with the name of the person who asked for it.

## Setup

### 1. Create the Discord application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application.
2. Open **Bot**, click **Reset Token** and copy the token.
3. On the same page enable both **Privileged Gateway Intents**: **Server Members Intent** and **Message Content Intent**.

### 2. Configure

```bash
cp .env.example .env
```

Fill in at least `DISCORD_TOKEN`, `LLM_MODEL` and the API key of your provider. Some examples for `LLM_MODEL`:

```
anthropic/claude-sonnet-5-5
openai/gpt-4o
gemini/gemini-2.5-pro
mistral/mistral-large-latest
groq/llama-3.3-70b-versatile
openrouter/<vendor>/<model>
ollama_chat/llama3.1          # together with LLM_API_BASE=http://localhost:11434
```

For a self-hosted OpenAI-compatible server (LM Studio, vLLM, LocalAI) use `openai/<model-name>` and set `LLM_API_BASE` and, if needed, `LLM_API_KEY`.

### 3. Run

With Python 3.10 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m bot
```

Or with Docker:

```bash
docker compose up -d --build
```

On startup the bot logs an invite link that asks for administrator permission. Open it and add the bot to your server. The console channel is created right away and the owner gets a welcome message there.

### 4. Role hierarchy

Discord doesn't let a bot change roles that are at or above its own highest role, even with administrator permission. Open **Server Settings → Roles** and drag the bot's role to the top if it should be able to manage every role.

## Slash commands

| Command | Who | Purpose |
| --- | --- | --- |
| `/ai setup` | owner | Create the console channel again or repair its permissions |
| `/ai grant @user` | owner | Give someone access to the console |
| `/ai revoke @user` | owner | Remove access |
| `/ai users` | console users | Show who has access |
| `/ai model [name]` | owner to change, users to view | Use a different LLM on this server (`default` resets it) |
| `/ai reset` | console users | Clear the conversation memory |
| `/ai status` | console users | Show model, tools and settings |

## Custom tools (self-programming)

When the assistant creates a tool, it writes a file like this to `plugins/`:

```python
TOOL_SPEC = {
    "name": "count_bots",
    "description": "Count the bots on the server.",
    "parameters": {"type": "object", "properties": {}},
    "destructive": False,
}

async def run(ctx, args):
    return sum(m.bot for m in ctx.guild.members)
```

`ctx` gives access to `ctx.bot`, `ctx.guild`, `ctx.channel`, `ctx.author`, `ctx.reason` and the helpers `ctx.find_channel()`, `ctx.find_role()`, `await ctx.find_member()` and `await ctx.find_target()`. You can also write or edit these files by hand and then ask the assistant to run `reload_custom_tools`, or restart the bot. `plugins/example_server_stats.py` is a working example.

### Security

Custom tools are real Python code that runs inside the bot process with full access to the host and to the bot token. For that reason:

* Only the **bot owner** can trigger code creation. By default that is the owner of the Discord application (or its team members). You can set other ids with `BOT_OWNER_IDS`. A server owner who is not the bot owner never sees these tools.
* Each new or changed tool needs approval through the Approve / Deny buttons, unless confirmations are turned off.
* Custom tools are global. A tool written on one server is available on every server the bot is in.
* If other people run the bot on servers you don't trust, or you don't want this feature at all, set `ENABLE_CUSTOM_TOOLS=false`.

Also keep in mind that anyone with console access can do almost anything to the server that an administrator can, so only grant access to people you trust.

## Configuration reference

All settings are environment variables (see `.env.example`).

| Variable | Default | Meaning |
| --- | --- | --- |
| `DISCORD_TOKEN` | | Bot token (required) |
| `LLM_MODEL` | | LiteLLM model string (required) |
| `LLM_API_KEY`, `LLM_API_BASE` | | Override key and endpoint |
| `LLM_TEMPERATURE`, `LLM_MAX_TOKENS` | provider default | Sampling settings |
| `CONSOLE_CHANNEL_NAME` | `ai-console` | Name of the private channel |
| `REQUIRE_CONFIRMATION` | `true` | Approve / Deny buttons for destructive tools |
| `ENABLE_CUSTOM_TOOLS` | `true` | Allow self-programming |
| `SHOW_TOOL_CALLS` | `true` | Post a short line for every tool call |
| `MAX_TOOL_ROUNDS` | `20` | Max LLM round trips per message |
| `MAX_HISTORY_MESSAGES` | `60` | Conversation memory per server |
| `BOT_OWNER_IDS` | application owner | Who may create custom tools |
| `SYSTEM_PROMPT_EXTRA` | | Extra instructions for the assistant |
| `DATA_DIR`, `PLUGINS_DIR` | `data`, `plugins` | Storage locations |

## Project layout

```
bot/
  __main__.py       entry point (python -m bot)
  client.py         discord.py bot and events
  agent.py          LLM loop, tool execution, confirmations
  console.py        private console channel and access list
  commands.py       /ai slash commands
  storage.py        JSON persistence (data/)
  ui.py             buttons and message splitting
  tools/
    base.py         Tool type, ToolContext and lookups
    builtin.py      Discord tools
    meta.py         tools for writing tools
    custom.py       loading and saving plugins/
plugins/            tools written by the assistant
```

## Notes

* Conversation history is stored per server in `data/history/`. Use `/ai reset` when the assistant gets confused or when you start a new topic.
* If you delete the console channel, it is recreated on the next restart or with `/ai setup`. To get rid of it for good, remove the bot from the server.
* The bot has to be able to see the console channel, so don't remove its permission overwrite there.
