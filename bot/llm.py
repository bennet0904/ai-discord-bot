"""Per-server LLM connections through LiteLLM."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import litellm

log = logging.getLogger(__name__)

litellm.drop_params = True  # silently drop params a provider does not support
litellm.suppress_debug_info = True

# Providers that authenticate with ambient host credentials (cloud IAM, credential files) instead of an
# API key the server owner provides. Allowing them could let one server use the host's cloud account.
BLOCKED_PROVIDERS = {"bedrock", "bedrock_converse", "sagemaker", "sagemaker_chat", "vertex_ai", "vertex_ai_beta"}

MODEL_EXAMPLES = (
    "anthropic/claude-sonnet-5-5, openai/gpt-4o, gemini/gemini-2.5-pro, mistral/mistral-large-latest, "
    "groq/llama-3.3-70b-versatile, deepseek/deepseek-chat, xai/grok-3, openrouter/<vendor>/<model>"
)


class LLMConfigError(Exception):
    pass


@dataclass
class LLMSettings:
    model: str
    api_key: str | None
    api_base: str | None

    @property
    def provider(self) -> str:
        return self.model.split("/", 1)[0]


def validate_model(model: str) -> str:
    model = model.strip()
    if "/" not in model:
        raise LLMConfigError(f"Use the form provider/model, for example: {MODEL_EXAMPLES}")
    provider = model.split("/", 1)[0].lower()
    if provider in BLOCKED_PROVIDERS:
        raise LLMConfigError(f"The provider {provider!r} needs host cloud credentials and is not supported here.")
    try:
        litellm.get_llm_provider(model)
    except Exception as exc:  # noqa: BLE001 - litellm raises various types
        raise LLMConfigError(f"Unknown model or provider {model!r}: {exc}") from None
    return model


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    return ip.is_global and not ip.is_multicast


async def validate_api_base(url: str, allow_private: bool) -> str:
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LLMConfigError("The API base must be a full http(s) URL, for example https://my-server.example.com/v1")
    if allow_private:
        return url
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, port)
    except OSError:
        raise LLMConfigError(f"Cannot resolve {parsed.hostname!r}.") from None
    for info in infos:
        if not _is_public_ip(info[4][0]):
            raise LLMConfigError(
                "The API base points to a private or local address, which this bot host does not allow."
            )
    return url


def _call_kwargs(settings: LLMSettings, timeout: int) -> dict[str, Any]:
    # Always pass a key explicitly so LiteLLM never falls back to anything on the host.
    kwargs: dict[str, Any] = {"model": settings.model, "api_key": settings.api_key or "none", "timeout": timeout}
    if settings.api_base:
        kwargs["api_base"] = settings.api_base
    return kwargs


async def complete(settings: LLMSettings, messages: list[dict], tools: list[dict] | None, timeout: int) -> Any:
    kwargs = _call_kwargs(settings, timeout)
    kwargs["messages"] = messages
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    return await litellm.acompletion(**kwargs)


async def test_connection(settings: LLMSettings) -> str:
    """Send a tiny request. Returns a warning string (possibly empty) or raises LLMConfigError."""
    kwargs = _call_kwargs(settings, 45)
    kwargs["messages"] = [{"role": "user", "content": "Reply with the single word OK."}]
    kwargs["max_tokens"] = 16
    try:
        await litellm.acompletion(**kwargs)
    except Exception as exc:  # noqa: BLE001
        raise LLMConfigError(f"Test request failed: {str(exc)[:800]}") from None
    try:
        if not litellm.supports_function_calling(model=settings.model):
            return "LiteLLM does not list this model as supporting tool calls. It may not be able to manage the server."
    except Exception:  # noqa: BLE001 - unknown models simply have no metadata
        pass
    return ""
