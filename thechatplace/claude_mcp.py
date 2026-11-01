"""Claude's connected tools: MCP servers and claude.ai connectors (#468).

What it knows
-------------
A Claude turn loads the MCP servers from the user's own settings, the
project's ``.mcp.json``, plugins and claude.ai connectors (The Chat Place
doesn't pass ``--strict-mcp-config``), plus The Chat Place's own session
tools (``--mcp-config``, #165). Nothing used to show them, so a connector
that needed signing in again was silently unavailable.

Claude Code answers a few control requests about them in an idle ``claude
-p`` with no message sent, so nothing is billed (checked with Claude Code
2.1.296):

* ``mcp_status`` lists every server with ``status`` (connected, failed,
  needs-auth, pending, disabled), ``scope`` and ``source`` (where it came
  from), ``error`` for a failed one, and its ``tools`` once connected.
  ``pending`` means still connecting: claude.ai connectors take a second or
  two, so the list is asked for again until nothing is pending.
* ``mcp_authenticate`` {serverName}: for a claude.ai connector, the claude.ai
  page that signs it in (``authUrl``, ``callbackExpected`` false); for a
  server with its own OAuth, the provider's page, with Claude Code waiting on
  a localhost callback (``callbackExpected`` true), so the process must stay
  running until the sign-in is done.
* ``mcp_reconnect`` {serverName}: try again; answers ``{}`` or an error.

Turning one off for a chat
--------------------------
``mcp_toggle`` is *not* used for that: it saves ``disabledMcpServers`` in
Claude Code's own config for the folder, which would turn the server off for
the desktop app and the terminal too, and stay off. Instead each turn of a
chat gets ``--settings {"deniedMcpServers": [...]}``: Claude Code merges
``deniedMcpServers`` from every settings source, the command line's included
("users can deny servers for themselves"), and a denied server isn't loaded
at all, for that process only. Checked with Claude Code 2.1.296 for a
claude.ai connector and a plugin's server: both were gone from
``mcp_status`` and from the turn's tools, and nothing was written.

When a server asks you something
--------------------------------
An MCP server can ask the user for input mid-turn (MCP elicitation). Claude
Code passes it on as an ``elicitation`` control request: ``mcp_server_name``,
``message``, ``mode`` ("form" or "url"), ``requested_schema`` for a form,
``url`` and ``elicitation_id`` for a page to open, and ``title``,
``display_name`` and ``description`` when the server gives them. The answer
is ``{"action": "accept"|"decline"|"cancel", "content": {...}}``, content
only with accept on a form. Captured from a real Haiku turn calling a small
stdio server's tool, in both modes, and the answers were accepted.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Collection, Dict, List, Optional, Tuple

from . import platform_paths
from .claude_cli import PermissionRequest, _decode, child_environment
from .session_tools import SERVER_NAME as SESSION_TOOLS_SERVER

#: What each ``mcp_status`` status means, in words.
STATUS_WORDS = {
    "connected": "connected",
    "needs-auth": "needs sign-in",
    "pending": "still connecting",
    "failed": "failed",
    "disabled": "turned off in Claude Code",
}

#: Where a server came from (its ``source``, or its ``scope`` on a Claude
#: Code that predates ``source``), in words.
SOURCE_WORDS = {
    "claudeai": "claude.ai connector",
    "plugin": "a plugin",
    "user": "your settings",
    "local": "your settings for this folder",
    "project": "this project",
    "dynamic": "added for this run",
    "managed": "your organization",
    "enterprise": "your organization",
    "sdk": "the app running Claude",
    "agent": "an agent",
}

#: Statuses that mean Claude can't use the server this turn, which a turn's
#: start says once (#468).
PROBLEM_STATUSES = ("failed", "needs-auth")

#: How long the dialog's list waits for servers still connecting.
STATUS_WAIT = 15.0
#: The longest an idle process lives: a server's own OAuth sign-in waits on
#: it for the browser to come back.
MAX_LIFETIME = 15 * 60.0
#: How long one control request waits for its answer.
REQUEST_WAIT = 20.0

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class McpError(Exception):
    """Claude Code refused a request, or couldn't be asked."""


