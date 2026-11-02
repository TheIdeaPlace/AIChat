"""Plain chat models over the OpenAI-compatible format, on this computer only.

One adapter for Ollama, LM Studio, llama.cpp and the like (#224, #489): a base
URL and a model. It has no tools, so approvals and questions stay off, and the
app keeps the history because there's nowhere else to read it from.

Addresses off this computer are refused, and no API key is sent. Paid hosted
services go beyond the subscription-only scope of #184, so that's a separate
decision, not something this adapter should decide by accident. A local server
can still forward a model elsewhere (Ollama's ``:cloud`` models do); the status
says so rather than promising otherwise.
"""
from __future__ import annotations

import json
import queue
import socket
import threading
import urllib.error
import urllib.request
import uuid
from typing import Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from ..contracts import (Capability, ChatRef, Emit, Integration, IntegrationError, Message, OptionGroup, OptionValue,
                         OwnedChat, Refused, ReplyText, Status, TurnEnded, TurnStatus, UnknownUpdate)
from ..drift import DriftCounters

DEFAULT_URL = "http://127.0.0.1:11434/v1"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_IMPLEMENTED = frozenset({Capability.READ, Capability.SEND, Capability.STOP, Capability.RESUME, Capability.OPTIONS})


def check_local_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in LOOPBACK:
        raise Refused("The Chat Place only connects plain chat models on this computer.")
    return url.rstrip("/")


FORWARDED_NOTE = "The server sends this one to the cloud."


