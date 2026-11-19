"""Per-server custom tools written by the LLM.

Each server has its own folder `data/tools/<guild_id>/` with one JSON file per tool:

    {"name": ..., "description": ..., "parameters": {...}, "destructive": false,
     "code": "async def run(ctx, args): ...", "author_id": 123, "updated_at": "..."}

The code is never imported into the bot process. It only runs inside the sandbox (bot.sandbox).
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import logging
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .base import Tool, ToolError

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")


@dataclass
class CustomToolRecord:
    name: str
    description: str
    parameters: dict
    destructive: bool
    code: str
    author_id: int | None = None
    updated_at: str | None = None


def validate_definition(name: str, description: str, parameters: dict, code: str, max_code_chars: int) -> None:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ToolError("Tool name must be snake_case, 2-48 chars, starting with a letter.")
    if not isinstance(description, str) or not description.strip():
        raise ToolError("The tool needs a description.")
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        raise ToolError("parameters must be a JSON schema with type 'object'.")
    if not isinstance(code, str) or not code.strip():
        raise ToolError("The tool needs code.")
    if len(code) > max_code_chars:
        raise ToolError(f"Code is too long ({len(code)} chars, limit {max_code_chars}).")
    try:
        tree = ast.parse(code)  # parsing only, the code is never executed here
    except SyntaxError as exc:
        raise ToolError(f"Syntax error in code: {exc}") from None
    if not any(isinstance(node, ast.AsyncFunctionDef) and node.name == "run" for node in tree.body):
        raise ToolError("The code must define a top-level `async def run(ctx, args)`.")


class CustomToolStore:
    def __init__(self, root: Path, make_handler: Callable[[CustomToolRecord], Callable]):
        self.root = root
        self._make_handler = make_handler
        self._cache: dict[int, dict[str, CustomToolRecord]] = {}

    def _dir(self, guild_id: int) -> Path:
        return self.root / str(int(guild_id))

    def records(self, guild_id: int) -> dict[str, CustomToolRecord]:
        if guild_id not in self._cache:
            records: dict[str, CustomToolRecord] = {}
            directory = self._dir(guild_id)
            if directory.exists():
                for path in sorted(directory.glob("*.json")):
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        records[data["name"]] = CustomToolRecord(**data)
                    except Exception as exc:  # noqa: BLE001 - skip a broken file, keep the rest
                        log.warning("Skipping broken custom tool file %s: %s", path, exc)
            self._cache[guild_id] = records
        return self._cache[guild_id]

    def tools(self, guild_id: int) -> list[Tool]:
        return [self.to_tool(r) for r in self.records(guild_id).values()]

    def to_tool(self, record: CustomToolRecord) -> Tool:
        return Tool(
            name=record.name,
            description=f"[custom tool] {record.description}",
            parameters=record.parameters,
            handler=self._make_handler(record),
            destructive=record.destructive,
            source="custom",
        )

    def get(self, guild_id: int, name: str) -> CustomToolRecord:
        record = self.records(guild_id).get(name)
        if record is None:
            raise ToolError(f"No custom tool named {name!r} on this server.")
        return record

    def save(self, guild_id: int, record: CustomToolRecord, *, reserved: set[str], max_tools: int, max_code_chars: int) -> bool:
        """Validate and store a tool. Returns True if an existing tool was replaced."""
        if record.name in reserved:
            raise ToolError(f"{record.name!r} is a built-in tool name.")
        validate_definition(record.name, record.description, record.parameters, record.code, max_code_chars)
        records = self.records(guild_id)
        existed = record.name in records
        if not existed and len(records) >= max_tools:
            raise ToolError(f"This server already has {max_tools} custom tools. Delete one first.")
        record.updated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        directory = self._dir(guild_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{record.name}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(record), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        records[record.name] = record
        return existed

    def delete(self, guild_id: int, name: str) -> None:
        self.get(guild_id, name)
        (self._dir(guild_id) / f"{name}.json").unlink(missing_ok=True)
        self.records(guild_id).pop(name, None)

    def forget_guild(self, guild_id: int) -> None:
        self._cache.pop(guild_id, None)
        shutil.rmtree(self._dir(guild_id), ignore_errors=True)