def is_safe_server_name(name) -> bool:
    """A server name fit to keep with a chat and give back in ``--settings``:
    a short string on one line. Names come from ``mcp_status``."""
    return isinstance(name, str) and 0 < len(name) <= 200 and not _CONTROL.search(name)


def server_label(name: str) -> str:
    """The name to show. Claude Code names a plugin's server
    ``plugin:<plugin>:<server>``; The Chat Place's own has a fixed name."""
    if name == SESSION_TOOLS_SERVER:
        return "The Chat Place's session tools"
    parts = name.split(":")
    if len(parts) == 3 and parts[0] == "plugin" and parts[1] and parts[2]:
        return f"{parts[2]} (from the {parts[1]} plugin)"
    return name


def source_words(source: str, scope: str = "", name: str = "") -> str:
    if name == SESSION_TOOLS_SERVER:
        return "The Chat Place"
    for key in (source, scope):
        if key in SOURCE_WORDS:
            return SOURCE_WORDS[key]
    return source or scope or "Claude Code"


@dataclass
class McpServer:
    """One server as ``mcp_status`` reports it."""

    name: str
    status: str
    scope: str = ""
    source: str = ""
    error: str = ""
    tools: List[str] = field(default_factory=list)
    #: The chat turns it off for its turns (``disabled_mcp``).
    off_for_chat: bool = False
    #: Kept off for the chat, but Claude Code no longer lists it.
    missing: bool = False

    @property
    def label(self) -> str:
        return server_label(self.name)

    @property
    def is_session_tools(self) -> bool:
        return self.name == SESSION_TOOLS_SERVER

    @property
    def is_connector(self) -> bool:
        return (self.source or self.scope) == "claudeai"

    def status_words(self) -> str:
        if self.off_for_chat:
            return "off for this chat"
        words = STATUS_WORDS.get(self.status, self.status or "unknown")
        if self.status == "failed" and self.error:
            words += f": {self.error}"
        return words

    def text(self) -> str:
        """One row of the Connected Tools list, read in full."""
        if self.missing:
            return f"{self.label}: off for this chat, no longer set up in Claude Code"
        parts = [self.status_words(), source_words(self.source, self.scope, self.name)]
        if self.status == "connected" and not self.off_for_chat and self.tools:
            count = len(self.tools)
            parts.append(f"{count} tool" + ("" if count == 1 else "s"))
        return f"{self.label}: " + ", ".join(parts)


def parse_servers(body) -> List[McpServer]:
    """The servers in an ``mcp_status`` answer (``{"mcpServers": [...]}``);
    entries without a usable name are left out."""
    items = body.get("mcpServers") if isinstance(body, dict) else None
    servers = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not is_safe_server_name(item.get("name")):
            continue
        tools = item.get("tools")
        names = [str(t.get("name")) for t in tools
                 if isinstance(t, dict) and isinstance(t.get("name"), str)] \
            if isinstance(tools, list) else []
        servers.append(McpServer(
            name=item["name"],
            status=str(item.get("status") or ""),
            scope=str(item.get("scope") or ""),
            source=str(item.get("source") or ""),
            error=" ".join(str(item.get("error") or "").split()),
            tools=names))
    return servers


def with_chat_settings(servers: List[McpServer], disabled: Collection[str]) -> List[McpServer]:
    """The list as a chat sees it: those it turns off say so, and one it
    keeps off that Claude Code no longer lists is still shown, so it can be
    turned on again. The session tools come last, as they're The Chat
    Place's own."""
    disabled = [n for n in disabled if is_safe_server_name(n)]
    rows = []
    for server in servers:
        server.off_for_chat = server.name in disabled
        rows.append(server)
    known = {s.name for s in servers}
    rows += [McpServer(name, "", off_for_chat=True, missing=True)
             for name in disabled if name not in known]
    return sorted(rows, key=lambda s: s.is_session_tools)


