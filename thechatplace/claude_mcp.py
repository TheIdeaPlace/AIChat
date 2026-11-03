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

import datetime
import ipaddress
import json
import math
import re
import subprocess
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Collection, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

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


def is_own_tools(name: str, source: str = "", scope: str = "") -> bool:
    """True for The Chat Place's own session tools. The name alone proves
    nothing, since any server can call itself that; the app's own comes from
    ``--mcp-config``, which Claude Code reports as ``dynamic``, and a plugin's
    (scope ``dynamic`` too) has a source of its own."""
    return name == SESSION_TOOLS_SERVER and (source or scope) == "dynamic"


def server_label(name: str, source: str = "", scope: str = "") -> str:
    """The name to show. Claude Code names a plugin's server
    ``plugin:<plugin>:<server>``; The Chat Place's own has a fixed name, used
    only with the source that shows it's ours. A server's question says no
    source, so it never gets the friendly label."""
    if is_own_tools(name, source, scope):
        return "The Chat Place's session tools"
    parts = name.split(":")
    if len(parts) == 3 and parts[0] == "plugin" and parts[1] and parts[2]:
        return f"{parts[2]} (from the {parts[1]} plugin)"
    return name


def source_words(source: str, scope: str = "", name: str = "") -> str:
    if is_own_tools(name, source, scope):
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
    #: Kept off for the chat and not in Claude Code's list, which is how every
    #: turned-off tool looks once it's kept out of the idle process (#468).
    missing: bool = False

    @property
    def label(self) -> str:
        return server_label(self.name, self.source, self.scope)

    @property
    def is_session_tools(self) -> bool:
        return is_own_tools(self.name, self.source, self.scope)

    @property
    def can_be_denied(self) -> bool:
        """``deniedMcpServers`` goes by name, so denying a server that shares
        the session tools' name would take the session tools away as well:
        ``settings_args`` leaves that name out, and the dialog says so."""
        return self.name != SESSION_TOOLS_SERVER

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
            return f"{self.label}: off for this chat"
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
    keeps off that Claude Code doesn't list is still shown, so it can be
    turned on again. That's every turned-off tool in a new idle process, which
    doesn't start them. The session tools come last, as they're The Chat
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


def with_settings(command: List[str], disabled: Collection[str]) -> List[str]:
    """``command`` with the chat's turned-off servers added to its
    ``--settings``. Claude Code keeps only the last ``--settings`` on a
    command line (checked with 2.1.296: a second one dropped the first's
    output style), so when Chat Options already passes one (#468), its JSON
    gets ``deniedMcpServers`` too rather than a second flag.

    A ``--settings`` that is another flag's value (a chat titled
    "--settings", say) is never taken for the flag itself."""
    extra = settings_args(disabled)
    if not extra:
        return list(command)
    command = list(command)
    takes_value = {"--name", "--model", "--effort", "--permission-mode", "--agent",
                   "--session-id", "--resume", "--resume-session-at", "--settings",
                   "--system-prompt-snapshot", "--input-format", "--output-format",
                   "--permission-prompts", "--permission-prompt-tool"}
    for i, part in enumerate(command[:-1]):
        if part != "--settings" or (i and command[i - 1] in takes_value):
            continue
        try:
            current = json.loads(command[i + 1])
        except ValueError:
            continue
        if isinstance(current, dict):
            current.update(json.loads(extra[1]))
            command[i + 1] = json.dumps(current, ensure_ascii=False)
            return command
    return command + extra


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


def tools_summary(rows) -> str:
    """The list in one line, for Refresh to say: "3 connected tools, 1 needs
    sign-in". Works for any provider's rows."""
    if not rows:
        return "No connected tools."
    on = [r for r in rows if not r.off_for_chat]
    needs = sum(r.status == "needs-auth" for r in on)
    failed = sum(r.status == "failed" for r in on)
    off = len(rows) - len(on)
    parts = [f"{len(rows)} connected tool" + ("" if len(rows) == 1 else "s")]
    if needs:
        parts.append(f"{needs} " + ("needs" if needs == 1 else "need") + " sign-in")
    if failed:
        parts.append(f"{failed} failed")
    if off:
        parts.append(f"{off} off for this chat")
    return ", ".join(parts)