def _forwarded(model: str) -> bool:
    """Ollama marks a model it forwards off this computer in its name."""
    return model.endswith(("-cloud", ":cloud"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A local server mustn't be able to send a chat's text somewhere else."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


class OpenAIChatIntegration(Integration):
    implemented = _IMPLEMENTED
    app_keeps_history = True

    def __init__(self, id: str = "local", name: str = "Local model", base_url: str = DEFAULT_URL,
                 history: Optional[Callable[[ChatRef], List[Message]]] = None):
        self.id, self.name = id, name
        self.base_url = check_local_url(base_url)
        self.drift = DriftCounters()
        #: The chat so far; the service gives it the store's copy.
        self.history = history or (lambda ref: [])
        self._lock = threading.Lock()
        self._running: Dict[str, object] = {}
        #: Turns underway, and those asked to stop. A Stop only counts for a turn
        #: underway, so a late one can't end the next message before it starts.
        self._active: set = set()
        self._stopping: set = set()

    def _models(self) -> Tuple[OptionValue, ...]:
        with _OPENER.open(self.base_url + "/models", timeout=2) as response:
            body = json.loads(response.read(4 * 1024 * 1024))
        data = body.get("data") if isinstance(body, dict) else None
        names = [m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)] \
            if isinstance(data, list) else []
        # Ones that run here come first, so the default stays on this computer.
        names.sort(key=_forwarded)
        return tuple(OptionValue(name, name, FORWARDED_NOTE if _forwarded(name) else "") for name in names)

    def status(self) -> Status:
        billing = "Nothing from The Chat Place: it sends no key. A model your server forwards elsewhere is up to that server."
        hint = f"Start a local server that answers at {self.base_url}, such as Ollama, then try again."
        try:
            count = len(self._models())
        except (OSError, ValueError):
            return Status(False, "No local model server is answering.", hint, billing)
        # Any server may answer here, so this says nothing about having checked it.
        return Status(True, f"A local model server is answering, with {count} models.", hint, billing)

    def use_kept_history(self, read: Callable[[ChatRef], List[Message]]) -> None:
        self.history = read

    def close(self) -> None:
        with self._lock:
            running = list(self._active)
        for native_id in running:
            self.stop(ChatRef(self.id, native_id))

    def new_chat_options(self) -> Tuple[OptionGroup, ...]:
        try:
            values = self._models()
        except (OSError, ValueError):
            return ()
        return (OptionGroup("model", "Model", values, values[0].id if values else ""),) if values else ()

    def start_chat(self, folder: str, title: str, options: Mapping[str, str]) -> OwnedChat:
        if not options.get("model"):
            raise Refused("Choose a model first.")
        return OwnedChat(ChatRef(self.id, str(uuid.uuid4())), title, folder, options={"model": options["model"]})

    def chat_options(self, chat: OwnedChat) -> Tuple[OptionGroup, ...]:
        groups = self.new_chat_options()
        return tuple(OptionGroup(g.id, g.label, g.values, chat.options.get(g.id, g.current)) for g in groups)

    def set_option(self, chat: OwnedChat, group: str, value: str) -> None:
        if group != "model" or not value:
            raise Refused("That isn't one of this chat's options.")
        chat.options["model"] = value

    def run_turn(self, chat: OwnedChat, text: str, emit: Emit) -> None:
        key = chat.ref.native_id
        messages = [{"role": "assistant" if m.role == "assistant" else "user", "content": m.text}
                    for m in self.history(chat.ref) if m.role in ("user", "assistant")]
        # The service records the user's message before the turn starts.
        if not messages or messages[-1] != {"role": "user", "content": text}:
            messages.append({"role": "user", "content": text})
        body = json.dumps({"model": chat.options.get("model", ""), "messages": messages, "stream": True}).encode("utf-8")
        request = urllib.request.Request(self.base_url + "/chat/completions", data=body,
                                         headers={"Content-Type": "application/json"})
        failure = ""
        with self._lock:
            self._active.add(key)
        try:
            response = _OPENER.open(request, timeout=300)
            with self._lock:
                self._running[key] = response
            self._read_stream(response, emit, key)
        except urllib.error.HTTPError:
            failure = "The model server refused that request."
        except (OSError, ValueError, AttributeError):
            failure = "The connection to the model server was lost."
        finally:
            with self._lock:
                self._running.pop(key, None)
                self._active.discard(key)
                stopped = key in self._stopping
                self._stopping.discard(key)
        if failure and not stopped:
            emit(TurnEnded(TurnStatus.FAILED, failure))
        else:
            emit(TurnEnded(TurnStatus.STOPPED if stopped else TurnStatus.COMPLETED))

    @staticmethod
    def _pump(response, lines: "queue.Queue") -> None:
        """Reads on its own thread: on Windows nothing wakes a read that's
        waiting on a socket, and a turn must be able to end regardless. If it's
        left behind, it finishes when the server closes or the read times out."""
        try:
            for raw in response:
                lines.put(raw)
            lines.put(None)
        except (OSError, ValueError, AttributeError) as error:
            lines.put(error)
        finally:
            try:
                response.close()
            except (OSError, ValueError, AttributeError):
                pass

    def _read_stream(self, response, emit: Emit, key: str) -> None:
        lines: "queue.Queue" = queue.Queue()
        threading.Thread(target=self._pump, args=(response, lines), daemon=True, name="model-stream").start()
        while True:
            with self._lock:
                if key in self._stopping:
                    return
            try:
                raw = lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if raw is None:
                return
            if isinstance(raw, Exception):
                raise raw
            line = raw.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                self.drift.unknown("sse-field")
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            try:
                event = json.loads(payload)
                choice = event["choices"][0] if event.get("choices") else {}
            except (ValueError, KeyError, IndexError, AttributeError, TypeError):
                self.drift.malformed("chunk")
                continue
            delta = choice.get("delta") if isinstance(choice, dict) else None
            if not isinstance(delta, dict):
                continue
            if isinstance(delta.get("content"), str) and delta["content"]:
                emit(ReplyText(delta["content"], "reply"))
            for field in set(delta) - {"content", "role", "reasoning", "reasoning_content", "tool_calls", "refusal"}:
                self.drift.unknown(f"delta:{field}")

    def stop(self, ref: ChatRef) -> None:
        with self._lock:
            if ref.native_id not in self._active:
                return
            self._stopping.add(ref.native_id)
            response = self._running.get(ref.native_id)
        if response is None:
            return
        # Closing a response another thread is reading can wait on that read,
        # so its socket is shut down, which ends the read at once.
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        try:
            sock.shutdown(socket.SHUT_RDWR) if sock is not None else response.close()
        except (OSError, ValueError, AttributeError):
            pass