def settings_args(disabled: Collection[str]) -> List[str]:
    """The ``--settings`` that keeps a chat's turned-off servers out of its
    turn (see the module docstring), or [] when it turns none off. The Chat
    Place's own session tools can't be turned off this way."""
    names = []
    for name in disabled:
        if is_safe_server_name(name) and name != SESSION_TOOLS_SERVER and name not in names:
            names.append(name)
    if not names:
        return []
    return ["--settings", json.dumps({"deniedMcpServers": [{"serverName": n} for n in names]},
                                     ensure_ascii=False)]


def init_servers(event_servers) -> List[Tuple[str, str]]:
    """(name, status) for each server a turn's ``system/init`` lists."""
    return [(item["name"], str(item.get("status") or ""))
            for item in event_servers if isinstance(item, dict)
            and is_safe_server_name(item.get("name"))] if isinstance(event_servers, list) else []


def problems_text(servers: List[Tuple[str, str]], skip: Collection[str] = ()) -> str:
    """What to say when a turn starts with servers Claude can't use, or "".
    ``skip``: names already said (or turned off for the chat)."""
    needs = [server_label(n) for n, status in servers
             if status == "needs-auth" and n not in skip]
    failed = [server_label(n) for n, status in servers if status == "failed" and n not in skip]
    if not needs and not failed:
        return ""
    sentences = []
    if needs:
        sentences.append(f"Claude can't use {_and(needs)}: "
                         + ("it needs" if len(needs) == 1 else "they need") + " sign-in.")
    if failed:
        sentences.append(f"Claude can't use {_and(failed)}: "
                         + ("it" if len(failed) == 1 else "they") + " failed to connect.")
    return " ".join(sentences) + " View, Connected Tools."