#: Addresses plain http may go to: nothing on the way can read or change them.
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_HOST_LABEL = r"[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?"
_PLAIN_HOST = re.compile(rf"{_HOST_LABEL}(?:\.{_HOST_LABEL})*\.?")


def _hidden_character(char: str) -> bool:
    """Control, format (a right-to-left override) and spacing characters, and
    ones with no meaning: anything that can make an address read differently
    from where it goes, or end it early."""
    return unicodedata.category(char)[0] in "CZ"


def web_url_host(url) -> str:
    """The host a web address really goes to, or "" if it isn't one The Chat
    Place opens. Only a web page is opened from Sign In or a server's
    request: never a file, a program or another app's link, and never an
    address that hides where it goes. ``https://good.example@evil.example/``
    goes to evil.example, so no user name or password is allowed before the
    host. Browsers end the host at a backslash where urlsplit doesn't, so
    there are no backslashes. A host with other alphabets' letters can look
    like a familiar one, so a host is plain letters, digits and punctuation
    (its ``xn--`` form shows what it is)."""
    if not isinstance(url, str) or "\\" in url or any(_hidden_character(c) for c in url):
        return ""
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - it raises on a port that isn't one
    except ValueError:
        return ""
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or "@" in parts.netloc or len(host) > 253:
        return ""
    if "[" in parts.netloc:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return ""
    elif not _PLAIN_HOST.fullmatch(host):
        return ""
    if parts.scheme == "http" and host not in _LOCAL_HOSTS:
        return ""
    return host


def is_web_url(url) -> bool:
    """True for an address The Chat Place will open (``web_url_host``)."""
    return bool(web_url_host(url))


def _visible(text: str) -> str:
    """``text`` with its hidden characters written out, for showing something
    that won't be opened. A NUL would otherwise end the box's text early."""
    return "".join(f"[U+{ord(c):04X}]" if _hidden_character(c) and c != " " else c
                   for c in text)


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
    ``MAX_LIFETIME``. Create and use it off the UI thread.

    ``disabled`` keeps a chat's turned-off servers out of it, so looking at
    the list doesn't start them. ``mcp_config`` is the ``--mcp-config`` the
    chat's turns get, so the session tools are listed as they are for a turn.
    Set ``on_expired`` to hear which sign-ins were still waiting when
    ``MAX_LIFETIME`` ended the process."""

    def __init__(self, executable: str, cwd: str,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 lifetime: float = MAX_LIFETIME, disabled: Collection[str] = (),
                 mcp_config: Optional[str] = None) -> None:
        if not cwd:
            raise McpError("This chat has no folder.")
        command = [executable, "-p", "--input-format", "stream-json", "--output-format",
                   "stream-json", "--verbose", "--permission-prompts", "none",
                   *settings_args(disabled)]
        if mcp_config:
            # Last: --mcp-config takes several values, as in a turn.
            command += ["--mcp-config", mcp_config]
        self.on_expired: Optional[Callable[[List[str]], None]] = None
        self._lifetime = lifetime
        self._callbacks: set = set()
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
        self._start_timer()
        threading.Thread(target=self._read, name="claude-mcp", daemon=True).start()
        self._write({"type": "control_request", "request_id": "init",
                     "request": {"subtype": "initialize", "hooks": None}})

    def _start_timer(self) -> None:
        self._timer = threading.Timer(self._lifetime, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def _expire(self) -> None:
        """``MAX_LIFETIME`` is up. A browser coming back after this finds
        nobody waiting, so whoever asked hears the sign-in has expired."""
        with self._lock:
            pending = [] if self._closed else sorted(self._callbacks)
        self.close()
        if pending and self.on_expired is not None:
            self.on_expired(pending)

    def _expect_callback(self, name: str) -> None:
        """A sign-in is waiting on this process: it gets the whole of
        ``MAX_LIFETIME`` from now, however long the dialog was already open."""
        with self._lock:
            if self._closed:
                return
            self._callbacks.add(name)
            self._timer.cancel()
            self._start_timer()

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

    @property
    def closed(self) -> bool:
        """Closed, ended by ``MAX_LIFETIME`` or died by itself: a new one is
        needed. ``close()`` is still wanted after it dies by itself, to end
        the job and any server it left behind."""
        return self._closed or self._process.poll() is not None

    def request(self, subtype: str, timeout: float = REQUEST_WAIT, **fields) -> dict:
        """Send one control request; its answer's body, or McpError with
        Claude Code's reason."""
        with self._lock:
            if self.closed:
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
            with self._lock:
                self._callbacks -= {s.name for s in servers if s.status == "connected"}
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
        expected = bool(body.get("callbackExpected"))
        if expected:
            self._expect_callback(name)
        return SignIn(url, expected)

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


