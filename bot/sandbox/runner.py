"""Locked-down process that executes one call of a custom tool.

This file is started by bot.sandbox.host as `python -I runner.py <cpu_seconds> <memory_mb>` with an
empty environment. It is the only trusted code in this process. Before any untrusted code runs it:

  * preloads the small set of stdlib modules custom tools may use,
  * drops root privileges to "nobody" if started as root,
  * sets resource limits: CPU time, address space, no file writes, no new processes, and
    RLIMIT_NOFILE pinned to the file descriptors already open, so no file or socket can be opened,
  * installs an audit hook that rejects process, signal, file system, network and ctypes events.

Communication with the bot happens over stdin/stdout using JSON lines:
  bot   -> child  {"code": str, "args": dict, "context": dict}
  child -> bot    {"type": "call", "id": n, "tool": str, "args": dict}
  bot   -> child  {"type": "result", "id": n, "ok": bool, "result": any, "error": str}
  child -> bot    {"type": "done", "result": any, "logs": str} or {"type": "error", "error": str, "logs": str}
"""

import asyncio
import base64  # noqa: F401
import bisect  # noqa: F401
import builtins
import calendar  # noqa: F401
import collections  # noqa: F401
import copy  # noqa: F401
import dataclasses  # noqa: F401
import datetime
import decimal  # noqa: F401
import enum  # noqa: F401
import fractions  # noqa: F401
import functools  # noqa: F401
import hashlib  # noqa: F401
import inspect
import heapq  # noqa: F401
import html  # noqa: F401
import io
import itertools  # noqa: F401
import json
import math  # noqa: F401
import operator  # noqa: F401
import os
import pprint  # noqa: F401
import random  # noqa: F401
import re  # noqa: F401
import resource
import statistics  # noqa: F401
import string  # noqa: F401
import sys
import textwrap  # noqa: F401
import time  # noqa: F401
import traceback
import typing  # noqa: F401
import unicodedata  # noqa: F401
import uuid  # noqa: F401

ALLOWED_MODULES = frozenset(
    {
        "asyncio", "base64", "bisect", "calendar", "collections", "copy", "dataclasses", "datetime", "decimal",
        "enum", "fractions", "functools", "hashlib", "heapq", "html", "itertools", "json", "math", "operator",
        "pprint", "random", "re", "statistics", "string", "textwrap", "time", "typing", "unicodedata", "uuid",
    }
)

# Imported by C code of allowed modules (e.g. datetime.strptime) through the caller's __import__.
INTERNAL_MODULES = frozenset({"_strptime"})

MAX_MESSAGE_BYTES = 1_000_000


def _audit_hook(event, args):
    # No global or builtin lookups that matter here: constants only, so untrusted code cannot change
    # the decision by patching module globals. Any exception (even a TypeError) blocks the event.
    if event in (
        "open", "os.kill", "os.killpg", "signal.pthread_kill", "os.system", "os.exec", "os.posix_spawn",
        "os.spawn", "os.fork", "os.forkpty", "os.vfork", "subprocess.Popen", "os.startfile",
        "resource.setrlimit", "resource.prlimit", "os.putenv", "os.unsetenv", "os.chdir", "os.chmod",
        "os.chown", "os.chflags", "os.link", "os.symlink", "os.mkdir", "os.remove", "os.rename",
        "os.rmdir", "os.truncate", "os.utime", "os.listdir", "os.scandir", "os.listxattr", "os.getxattr",
        "os.setxattr", "os.removexattr", "os.add_dll_directory", "mmap.__new__", "socket.__new__",
        "socket.connect", "socket.bind", "socket.getaddrinfo", "socket.sendto", "socket.sendmsg",
        "socket.gethostbyname", "socket.gethostbyaddr", "urllib.Request", "fcntl.ioctl", "fcntl.fcntl",
        "pty.spawn", "sys.addaudithook", "sys.setprofile", "sys.settrace", "sys.remote_exec",
        "cpython.remote_debugger_script", "gc.get_referrers", "gc.get_objects", "sys._current_frames",
        "sys._current_exceptions", "code.__new__", "function.__new__", "marshal.loads", "marshal.load",
        "pickle.find_class", "shutil.copyfile", "shutil.rmtree", "shutil.move", "tempfile.mkstemp",
        "winreg.OpenKey",
    ):
        raise PermissionError("blocked by sandbox: " + event)
    if event.startswith("ctypes.") or event.startswith("_posixsubprocess") or event.startswith("os.setuid"):
        raise PermissionError("blocked by sandbox: " + event)
    if event == "object.__setattr__" and args[1] in ("__code__", "__globals__", "__builtins__", "__closure__"):
        raise PermissionError("blocked by sandbox: " + event)
    if event == "import" and args[0].split(".")[0] not in (
        "asyncio", "base64", "bisect", "calendar", "collections", "copy", "dataclasses", "datetime", "decimal",
        "enum", "fractions", "functools", "hashlib", "heapq", "html", "itertools", "json", "math", "operator",
        "pprint", "random", "re", "statistics", "string", "textwrap", "time", "typing", "unicodedata", "uuid",
        "encodings", "_strptime",
    ):
        raise PermissionError("import blocked by sandbox: " + args[0])