def _and(names: List[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def is_web_url(url) -> bool:
    """Only a web page is opened from Sign In or a server's request: never a
    file, a program or another app's link."""
    return isinstance(url, str) and bool(re.match(r"https?://[^\s]+$", url, re.IGNORECASE))


@dataclass
class SignIn:
    """What ``mcp_authenticate`` answered."""

    url: str
    #: True when Claude Code waits for the browser to come back to it (a
    #: server's own OAuth), so its process must stay running meanwhile.
    callback_expected: bool = False


class McpSession:
    """An idle ``claude -p`` in a chat's folder, asked about its MCP servers
    with control requests and never sent a message, so no session is made
    and nothing is billed. Same safety as ``fetch_commands``: the native
    ``claude`` only, the child environment, no permission prompts, and the
    process (with any server it started) ends on ``close`` or after
    ``MAX_LIFETIME``. Create and use it off the UI thread."""

    def __init__(self, executable: str, cwd: str,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 lifetime: float = MAX_LIFETIME) -> None:
        if not cwd:
            raise McpError("This chat has no folder.")
        command = [executable, "-p", "--input-format", "stream-json", "--output-format",
                   "stream-json", "--verbose", "--permission-prompts", "none"]
        self._tree = platform_paths.ProcessTree()
        self._lock = threading.Lock()
        self._answers: Dict[str, dict] = {}
        self._waiting: Dict[str, threading.Event] = {}
        self._counter = 0
        self._closed = False
        try:
            self._process = popen(command, cwd=cwd, env=child_environment(),
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL,
                                  **platform_paths.ProcessTree.popen_kwargs())
            self._tree.attach(self._process)
        except OSError as exc:
            raise McpError(f"Couldn't start Claude Code: {exc}") from exc
        self._timer = threading.Timer(lifetime, self.close)
        self._timer.daemon = True
        self._timer.start()
        threading.Thread(target=self._read, name="claude-mcp", daemon=True).start()
        self._write({"type": "control_request", "request_id": "init",
                     "request": {"subtype": "initialize", "hooks": None}})

    def _write(self, obj: dict) -> None:
        try:
            self._process.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            self._process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpError("Claude Code stopped before it answered.") from exc

    def _read(self) -> None:
        try:
            for raw in iter(self._process.stdout.readline, b""):
                try:
                    event = json.loads(_decode(raw))
                except ValueError:
                    continue
                response = event.get("response") if isinstance(event, dict) \
                    and event.get("type") == "control_response" else None
                if not isinstance(response, dict):
                    continue
                request_id = response.get("request_id")
                with self._lock:
                    waiting = self._waiting.get(request_id)
                    if waiting is not None:
                        self._answers[request_id] = response
                        waiting.set()
        except (OSError, ValueError):
            pass
        finally:
            # Whoever is still waiting hears that it's over, at once.
            with self._lock:
                for waiting in self._waiting.values():
                    waiting.set()

    def request(self, subtype: str, timeout: float = REQUEST_WAIT, **fields) -> dict:
        """Send one control request; its answer's body, or McpError with
        Claude Code's reason."""
        with self._lock:
            if self._closed:
                raise McpError("Claude Code has stopped.")
            self._counter += 1
            request_id = f"tcp-mcp-{self._counter}"
            event = self._waiting[request_id] = threading.Event()
        try:
            self._write({"type": "control_request", "request_id": request_id,
                         "request": {"subtype": subtype, **fields}})
            if not event.wait(timeout):
                raise McpError("Claude Code didn't answer in time.")
            with self._lock:
                answer = self._answers.pop(request_id, None)
            if answer is None:
                raise McpError("Claude Code stopped before it answered.")
            if answer.get("subtype") == "error":
                raise McpError(_refusal(subtype, str(answer.get("error") or "")))
            body = answer.get("response")
            return body if isinstance(body, dict) else {}
        finally:
            with self._lock:
                self._waiting.pop(request_id, None)

    def status(self, wait: float = STATUS_WAIT, poll: float = 1.0,
               sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic) -> List[McpServer]:
        """Every server, asked again while any is still connecting, for up
        to ``wait`` seconds; after that, ones still connecting say so."""
        end = clock() + wait
        while True:
            servers = parse_servers(self.request("mcp_status"))
            if not any(s.status == "pending" for s in servers) or clock() >= end:
                return servers
            sleep(poll)

    def sign_in(self, name: str) -> SignIn:
        body = self.request("mcp_authenticate", serverName=name)
        url = body.get("authUrl")
        if not body.get("requiresUserAction", bool(url)):
            return SignIn("")  # signed in already, nothing to do
        if not is_web_url(url):
            raise McpError("Claude Code didn't give a web page to sign in on.")
        return SignIn(url, bool(body.get("callbackExpected")))

    def reconnect(self, name: str) -> None:
        self.request("mcp_reconnect", serverName=name, timeout=60.0)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._timer.cancel()
        try:
            self._process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self._process.wait(timeout=5)
        except Exception:  # noqa: BLE001 - it didn't exit by itself
            self._tree.kill()
            try:
                self._process.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
        self._tree.close()


def _refusal(subtype: str, error: str) -> str:
    """Claude Code's reason, or what an older one that doesn't know the
    request means."""
    if "Unsupported control request" in error or not error:
        return ("This version of Claude Code can't do that. Update it (in a terminal, run: "
                "claude update), then try again.")
    return error


def open_session(executable: str, cwd: str) -> McpSession:
    """The session the window uses (tests put a fake here)."""
    return McpSession(executable, cwd)


# ---------------------------------------------------------------------------
# Elicitation: a server asking you for input
# ---------------------------------------------------------------------------

#: PermissionRequest.tool_name for an elicitation, which is no tool.
ELICITATION = "MCP elicitation"

ACCEPT, DECLINE, CANCEL = "accept", "decline", "cancel"


@dataclass
class FormField:
    """One thing a server's form asks for (a property of its schema)."""

    key: str
    #: text | number | integer | boolean | choice | multi
    kind: str
    label: str
    description: str = ""
    required: bool = False
    default: object = None
    #: (value, label) for a choice or several choices.
    choices: List[Tuple[str, str]] = field(default_factory=list)
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    format: str = ""

    def name(self) -> str:
        """The control's name for a screen reader: label, then "required"."""
        return self.label + (", required" if self.required else "")


def _choices(prop: dict) -> List[Tuple[str, str]]:
    """A schema's enum, with its titles: ``enum`` with ``enumNames``, or
    ``oneOf``/``anyOf`` of ``{const, title}`` (both forms MCP allows)."""
    values = prop.get("enum")
    if isinstance(values, list):
        names = prop.get("enumNames")
        names = names if isinstance(names, list) and len(names) == len(values) else values
        return [(str(v), str(n)) for v, n in zip(values, names)]
    options = prop.get("oneOf") or prop.get("anyOf")
    if isinstance(options, list):
        return [(str(o["const"]), str(o.get("title") or o["const"])) for o in options
                if isinstance(o, dict) and "const" in o]
    return []


def _number(value) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else None


def form_fields(schema) -> List[FormField]:
    """The fields of a requested schema (a flat object of simple values, as
    MCP allows), in the server's order."""
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return []
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    fields_ = []
    for key, prop in props.items():
        if not isinstance(key, str) or not isinstance(prop, dict):
            continue
        kind_name = prop.get("type")
        label = str(prop.get("title") or key)
        common = dict(key=key, label=label, description=str(prop.get("description") or ""),
                      required=key in required, default=prop.get("default"))
        if kind_name == "array":
            items = prop.get("items") if isinstance(prop.get("items"), dict) else {}
            fields_.append(FormField(kind="multi", choices=_choices(items), **common))
        elif _choices(prop):
            fields_.append(FormField(kind="choice", choices=_choices(prop), **common))
        elif kind_name == "boolean":
            fields_.append(FormField(kind="boolean", **common))
        elif kind_name in ("number", "integer"):
            fields_.append(FormField(kind=kind_name, minimum=_number(prop.get("minimum")),
                                     maximum=_number(prop.get("maximum")), **common))
        else:
            lengths = {k: prop.get(k) if type(prop.get(k)) is int else None
                       for k in ("minLength", "maxLength")}
            fields_.append(FormField(kind="text", min_length=lengths["minLength"],
                                     max_length=lengths["maxLength"],
                                     format=str(prop.get("format") or ""), **common))
    return fields_


def _bounds(f: FormField) -> str:
    def show(n):
        return str(int(n)) if f.kind == "integer" or float(n).is_integer() else str(n)
    if f.minimum is not None and f.maximum is not None:
        return f"between {show(f.minimum)} and {show(f.maximum)}"
    if f.minimum is not None:
        return f"at least {show(f.minimum)}"
    return f"at most {show(f.maximum)}"


def form_content(fields_: List[FormField], values: Dict[str, object],
                 with_field: bool = False):
    """The answer's ``content`` from what was entered (text as typed, a
    boolean, a choice's value or None, a list of values), and "" or a
    message naming the first field that's wrong (with that field's key too,
    if ``with_field``). Empty optional fields are left out."""
    content, problem, key = _check(fields_, values)
    return (content, problem, key) if with_field else (content, problem)


def _check(fields_: List[FormField], values: Dict[str, object]) -> Tuple[dict, str, str]:
    content: dict = {}
    for f in fields_:
        problem = _check_one(f, values.get(f.key), content)
        if problem:
            return {}, problem, f.key
    return content, "", ""


def _check_one(f: FormField, value, content: dict) -> str:
    """Put ``f``'s value in ``content``; or say what's wrong with it."""
    if f.kind == "boolean":
        content[f.key] = bool(value)
        return ""
    if f.kind == "multi":
        chosen = [v for v in value or [] if v in {c[0] for c in f.choices}]
        if f.required and not chosen:
            return f"Choose at least one for {f.label}."
        if chosen:
            content[f.key] = chosen
        return ""
    if f.kind == "choice":
        if value in {c[0] for c in f.choices}:
            content[f.key] = value
        elif f.required:
            return f"Choose one for {f.label}."
        return ""
    text = str(value or "").strip()
    if not text:
        if f.required:
            return f"{f.label} is required."
        return ""
    if f.kind in ("number", "integer"):
        try:
            number = int(text) if f.kind == "integer" else float(text)
        except ValueError:
            whole = "a whole number" if f.kind == "integer" else "a number"
            return f"{f.label} must be {whole}."
        if isinstance(number, float) and not math.isfinite(number):
            return f"{f.label} must be a number."
        if (f.minimum is not None and number < f.minimum
                or f.maximum is not None and number > f.maximum):
            return f"{f.label} must be {_bounds(f)}."
        content[f.key] = number
        return ""
    if f.min_length is not None and len(text) < f.min_length:
        return f"{f.label} must be at least {f.min_length} characters."
    if f.max_length is not None and len(text) > f.max_length:
        return f"{f.label} must be at most {f.max_length} characters."
    if f.format == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+", text):
        return f"{f.label} must be an email address."
    content[f.key] = text
    return ""


def elicitation_response(action: str, content: Optional[dict] = None) -> dict:
    """The answer Claude Code expects: accept (with the form's content),
    decline or cancel."""
    if action not in (ACCEPT, DECLINE, CANCEL):
        raise ValueError(f"Not an elicitation action: {action!r}")
    response: dict = {"action": action}
    if action == ACCEPT and content is not None:
        response["content"] = content
    return response


@dataclass
class ElicitationRequest(PermissionRequest):
    """An MCP server asking for input, queued and answered with Answer
    (Ctrl+Shift+A) like Claude's own requests."""

    server: str = ""
    message: str = ""
    mode: str = "form"
    url: str = ""
    elicitation_id: str = ""
    schema: dict = field(default_factory=dict)
    title: str = ""

    @property
    def is_url(self) -> bool:
        return self.mode == "url"

    @property
    def server_label(self) -> str:
        return server_label(self.server) if self.server else "A connected tool"

    def fields(self) -> List[FormField]:
        return [] if self.is_url else form_fields(self.schema)

    def summary(self) -> str:
        if self.is_url:
            return f"{self.server_label} asks you to open a web page"
        first = " ".join(self.message.split())
        if len(first) > 120:
            first = first[:117].rstrip() + "..."
        return f"{self.server_label} asks: {first}" if first \
            else f"{self.server_label} asks you for information"

    def detail(self) -> str:
        lines = [self.title] if self.title else []
        lines.append(self.message or "(The server gave no message.)")
        if self.is_url:
            lines += ["", f"Page: {self.url}"]
        return "\n".join(lines)

    def allow_for_session_label(self) -> str:
        return ""


def elicitation_from(request_id: str, request: dict) -> ElicitationRequest:
    """An ``elicitation`` control request as an ElicitationRequest."""
    schema = request.get("requested_schema")
    url = request.get("url")
    mode = "url" if request.get("mode") == "url" else "form"
    return ElicitationRequest(
        request_id=request_id, tool_name=ELICITATION,
        server=str(request.get("mcp_server_name") or ""),
        message=str(request.get("message") or ""),
        mode=mode,
        url=url if isinstance(url, str) else "",
        elicitation_id=str(request.get("elicitation_id") or ""),
        schema=schema if isinstance(schema, dict) else {},
        title=str(request.get("title") or ""),
        description=str(request.get("description") or ""))
