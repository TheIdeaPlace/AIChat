"""One backend for every Agent Client Protocol agent (#489).

An agent is a row in ``agents.py``. What this file knows is the protocol: start
the program, initialize, open or reload a session, prompt, follow updates,
answer permission requests, cancel. Nothing here names an agent.

What a shared protocol can't promise is handled deliberately:

* ACP capabilities are optional, so a chat can't be reopened after a restart
  unless the agent said ``loadSession``; the chat then says so rather than
  failing strangely.
* Protocol v2 is a breaking revision, so anything but version 1 is refused.
* Only the permission request is accepted from the agent. Its other requests
  (files, terminals, elicitation) are refused, because this client offers none.
* Options are shown only from a short list of kinds this app understands: an
  unknown one might change what the agent may do without asking.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple

from .. import __version__, platform_paths, policy
from ..contracts import (Activity, AskPermission, Capability, ChatRef, Decision, Emit, Integration,
                         IntegrationError, Notice, OptionGroup, OptionsChanged, OptionValue, OwnedChat,
                         PermissionRequest, PlanUpdate, Refused, ReplyText, Status, TurnEnded, TurnStatus,
                         UnknownUpdate, UsageUpdate)
from ..drift import DriftCounters
from .jsonrpc import Connection, RpcError

PROTOCOL_VERSION = 1
#: ACP's code for "sign in first".
AUTH_REQUIRED = -32000
#: How long an agent gets to honour a cancel before its process is closed.
STOP_GRACE_SECONDS = 15.0
LOAD_SECONDS = 300.0
#: An option in one of these is shown; others are left alone.
SHOWN_CATEGORIES = {"model": "Model", "thought_level": "Effort"}
#: Updates understood and deliberately not shown.
IGNORED_UPDATES = {"available_commands_update", "session_info_update", "user_message_chunk"}
_IMPLEMENTED = frozenset({Capability.READ, Capability.SEND, Capability.STOP, Capability.RESUME,
                          Capability.APPROVALS, Capability.OPTIONS, Capability.USAGE, Capability.PLANS})


@dataclass(frozen=True)
class AgentDefinition:
    """All that's specific to one ACP agent. Adding an agent means adding one
    of these, then checking its install, sign-in, billing, permissions and
    lifecycle: being described here doesn't make it trusted."""
    id: str
    name: str
    program: str
    args: Tuple[str, ...]
    sign_in_hint: str
    billing: str
    npm_package: str = ""
    #: The names ``initialize`` may report. Empty accepts any.
    agent_names: Tuple[str, ...] = ()
    #: Versions whose approvals, cancel, reload and options have been exercised.
    verified_versions: Tuple[str, ...] = ()
    #: Mode id to TCP's own (label, description). Modes not here are never
    #: offered, and with any listed, an agent in another mode is stopped.
    safe_modes: Mapping[str, Tuple[str, str]] = field(default_factory=dict)
    #: Option id to values meaning the agent stops asking first.
    forbidden_options: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    strip_env_names: Tuple[str, ...] = ()
    strip_env_prefixes: Tuple[str, ...] = ()
    version_args: Tuple[str, ...] = ("--version",)
    enabled_by_default: bool = False


def _text(value: object, limit: int = 4096) -> str:
    return value[:limit] if isinstance(value, str) else ""


@dataclass
class _Live:
    native_id: str
    folder: str
    emit: Optional[Emit] = None
    busy: bool = False
    loading: bool = False
    stop_requested: bool = False
    safety_stop: str = ""
    block: int = 0
    block_open: bool = False
    mode: str = ""
    config: Dict[str, str] = field(default_factory=dict)
    groups: Dict[str, dict] = field(default_factory=dict)
    models: List[dict] = field(default_factory=list)
    current_model: str = ""
    modes_via_config: bool = False
    pending: Dict[str, Tuple[object, policy.AcpPermission]] = field(default_factory=dict)
    next_request: int = 0
    turn_number: int = 0


