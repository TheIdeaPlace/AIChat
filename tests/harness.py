"""Builds each integration against its fake, for the shared suite."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, FrozenSet, List, Optional

from thechatplace.contracts import ChatRef, Integration, TurnEnded
from thechatplace.integrations.acp import AcpIntegration, AgentDefinition
from thechatplace.integrations.claude import ClaudeIntegration
from thechatplace.integrations.codex import CodexIntegration
from thechatplace.integrations.openai_chat import OpenAIChatIntegration
from thechatplace.platform_paths import OwnedProcess
from thechatplace.service import ChatService
from thechatplace.store import Store

HERE = Path(__file__).parent
MODE_AGENT, MODE_PLAN, MODE_BAD = "urn:fake#agent", "urn:fake#plan", "urn:fake#autopilot"


def fake_definition(id: str = "fakeagent", name: str = "Fake Agent", **overrides) -> AgentDefinition:
    values = dict(id=id, name=name, program="fake-agent", args=(), sign_in_hint="Sign in to the fake.",
                  billing="Nothing: it's a fake.", verified_versions=("1",),
                  safe_modes={MODE_AGENT: ("Agent", "Asks first."), MODE_PLAN: ("Plan", "Plans first.")},
                  forbidden_options={"allow_all": ("on",)})
    values.update(overrides)
    return AgentDefinition(**values)


def acp(mode: str = "answer", *flags: str, definition: Optional[AgentDefinition] = None, env: Optional[dict] = None) -> AcpIntegration:
    def start(cwd: str):
        return OwnedProcess([sys.executable, "-u", str(HERE / "fake_acp_agent.py"), mode, *flags],
                            env={**os.environ, **(env or {})}, cwd=cwd)
    return AcpIntegration(definition or fake_definition(), start=start)


def claude(mode: str, data: Path, env: Optional[dict] = None) -> ClaudeIntegration:
    def start(command: List[str], folder: str):
        extra = {"FAKE_CLAUDE_MODE": mode, "FAKE_CLAUDE_LOG": str(data / "claude.log"), **(env or {})}
        return OwnedProcess([sys.executable, "-u", str(HERE / "fake_claude.py"), *command[1:]],
                            env={**os.environ, **extra}, cwd=folder)
    return ClaudeIntegration(start=start, projects_dir=lambda: data / "projects", program="fake-claude")


def codex(mode: str, data: Path) -> CodexIntegration:
    def start():
        extra = {"FAKE_CODEX_DIR": str(data), "FAKE_CODEX_LOG": str(data / "codex.log")}
        return OwnedProcess([sys.executable, "-u", str(HERE / "fake_codex.py"), mode], env={**os.environ, **extra})
    return CodexIntegration(start=start)


class FakeModelServer:
    """An OpenAI-compatible server on this computer, for the plain chat adapter."""

    def __init__(self, mode: str = "answer"):
        self.mode = mode
        self.requests: List[dict] = []
        self.stop_event = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps({"data": [{"id": "m1"}, {"id": "m2"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                request = json.loads(self.rfile.read(length))
                outer.requests.append({"body": request, "auth": self.headers.get("Authorization")})
                if outer.mode == "error":
                    self.send_response(500)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def chunk(delta):
                    self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": delta}]}).encode() + b"\n\n")
                    self.wfile.flush()
                try:
                    chunk({"role": "assistant", "content": "Hello"})
                    if outer.mode == "hang":
                        outer.stop_event.wait(30)
                        return
                    if outer.mode == "unknown":
                        chunk({"something_new": "must never be stored"})
                    chunk({"content": " there: " + request["messages"][-1]["content"]})
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.stop_event.set()
        self.server.shutdown()
        self.server.server_close()


class Run:
    """A service and a queue of what it reported, with waits that time out."""

    def __init__(self, integration: Integration, data: Path, folder: Path):
        self.integration = integration
        self.data, self.folder = data, folder
        self.events: "queue.Queue" = queue.Queue()
        self.service = ChatService([integration], Store(data / "store"), lambda ref, event: self.events.put((ref, event)))
        self.service.store.set_enabled(integration.id, True)

    def start(self, **options):
        return self.service.start_chat(self.integration.id, str(self.folder), "A chat", options)

    def wait(self, kind, timeout: float = 20.0):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"{kind} never came")
            try:
                _ref, event = self.events.get(timeout=remaining)
            except queue.Empty:
                raise AssertionError(f"{kind} never came") from None
            if isinstance(event, kind):
                return event

    def end_turn(self, timeout: float = 20.0) -> TurnEnded:
        return self.wait(TurnEnded, timeout)

    def text(self, ref: ChatRef) -> List[str]:
        return [f"{m.role}:{m.text}" for m in self.service.messages(ref)]

    def idle(self, ref: ChatRef, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while self.service.state(ref).busy:
            if time.monotonic() > deadline:
                raise AssertionError("the chat stayed busy")
            time.sleep(0.02)

    def close(self) -> None:
        self.service.close()
