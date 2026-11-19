"""Entry point: `python -m bot`."""

from __future__ import annotations

import logging
import os

from .client import AIBot
from .config import Config


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = Config.from_env()
    bot = AIBot(config)
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