class AcpIntegration(Integration):
    implemented = _IMPLEMENTED
    app_keeps_history = True

    def __init__(self, definition: AgentDefinition,
                 start: Optional[Callable[[str], object]] = None):
        self.definition = definition
        self.id, self.name = definition.id, definition.name
        self.enabled_by_default = definition.enabled_by_default
        self.drift = DriftCounters()
        self._start = start
        self._lock = threading.RLock()
        #: Held while a program starts, which can take a minute. ``_lock`` isn't,
        #: because answering a request from the window takes it.
        self._connect_lock = threading.Lock()
        self._connection: Optional[Connection] = None
        self._can_load: Optional[bool] = None
        self._live: Dict[str, _Live] = {}
        self._version: Optional[str] = None

    # -- status ---------------------------------------------------------------

    def _program(self) -> Optional[str]:
        return platform_paths.find_program(self.definition.program, self.definition.npm_package)

    def status(self) -> Status:
        program = self._program() if self._start is None else "fake"
        if not program:
            return Status(False, f"{self.name} isn't installed.", self.definition.sign_in_hint,
                          self.definition.billing)
        if self._start is None and self._version is None:
            self._version = platform_paths.program_version(program, self.definition.version_args, self._environment())
        version = self._version or ""
        verified = platform_paths.version_is_checked(version, self.definition.verified_versions)
        summary = f"{self.name} is installed" + (f": {version}" if version else ".")
        return Status(True, summary, self.definition.sign_in_hint, self.definition.billing, verified, version)

    def advertised(self) -> Optional[FrozenSet[Capability]]:
        if self._can_load is False:
            return frozenset(_IMPLEMENTED - {Capability.RESUME})
        return frozenset(_IMPLEMENTED)

    def capabilities_for(self, chat: OwnedChat) -> FrozenSet[Capability]:
        caps = super().capabilities_for(chat)
        if chat.ref.native_id not in self._live and self._can_load is False:
            # Started before this run, by an agent that can't reopen a chat.
            return frozenset(caps - {Capability.SEND, Capability.STOP, Capability.OPTIONS, Capability.APPROVALS})
        return caps

    def check_prompt(self, text: str) -> Optional[str]:
        if text.lstrip().startswith("/"):
            # A slash command can change what an agent may do (/allow-all).
            return "The Chat Place doesn't send slash commands to this agent, because some change its permissions."
        return None

    # -- the process ----------------------------------------------------------

    def _environment(self) -> dict:
        return platform_paths.clean_environment(self.definition.strip_env_names,
                                                self.definition.strip_env_prefixes)

    def _spawn(self, cwd: str):
        if self._start is not None:
            return self._start(cwd)
        program = self._program()
        if not program:
            raise IntegrationError(f"{self.name} isn't installed. {self.definition.sign_in_hint}")
        try:
            return platform_paths.OwnedProcess([program, *self.definition.args], env=self._environment(), cwd=cwd)
        except OSError:
            raise IntegrationError(f"{self.name} couldn't be started.") from None

    def _connect(self, cwd: str) -> Connection:
        with self._connect_lock:
            with self._lock:
                if self._connection is not None and not self._connection.closed:
                    return self._connection
                self._live.clear()
            connection = Connection(self._spawn(cwd), self._on_notification, self._on_request,
                                    self._on_closed, self.drift, self.name)
            try:
                self._check_initialize(connection.request("initialize", {
                    "protocolVersion": PROTOCOL_VERSION,
                    "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                           "terminal": False},
                    "clientInfo": {"name": "thechatplace", "title": "The Chat Place",
                                   "version": __version__}}))
            except Exception:
                connection.close()
                raise
            with self._lock:
                self._connection = connection
            return connection

    def _check_initialize(self, result: object) -> None:
        result = result if isinstance(result, dict) else {}
        info = result.get("agentInfo") if isinstance(result.get("agentInfo"), dict) else {}
        names = self.definition.agent_names
        if result.get("protocolVersion") != PROTOCOL_VERSION or (names and info.get("name") not in names):
            raise IntegrationError(f"This version of {self.name} isn't supported.")
        caps = result.get("agentCapabilities") if isinstance(result.get("agentCapabilities"), dict) else {}
        self._can_load = caps.get("loadSession") is True

    def _on_closed(self, connection: Connection) -> None:
        with self._lock:
            if self._connection is connection:
                self._connection = None

    def _request(self, connection: Connection, method: str, params: dict, timeout=60.0):
        try:
            return connection.request(method, params, timeout=timeout)
        except RpcError as error:
            if error.code == AUTH_REQUIRED:
                raise IntegrationError(f"{self.name} isn't signed in. {self.definition.sign_in_hint}") from None
            raise

    # -- chats ----------------------------------------------------------------

    def new_chat_options(self) -> Tuple[OptionGroup, ...]:
        modes = self.definition.safe_modes
        if not modes:
            return ()
        values = tuple(OptionValue(mode, label, words) for mode, (label, words) in modes.items())
        return (OptionGroup("mode", "Mode", values, values[0].id),)

    def start_chat(self, folder: str, title: str, options: Mapping[str, str]) -> OwnedChat:
        connection = self._connect(folder)
        result = self._request(connection, "session/new", {"cwd": folder, "mcpServers": []})
        native_id = result.get("sessionId") if isinstance(result, dict) else None
        if not isinstance(native_id, str) or not native_id:
            raise IntegrationError(f"{self.name} didn't start a chat.")
        live = _Live(native_id, folder)
        self._read_settings(live, result)
        with self._lock:
            self._live[native_id] = live
        problem = self._safety_problem(live)
        if problem:
            self._forget(connection, live)
            raise Refused(f"{self.name} would start without asking before it acts, so the chat wasn't started.")
        chosen = dict(options)
        if chosen.get("mode") and chosen["mode"] != live.mode:
            self._set_mode(connection, live, chosen["mode"])
        return OwnedChat(ChatRef(self.id, native_id), title, folder, options=chosen)

    def _forget(self, connection: Connection, live: _Live) -> None:
        with self._lock:
            self._live.pop(live.native_id, None)
        try:
            connection.request("session/close", {"sessionId": live.native_id}, timeout=5)
        except RpcError:
            pass

    def _read_settings(self, live: _Live, result: object) -> None:
        if not isinstance(result, dict):
            return
        self._read_config(live, result.get("configOptions"))
        modes = result.get("modes")
        if isinstance(modes, dict) and isinstance(modes.get("currentModeId"), str):
            live.mode = modes["currentModeId"]
        models = result.get("models")
        if isinstance(models, dict):
            if isinstance(models.get("availableModels"), list):
                live.models = [m for m in models["availableModels"] if isinstance(m, dict)]
            if isinstance(models.get("currentModelId"), str):
                live.current_model = models["currentModelId"]

    def _read_config(self, live: _Live, options: object) -> None:
        if not isinstance(options, list):
            return
        for option in options:
            if not isinstance(option, dict) or not isinstance(option.get("id"), str):
                continue
            live.groups[option["id"]] = option
            if isinstance(option.get("currentValue"), str):
                live.config[option["id"]] = option["currentValue"]
                if option["id"] == "mode" or option.get("category") == "mode":
                    live.mode = option["currentValue"]
                    live.modes_via_config = True

    def _safety_problem(self, live: _Live) -> Optional[str]:
        return (policy.mode_problem(live.mode, self.definition.safe_modes)
                or policy.option_problem(live.config, self.definition.forbidden_options))

    def _set_mode(self, connection: Connection, live: _Live, mode: str) -> None:
        if mode not in self.definition.safe_modes:
            raise Refused("That mode isn't one The Chat Place can offer for this agent.")
        if live.modes_via_config:
            result = self._request(connection, "session/set_config_option",
                                   {"sessionId": live.native_id, "configId": "mode", "value": mode})
            self._read_settings(live, result)
        else:
            self._request(connection, "session/set_mode", {"sessionId": live.native_id, "modeId": mode})
            live.mode = mode
        if live.mode != mode or self._safety_problem(live):
            # The reply decides, not the request: an agent may keep its old
            # mode, or answer yes and switch to something else.
            raise IntegrationError(f"{self.name} didn't change to that mode.")

    def _open(self, chat: OwnedChat) -> Tuple[Connection, _Live]:
        """This chat's session in the running agent, reloading it if needed."""
        connection = self._connect(chat.folder)
        with self._lock:
            live = self._live.get(chat.ref.native_id)
        if live is not None:
            return connection, live
        if not self._can_load:
            raise Refused(f"{self.name} can't reopen a chat after The Chat Place restarts. Start a new chat instead.")
        live = _Live(chat.ref.native_id, chat.folder, loading=True)
        with self._lock:
            self._live[live.native_id] = live
        try:
            result = self._request(connection, "session/load",
                                   {"sessionId": live.native_id, "cwd": chat.folder, "mcpServers": []},
                                   timeout=LOAD_SECONDS)
        except Exception:
            with self._lock:
                self._live.pop(live.native_id, None)
            raise
        live.loading = False
        self._read_settings(live, result)
        wanted = chat.options.get("mode")
        if wanted and wanted != live.mode:
            try:
                self._set_mode(connection, live, wanted)
            except Exception:
                # Forgotten, so the next turn tries again rather than running
                # in a mode the chat isn't set to.
                with self._lock:
                    self._live.pop(live.native_id, None)
                raise
        return connection, live

    def run_turn(self, chat: OwnedChat, text: str, emit: Emit) -> None:
        try:
            connection, live = self._open(chat)
        except IntegrationError as error:
            emit(TurnEnded(TurnStatus.FAILED, str(error)))
            return
        problem = self._safety_problem(live)
        if problem:
            emit(TurnEnded(TurnStatus.FAILED, problem))
            return
        with self._lock:
            live.emit, live.busy, live.stop_requested, live.safety_stop = emit, True, False, ""
            live.block_open = False
            live.turn_number += 1
        try:
            result = self._request(connection, "session/prompt",
                                   {"sessionId": live.native_id,
                                    "prompt": [{"type": "text", "text": text}]}, timeout=None)
            reason = result.get("stopReason") if isinstance(result, dict) else None
            emit(self._ending(live, reason))
        except IntegrationError as error:
            if live.stop_requested:
                emit(TurnEnded(TurnStatus.STOPPED, live.safety_stop))
            else:
                emit(TurnEnded(TurnStatus.FAILED, str(error)))
        finally:
            with self._lock:
                live.busy, live.emit = False, None
                pending, live.pending = live.pending, {}
            for request_id, _permission in pending.values():
                self._answer_cancelled(connection, request_id)

    def _ending(self, live: _Live, reason: object) -> TurnEnded:
        if live.safety_stop:
            return TurnEnded(TurnStatus.STOPPED, live.safety_stop)
        if reason == "cancelled" or live.stop_requested:
            return TurnEnded(TurnStatus.STOPPED)
        if reason == "end_turn":
            return TurnEnded(TurnStatus.COMPLETED)
        if reason == "refusal":
            return TurnEnded(TurnStatus.FAILED, f"{self.name} refused that request.")
        if reason in ("max_tokens", "max_turn_requests"):
            return TurnEnded(TurnStatus.FAILED, f"{self.name} reached a limit and stopped before finishing.")
        self.drift.unknown(f"stopReason:{reason}")
        return TurnEnded(TurnStatus.COMPLETED)

    def stop(self, ref: ChatRef) -> None:
        with self._lock:
            live, connection = self._live.get(ref.native_id), self._connection
            if live is None or connection is None or not live.busy:
                return
            first = not live.stop_requested
            live.stop_requested = True
            pending, live.pending = live.pending, {}
        for request_id, _permission in pending.values():
            self._answer_cancelled(connection, request_id)
        try:
            connection.notify("session/cancel", {"sessionId": live.native_id})
        except RpcError:
            pass
        if not first:
            return

        turn_number = live.turn_number

        def give_up() -> None:
            # An agent that ignores a cancel leaves no other way to stop. The
            # process is shared, so other chats reopen on their next message.
            if live.busy and live.stop_requested and live.turn_number == turn_number:
                connection.close()
        timer = threading.Timer(STOP_GRACE_SECONDS, give_up)
        timer.daemon = True
        timer.start()

    # -- what the agent sends -------------------------------------------------

    def _answer_cancelled(self, connection: Connection, request_id) -> None:
        try:
            connection.respond(request_id, {"outcome": {"outcome": "cancelled"}})
        except RpcError:
            pass

    def _on_request(self, connection: Connection, request_id, method: str, params: dict) -> None:
        if method != "session/request_permission":
            self.drift.unknown(f"request:{method}")
            live = self._live.get(_text(params.get("sessionId"), 500))
            if live is not None and live.emit is not None:
                live.emit(UnknownUpdate(f"request:{method}"))
            connection.respond_error(request_id, -32601, "The Chat Place doesn't support this request.")
            return
        with self._lock:
            live = self._live.get(_text(params.get("sessionId"), 500))
            stale = connection is not self._connection
        if stale or live is None or not live.busy or live.emit is None or live.loading or live.stop_requested:
            self._answer_cancelled(connection, request_id)
            return
        permission = policy.read_acp_options(params.get("options"))
        if permission is None:
            self.drift.malformed("permission-options")
            self._answer_cancelled(connection, request_id)
            live.emit(Notice(f"{self.name} asked for permission without a way to say no, "
                             "so The Chat Place refused. Nothing was allowed."))
            return
        detail = self._describe_call(params.get("toolCall"))
        # With nothing to read, there's nothing to allow.
        decisions = permission.decisions if detail else (Decision.DENY,)
        with self._lock:
            live.next_request += 1
            key = str(live.next_request)
            live.pending[key] = (request_id, policy.AcpPermission(decisions, permission.option_ids))
        live.emit(AskPermission(PermissionRequest(
            key, f"{self.name} asks for permission.", detail, decisions,
            "" if self.status_verified() else policy.UNVERIFIED_CAUTION)))

    def status_verified(self) -> bool:
        if self._start is not None:
            return bool(self.definition.verified_versions)
        return self.status().verified

    @staticmethod
    def _describe_call(call: object) -> str:
        if not isinstance(call, dict):
            return ""
        lines = [_text(call.get("title"), 500)]
        raw = call.get("rawInput") if isinstance(call.get("rawInput"), dict) else {}
        for key in ("command", "commandLine", "fileName", "path", "url"):
            if isinstance(raw.get(key), str):
                lines.append(f"{key}: {raw[key][:2000]}")
        return "\n".join(line for line in lines if line)

    def answer_permission(self, ref: ChatRef, request_id: str, decision: Decision) -> None:
        with self._lock:
            live, connection = self._live.get(ref.native_id), self._connection
            found = live.pending.pop(request_id, None) if live is not None else None
        if found is None or connection is None:
            return
        rpc_id, permission = found
        choice = policy.checked_decision(decision, permission.decisions)
        connection.respond(rpc_id, {"outcome": {"outcome": "selected", "optionId": permission.option_ids[choice]}})

    def _on_notification(self, connection: Connection, method: str, params: dict) -> None:
        if connection is not self._connection and self._connection is not None:
            return  # a late message from a connection that's been replaced
        if method != "session/update":
            self.drift.unknown(f"notification:{method}")
            return
        update = params.get("update")
        with self._lock:
            live = self._live.get(_text(params.get("sessionId"), 500))
        if live is None or not isinstance(update, dict):
            return
        kind = update.get("sessionUpdate")
        if kind in ("config_option_update", "current_mode_update"):
            # Settings count even while history replays.
            self._read_config(live, update.get("configOptions"))
            if kind == "current_mode_update" and isinstance(update.get("currentModeId"), str):
                live.mode = update["currentModeId"]
            self._after_settings(live)
            return
        if live.loading or live.emit is None:
            return  # replayed history: the app has its own copy
        emit = live.emit
        if kind == "agent_message_chunk":
            content = update.get("content") if isinstance(update.get("content"), dict) else {}
            piece = _text(content.get("text"), 1024 * 1024)
            if piece:
                if not live.block_open:
                    live.block += 1
                    live.block_open = True
                emit(ReplyText(piece, str(live.block)))
        elif kind in ("tool_call", "tool_call_update"):
            live.block_open = False
            title = _text(update.get("title"), 500)
            if title:
                emit(Activity(title))
        elif kind == "agent_thought_chunk":
            self.drift.ignored(kind)
        elif kind == "plan":
            entries = update.get("entries") if isinstance(update.get("entries"), list) else []
            emit(PlanUpdate(tuple((_text(e.get("content"), 500), _text(e.get("status"), 50))
                                  for e in entries[:256] if isinstance(e, dict))))
        elif kind == "usage_update":
            used, size = update.get("used"), update.get("size")
            if type(used) is int and type(size) is int:
                emit(UsageUpdate(used, size))
        elif kind in IGNORED_UPDATES:
            self.drift.ignored(kind)
        else:
            self.drift.unknown(f"update:{kind}")
            emit(UnknownUpdate(f"update:{kind}"))

    def _after_settings(self, live: _Live) -> None:
        problem = self._safety_problem(live)
        emit = live.emit
        if emit is not None:
            emit(OptionsChanged())
        if problem and live.busy and not live.stop_requested:
            live.safety_stop = problem
            self.stop(ChatRef(self.id, live.native_id))

    # -- options --------------------------------------------------------------

    def chat_options(self, chat: OwnedChat) -> Tuple[OptionGroup, ...]:
        with self._lock:
            live = self._live.get(chat.ref.native_id)
        groups: List[OptionGroup] = []
        safe = self.definition.safe_modes
        if safe:
            current = live.mode if live is not None else chat.options.get("mode", "")
            groups.append(OptionGroup("mode", "Mode", tuple(OptionValue(m, label, words)
                                                            for m, (label, words) in safe.items()), current))
        if live is None:
            return tuple(groups)
        for option_id, option in live.groups.items():
            label = SHOWN_CATEGORIES.get(_text(option.get("category"), 50))
            if option_id == "model":
                label = "Model"
            values = self._flat_values(option.get("options"))
            if label and values:
                groups.append(OptionGroup(option_id, label, values, live.config.get(option_id, "")))
        if live.models and "model" not in live.groups:
            values = tuple(OptionValue(_text(m.get("modelId"), 200), _text(m.get("name"), 200) or
                                       _text(m.get("modelId"), 200), _text(m.get("description"), 300))
                           for m in live.models if _text(m.get("modelId"), 200))
            if values:
                groups.append(OptionGroup("session-model", "Model", values, live.current_model))
        return tuple(groups)

    @staticmethod
    def _flat_values(options: object) -> Tuple[OptionValue, ...]:
        """One value of each: an agent may list a value twice."""
        seen, values = set(), []
        for option in options if isinstance(options, list) else ():
            if not isinstance(option, dict) or not isinstance(option.get("value"), str):
                continue
            if option["value"] not in seen:
                seen.add(option["value"])
                values.append(OptionValue(option["value"], _text(option.get("name"), 200) or option["value"],
                                          _text(option.get("description"), 300)))
        return tuple(values)

    def set_option(self, chat: OwnedChat, group: str, value: str) -> None:
        connection, live = self._open(chat)
        if group == "mode":
            self._set_mode(connection, live, value)
            chat.options["mode"] = value
            return
        if not any(g.id == group and any(v.id == value for v in g.values) for g in self.chat_options(chat)):
            raise Refused("That isn't one of this chat's options.")
        if group == "session-model":
            self._request(connection, "session/set_model", {"sessionId": live.native_id, "modelId": value})
            live.current_model = value
            return
        result = self._request(connection, "session/set_config_option",
                               {"sessionId": live.native_id, "configId": group, "value": value})
        self._read_settings(live, result)
        problem = self._safety_problem(live)
        if problem:
            raise IntegrationError(problem)

    def close(self) -> None:
        with self._lock:
            connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()
