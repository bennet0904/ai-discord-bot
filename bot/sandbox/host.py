"""Starts sandboxed custom tool runs and relays their calls to built-in tools."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

RUNNER = Path(__file__).with_name("runner.py")
SUPPORTED = sys.platform.startswith("linux") or sys.platform == "darwin"

CallHandler = Callable[[str, dict], Awaitable[Any]]


class SandboxError(Exception):
    pass


class Sandbox:
    def __init__(self, *, cpu_seconds: int, memory_mb: int, wall_seconds: int, max_calls: int, max_parallel: int):
        self.cpu_seconds = cpu_seconds
        self.memory_mb = memory_mb
        self.wall_seconds = wall_seconds
        self.max_calls = max_calls
        self._slots = asyncio.Semaphore(max_parallel)

    async def run(self, code: str, args: dict, context: dict, on_call: CallHandler) -> dict:
        """Run `code` and return {"result": ..., "logs": str}. Raises SandboxError on failure."""
        if not SUPPORTED:
            raise SandboxError("Custom tools need Linux or macOS.")
        async with self._slots:
            with tempfile.TemporaryDirectory(prefix="tool-") as workdir:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-I", "-B", str(RUNNER), str(self.cpu_seconds), str(self.memory_mb),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={},
                    cwd=workdir,
                    limit=4 * 1024 * 1024,
                )
                try:
                    return await asyncio.wait_for(self._converse(proc, code, args, context, on_call), self.wall_seconds)
                except asyncio.TimeoutError:
                    raise SandboxError(f"The tool ran longer than {self.wall_seconds}s and was stopped.") from None
                finally:
                    if proc.returncode is None:
                        proc.kill()
                    await proc.wait()

    async def _converse(self, proc, code: str, args: dict, context: dict, on_call: CallHandler) -> dict:
        assert proc.stdin and proc.stdout and proc.stderr

        async def send(message: dict) -> None:
            proc.stdin.write((json.dumps(message, default=str) + "\n").encode())
            await proc.stdin.drain()

        await send({"code": code, "args": args, "context": context})
        calls = 0
        while True:
            line = await proc.stdout.readline()
            if not line:
                stderr = (await proc.stderr.read())[-1500:].decode(errors="replace")
                await proc.wait()
                hint = " (CPU or memory limit reached?)" if proc.returncode and proc.returncode < 0 else ""
                raise SandboxError(f"The tool process exited unexpectedly{hint}. {stderr}".strip())
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                raise SandboxError("The tool sent invalid data.") from None

            kind = message.get("type")
            if kind == "done":
                return {"result": message.get("result"), "logs": message.get("logs", "")}
            if kind == "error":
                raise SandboxError(f"{message.get('error', '').strip()}\n{message.get('logs', '')}".strip())
            if kind != "call":
                raise SandboxError("The tool sent an unknown message.")

            calls += 1
            reply: dict[str, Any] = {"type": "result", "id": message.get("id")}
            if calls > self.max_calls:
                reply.update(ok=False, error=f"Too many tool calls (limit {self.max_calls}).")
            else:
                try:
                    tool_args = message.get("args")
                    if not isinstance(tool_args, dict):
                        raise SandboxError("args must be an object")
                    result = await on_call(str(message.get("tool")), tool_args)
                    reply.update(ok=True, result=json.loads(json.dumps(result, default=str)))
                except Exception as exc:  # noqa: BLE001 - report every failure back into the tool
                    reply.update(ok=False, error=str(exc) or type(exc).__name__)
            await send(reply)
