"""Codex through its own app-server: the native route #489 keeps.

It stays native because the app-server gives what ACP doesn't: readable history
from outside this app, and a sign-in that can be checked before each chat. It
reaches the UI only through the shared contracts.

Chats start read-only unless the user chooses otherwise. Before a chat starts,
the account must be ChatGPT sign-in. Approvals are offered only when they're
fully understood: anything with extra access, a network request or a kind this
app can't read gets Deny alone. Requests it doesn't know are refused.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple

from .. import __version__, platform_paths
from ..contracts import (Activity, AskPermission, AskQuestion, Capability, ChatInfo, ChatRef, Decision, Emit,
                         Integration, IntegrationError, Message, Notice, OptionGroup, OptionValue, OwnedChat,
                         PermissionRequest, PlanUpdate, QuestionItem, QuestionRequest, Refused, ReplyText, Status,
                         TurnEnded, TurnStatus, UnknownUpdate, UsageUpdate, effective_capabilities)
from ..drift import DriftCounters
from ..policy import DETAIL_LIMIT, checked_decision
from .jsonrpc import Connection, RpcError

COMMAND, FILES, QUESTIONS = ("item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                             "item/tool/requestUserInput")
BILLING_NAMES = ("CODEX_API_KEY", "CODEX_BASE_URL", "CODEX_CHATGPT_BASE_URL", "CODEX_AUTH_TOKEN",
                 "CHATGPT_BASE_URL", "AZURE_API_KEY")
BILLING_PREFIXES = ("OPENAI_", "AZURE_OPENAI_")
CHATGPT_BASE_URL = "https://chatgpt.com/backend-api/"
STOP_GRACE_SECONDS = 15.0
#: Versions whose approvals, cancel, resume and history have been exercised.
CHECKED_VERSIONS = ("0.163.0",)
ACTIVITIES = {"reasoning": "Thinking.", "webSearch": "Searching the web.", "mcpToolCall": "Using a tool."}
IGNORED = {"thread/started", "thread/status/changed", "turn/started", "turn/diff/updated",
           "account/updated", "account/rateLimits/updated", "skills/changed", "thread/name/updated",
           "item/commandExecution/outputDelta", "item/reasoning/summaryTextDelta",
           "item/reasoning/summaryPartAdded", "item/reasoning/textDelta", "item/fileChange/outputDelta",
           "mcpServer/startupStatus/updated", "serverRequest/resolved", "configWarning",
           "remoteControl/status/changed", "turn/moderationMetadata", "thread/readState/changed",
           "thread/goal/updated", "thread/goal/cleared"}
KNOWN_ITEMS = {"userMessage", "agentMessage", "commandExecution", "fileChange", "contextCompaction", "plan",
               *ACTIVITIES}
_IMPLEMENTED = frozenset({Capability.READ, Capability.SEND, Capability.STOP, Capability.RESUME,
                          Capability.APPROVALS, Capability.QUESTIONS, Capability.OPTIONS,
                          Capability.USAGE, Capability.PLANS})
FILE_ACCESS = OptionGroup("sandbox", "File access", (
    OptionValue("read-only", "Read only", "Can read this folder but not change it."),
    OptionValue("workspace-write", "Edit this folder", "Can change files in this folder; asks before going further."),
), "read-only")


def _text(value: object, limit: int = 4096) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _sandbox_config(sandbox: str) -> dict:
    if sandbox == "read-only":
        return {}
    # Pin these rather than inherit any broader roots from the user's config.
    return {"sandbox_workspace_write": {"writable_roots": [], "network_access": False,
                                        "exclude_slash_tmp": True, "exclude_tmpdir_env_var": True}}


def _turn_policy(sandbox: str, folder: str) -> dict:
    if sandbox == "read-only":
        return {"type": "readOnly"}
    return {"type": "workspaceWrite", "writableRoots": [folder], "networkAccess": False,
            "excludeSlashTmp": True, "excludeTmpdirEnvVar": True}


@dataclass
class _Turn:
    emit: Emit
    done: threading.Event = field(default_factory=threading.Event)
    turn_id: str = ""
    status: str = ""
    #: Which connection this turn runs on: another closing doesn't end it.
    connection: Optional[Connection] = None
    stop_requested: bool = False
    pending: Dict[str, Tuple[object, str, tuple]] = field(default_factory=dict)
    next_request: int = 0
    files: Dict[str, str] = field(default_factory=dict)


class CodexIntegration(Integration):
    id = "codex"
    name = "Codex"
    implemented = _IMPLEMENTED

    def __init__(self, start: Optional[Callable[[], object]] = None):
        self.drift = DriftCounters()
        self._start = start
        self._lock = threading.RLock()
        #: Held while the program starts. ``_lock`` isn't, because answering a
        #: request from the window takes it.
        self._connect_lock = threading.Lock()
        #: Counts closes, so a program still starting when close came isn't kept.
        self._closes = 0
        self._connection: Optional[Connection] = None
        self._turns: Dict[str, _Turn] = {}
        self._resumed: set = set()
        self._version: Optional[str] = None

    def _program(self) -> Optional[str]:
        return platform_paths.find_program("codex", "@openai/codex")

    def _environment(self) -> dict:
        return platform_paths.clean_environment(BILLING_NAMES, BILLING_PREFIXES)

    def status(self) -> Status:
        billing = "Your ChatGPT plan. A chat won't start on any other sign-in."
        hint = "Run codex login in a terminal, then try again."
        if self._start is not None:
            return Status(True, "Codex is installed.", hint, billing, True)
        program = self._program()
        if not program:
            return Status(False, "Codex isn't installed, or only as a launcher this app won't start.", hint, billing)
        if self._version is None:
            self._version = platform_paths.program_version(program, env=self._environment())
        checked = platform_paths.version_is_checked(self._version, CHECKED_VERSIONS)
        return Status(True, f"Codex is installed: {self._version}", hint, billing, checked, self._version)

    # -- the app-server -------------------------------------------------------

    def _connect(self) -> Connection:
        with self._connect_lock:
            with self._lock:
                if self._connection is not None and not self._connection.closed:
                    return self._connection
                self._resumed.clear()
                closes = self._closes
            if self._start is not None:
                process = self._start()
            else:
                program = self._program()
                if not program:
                    raise IntegrationError("Codex isn't installed.")
                try:
                    process = platform_paths.OwnedProcess([program, "app-server", "--listen", "stdio://"],
                                                          env=self._environment())
                except OSError:
                    raise IntegrationError("Codex couldn't be started.") from None
            connection = Connection(process, self._on_notification, self._on_request,
                                    self._on_closed, self.drift, "Codex")
            try:
                connection.request("initialize", {
                    "clientInfo": {"name": "thechatplace", "title": "The Chat Place", "version": __version__},
                    "capabilities": {"experimentalApi": True}})
                connection.notify("initialized", {})
            except Exception:
                connection.close()
                raise
            with self._lock:
                if closes == self._closes:
                    self._connection = connection
                    return connection
            connection.close()
            raise IntegrationError("Codex was turned off as it started.")

    def _on_closed(self, connection: Connection) -> None:
        with self._lock:
            if self._connection is connection:
                self._connection = None
            turns = [t for t in self._turns.values() if t.connection is connection]
        for turn in turns:
            turn.done.set()

    def _thread_params(self, chat_folder: str, options: Mapping[str, str]) -> dict:
        sandbox = options.get("sandbox", "read-only")
        if sandbox not in ("read-only", "workspace-write"):
            raise Refused("That file access isn't one The Chat Place offers.")
        return {"cwd": chat_folder, "modelProvider": "openai", "sandbox": sandbox,
                "approvalPolicy": "on-request",
                "config": {"model_provider": "openai", "model_providers": {}, "forced_login_method": "chatgpt",
                           "openai_base_url": None, "chatgpt_base_url": CHATGPT_BASE_URL,
                           "approvals_reviewer": "user", **_sandbox_config(sandbox)}}

    def _check_account(self, connection: Connection) -> None:
        result = connection.request("account/read", {"refreshToken": True})
        account = result.get("account") if isinstance(result, dict) else None
        if not isinstance(account, dict) or account.get("type") != "chatgpt":
            raise Refused("Codex isn't signed in with ChatGPT, so The Chat Place didn't start it. "
                          "Run codex login in a terminal, then try again.")

    # -- chats ----------------------------------------------------------------

    def new_chat_options(self) -> Tuple[OptionGroup, ...]:
        return (FILE_ACCESS,)

    def chat_options(self, chat: OwnedChat) -> Tuple[OptionGroup, ...]:
        return (OptionGroup(FILE_ACCESS.id, FILE_ACCESS.label, FILE_ACCESS.values,
                            chat.options.get("sandbox", "read-only")),)

    def set_option(self, chat: OwnedChat, group: str, value: str) -> None:
        if group != "sandbox" or value not in ("read-only", "workspace-write"):
            raise Refused("That isn't one of this chat's options.")
        chat.options["sandbox"] = value  # each turn says what it may do

    def check_prompt(self, text: str) -> Optional[str]:
        if text.lstrip().startswith("/"):
            # Codex's commands are its terminal's: here it'd only be text.
            return "Codex runs slash commands only in its own terminal. Sent from here, the model would just read it as text."
        return None

    def start_chat(self, folder: str, title: str, options: Mapping[str, str]) -> OwnedChat:
        connection = self._connect()
        self._check_account(connection)
        chosen = {"sandbox": options.get("sandbox", "read-only")}
        result = connection.request("thread/start", {**self._thread_params(folder, chosen), "ephemeral": False})
        thread = result.get("thread") if isinstance(result, dict) else None
        native_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(native_id, str) or not native_id:
            raise IntegrationError("Codex didn't start a chat.")
        self._resumed.add(native_id)
        return OwnedChat(ChatRef(self.id, native_id), title, folder, options=chosen)

    def _ensure_resumed(self, connection: Connection, chat: OwnedChat) -> None:
        if chat.ref.native_id in self._resumed:
            return
        self._check_account(connection)
        connection.request("thread/resume", {**self._thread_params(chat.folder, chat.options),
                                             "threadId": chat.ref.native_id})
        self._resumed.add(chat.ref.native_id)

    def run_turn(self, chat: OwnedChat, text: str, emit: Emit) -> None:
        native_id = chat.ref.native_id
        turn = _Turn(emit)
        with self._lock:
            self._turns[native_id] = turn
        try:
            connection = self._connect()
            turn.connection = connection
            if connection.closed:
                turn.done.set()
            self._ensure_resumed(connection, chat)
            sandbox = chat.options.get("sandbox", "read-only")
            result = connection.request("turn/start", {
                "threadId": native_id, "cwd": chat.folder, "approvalPolicy": "on-request",
                "approvalsReviewer": "user", "sandboxPolicy": _turn_policy(sandbox, chat.folder),
                "input": [{"type": "text", "text": text}]})
            started = result.get("turn") if isinstance(result, dict) else None
            if isinstance(started, dict) and isinstance(started.get("id"), str):
                turn.turn_id = started["id"]
                if turn.stop_requested:
                    self._interrupt(connection, native_id, turn)
            turn.done.wait()
            if connection.closed and not turn.status:
                raise IntegrationError("Codex closed before the turn ended.")
            emit(self._ending(turn))
        except IntegrationError as error:
            emit(TurnEnded(TurnStatus.STOPPED if turn.stop_requested else TurnStatus.FAILED,
                           "" if turn.stop_requested else str(error)))
        finally:
            with self._lock:
                self._turns.pop(native_id, None)
            if turn.connection is not None:
                for rpc_id, method, _offered in turn.pending.values():
                    self._decline(turn.connection, rpc_id, method)

    @staticmethod
    def _ending(turn: _Turn) -> TurnEnded:
        if turn.status == "failed":
            # Codex's own error text may hold account details, so it isn't copied.
            return TurnEnded(TurnStatus.FAILED, "Codex couldn't finish that turn.")
        if turn.status == "interrupted" or turn.stop_requested:
            return TurnEnded(TurnStatus.STOPPED)
        return TurnEnded(TurnStatus.COMPLETED)

    def _interrupt(self, connection: Connection, native_id: str, turn: _Turn) -> None:
        threading.Thread(target=self._send_interrupt, args=(connection, native_id, turn.turn_id),
                         daemon=True).start()

    @staticmethod
    def _send_interrupt(connection: Connection, native_id: str, turn_id: str) -> None:
        try:
            connection.request("turn/interrupt", {"threadId": native_id, "turnId": turn_id}, timeout=10)
        except RpcError:
            pass

    def stop(self, ref: ChatRef) -> None:
        with self._lock:
            turn = self._turns.get(ref.native_id)
            connection = turn.connection if turn is not None else None
            if turn is None or connection is None:
                return
            first = not turn.stop_requested
            turn.stop_requested = True
            pending, turn.pending = turn.pending, {}
        for rpc_id, method, _offered in pending.values():
            self._decline(connection, rpc_id, method)
        if turn.turn_id:
            self._interrupt(connection, ref.native_id, turn)
        if not first:
            return

        def give_up() -> None:
            # Codex ignored the interrupt, so nothing else can end the turn.
            # The app-server is shared: other chats resume on their next message.
            # Only if this turn is still the one running: one that has already
            # ended must not take another chat's turn down with it.
            with self._lock:
                running = self._turns.get(ref.native_id) is turn
            if running and not turn.status:
                connection.close()
                turn.done.set()
        timer = threading.Timer(STOP_GRACE_SECONDS, give_up)
        timer.daemon = True
        timer.start()

    # -- what Codex sends -----------------------------------------------------

    def _decline(self, connection: Connection, rpc_id, method: str) -> None:
        try:
            if method == QUESTIONS:
                connection.respond_error(rpc_id, -32000, "The question was not answered.")
            else:
                connection.respond(rpc_id, {"decision": "decline"})
        except RpcError:
            pass

    def _current_turn(self, connection: Connection, params: dict) -> Optional[_Turn]:
        """The turn this message belongs to, or None. A turn that was given up
        on can still be sending, and must never reach the one after it."""
        with self._lock:
            turn = self._turns.get(_text(params.get("threadId"), 256))
        if turn is None or turn.connection is not connection:
            return None
        said = _text(params.get("turnId"), 256)
        if not said and isinstance(params.get("turn"), dict):
            said = _text(params["turn"].get("id"), 256)
        if said and turn.turn_id and said != turn.turn_id:
            self.drift.ignored("message-from-an-earlier-turn")
            return None
        return turn

    def _on_request(self, connection: Connection, rpc_id, method: str, params: dict) -> None:
        turn = self._current_turn(connection, params)
        if method not in (COMMAND, FILES, QUESTIONS):
            self.drift.unknown(f"request:{method}")
            connection.respond_error(rpc_id, -32601, "The Chat Place doesn't support this request.")
            if turn is not None:
                turn.emit(UnknownUpdate(f"request:{method}"))
                turn.emit(Notice(f"Codex asked for something The Chat Place can't show yet, so it was refused. "
                                 "Nothing was allowed."))
            return
        if turn is None or turn.stop_requested:
            self._decline(connection, rpc_id, method)
            return
        if method == QUESTIONS:
            self._ask_question(connection, turn, rpc_id, params)
            return
        offered, summary, detail = self._read_approval(turn, method, params)
        with self._lock:
            turn.next_request += 1
            key = str(turn.next_request)
            turn.pending[key] = (rpc_id, method, offered)
        turn.emit(AskPermission(PermissionRequest(key, summary, detail, offered)))

    def _read_approval(self, turn: _Turn, method: str, params: dict) -> Tuple[Tuple[Decision, ...], str, str]:
        """Only a request understood completely can be allowed."""
        deny_only = (Decision.DENY,)
        both = (Decision.ALLOW_ONCE, Decision.DENY)
        listed = params.get("availableDecisions")
        accepted = listed is None or (isinstance(listed, list) and "accept" in listed)
        reason = _text(params.get("reason"), 2000)
        if method == FILES:
            # Allow rests on which files, never on Codex's reason for them alone.
            files = turn.files.get(_text(params.get("itemId"), 256), "")
            detail = "\n".join(filter(None, [files, reason]))
            if params.get("grantRoot"):
                return deny_only, "Codex asks to write outside this chat's folder.", detail
            return (both if files and accepted else deny_only), "Codex asks to change files.", detail
        command = params.get("command") if isinstance(params.get("command"), str) else ""
        lines = [f"Command: {command[:DETAIL_LIMIT]}" if command else "", f"Folder: {_text(params.get('cwd'), 1000)}", reason]
        detail = "\n".join(filter(None, lines))
        extra = (params.get("kind", "command") != "command" or params.get("networkApprovalContext")
                 or params.get("additionalPermissions")
                 or params.get("environmentId") not in (None, "local")
                 or len(command) > DETAIL_LIMIT)  # the rest of it couldn't be read
        if extra or not command or not accepted:
            return deny_only, "Codex asks for access The Chat Place can't fully show, so it can only be denied.", detail
        return both, "Codex asks to run a command.", detail

    def _ask_question(self, connection: Connection, turn: _Turn, rpc_id, params: dict) -> None:
        items: List[QuestionItem] = []
        questions = params.get("questions")
        for question in questions if isinstance(questions, list) else ():
            if not isinstance(question, dict) or not isinstance(question.get("id"), str) or question.get("isSecret"):
                self._decline(connection, rpc_id, QUESTIONS)
                return
            choices = tuple(OptionValue(_text(o.get("label"), 500), _text(o.get("label"), 500),
                                        _text(o.get("description"), 1000))
                            for o in question.get("options") or () if isinstance(o, dict) and o.get("label"))
            items.append(QuestionItem(question["id"], _text(question.get("question"), 4000), choices,
                                      bool(question.get("isOther")) or not choices))
        if not items:
            self._decline(connection, rpc_id, QUESTIONS)
            return
        with self._lock:
            turn.next_request += 1
            key = str(turn.next_request)
            turn.pending[key] = (rpc_id, QUESTIONS, tuple(items))
        turn.emit(AskQuestion(QuestionRequest(key, tuple(items))))

    def answer_permission(self, ref: ChatRef, request_id: str, decision: Decision) -> None:
        with self._lock:
            turn = self._turns.get(ref.native_id)
            connection = turn.connection if turn is not None else None
            found = turn.pending.pop(request_id, None) if turn is not None else None
        if found is None or connection is None or found[1] == QUESTIONS:
            return
        choice = checked_decision(decision, found[2])
        connection.respond(found[0], {"decision": "accept" if choice is Decision.ALLOW_ONCE else "decline"})

    def answer_question(self, ref: ChatRef, request_id: str, answers: Optional[Mapping[str, str]]) -> None:
        with self._lock:
            turn = self._turns.get(ref.native_id)
            connection = turn.connection if turn is not None else None
            found = turn.pending.pop(request_id, None) if turn is not None else None
        if found is None or connection is None or found[1] != QUESTIONS:
            return
        items = found[2]
        complete = answers is not None and all((answers.get(item.id) or "").strip() for item in items)
        if not complete:
            self._decline(connection, found[0], QUESTIONS)
            return
        connection.respond(found[0], {"answers": {item.id: {"answers": [answers[item.id]]} for item in items}})

    def _on_notification(self, connection: Connection, method: str, params: dict) -> None:
        turn = self._current_turn(connection, params)
        if method in IGNORED:
            self.drift.ignored(method)
            return
        if method == "item/agentMessage/delta":
            if turn is not None:
                turn.emit(ReplyText(_text(params.get("delta"), 1024 * 1024), _text(params.get("itemId"), 256)))
        elif method in ("item/started", "item/completed"):
            self._on_item(turn, method, params)
        elif method == "turn/plan/updated":
            plan = params.get("plan")
            if turn is not None and isinstance(plan, list):
                turn.emit(PlanUpdate(tuple((_text(e.get("step"), 500), _text(e.get("status"), 50))
                                           for e in plan[:256] if isinstance(e, dict))))
        elif method == "thread/tokenUsage/updated":
            self._on_usage(turn, params)
        elif method == "turn/completed":
            ended = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            if turn is not None:
                turn.status = _text(ended.get("status"), 50) or "completed"
                turn.done.set()
        elif method.startswith("item/") or method.startswith("turn/") or method.startswith("thread/"):
            self.drift.unknown(f"notification:{method}")
            if turn is not None:
                turn.emit(UnknownUpdate(f"notification:{method}"))
        else:
            self.drift.unknown(f"notification:{method}")

    def _on_item(self, turn: Optional[_Turn], method: str, params: dict) -> None:
        item = params.get("item")
        if not isinstance(item, dict):
            self.drift.malformed(method)
            return
        kind = item.get("type")
        if kind not in KNOWN_ITEMS:
            # A provider can add an item without adding a method (#466).
            self.drift.unknown(f"item:{kind}")
            if turn is not None:
                turn.emit(UnknownUpdate(f"item:{kind}"))
            return
        if turn is None:
            return
        item_id = _text(item.get("id"), 256)
        if kind == "agentMessage" and method == "item/completed":
            turn.emit(ReplyText(_text(item.get("text"), 1024 * 1024), item_id, final=True))
        elif kind == "commandExecution" and method == "item/started":
            command = " ".join(_text(item.get("command"), 200).split())
            turn.emit(Activity(f"Running command: {command}" if command else "Running a command."))
        elif kind == "fileChange":
            changes = item.get("changes") if isinstance(item.get("changes"), list) else []
            paths = [_text(c.get("path"), 1000) for c in changes[:64] if isinstance(c, dict)]
            turn.files[item_id] = "\n".join(f"File: {p}" for p in paths if p)
            if method == "item/started":
                turn.emit(Activity("Changing files."))
        elif kind in ACTIVITIES and method == "item/started":
            turn.emit(Activity(ACTIVITIES[kind]))

    def _on_usage(self, turn: Optional[_Turn], params: dict) -> None:
        usage = params.get("tokenUsage") if isinstance(params.get("tokenUsage"), dict) else {}
        last = usage.get("last") if isinstance(usage.get("last"), dict) else {}
        used, size = last.get("totalTokens"), usage.get("modelContextWindow")
        if turn is not None and type(used) is int and type(size) is int:
            turn.emit(UsageUpdate(used, size))

    # -- reading --------------------------------------------------------------

    def list_foreign(self) -> List[ChatInfo]:
        """Threads Codex knows about. Read-only: the service marks this app's own."""
        connection = self._connect()
        caps = effective_capabilities(self.implemented, None, False)
        chats: List[ChatInfo] = []
        cursor = None
        for _ in range(5):
            params: dict = {"limit": 100, "sortKey": "updated_at"}
            if cursor:
                params["cursor"] = cursor
            page = connection.request("thread/list", params)
            data = page.get("data") if isinstance(page, dict) else None
            if not isinstance(data, list):
                self.drift.malformed("thread/list")
                break
            for thread in data:
                if not isinstance(thread, dict) or not isinstance(thread.get("id"), str) or thread.get("parentThreadId"):
                    continue
                chats.append(ChatInfo(ChatRef(self.id, thread["id"]),
                                      _text(thread.get("name"), 200) or _text(thread.get("preview"), 80) or "Untitled",
                                      _text(thread.get("cwd"), 1000), self.name, "Codex app or terminal", False,
                                      float(thread["updatedAt"]) if isinstance(thread.get("updatedAt"), (int, float)) else 0.0,
                                      caps, "Reply to this chat where it started; The Chat Place only reads it."))
            cursor = page.get("nextCursor")
            if not cursor:
                break
        return chats

    def read_history(self, ref: ChatRef) -> List[Message]:
        connection = self._connect()
        items = self._read_items(connection, ref.native_id)
        messages: List[Message] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "userMessage":
                content = item.get("content") if isinstance(item.get("content"), list) else []
                text = "\n".join(_text(c.get("text"), 1024 * 1024) for c in content
                                 if isinstance(c, dict) and c.get("type") == "text")
                if text.strip():
                    messages.append(Message("user", text))
            elif kind == "agentMessage" and _text(item.get("text"), 1024 * 1024).strip():
                messages.append(Message("assistant", _text(item.get("text"), 1024 * 1024)))
            elif kind not in KNOWN_ITEMS:
                self.drift.unknown(f"item:{kind}")
        return messages

    def _read_items(self, connection: Connection, native_id: str) -> list:
        try:
            result = connection.request("thread/read", {"threadId": native_id, "includeTurns": True})
        except RpcError:
            # A failure here raises rather than returning nothing: a read that
            # failed must never look like an empty chat.
            return self._paged_items(connection, native_id)
        thread = result.get("thread") if isinstance(result, dict) else None
        turns = thread.get("turns") if isinstance(thread, dict) else None
        if not isinstance(turns, list) or not turns:
            return self._paged_items(connection, native_id)
        return [item for turn in turns if isinstance(turn, dict)
                for item in (turn.get("items") if isinstance(turn.get("items"), list) else [])]

    @staticmethod
    def _paged_items(connection: Connection, native_id: str) -> list:
        items: list = []
        cursor = None
        for _ in range(1000):
            params: dict = {"threadId": native_id, "sortDirection": "asc", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            page = connection.request("thread/items/list", params)
            data = page.get("data") if isinstance(page, dict) else None
            if not isinstance(data, list):
                raise IntegrationError("Codex couldn't read this chat's messages.")
            items.extend(entry.get("item") for entry in data if isinstance(entry, dict))
            cursor = page.get("nextCursor")
            if not cursor:
                return items
        return items

    def close(self) -> None:
        with self._lock:
            self._closes += 1
            connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()
