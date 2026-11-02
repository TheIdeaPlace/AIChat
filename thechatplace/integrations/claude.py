"""Claude through ``claude -p`` stream-json: the native route #489 keeps.

It stays native for what ACP's adapter hasn't shown it can guarantee: turns stay
on the subscription, and sessions the app didn't start can't be touched. Rules
kept from the shipped app, with tests:

* Only a chat this app started can be sent to. ``--resume`` is refused for any
  other id, including anything that looks like a desktop app session.
* Never ``--bare``, and never a script launcher.
* The environment loses anything inherited from a parent Claude session or that
  would move billing off the subscription, and a turn reporting an API key as
  its credential is stopped.

History is read from Claude's transcripts and never written. Unknown records
are skipped and counted, and a bad line never ends the read.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .. import platform_paths
from ..contracts import (Activity, AskPermission, AskQuestion, Capability, ChatInfo, ChatRef, Decision, Emit,
                         Integration, IntegrationError, Message, Notice, OptionGroup, OptionValue, OwnedChat,
                         PermissionRequest, QuestionItem, QuestionRequest, Refused, ReplyText, Status, TurnEnded,
                         TurnStatus, UnknownUpdate, UsageUpdate, effective_capabilities)
from ..drift import DriftCounters
from ..policy import checked_decision

SESSION_INJECTED = (
    "CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_PREVIEW_CLASSIFIER_FLOOR",
    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
    "CLAUDE_CODE_OAUTH_SCOPES", "CLAUDE_CODE_ACCOUNT_UUID", "CLAUDE_CODE_ORGANIZATION_UUID",
    "CLAUDE_CODE_USER_EMAIL", "CLAUDE_CODE_DESKTOP_APP_VERSION", "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_EMIT_TOOL_USE_SUMMARIES", "CLAUDE_CODE_ENABLE_ASK_USER_QUESTION_TOOL",
    "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING", "CLAUDE_CODE_REPORT_FINDINGS", "CLAUDE_CODE_EAGER_FLUSH",
    "CLAUDE_CODE_DISABLE_CRON", "CLAUDE_CODE_DISABLE_TERMINAL_TITLE", "CLAUDE_CODE_TERMINAL_MCP_TOOLS")
SESSION_PREFIXES = ("CLAUDE_CODE_SDK_", "CLAUDE_CODE_HOST_", "CLAUDE_CODE_MESSAGING_")
BILLING = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL", "ANTHROPIC_VERTEX_PROJECT_ID",
    "ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_BASE_URL", "ANTHROPIC_FOUNDRY_RESOURCE",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
#: Versions whose approvals, stop, resume and history have been exercised.
CHECKED_VERSIONS = ("2.1.296",)
QUESTION_TOOL = "AskUserQuestion"
PLAN_TOOL = "ExitPlanMode"
INITIALIZE_ID = "thechatplace-init"
#: Records understood and not shown as their own events.
IGNORED_TYPES = {"user", "rate_limit_event", "control_response", "control_cancel_request", "stream_event"}
IGNORED_HISTORY = {"summary", "queue-operation", "ai-title", "custom-title", "last-prompt", "attachment",
                   "file-history-snapshot", "system", "progress", "permission-mode", "mode", "tag",
                   "agent-name", "pr-link", "atis-latch"}
PERMISSION_MODES = OptionGroup("mode", "Permission mode", (
    OptionValue("manual", "Manual", "Claude asks before anything that needs approval."),
    OptionValue("acceptEdits", "Accept edits", "File edits are allowed; Claude asks before commands that need approval."),
    OptionValue("plan", "Plan", "Claude plans but doesn't change anything."),
), "manual")
_MODE_IDS = tuple(v.id for v in PERMISSION_MODES.values)
_IMPLEMENTED = frozenset({Capability.READ, Capability.SEND, Capability.STOP, Capability.RESUME,
                          Capability.APPROVALS, Capability.QUESTIONS, Capability.OPTIONS, Capability.USAGE})


def child_environment(environ: Optional[Mapping[str, str]] = None) -> dict:
    return platform_paths.clean_environment(SESSION_INJECTED + BILLING, SESSION_PREFIXES, environ)


def check_can_send(native_id: str, owned: bool) -> None:
    """Desktop sessions are read-only: only a chat this app started is resumed."""
    if not native_id or not _SAFE_ID.match(native_id):
        raise Refused("That isn't a valid session id.")
    if native_id.startswith("local_"):
        raise Refused("That's a Claude desktop app session. Reply to it in Claude.")
    if not owned:
        raise Refused("The Chat Place only sends messages to chats it started.")


def build_command(program: str, chat: OwnedChat, first: bool) -> List[str]:
    """``first`` means Claude has no such session yet, so it's made under this id."""
    check_can_send(chat.ref.native_id, True)
    mode = chat.options.get("mode", "manual")
    if mode not in _MODE_IDS:
        raise Refused("That permission mode isn't one The Chat Place offers.")
    command = [program, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--permission-mode", mode, "--permission-prompts", "host", "--permission-prompt-tool", "stdio"]
    return command + (["--session-id", chat.ref.native_id] if first else ["--resume", chat.ref.native_id])


def api_key_problem(source: object) -> Optional[str]:
    if source is None or source in ("", "none"):
        return None
    # The message goes in before Claude says which credential it has, so it may
    # already have gone. Claude reports this only once it's started.
    return ("Claude is set up to use an API key instead of your subscription login, so The Chat Place stopped "
            "the turn. Your message may already have been sent with that key. Remove the key from your settings, "
            "then try again.")


def _target(tool_input: dict, limit: int = 2000) -> str:
    for key in ("command", "file_path", "notebook_path", "path", "url", "pattern", "query"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return f"{key}: {value[:limit]}"
    return ""


def _describe_input(tool_input: dict, limit: int = 4000) -> str:
    """What a tool will do, in full enough to decide on: its target, and an
    edit's change. Input with no target this app knows is shown as it is."""
    target = _target(tool_input)
    parts = [target] if target else []
    for key, label in (("old_string", "Replace"), ("new_string", "With"), ("content", "Content")):
        if isinstance(tool_input.get(key), str):
            parts.append(f"{label}:\n{tool_input[key][:limit]}")
    if not parts and tool_input:
        parts.append(json.dumps(tool_input, ensure_ascii=False)[:limit])
    return "\n".join(parts)


@dataclass
class _Turn:
    process: object
    emit: Emit
    write: Callable[[dict], bool]
    stop_requested: bool = False
    pending: Dict[str, Tuple[str, dict, tuple]] = field(default_factory=dict)
    aborted: str = ""


class ClaudeIntegration(Integration):
    id = "claude"
    name = "Claude"
    implemented = _IMPLEMENTED
    enabled_by_default = True

    def __init__(self, start: Optional[Callable[[List[str], str], object]] = None,
                 projects_dir: Optional[Callable[[], Path]] = None, program: Optional[str] = None):
        self.drift = DriftCounters()
        self._start = start
        self._projects_dir = projects_dir or platform_paths.claude_projects_dir
        self._program_override = program
        self._lock = threading.RLock()
        self._turns: Dict[str, _Turn] = {}
        self._version: Optional[str] = None

    def _program(self) -> Optional[str]:
        return self._program_override or platform_paths.find_program("claude")

    def status(self) -> Status:
        hint = "Run claude and sign in, then try again."
        billing = "Your Claude subscription. A turn reporting an API key is stopped."
        program = self._program()
        if program:
            if self._start is not None:
                return Status(True, "Claude Code is installed.", hint, billing, True)
            if self._version is None:
                self._version = platform_paths.program_version(program, env=child_environment())
            checked = platform_paths.version_is_checked(self._version, CHECKED_VERSIONS)
            return Status(True, f"Claude Code is installed: {self._version}", hint, billing, checked, self._version)
        return Status(False, "Claude Code isn't installed, or only as a launcher this app won't start.", hint, billing)

    def new_chat_options(self) -> Tuple[OptionGroup, ...]:
        return (PERMISSION_MODES,)

    def start_chat(self, folder: str, title: str, options: Mapping[str, str]) -> OwnedChat:
        # Claude creates the session on the first turn, under this id.
        mode = options.get("mode", "manual")
        if mode not in _MODE_IDS:
            raise Refused("That permission mode isn't one The Chat Place offers.")
        return OwnedChat(ChatRef(self.id, str(uuid.uuid4())), title, folder, options={"mode": mode})

    def chat_options(self, chat: OwnedChat) -> Tuple[OptionGroup, ...]:
        return (OptionGroup("mode", PERMISSION_MODES.label, PERMISSION_MODES.values,
                            chat.options.get("mode", "manual")),)

    def set_option(self, chat: OwnedChat, group: str, value: str) -> None:
        if group != "mode" or value not in _MODE_IDS:
            raise Refused("That isn't one of this chat's options.")
        chat.options["mode"] = value  # each turn is a new process, so it takes effect next turn

    # -- a turn ---------------------------------------------------------------

    def _spawn(self, command: List[str], folder: str):
        if self._start is not None:
            return self._start(command, folder)
        try:
            return platform_paths.OwnedProcess(command, env=child_environment(), cwd=folder)
        except OSError:
            raise IntegrationError("Claude Code couldn't be started.") from None

    def run_turn(self, chat: OwnedChat, text: str, emit: Emit) -> None:
        program = self._program()
        if not program:
            emit(TurnEnded(TurnStatus.FAILED, "Claude Code isn't installed."))
            return
        try:
            # By what's on disk, not a count of turns: a turn stopped before
            # Claude made the session must not leave the chat resuming nothing.
            command = build_command(program, chat, not self._has_transcript(chat.ref.native_id))
            process = self._spawn(command, chat.folder)
        except IntegrationError as error:
            emit(TurnEnded(TurnStatus.FAILED, str(error)))
            return
        write_lock = threading.Lock()

        def write(message: dict) -> bool:
            with write_lock:
                try:
                    process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
                    process.stdin.flush()
                    return True
                except (OSError, ValueError):
                    return False

        turn = _Turn(process, emit, write)
        with self._lock:
            self._turns[chat.ref.native_id] = turn
        write({"type": "control_request", "request_id": INITIALIZE_ID,
               "request": {"subtype": "initialize", "hooks": None}})
        write({"type": "user", "message": {"role": "user", "content": text}})
        ended: Optional[TurnEnded] = None
        try:
            for raw in process.stdout:
                ended = self._on_line(turn, raw)
                if ended is not None:
                    break
        except (OSError, ValueError):
            pass
        finally:
            with self._lock:
                self._turns.pop(chat.ref.native_id, None)
            try:
                process.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass
        if turn.aborted:
            emit(TurnEnded(TurnStatus.FAILED, turn.aborted))
        elif turn.stop_requested:
            emit(TurnEnded(TurnStatus.STOPPED))
        else:
            emit(ended or TurnEnded(TurnStatus.FAILED, "Claude Code stopped before the turn ended."))

    def _has_transcript(self, native_id: str) -> bool:
        return bool(_SAFE_ID.match(native_id)) and any(self._projects_dir().glob(f"*/{native_id}.jsonl"))

    def _on_line(self, turn: _Turn, raw: bytes) -> Optional[TurnEnded]:
        if turn.aborted:
            return TurnEnded(TurnStatus.FAILED, turn.aborted)
        try:
            event = json.loads(raw)
        except ValueError:
            self.drift.malformed("not-json")
            return None
        if not isinstance(event, dict):
            self.drift.malformed("not-an-object")
            return None
        kind = event.get("type")
        if kind == "system":
            return self._on_system(turn, event)
        if kind == "assistant":
            if not event.get("parent_tool_use_id"):
                self._on_assistant(turn, event)
            return None
        if kind == "control_request":
            self._on_control(turn, event)
            return None
        if kind == "result":
            return self._on_result(turn, event)
        if kind in IGNORED_TYPES:
            self.drift.ignored(kind)
            return None
        self.drift.unknown(f"type:{kind}")
        turn.emit(UnknownUpdate(f"type:{kind}"))
        return None

    def _on_system(self, turn: _Turn, event: dict) -> Optional[TurnEnded]:
        if event.get("subtype") == "init":
            if "apiKeySource" not in event:
                self.drift.malformed("init-without-apiKeySource")
            problem = api_key_problem(event.get("apiKeySource"))
            if problem:
                turn.aborted = problem
                turn.process.kill()
        else:
            self.drift.ignored(f"system:{event.get('subtype')}")
        return None

    def _on_assistant(self, turn: _Turn, event: dict) -> None:
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content")
        block = str(message.get("id") or uuid.uuid4())
        if isinstance(content, str) and content.strip():
            turn.emit(ReplyText(content, block, final=True))
        for index, part in enumerate(content if isinstance(content, list) else ()):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and str(part.get("text") or "").strip():
                turn.emit(ReplyText(str(part["text"]), f"{block}:{index}", final=True))
            elif part.get("type") == "tool_use":
                name = str(part.get("name") or "a tool")
                tool_input = part.get("input") if isinstance(part.get("input"), dict) else {}
                turn.emit(Activity(f"{name}: {_target(tool_input, 160)}" if _target(tool_input, 160) else name))
            elif part.get("type") not in ("text", "tool_use", "thinking", "redacted_thinking"):
                self.drift.unknown(f"content:{part.get('type')}")

    def _on_control(self, turn: _Turn, event: dict) -> None:
        request_id = str(event.get("request_id") or "")
        request = event.get("request") if isinstance(event.get("request"), dict) else {}
        subtype = request.get("subtype")
        if subtype != "can_use_tool" or not request_id:
            # Refused, and it can't become an approval: only can_use_tool asks.
            self.drift.unknown(f"control:{subtype}")
            turn.emit(UnknownUpdate(f"control:{subtype}"))
            turn.write({"type": "control_response", "response": {
                "subtype": "error", "request_id": request_id, "error": "The Chat Place doesn't support this request."}})
            return
        tool = str(request.get("tool_name") or "a tool")
        tool_input = request.get("input") if isinstance(request.get("input"), dict) else {}
        if tool == QUESTION_TOOL:
            items = self._questions(tool_input)
            if not items:
                # Nothing to ask, so answer now: it would otherwise wait forever.
                self.drift.malformed("question-without-questions")
                turn.write({"type": "control_response", "response": {
                    "subtype": "success", "request_id": request_id,
                    "response": {"behavior": "deny", "message": "That question had nothing to ask."}}})
                return
            with self._lock:
                turn.pending[request_id] = (QUESTION_TOOL, tool_input, tuple(items))
            turn.emit(AskQuestion(QuestionRequest(request_id, tuple(items))))
            return
        detail = str(tool_input.get("plan") or "") if tool == PLAN_TOOL else _describe_input(tool_input)
        # With nothing to read, there's nothing to allow.
        offered = (Decision.ALLOW_ONCE, Decision.DENY) if detail else (Decision.DENY,)
        summary = ("Claude has a plan and asks to go ahead." if tool == PLAN_TOOL
                   else f"Claude asks to use {tool}.")
        with self._lock:
            turn.pending[request_id] = (tool, tool_input, offered)
        turn.emit(AskPermission(PermissionRequest(request_id, summary, detail, offered)))

    @staticmethod
    def _questions(tool_input: dict) -> List[QuestionItem]:
        items: List[QuestionItem] = []
        questions = tool_input.get("questions")
        for question in questions if isinstance(questions, list) else ():
            if not isinstance(question, dict) or not isinstance(question.get("question"), str):
                continue
            options = question.get("options") if isinstance(question.get("options"), list) else []
            choices = tuple(OptionValue(str(o["label"]), str(o["label"]), str(o.get("description") or ""))
                            for o in options if isinstance(o, dict) and o.get("label"))
            items.append(QuestionItem(question["question"], question["question"], choices, True))
        return items

    def answer_permission(self, ref: ChatRef, request_id: str, decision: Decision) -> None:
        with self._lock:
            turn = self._turns.get(ref.native_id)
            found = turn.pending.pop(request_id, None) if turn is not None else None
        if found is None or found[0] == QUESTION_TOOL:
            return
        _tool, tool_input, offered = found
        if checked_decision(decision, offered) is Decision.ALLOW_ONCE:
            answer = {"behavior": "allow", "updatedInput": tool_input}
        else:
            answer = {"behavior": "deny", "message": "The user refused this. Don't try it another way without asking."}
        turn.write({"type": "control_response", "response": {"subtype": "success", "request_id": request_id,
                                                             "response": answer}})

    def answer_question(self, ref: ChatRef, request_id: str, answers: Optional[Mapping[str, str]]) -> None:
        with self._lock:
            turn = self._turns.get(ref.native_id)
            found = turn.pending.pop(request_id, None) if turn is not None else None
        if found is None or found[0] != QUESTION_TOOL:
            return
        _tool, tool_input, items = found
        if answers is None or not all((answers.get(item.id) or "").strip() for item in items):
            answer: dict = {"behavior": "deny", "message": "The user chose not to answer."}
        else:
            answer = {"behavior": "allow", "updatedInput": {**tool_input, "answers": dict(answers)}}
        turn.write({"type": "control_response", "response": {"subtype": "success", "request_id": request_id,
                                                             "response": answer}})

    def _on_result(self, turn: _Turn, event: dict) -> TurnEnded:
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        models = event.get("modelUsage") if isinstance(event.get("modelUsage"), dict) else {}
        windows = [m.get("contextWindow") for m in models.values() if isinstance(m, dict)
                   and type(m.get("contextWindow")) is int]
        used = sum(v for k, v in usage.items() if k.endswith("input_tokens") and type(v) is int)
        if windows and used:
            turn.emit(UsageUpdate(used, max(windows)))
        if event.get("is_error") or event.get("subtype") not in (None, "", "success"):
            return TurnEnded(TurnStatus.FAILED, "Claude couldn't finish that turn.")
        return TurnEnded(TurnStatus.COMPLETED)

    def stop(self, ref: ChatRef) -> None:
        with self._lock:
            turn = self._turns.get(ref.native_id)
            if turn is None:
                return
            turn.stop_requested = True
            turn.pending.clear()
        turn.process.kill()

    def close(self) -> None:
        with self._lock:
            running = list(self._turns)
        for native_id in running:
            self.stop(ChatRef(self.id, native_id))

    # -- reading --------------------------------------------------------------

    def _transcripts(self) -> Iterable[Path]:
        root = self._projects_dir()
        try:
            return sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]
        except OSError:
            return []

    def list_foreign(self) -> List[ChatInfo]:
        caps = effective_capabilities(self.implemented, None, False)
        chats = []
        for path in self._transcripts():
            title, folder = self._peek(path)
            if not title:
                continue
            try:
                updated = path.stat().st_mtime
            except OSError:
                updated = 0.0
            chats.append(ChatInfo(ChatRef(self.id, path.stem), title, folder, self.name, "Claude app or terminal",
                                  False, updated, caps,
                                  "Reply to this chat where it started; The Chat Place only reads it."))
        return chats

    def _peek(self, path: Path) -> Tuple[str, str]:
        """A title and folder from the start of a transcript, read cheaply."""
        title, folder = "", ""
        try:
            with path.open("rb") as handle:
                for raw in handle.readlines(256 * 1024):
                    record = self._parse(raw)
                    if record is None:
                        continue
                    folder = folder or (record["cwd"] if isinstance(record.get("cwd"), str) else "")
                    if record.get("type") in ("custom-title", "ai-title"):
                        title = str(record.get("customTitle") or record.get("aiTitle") or title)
                    if not title and record.get("type") == "user" and not record.get("isSidechain") and not record.get("isMeta"):
                        title = " ".join(self._text_of(record).split())[:80]
        except OSError:
            return "", ""
        return title, folder

    @staticmethod
    def _parse(raw: bytes) -> Optional[dict]:
        try:
            record = json.loads(raw)
        except ValueError:
            return None
        return record if isinstance(record, dict) else None

    @staticmethod
    def _text_of(record: dict) -> str:
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        parts = [str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text"] \
            if isinstance(content, list) else []
        return "\n".join(parts)

    def read_history(self, ref: ChatRef) -> List[Message]:
        if not _SAFE_ID.match(ref.native_id):
            return []
        messages: List[Message] = []
        for path in self._projects_dir().glob(f"*/{ref.native_id}.jsonl"):
            try:
                handle = path.open("rb")
            except OSError:
                continue
            with handle:
                for raw in handle:
                    record = self._parse(raw)
                    if record is None:
                        self.drift.malformed("transcript-line")
                        continue
                    kind = record.get("type")
                    if kind in ("user", "assistant"):
                        if record.get("isSidechain") or record.get("isMeta"):
                            continue
                        text = self._text_of(record)
                        if text.strip():
                            messages.append(Message(kind, text))
                    elif kind not in IGNORED_HISTORY:
                        self.drift.unknown(f"transcript:{kind}")
            break
        return messages
