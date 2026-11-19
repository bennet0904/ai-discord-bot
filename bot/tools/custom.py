"""Custom tools the LLM writes for itself.

Each custom tool is a Python file in the plugins directory that looks like:

    TOOL_SPEC = {
        "name": "...",
        "description": "...",
        "parameters": {...JSON schema...},
        "destructive": False,
    }

    async def run(ctx, args):
        ...
        return "result"

`ctx` is a bot.tools.base.ToolContext, `args` a dict matching the parameters schema.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import logging
import pprint
import re
import sys
from pathlib import Path

from .base import Tool, ToolError

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
MODULE_PREFIX = "ai_custom_tool_"


def _validate_spec(spec: dict) -> None:
    if not isinstance(spec, dict):
        raise ToolError("TOOL_SPEC must be a dict.")
    name = spec.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ToolError("Tool name must be snake_case, 2-48 chars, start with a letter.")
    if not isinstance(spec.get("description"), str) or not spec["description"].strip():
        raise ToolError("Tool needs a non-empty description.")
    parameters = spec.get("parameters")
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        raise ToolError("parameters must be a JSON schema with type 'object'.")


def _load_file(path: Path) -> Tool:
    module_name = MODULE_PREFIX + path.stem
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ToolError(f"Cannot import {path}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise

    tool_spec = getattr(module, "TOOL_SPEC", None)
    _validate_spec(tool_spec)
    run = getattr(module, "run", None)
    if run is None or not inspect.iscoroutinefunction(run):
        raise ToolError("The code must define `async def run(ctx, args)`.")

    async def handler(ctx, args, _run=run):
        return await _run(ctx, args)

    return Tool(
        name=tool_spec["name"],
        description=tool_spec["description"],
        parameters=tool_spec["parameters"],
        handler=handler,
        destructive=bool(tool_spec.get("destructive", False)),
        source="custom",
    )


class CustomToolManager:
    def __init__(self, directory: Path):
        self.directory = directory
        self.tools: dict[str, Tool] = {}
        self.errors: dict[str, str] = {}

    def path_for(self, name: str) -> Path:
        return self.directory / f"{name}.py"

    def load_all(self, reserved: set[str]) -> None:
        self.tools.clear()
        self.errors.clear()
        if not self.directory.exists():
            return
        for path in sorted(self.directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                tool = _load_file(path)
                if tool.name in reserved:
                    raise ToolError(f"name {tool.name!r} collides with a built-in tool")
                if tool.name != path.stem:
                    raise ToolError(f"TOOL_SPEC name {tool.name!r} does not match file name {path.stem!r}")
                self.tools[tool.name] = tool
                log.info("Loaded custom tool %s", tool.name)
            except Exception as exc:  # noqa: BLE001 - a broken plugin must not stop the bot
                self.errors[path.stem] = f"{type(exc).__name__}: {exc}"
                log.warning("Failed to load custom tool %s: %s", path, exc)

    @staticmethod
    def render_source(name: str, description: str, parameters: dict, destructive: bool, code: str) -> str:
        spec = {"name": name, "description": description, "parameters": parameters, "destructive": destructive}
        header = (
            "# Custom tool written by the AI console. Loaded automatically on startup.\n"
            f"TOOL_SPEC = {pprint.pformat(spec, sort_dicts=False, width=100)}\n\n"
        )
        return header + code.strip() + "\n"

    def save(self, name: str, description: str, parameters: dict, destructive: bool, code: str, reserved: set[str]) -> Tool:
        if name in reserved:
            raise ToolError(f"{name!r} is a built-in tool name.")
        _validate_spec({"name": name, "description": description, "parameters": parameters})
        source = self.render_source(name, description, parameters, destructive, code)
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ToolError(f"Syntax error in code: {exc}") from None
        if not any(isinstance(n, ast.AsyncFunctionDef) and n.name == "run" for n in tree.body):
            raise ToolError("The code must define a top-level `async def run(ctx, args)`.")

        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(name)
        previous = path.read_text(encoding="utf-8") if path.exists() else None
        path.write_text(source, encoding="utf-8")
        try:
            tool = _load_file(path)
        except Exception as exc:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(previous, encoding="utf-8")
            raise ToolError(f"Tool failed to load, nothing was saved: {type(exc).__name__}: {exc}") from None
        self.tools[name] = tool
        self.errors.pop(name, None)
        return tool

    def delete(self, name: str) -> None:
        path = self.path_for(name)
        if not path.exists():
            raise ToolError(f"No custom tool named {name!r}.")
        path.unlink()
        self.tools.pop(name, None)
        self.errors.pop(name, None)
        sys.modules.pop(MODULE_PREFIX + name, None)

    def source(self, name: str) -> str:
        path = self.path_for(name)
        if not path.exists():
            raise ToolError(f"No custom tool named {name!r}.")
        return path.read_text(encoding="utf-8")
