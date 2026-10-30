"""The MCP server that gives The Chat Place's Claude turns their session
tools (#165). See ``session_tools`` for what the tools do and why the window
answers them.

Claude Code starts it for each turn (``--mcp-config``) and talks to it over
stdin and stdout: one JSON-RPC message per line, as MCP's stdio transport
has it. Nothing else may be written to stdout. It answers ``initialize``,
``ping`` and ``tools/list`` itself, and passes each ``tools/call`` to the
window. If the window can't be reached, the call says so as an error result
rather than failing the turn.

It's the app started with ``--session-tools-mcp`` (``app.main``), so a built
app needs nothing extra to run it, and it never imports wx.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import BinaryIO, List, Optional

from . import __version__
from .session_tools import (ARCHIVE_TIMEOUT, CALL_TIMEOUT, SEARCH_TIMEOUT, SERVER_NAME, TOOL_NAMES, TOOLS,
                            Unreachable, ask)

#: The versions of MCP this server speaks; the newest is offered when the
#: client asks for one it doesn't know.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


class Server:
    def __init__(self, data_dir: Optional[Path], self_id: str, asker=ask) -> None:
        self.data_dir = data_dir
        self.self_id = self_id
        self._ask = asker

    def handle(self, message) -> Optional[dict]:
        """The reply to one message, or None for a notification."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "Not a JSON-RPC 2.0 message.")
        method = message.get("method")
        if "id" not in message:
            return None  # notifications/initialized, cancelled: nothing to say
        request_id = message["id"]
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return _result(request_id, {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": "The Chat Place session tools",
                               "version": __version__},
                "instructions": (
                    "Session tools from The Chat Place, standing in for the Claude desktop "
                    "app's. Archiving hides one of The Chat Place's own sessions; desktop "
                    "app sessions can't be archived from here.")})
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": [dict(tool) for tool in TOOLS]})
        if method == "tools/call":
            name = params.get("name")
            if name not in TOOL_NAMES:
                return _error(request_id, INVALID_PARAMS, f"Unknown tool: {name}")
            arguments = params.get("arguments")
            return _result(request_id, self.call(name, arguments if isinstance(arguments, dict)
                                                 else {}))
        return _error(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")

    def call(self, name: str, arguments: dict) -> dict:
        timeout = {"search_session_transcripts": SEARCH_TIMEOUT,
                   "archive_session": ARCHIVE_TIMEOUT}.get(name, CALL_TIMEOUT)
        try:
            answer = self._ask({"tool": name, "arguments": arguments, "self": self.self_id},
                               self.data_dir, timeout)
            text, is_error = str(answer.get("text", "")), bool(answer.get("is_error"))
        except Unreachable as exc:
            text, is_error = (f"{exc} The session tools work only while The Chat Place is "
                              "running."), True
        return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _result(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(server: Server, reader: BinaryIO, writer: BinaryIO) -> None:
    """Answer messages from ``reader`` until it closes."""
    for raw in iter(reader.readline, b""):
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except ValueError:
            reply = _error(None, PARSE_ERROR, "Not valid JSON.")
        else:
            if isinstance(message, list):
                # Batches were dropped from MCP in 2025-06-18; nobody sends them.
                reply = _error(None, INVALID_REQUEST, "Batches aren't supported.")
            else:
                try:
                    reply = server.handle(message)
                except Exception as exc:  # noqa: BLE001 - one bad call mustn't end the server
                    reply = _error(message.get("id") if isinstance(message, dict) else None,
                                   -32603, f"Internal error: {exc}")
        if reply is not None:
            writer.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            writer.flush()


def parse_arguments(argv: List[str]) -> "tuple[Optional[Path], str]":
    data_dir, self_id = None, ""
    for index, value in enumerate(argv):
        following = argv[index + 1] if index + 1 < len(argv) else ""
        if value == "--data-dir" and following:
            data_dir = Path(following)
        elif value == "--self":
            self_id = following
    return data_dir, self_id


def _std_streams():
    """stdin and stdout as bytes. A built Windows app has no console, so
    Python may not have made ``sys.stdin``; the pipes Claude Code gave it
    are still there as file descriptors 0 and 1."""
    reader = getattr(sys.stdin, "buffer", None)
    writer = getattr(sys.stdout, "buffer", None)
    if reader is None:
        reader = open(0, "rb", buffering=0, closefd=False)
    if writer is None:
        writer = open(1, "wb", buffering=0, closefd=False)
    return reader, writer


def main(argv: List[str]) -> int:
    data_dir, self_id = parse_arguments(argv)
    reader, writer = _std_streams()
    # Anything printed by mistake would break the protocol on stdout.
    sys.stdout = sys.stderr
    serve(Server(data_dir, self_id), reader, writer)
    return 0


def self_test(timeout: float = 60.0) -> dict:
    """Start the server the way Claude Code does and check it answers: for
    the built app's smoke test, which catches a build whose server can't
    start or can't use its stdin and stdout (a Windows app built without a
    console). Raises RuntimeError with what went wrong."""
    import subprocess
    import tempfile

    from . import platform_paths
    from .session_tools import server_command

    with tempfile.TemporaryDirectory() as folder:
        command, args, env = server_command("smoke-test", Path(folder))
        environment = dict(os.environ, **env)
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": PROTOCOL_VERSIONS[0], "capabilities": {},
                        "clientInfo": {"name": "smoke-test", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            # No window to ask: it must say so, not fail.
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "list_sessions", "arguments": {}}},
        ]
        data = b"".join(json.dumps(m).encode("utf-8") + b"\n" for m in messages)
        completed = subprocess.run([command, *args], input=data, capture_output=True,
                                   env=environment, timeout=timeout,
                                   creationflags=platform_paths.hidden_window_flags())
    replies = {}
    for line in completed.stdout.splitlines():
        if line.strip():
            reply = json.loads(line)
            replies[reply.get("id")] = reply
    try:
        names = sorted(tool["name"] for tool in replies[2]["result"]["tools"])
        call = replies[3]["result"]
        version = replies[1]["result"]["protocolVersion"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"the server's replies were incomplete (exit {completed.returncode}): "
                           f"{completed.stdout[:300]!r} {completed.stderr[:300]!r}") from exc
    if names != sorted(TOOL_NAMES) or not call.get("isError"):
        raise RuntimeError(f"unexpected replies: tools {names}, call {call}")
    return {"protocol": version, "tools": names}
