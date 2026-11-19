# AI Discord Admin Bot

A Discord bot that lets server owners run their server by chatting with an LLM.

When the bot joins a server it creates a private channel (`#ai-console` by default) that only the server owner can see. Every message written there goes to the LLM, which acts on the server through tools. You can ask it things like "create a category called Gaming with two text channels and one voice channel", "make a Moderator role in blue that can kick and time out members" or "delete all channels that start with test". The owner can give other people access to the console.

Every server brings its own LLM. The owner connects a model and their own API key with `/ai llm`, so each server pays for its own usage. The bot host doesn't provide any LLM access.

Every server also has its own tools. If you ask for something the built-in tools can't do in one step, the assistant writes a new tool in Python for your server. Other servers never see it. These tools run in an isolated sandbox (see [Security](#security)).

Any provider supported by [LiteLLM](https://docs.litellm.ai/docs/providers) that uses an API key and supports tool calling works. That includes Anthropic, OpenAI, Google Gemini, Mistral, Groq, DeepSeek, xAI, OpenRouter, Azure OpenAI and any OpenAI-compatible server.

## What it can do

Built-in tools:

| Area | Tools |
| --- | --- |
| Server | `get_server_info`, `edit_server` |
| Channels | `list_channels`, `create_channel` (text, voice, category, forum, stage, announcement, private), `edit_channel`, `delete_channel`, `set_channel_permissions`, `create_thread` |
| Roles | `list_roles`, `create_role`, `edit_role`, `delete_role`, `list_permission_names` |
| Members | `list_members`, `get_member`, `add_roles`, `remove_roles`, `set_nickname`, `timeout_member`, `kick_member`, `ban_user`, `unban_user`, `list_bans` |
| Messages | `send_message` (with embeds), `read_messages`, `purge_messages`, `create_invite`, `list_emojis` |
| Own tools | `create_custom_tool`, `list_custom_tools`, `view_custom_tool`, `delete_custom_tool` |

Destructive tools (deleting channels or roles, kick, ban, timeout, purge, writing tools) post an Approve / Deny prompt in the console first. Only the person who asked and the server owner can press the buttons. The bot host can turn this off with `REQUIRE_CONFIRMATION=false`.

Every action shows up in the Discord audit log with the name of the person who asked for it.

## For server owners

1. Invite the bot. The host gives you the link.
2. Open the new `#ai-console` channel.
3. Run `/ai llm` and fill in the form:
   * **Model** in the form `provider/model`, for example `anthropic/claude-sonnet-5-5`, `openai/gpt-4o`, `gemini/gemini-2.5-pro`, `mistral/mistral-large-latest`, `groq/llama-3.3-70b-versatile` or `openrouter/<vendor>/<model>`.
   * **API key** from your provider.
   * **API base URL**, only needed for Azure or your own OpenAI-compatible server (then use `openai/<model-name>` as the model).

   The bot sends a short test request before saving. Your key is stored encrypted, is only used for your server, and is never shown again. `/ai llm-remove` deletes it.
4. Drag the bot's role to the top in **Server Settings → Roles**. Discord doesn't let a bot change roles at or above its own highest role, even with administrator permission.
5. Start writing in the console.

### Slash commands

| Command | Who | Purpose |
| --- | --- | --- |
| `/ai llm` | owner | Connect or change your LLM and API key |
| `/ai llm-remove` | owner | Delete your LLM settings and key |
| `/ai grant @user` | owner | Give someone access to the console |
| `/ai revoke @user` | owner | Remove access |
| `/ai users` | console users | Show who has access |
| `/ai tools` | console users | List this server's custom tools |
| `/ai tool-delete` | owner | Delete a custom tool |
| `/ai reset` | console users | Clear the conversation memory |
| `/ai status` | console users | Show model, tools and settings |
| `/ai setup` | owner | Create the console channel again or repair its permissions |

## For the bot host

### 1. Create the Discord application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application.
2. Open **Bot**, click **Reset Token** and copy the token.
3. On the same page enable both **Privileged Gateway Intents**: **Server Members Intent** and **Message Content Intent**.
4. If other people should be able to add the bot, leave **Public Bot** enabled.

### 2. Configure

```bash
cp .env.example .env
```

Only `DISCORD_TOKEN` is required. Set `ENCRYPTION_KEY` as well, or back up `data/secret.key` once it has been generated. Without that key, the saved API keys can't be decrypted and every server has to enter its key again.

### 3. Run

Docker is the recommended way, because it gives the sandbox an extra layer of protection (see below):

```bash
docker compose up -d --build
```

Without Docker (Python 3.10 or newer, Linux or macOS for custom tools):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m bot
```

On startup the bot logs an invite link that asks for administrator permission. Share it with the server owners who should use the bot.

## Security

Several servers share one bot process, and their owners don't trust each other. So the bot is built to keep them apart.

**API keys**
* Keys are encrypted with Fernet before they are written to disk, and they are never shown in Discord or written to the logs.
* LLM keys in the host's environment (`OPENAI_API_KEY`, AWS and Azure credentials and so on) are removed at startup. Every request passes the server's own key explicitly, so a server without a key can't fall back to the host's.
* Providers that use the host's cloud credentials instead of an API key (Bedrock, SageMaker, Vertex AI) are refused.
* The API base URL can't point to private, local or link-local addresses, so nobody can use the bot to reach the host's internal network or cloud metadata. A host who runs the bot only for themselves can allow this with `ALLOW_PRIVATE_API_BASE=true`, for example to use a local Ollama.

**Custom tools** are code written by an LLM on behalf of someone you may not know. That code never runs inside the bot process. Each call starts a separate Python process that, before running the tool:

* starts with an empty environment, so the Discord token and keys are not there,
* switches to the `nobody` user when the bot runs as root (the default in Docker), so it can't read the data folder or signal the bot,
* gets limits on CPU time, memory and process count, and file writes are forbidden,
* pins its file descriptor table, so the operating system refuses to open any file or socket,
* installs an audit hook that blocks process, signal, file system, network and `ctypes` operations,
* only lets the code import a small list of pure standard library modules.

The tool can only reach Discord by calling built-in tools (`await ctx.call("create_role", {...})`). Those calls are executed by the bot, limited to the server the tool belongs to, and destructive ones still ask for approval. Each run also has a time limit and a maximum number of calls.

No sandbox is perfect. If you don't need this feature, set `ENABLE_CUSTOM_TOOLS=false`. If you run the bot without Docker as a normal user, the sandbox can't switch users, so the remaining protection is the descriptor and audit hook layers.

**Server access**: anyone with console access can do almost anything to the server that an administrator can. Only grant access to people you trust.

## Custom tools

A custom tool is stored as `data/tools/<server id>/<name>.json` and contains a description, a JSON schema for its arguments and code like this:

```python
async def run(ctx, args):
    created = []
    for name in args["names"]:
        result = await ctx.call("create_role", {"name": name, "color": args.get("color")})
        created.append(result["created"]["name"])
    return f"Created {len(created)} roles: {', '.join(created)}"
```

Inside a tool, `ctx.call(tool, args)` runs a built-in tool and returns its result. `ctx.guild_id`, `ctx.guild_name`, `ctx.author_id` and `ctx.author_name` describe the request. `print()` output is returned to the assistant as logs. A failed call raises `ToolCallError`.

## Configuration reference

All settings are environment variables (see `.env.example`).

| Variable | Default | Meaning |
| --- | --- | --- |
| `DISCORD_TOKEN` | | Bot token (required) |
| `ENCRYPTION_KEY` | generated in `data/secret.key` | Key for encrypting stored API keys |
| `ALLOW_PRIVATE_API_BASE` | `false` | Allow LLM endpoints on private or local addresses |
| `CONSOLE_CHANNEL_NAME` | `ai-console` | Name of the private channel |
| `REQUIRE_CONFIRMATION` | `true` | Approve / Deny buttons for destructive tools |
| `SHOW_TOOL_CALLS` | `true` | Post a short line for every tool call |
| `MAX_TOOL_ROUNDS` | `20` | Max LLM round trips per message |
| `MAX_HISTORY_MESSAGES` | `60` | Conversation memory per server |
| `LLM_TIMEOUT_SECONDS` | `180` | Timeout per LLM request |
| `SYSTEM_PROMPT_EXTRA` | | Extra instructions for the assistant on every server |
| `ENABLE_CUSTOM_TOOLS` | `true` | Allow servers to have their own tools |
| `MAX_CUSTOM_TOOLS` | `50` | Custom tools per server |
| `MAX_CUSTOM_TOOL_CODE_CHARS` | `20000` | Max code size per tool |
| `SANDBOX_CPU_SECONDS` | `10` | CPU time per tool run |
| `SANDBOX_MEMORY_MB` | `512` | Memory per tool run |
| `SANDBOX_TIMEOUT_SECONDS` | `600` | Total time per tool run, including approvals |
| `SANDBOX_MAX_CALLS` | `100` | Built-in tool calls per run |
| `SANDBOX_MAX_PARALLEL` | `4` | Tool runs at the same time across all servers |
| `DATA_DIR` | `data` | Storage location |

## Project layout

```
bot/
  __main__.py       entry point (python -m bot)
  client.py         discord.py bot and events
  agent.py          LLM loop, tool execution, confirmations
  llm.py            per-server LLM settings, validation and calls
  crypto.py         encryption of stored API keys
  console.py        private console channel and access list
  commands.py       /ai slash commands and the LLM form
  storage.py        JSON persistence (data/)
  ui.py             buttons and message splitting
  tools/
    base.py         Tool type, ToolContext and lookups
    builtin.py      Discord tools
    meta.py         tools for writing tools
    custom.py       per-server tool storage
  sandbox/
    host.py         starts sandbox processes and relays tool calls
    runner.py       the locked-down process that runs custom tool code
```

## Notes

* Conversation history is stored per server in `data/history/`. Use `/ai reset` when the assistant gets confused or when you start a new topic.
* When the bot is removed from a server, that server's settings, API key, history and custom tools are deleted.
* If the console channel is deleted, it is recreated on the next restart or with `/ai setup`.