def open_session(executable: str, cwd: str, disabled: Collection[str] = (),
                 mcp_config: Optional[str] = None) -> McpSession:
    """The session the window uses (tests put a fake here)."""
    return McpSession(executable, cwd, disabled=disabled, mcp_config=mcp_config)


def lifetime_minutes() -> int:
    return int(MAX_LIFETIME // 60)


def expired_text(names: Collection[str]) -> str:
    """What to say when ``MAX_LIFETIME`` ends a sign-in still waiting."""
    return (f"The sign-in for {_and([server_label(n) for n in names])} expired after "
            f"{lifetime_minutes()} minutes. If you didn't finish, press Sign In to start again.")


# ---------------------------------------------------------------------------
# Elicitation: a server asking you for input
# ---------------------------------------------------------------------------

#: PermissionRequest.tool_name for an elicitation, which is no tool.
ELICITATION = "MCP elicitation"

ACCEPT, DECLINE, CANCEL = "accept", "decline", "cancel"

#: The most a form may ask: past these it isn't one a person can answer by
#: keyboard, and building it would hold the window up.
MAX_FORM_FIELDS = 40
MAX_FORM_CHOICES = 100


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
    #: (value, label) for a choice or several choices; the value as the
    #: schema gives it (a number stays a number in the answer).
    choices: List[Tuple[object, str]] = field(default_factory=list)
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    format: str = ""
    min_items: Optional[int] = None
    max_items: Optional[int] = None

    def name(self) -> str:
        """The control's name for a screen reader: label, then "required"."""
        return self.label + (", required" if self.required else "")


def _choices(prop: dict) -> List[Tuple[object, str]]:
    """A schema's enum, with its titles: ``enum`` with ``enumNames``, or
    ``oneOf``/``anyOf`` of ``{const, title}`` (both forms MCP allows)."""
    values = prop.get("enum")
    if isinstance(values, list):
        names = prop.get("enumNames")
        names = names if isinstance(names, list) and len(names) == len(values) else values
        return [(v, str(n)) for v, n in zip(values, names)]
    options = prop.get("oneOf") or prop.get("anyOf")
    if isinstance(options, list):
        return [(o["const"], str(o.get("title") or o["const"])) for o in options
                if isinstance(o, dict) and "const" in o]
    return []


def _number(value) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else None


def _whole(value) -> Optional[int]:
    """A count from a schema; ``True`` is an int to Python but not a count."""
    return value if type(value) is int else None


def _type_of(prop: dict):
    """The one type a property's value has. ``["number", "null"]`` is a number
    that may be left empty, which is how an optional box already answers. A
    type of several kinds is returned whole, and isn't one a box can ask for."""
    kind = prop.get("type")
    if isinstance(kind, list):
        others = [k for k in kind if k != "null"]
        return others[0] if len(others) == 1 else kind
    return kind


def _lists_every_option(prop: dict) -> bool:
    """False for an ``enum`` or ``oneOf`` that doesn't name each value it
    allows: one with no values, or with a type in place of a value, can't be
    shown as a list. True with none of them, where there's no list to show."""
    if "enum" in prop:
        return bool(_choices(prop))
    if "oneOf" not in prop and "anyOf" not in prop:
        return True
    options = prop.get("oneOf") or prop.get("anyOf")
    return isinstance(options, list) and bool(options) and len(_choices(prop)) == len(options)


def _options_of(prop: dict) -> dict:
    """The part of a property that lists its choices: its items, for an array."""
    items = prop.get("items")
    return items if _type_of(prop) == "array" and isinstance(items, dict) else prop


def _cant_show(prop, is_required: bool) -> bool:
    """True when no control here could give a valid answer for ``prop``."""
    if not isinstance(prop, dict):
        return is_required
    kind = _type_of(prop)
    if kind not in (None, "string", "number", "integer", "boolean", "array"):
        return True  # a nested object, or a mix of types
    if any(word in prop for word in ("properties", "allOf", "$ref", "not", "if")):
        return True
    if kind == "array":
        choices = _choices(_options_of(prop)) if isinstance(prop.get("items"), dict) else []
        least, most = _whole(prop.get("minItems")), _whole(prop.get("maxItems"))
        return (not choices or not _lists_every_option(_options_of(prop))
                or (is_required and least is not None and least > len(choices))
                or (least is not None and most is not None and least > most))
    return not _lists_every_option(prop)


def form_problem(schema) -> str:
    """Why this form can't be answered here, as a sentence, or "". Sending
    what a box happens to hold would give the server something it didn't ask
    for (a nested object as a string), and leaving a required part out would
    send it nothing, so such a form can only be declined or left for later."""
    if not isinstance(schema, dict):
        return ""
    too_long = ("This form is too long for The Chat Place to show, so you can only decline it "
                "or answer later.")

    def cant(label) -> str:
        return (f"This form asks for something The Chat Place can't show "
                f"({_visible(str(label))[:80]}), so you can only decline it or answer later.")
    props = schema.get("properties", {})
    if schema.get("type") not in (None, "object") or not isinstance(props, dict):
        return cant("not a list of fields")
    if len(props) > MAX_FORM_FIELDS:
        return too_long
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    for key in required:
        if not isinstance(key, str) or key not in props:
            return cant(key)
    for key, prop in props.items():
        if isinstance(prop, dict) and len(_choices(_options_of(prop))) > MAX_FORM_CHOICES:
            return too_long
        if _cant_show(prop, key in required):
            return cant((prop.get("title") if isinstance(prop, dict) else None) or key)
    return ""


def form_fields(schema) -> List[FormField]:
    """The fields of a requested schema (a flat object of simple values, as
    MCP allows), in the server's order. ``form_problem`` says first whether
    the schema is one these can answer."""
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return []
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    fields_ = []
    for key, prop in props.items():
        if not isinstance(key, str) or not isinstance(prop, dict):
            continue
        kind_name = _type_of(prop)
        label = str(prop.get("title") or key)
        common = dict(key=key, label=label, description=str(prop.get("description") or ""),
                      required=key in required, default=prop.get("default"))
        if kind_name == "array":
            items = prop.get("items") if isinstance(prop.get("items"), dict) else {}
            fields_.append(FormField(kind="multi", choices=_choices(items),
                                     min_items=_whole(prop.get("minItems")),
                                     max_items=_whole(prop.get("maxItems")), **common))
        elif _choices(prop):
            fields_.append(FormField(kind="choice", choices=_choices(prop), **common))
        elif kind_name == "boolean":
            fields_.append(FormField(kind="boolean", **common))
        elif kind_name in ("number", "integer"):
            fields_.append(FormField(kind=kind_name, minimum=_number(prop.get("minimum")),
                                     maximum=_number(prop.get("maximum")), **common))
        else:
            fields_.append(FormField(kind="text", min_length=_whole(prop.get("minLength")),
                                     max_length=_whole(prop.get("maxLength")),
                                     format=str(prop.get("format") or ""), **common))
    return fields_


def _bounds(f: FormField) -> str:
    def show(n):
        return str(int(n)) if float(n).is_integer() else str(n)
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
        chosen = [v for v in value or [] if v in [c[0] for c in f.choices]]
        if f.required and not chosen and f.min_items != 0:
            return f"Choose at least {f.min_items or 'one'} for {f.label}."
        # Left empty, an optional one isn't answered at all, so its limits
        # don't come into it.
        if chosen and f.min_items is not None and len(chosen) < f.min_items:
            return f"Choose at least {f.min_items} for {f.label}."
        if f.max_items is not None and len(chosen) > f.max_items:
            return f"Choose at most {f.max_items} for {f.label}."
        if chosen or f.required:
            content[f.key] = chosen
        return ""
    if f.kind == "choice":
        if value is not None and value in [c[0] for c in f.choices]:
            content[f.key] = value
        elif f.required:
            return f"Choose one for {f.label}."
        return ""
    # As typed: spaces can be part of a passphrase. All spaces is still empty.
    text = str(value or "")
    if not text.strip():
        if f.required:
            return f"{f.label} is required."
        return ""
    if f.kind in ("number", "integer"):
        try:
            number = int(text.strip()) if f.kind == "integer" else float(text.strip())
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
    if f.format:
        # A checked format has no room for spaces around it, and a copied
        # address often brings some.
        text = text.strip()
    if f.min_length is not None and len(text) < f.min_length:
        return f"{f.label} must be at least {f.min_length} characters."
    if f.max_length is not None and len(text) > f.max_length:
        return f"{f.label} must be at most {f.max_length} characters."
    if f.format == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+", text):
        return f"{f.label} must be an email address."
    if f.format == "date" and not _is_date(text):
        return f"{f.label} must be a date written as year-month-day, such as 2026-11-02."
    if f.format == "date-time" and not _is_date_time(text):
        return f"{f.label} must be a date and time, such as 2026-11-02T14:30:00Z."
    if f.format == "uri" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*:\S+", text):
        return f"{f.label} must be a full address, such as https://example.com."
    content[f.key] = text
    return ""


def _is_date(text: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _is_date_time(text: str) -> bool:
    """RFC 3339, the form MCP's ``date-time`` means: with a zone."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})", text):
        return False
    plain = re.sub(r"\.\d+", "", text).upper().replace("Z", "+00:00")
    try:
        datetime.datetime.fromisoformat(plain)
    except ValueError:
        return False
    return True


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

    def form_problem(self) -> str:
        """Why this form can't be answered here, or "" (``form_problem``)."""
        return "" if self.is_url else form_problem(self.schema)

    def fields(self) -> List[FormField]:
        return [] if self.is_url or self.form_problem() else form_fields(self.schema)

    def summary(self) -> str:
        if self.is_url:
            return f"{self.server_label} asks you to open a web page"
        first = " ".join(self.message.split())
        if len(first) > 120:
            first = first[:117].rstrip() + "..."
        return f"{self.server_label} asks: {first}" if first \
            else f"{self.server_label} asks you for information"

    def detail(self) -> str:
        """The server's own words. A page's address is never among them
        (``address_text``): whatever comes after a message reads as more of
        it, and a message can carry a made-up address of its own."""
        lines = [self.title] if self.title else []
        lines.append(self.message or "(The server gave no message.)")
        if self.description:
            lines += ["", self.description]
        return "\n".join(lines)

    def address_text(self) -> str:
        """What the Address box says, from The Chat Place and not the server:
        where the page really is, on a line of its own, then the address."""
        host = web_url_host(self.url)
        if host:
            return f"Opens {host}\n{self.url}"
        return ("The Chat Place won't open this: it isn't a plain web page's address.\n"
                + (_visible(self.url) or "(The server gave no address.)"))

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
