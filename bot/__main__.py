"""Entry point: `python -m bot`."""

from __future__ import annotations

import logging
import os

from .client import AIBot
from .config import Config, strip_ambient_llm_credentials


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    os.umask(0o077)  # data files (API keys, history) readable by the bot user only
    config = Config.from_env()
    removed = strip_ambient_llm_credentials()
    if removed:
        logging.getLogger("bot").warning(
            "Ignoring host LLM credentials %s: every server has to bring its own key.", ", ".join(sorted(removed))
        )
    bot = AIBot(config)
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
