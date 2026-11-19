"""Configuration loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _float_or_none(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return float(value)


def _id_list(name: str) -> set[int]:
    raw = os.getenv(name, "")
    return {int(part) for part in raw.replace(";", ",").split(",") if part.strip()}


@dataclass
class Config:
    discord_token: str
    llm_model: str
    llm_api_key: str | None = None
    llm_api_base: str | None = None
    llm_temperature: float | None = None
    llm_max_tokens: int | None = None

    console_channel_name: str = "ai-console"
    data_dir: Path = Path("data")
    plugins_dir: Path = Path("plugins")

    require_confirmation: bool = True
    enable_custom_tools: bool = True
    show_tool_calls: bool = True
    max_tool_rounds: int = 20
    max_history_messages: int = 60
    bot_owner_ids: set[int] = field(default_factory=set)
    system_prompt_extra: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
        model = os.getenv("LLM_MODEL", "").strip()
        if not model:
            raise SystemExit("LLM_MODEL is not set (for example 'openai/gpt-4o' or 'anthropic/claude-sonnet-5-5').")

        max_tokens = os.getenv("LLM_MAX_TOKENS", "").strip()
        return cls(
            discord_token=token,
            llm_model=model,
            llm_api_key=os.getenv("LLM_API_KEY") or None,
            llm_api_base=os.getenv("LLM_API_BASE") or None,
            llm_temperature=_float_or_none("LLM_TEMPERATURE"),
            llm_max_tokens=int(max_tokens) if max_tokens else None,
            console_channel_name=os.getenv("CONSOLE_CHANNEL_NAME", "ai-console").strip() or "ai-console",
            data_dir=Path(os.getenv("DATA_DIR", "data")),
            plugins_dir=Path(os.getenv("PLUGINS_DIR", "plugins")),
            require_confirmation=_bool("REQUIRE_CONFIRMATION", True),
            enable_custom_tools=_bool("ENABLE_CUSTOM_TOOLS", True),
            show_tool_calls=_bool("SHOW_TOOL_CALLS", True),
            max_tool_rounds=_int("MAX_TOOL_ROUNDS", 20),
            max_history_messages=_int("MAX_HISTORY_MESSAGES", 60),
            bot_owner_ids=_id_list("BOT_OWNER_IDS"),
            system_prompt_extra=os.getenv("SYSTEM_PROMPT_EXTRA", ""),
        )