_real_import = builtins.__import__


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level != 0 or (name.split(".")[0] not in ALLOWED_MODULES and name not in INTERNAL_MODULES):
        raise ImportError(f"Module {name!r} is not available in the sandbox. Allowed: {', '.join(sorted(ALLOWED_MODULES))}")
    return _real_import(name, globals, locals, fromlist, level)


def _restricted_builtins() -> dict:
    safe = dict(vars(builtins))
    for name in ("open", "input", "breakpoint", "exit", "quit", "help", "copyright", "credits", "license"):
        safe.pop(name, None)
    safe["__import__"] = _guarded_import
    return safe


class ToolCallError(Exception):
    """Raised inside a custom tool when a built-in tool call fails."""


class SandboxContext:
    """The `ctx` object custom tools receive."""

    def __init__(self, context: dict, channel: "Channel"):
        self.guild_id = context.get("guild_id")
        self.guild_name = context.get("guild_name")
        self.author_id = context.get("author_id")
        self.author_name = context.get("author_name")
        self._channel = channel

    async def call(self, tool: str, args: dict | None = None, **kwargs):
        """Call a built-in tool, e.g. await ctx.call("create_role", {"name": "VIP"}). Returns its result."""
        payload = dict(args or {})
        payload.update(kwargs)
        return self._channel.call(tool, payload)


class Channel:
    def __init__(self, reader, out_fd: int):
        self.reader = reader
        self.out_fd = out_fd
        self.counter = 0

    def send(self, message: dict) -> None:
        data = (json.dumps(message, default=str) + "\n").encode()
        if len(data) > MAX_MESSAGE_BYTES:
            raise ValueError("message to the bot is too large")
        view = memoryview(data)
        while view:
            written = os.write(self.out_fd, view)
            view = view[written:]

    def receive(self) -> dict:
        line = self.reader.readline(MAX_MESSAGE_BYTES)
        if not line:
            raise SystemExit("bot closed the connection")
        return json.loads(line)

    def call(self, tool: str, args: dict):
        self.counter += 1
        call_id = self.counter
        self.send({"type": "call", "id": call_id, "tool": tool, "args": args})
        reply = self.receive()
        if reply.get("type") != "result" or reply.get("id") != call_id:
            raise ToolCallError("protocol error")
        if not reply.get("ok"):
            raise ToolCallError(reply.get("error") or "tool call failed")
        return reply.get("result")


def _warm_up() -> None:
    """Trigger lazy imports (traceback suggestions, strptime, codecs) while imports still work."""
    for snippet in ("undefined_name", "(1).undefined_attr", "import nonexistent_module_xyz"):
        try:
            exec(snippet, {})
        except Exception:  # noqa: BLE001
            traceback.format_exc()
    datetime.datetime.strptime("2024-01-01", "%Y-%m-%d")
    "x".encode("idna")


def _lock_down(cpu_seconds: int, memory_mb: int) -> None:
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(65534)
        os.setuid(65534)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    for name, value in (("RLIMIT_AS", memory_mb * 1024 * 1024), ("RLIMIT_NPROC", 0)):
        limit = getattr(resource, name, None)
        if limit is not None:
            try:
                resource.setrlimit(limit, (value, value))
            except (ValueError, OSError):
                pass
    # Pin the descriptor table: every slot below the lowest free one is in use, so with the limit set to
    # that number no new file, pipe or socket can ever be created.
    lowest_free = os.dup(0)
    os.close(lowest_free)
    resource.setrlimit(resource.RLIMIT_NOFILE, (lowest_free, lowest_free))
    sys.addaudithook(_audit_hook)


def main() -> None:
    cpu_seconds = int(sys.argv[1])
    memory_mb = int(sys.argv[2])
    reader = sys.stdin.buffer
    channel = Channel(reader, sys.stdout.fileno())
    job = json.loads(reader.readline(MAX_MESSAGE_BYTES * 4))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    logs = io.StringIO()
    sys.stdout = logs
    sys.stderr = logs

    _warm_up()
    _lock_down(cpu_seconds, memory_mb)

    namespace = {"__builtins__": _restricted_builtins(), "__name__": "custom_tool", "ToolCallError": ToolCallError}
    try:
        code = compile(job["code"], "<custom tool>", "exec")
        exec(code, namespace)
        run = namespace.get("run")
        if run is None or not inspect.iscoroutinefunction(run):
            raise TypeError("the tool must define `async def run(ctx, args)`")
        ctx = SandboxContext(job.get("context") or {}, channel)
        result = loop.run_until_complete(run(ctx, job.get("args") or {}))
        channel.send({"type": "done", "result": result, "logs": logs.getvalue()[-4000:]})
    except BaseException:  # noqa: BLE001 - everything is reported back to the bot
        try:
            error = traceback.format_exc(limit=8)
        except BaseException:  # noqa: BLE001
            error = repr(sys.exc_info()[1])
        try:
            channel.send({"type": "error", "error": error[-4000:], "logs": logs.getvalue()[-4000:]})
        except BaseException:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
