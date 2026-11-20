"""Host configuration loaded from environment variables (and an optional .env file).

There is deliberately no LLM configuration here: every server brings its own model and API key
through the /ai llm command.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)


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


# Environment variables LiteLLM would otherwise pick up as a fallback. If any of these were set on the
# host, a server without its own key could end up spending the host's credits, so they are removed.
_AMBIENT_CREDENTIAL = re.compile(
    r"(_API_KEY|_API_TOKEN|_API_BASE|_ACCESS_TOKEN)$|^(AWS_|AZURE_|VERTEX|GOOGLE_APPLICATION_CREDENTIALS|"
    r"OPENAI_|ANTHROPIC_|GEMINI_|GROQ_|MISTRAL_|COHERE_|HF_|HUGGINGFACE_|OLLAMA_|LITELLM_)"
)


def strip_ambient_llm_credentials() -> list[str]:
    removed = [name for name in os.environ if _AMBIENT_CREDENTIAL.search(name)]
    for name in removed:
        del os.environ[name]
    return removed


@dataclass
class Config:
    discord_token: str
    encryption_key: str | None = None
    data_dir: Path = Path("data")
    console_channel_name: str = "ai-console"

    require_confirmation: bool = True
    show_tool_calls: bool = True
    max_tool_rounds: int = 20
    max_history_messages: int = 60
    llm_timeout_seconds: int = 180
    llm_retries: int = 4  # -1 = retry forever
    llm_retry_max_delay: int = 60
    allow_private_api_base: bool = False
    system_prompt_extra: str = ""

    enable_custom_tools: bool = True
    max_custom_tools: int = 50
    max_custom_tool_code_chars: int = 20000
    sandbox_cpu_seconds: int = 10
    sandbox_memory_mb: int = 512
    sandbox_timeout_seconds: int = 600
    sandbox_max_calls: int = 100
    sandbox_max_parallel: int = 4

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
        return cls(
            discord_token=token,
            encryption_key=os.getenv("ENCRYPTION_KEY", "").strip() or None,
            data_dir=Path(os.getenv("DATA_DIR", "data")),
            console_channel_name=os.getenv("CONSOLE_CHANNEL_NAME", "ai-console").strip() or "ai-console",
            require_confirmation=_bool("REQUIRE_CONFIRMATION", True),
            show_tool_calls=_bool("SHOW_TOOL_CALLS", True),
            max_tool_rounds=_int("MAX_TOOL_ROUNDS", 20),
            max_history_messages=_int("MAX_HISTORY_MESSAGES", 60),
            llm_timeout_seconds=_int("LLM_TIMEOUT_SECONDS", 180),
            llm_retries=_int("LLM_RETRIES", 4),
            llm_retry_max_delay=max(1, _int("LLM_RETRY_MAX_DELAY", 60)),
            allow_private_api_base=_bool("ALLOW_PRIVATE_API_BASE", False),
            system_prompt_extra=os.getenv("SYSTEM_PROMPT_EXTRA", ""),
            enable_custom_tools=_bool("ENABLE_CUSTOM_TOOLS", True),
            max_custom_tools=_int("MAX_CUSTOM_TOOLS", 50),
            max_custom_tool_code_chars=_int("MAX_CUSTOM_TOOL_CODE_CHARS", 20000),
            sandbox_cpu_seconds=_int("SANDBOX_CPU_SECONDS", 10),
            sandbox_memory_mb=_int("SANDBOX_MEMORY_MB", 512),
            sandbox_timeout_seconds=_int("SANDBOX_TIMEOUT_SECONDS", 600),
            sandbox_max_calls=_int("SANDBOX_MAX_CALLS", 100),
            sandbox_max_parallel=_int("SANDBOX_MAX_PARALLEL", 4),
        )
