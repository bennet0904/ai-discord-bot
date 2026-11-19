"""Small JSON-file persistence for per-guild settings and conversation history."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GuildSettings:
    console_channel_id: int | None = None
    allowed_user_ids: list[int] = field(default_factory=list)
    llm_model: str | None = None
    llm_api_key_encrypted: str | None = None
    llm_api_base: str | None = None


def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Storage:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.settings_path = data_dir / "guilds.json"
        self.history_dir = data_dir / "history"
        self._settings: dict[int, GuildSettings] = {}
        self._history: dict[int, list[dict]] = {}
        self._load_settings()

    # ---- guild settings -------------------------------------------------

    def _load_settings(self) -> None:
        if not self.settings_path.exists():
            return
        raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        for guild_id, values in raw.items():
            self._settings[int(guild_id)] = GuildSettings(
                console_channel_id=values.get("console_channel_id"),
                allowed_user_ids=[int(u) for u in values.get("allowed_user_ids", [])],
                llm_model=values.get("llm_model"),
                llm_api_key_encrypted=values.get("llm_api_key_encrypted"),
                llm_api_base=values.get("llm_api_base"),
            )

    def _save_settings(self) -> None:
        _atomic_write(self.settings_path, {str(k): asdict(v) for k, v in self._settings.items()})

    def guild(self, guild_id: int) -> GuildSettings:
        if guild_id not in self._settings:
            self._settings[guild_id] = GuildSettings()
        return self._settings[guild_id]

    def update_guild(self, guild_id: int, **changes: Any) -> GuildSettings:
        settings = self.guild(guild_id)
        for key, value in changes.items():
            setattr(settings, key, value)
        self._save_settings()
        return settings

    def forget_guild(self, guild_id: int) -> None:
        self._settings.pop(guild_id, None)
        self._save_settings()
        self.clear_history(guild_id)

    def guild_for_console(self, channel_id: int) -> int | None:
        for guild_id, settings in self._settings.items():
            if settings.console_channel_id == channel_id:
                return guild_id
        return None

    # ---- conversation history -------------------------------------------

    def _history_path(self, guild_id: int) -> Path:
        return self.history_dir / f"{guild_id}.json"

    def history(self, guild_id: int) -> list[dict]:
        if guild_id not in self._history:
            path = self._history_path(guild_id)
            if path.exists():
                try:
                    self._history[guild_id] = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    self._history[guild_id] = []
            else:
                self._history[guild_id] = []
        return list(self._history[guild_id])

    def set_history(self, guild_id: int, messages: list[dict]) -> None:
        self._history[guild_id] = messages
        _atomic_write(self._history_path(guild_id), messages)

    def clear_history(self, guild_id: int) -> None:
        self._history[guild_id] = []
        path = self._history_path(guild_id)
        if path.exists():
            path.unlink()
