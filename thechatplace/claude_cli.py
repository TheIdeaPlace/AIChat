"""Running turns of The Chat Place's own sessions through the ``claude`` command.

Why the command line, and how it stays on the subscription
---------------------------------------------------------
``claude -p`` (print / headless mode) runs a turn under the same login as
the desktop app and the terminal: the subscription, never an API key. (Claude
plans count ``claude -p`` against a monthly Agent SDK credit; past it, turns
stop unless extra usage is on. usage.py reports the limits a turn sees.) The
things that would move a turn off the subscription are all ruled out here:

* **Never ``--bare``.** Bare mode skips the OAuth login and requires an API key.
* **A host session's variables don't reach the child.** If The Chat Place is
  started from inside a Claude Code session (a terminal tab in the desktop app,
  say), it inherits variables that session injected: ``ANTHROPIC_BASE_URL``
  pointing at the desktop app's local proxy, ``CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH``
  and friends that tell the CLI a host will refresh its OAuth token. A child
  run with those waits on a token refresh that never comes (the lock seen
  during the investigation for issue #168). Those are removed by name
  (``SESSION_INJECTED_VARS``), as are the variables that would move billing
  off the subscription (``BILLING_VARS``: API keys, auth tokens, a different
  endpoint, Bedrock/Vertex/Foundry). Everything else is kept, including
  settings the user chose such as ``CLAUDE_CODE_GIT_BASH_PATH``,
  ``CLAUDE_CONFIG_DIR``, proxies and timeouts.
* **Permission prompts come to The Chat Place** (issues #187, #188), over the
  same pipes, with the control protocol the Agent SDK uses:
  ``--input-format stream-json --permission-prompts host
  --permission-prompt-tool stdio``. Anything that would ask arrives as a
  ``control_request`` (``can_use_tool``) and the turn waits until it is
  answered with a ``control_response``: allow (optionally with the input
  changed, which is how Claude's questions are answered), or deny with a
  reason Claude reads. Nothing is ever answered by timing out; Stop ends the
  turn. Claude's questions (AskUserQuestion) and plans (ExitPlanMode) come
  the same way. Checked against Claude Code 2.1.286.
* **The run is stopped if the CLI says it is using an API key.** The first
  stream-json event (``system/init``) reports ``apiKeySource``; on a
  subscription login it is ``"none"``. Anything else ends the turn before a
  request is made.

Never into a desktop session
----------------------------
Two clients resuming one session interleave their turns into one transcript.
So ``--resume`` is only ever built for a session in The Chat Place's own store,
and is refused for any id the desktop app knows about or any ``local_`` id
(``ResumeRefused``). That check lives in ``build_resume_command`` so no caller
can skip it.

Prompts go in on stdin, as a stream-json user message (UTF-8, the newlines
exactly as typed, escaped by JSON), not on the command line: a message that
begins with ``-`` cannot be mistaken for a flag, and there is no command-line
length limit. Stdin stays open for the turn, for answers to permission
requests, and is closed when the result arrives so the CLI exits. Output is
read as bytes and split only on newline bytes.

Background work keeps the turn open (#161). When Claude ends its reply with a
shell command or agent still running in the background, closing stdin would
end the CLI and the kill-on-close job would end the work with it, and nothing
would be left to pick it up. So stdin stays open until the work is done:
Claude Code starts a turn of its own when it finishes, as it does in the
desktop app, and the turn ends once it goes idle with nothing left running.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Collection, Dict, List, Optional

from . import platform_paths
from .transcript import compaction_text

#: (value passed to --permission-mode, label shown in the New Session dialog)
PERMISSION_MODES = [
    ("auto", "Auto: Claude decides what is safe to run without asking"),
    ("acceptEdits", "Accept edits: file edits allowed; Claude asks before commands that need "
                    "approval"),
    ("manual", "Manual: Claude asks before anything that needs approval"),
    ("plan", "Plan: Claude plans but does not change anything"),
]
PERMISSION_MODE_VALUES = [value for value, _label in PERMISSION_MODES]
DEFAULT_PERMISSION_MODE = "auto"
#: Older names the CLI still accepts; stored sessions may carry them.
_MODE_ALIASES = {"default": "manual"}

#: Set by a host Claude Code session (seen in the desktop app's terminal on
#: Claude Code 2.1.289). They describe *that* session, and some of them make a
#: child wait for the host to refresh its login.
SESSION_INJECTED_VARS = frozenset({
    "CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_AGENT_SDK_VERSION",
    "CLAUDE_PREVIEW_CLASSIFIER_FLOOR",
    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH", "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "CLAUDE_CODE_OAUTH_SCOPES",
    "CLAUDE_CODE_ACCOUNT_UUID", "CLAUDE_CODE_ORGANIZATION_UUID", "CLAUDE_CODE_USER_EMAIL",
    "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_DESKTOP_APP_VERSION", "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_EMIT_TOOL_USE_SUMMARIES", "CLAUDE_CODE_ENABLE_ASK_USER_QUESTION_TOOL",
    "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING", "CLAUDE_CODE_REPORT_FINDINGS",
    "CLAUDE_CODE_EAGER_FLUSH", "CLAUDE_CODE_DISABLE_CRON",
    "CLAUDE_CODE_DISABLE_TERMINAL_TITLE", "CLAUDE_CODE_TERMINAL_MCP_TOOLS",
})

#: Would take the turn off the subscription login (per-use billing or a cloud
#: provider), or point it at another endpoint.
BILLING_VARS = frozenset({
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_VERTEX_PROJECT_ID", "ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
})

_STRIP = SESSION_INJECTED_VARS | BILLING_VARS
#: For a Terminal window on a Mac, which can't be given child_environment().
STRIPPED_VARS = _STRIP

#: Whole families a host session sets, including names newer Claude Code
#: versions add. User settings (CLAUDE_CODE_GIT_BASH_PATH and the like) don't
#: use these prefixes.
SESSION_INJECTED_PREFIXES = ("CLAUDE_CODE_SDK_", "CLAUDE_CODE_HOST_",
                             "CLAUDE_CODE_MESSAGING_")


#: Turns Claude's task-list tools on for every model (#231).
TASK_LIST_VAR = "CLAUDE_CODE_ENABLE_TODO_TOOLS"


class ResumeRefused(ValueError):
    """--resume was asked for a session The Chat Place does not own."""


def child_environment(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The environment for a ``claude`` child process (see module docstring)."""
    base = dict(os.environ if base is None else base)
    return {k: v for k, v in base.items()
            if k.upper() not in _STRIP
            and not k.upper().startswith(SESSION_INJECTED_PREFIXES)}


def normalize_permission_mode(mode: str) -> str:
    return _MODE_ALIASES.get(mode, mode)


#: The model choices for a session: ``--model`` value and what the picker
#: says. Aliases, so each means that family's latest model. "" passes no
#: ``--model`` at all, leaving it to Claude Code's own setting.
#:
#: Fable is left out on purpose. Claude Code's docs: on some plans Fable
#: bills to usage credits, and in ``-p`` mode (every turn The Chat Place runs) it
#: does so without asking. The Chat Place must never cost extra, and the
#: ``apiKeySource`` check can't catch this (it's still the subscription
#: login). Add it only once Kelly's plan is known to include it.
MODELS = [
    ("", "Default (your Claude Code setting)"),
    ("opus", "Opus"),
    ("sonnet", "Sonnet"),
    ("haiku", "Haiku"),
]
MODEL_LABELS = dict(MODELS)

#: The effort choices for a session (#189): ``--effort`` value and what the
#: picker says. Checked with Claude Code 2.1.296: ``-p`` takes ``--effort``
#: with these values on every model, warning about (and ignoring) any other.
#: "" passes no ``--effort``, leaving it to Claude Code's own setting.
EFFORTS = [
    ("", "Default (your Claude Code setting)"),
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
    ("xhigh", "Extra high"),
    ("max", "Max"),
]
EFFORT_LABELS = dict(EFFORTS)
#: Said with the effort choices: not every model thinks harder for it.
EFFORT_NOTE = "Models that don't support effort levels ignore this."
# A full model name ("claude-opus-5-5") may be stored by hand; nothing that
# could read as another option. No brackets: "sonnet[1m]" (1M context) needs
# usage credits on every subscription plan. No "@" or ":": those are Vertex
# and Bedrock ids, and their switches are stripped anyway.
_SAFE_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def is_safe_model(model: str) -> bool:
    """Whether ``model`` may be passed as ``--model`` ("" means none)."""
    return not model or bool(_SAFE_MODEL.fullmatch(model))


def model_matches(chosen: str, actual: str) -> bool:
    """Whether the model Claude Code reports ("claude-opus-5-5") is the one
    chosen: an alias ("opus") matches its family, a full name itself. True
    when nothing was chosen, or Claude Code didn't say."""
    if not chosen or not actual:
        return True
    chosen = chosen.lower()
    actual = re.sub(r"\[[^\]]*\]$", "", actual.lower())  # "[1m]": the same model
    if chosen in MODEL_LABELS:
        return chosen in actual
    return actual == chosen or actual.startswith(chosen + "-")


_MODEL_ID = re.compile(r"claude-([a-z]+)-(\d{1,2}(?:-\d{1,2})*)(?:-\d{8})?(?:\[[^\]]*\])?")


def model_spoken(model_id: str) -> str:
    """A model id as words: "claude-sonnet-5-5" is "Sonnet 5.5",
    "claude-haiku-4-5-20251001" "Haiku 4.5". Anything else as it is."""
    match = _MODEL_ID.fullmatch((model_id or "").lower())
    if not match:
        return model_id
    family, version = match.groups()
    return f"{family.capitalize()} {version.replace('-', '.')}"


def model_label(model: str) -> str:
    """How a session's model is named to Kelly."""
    return MODEL_LABELS.get(model, model) if model else "the default model"


def effort_label(effort: str) -> str:
    """How a session's effort is said: "high effort", "the default effort"."""
    return f"{EFFORT_LABELS[effort].lower()} effort" if effort else "the default effort"


def mode_label(mode: str) -> str:
    """A permission mode's short name: "auto", "accept edits", "plan"."""
    label = dict(PERMISSION_MODES).get(normalize_permission_mode(mode), mode)
    return label.split(":")[0].lower()


#: A permission rule as Claude Code writes them: ``Tool`` or
#: ``Tool(content)``, one line. Rules come from Claude Code's own suggestions,
#: and must never read as a flag.
_SAFE_RULE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,99}(\([^\r\n]{0,500}\))?")


def is_safe_rule(rule: str) -> bool:
    return isinstance(rule, str) and bool(_SAFE_RULE.fullmatch(rule))


def is_safe_dir(folder: str) -> bool:
    """A folder Claude Code suggested working in, fit to give back as
    ``--add-dir``: one specific full path, one line, and never readable as a flag."""
    return (isinstance(folder, str) and 0 < len(folder) <= 1024
            and not folder.startswith("-") and not any(c in folder for c in "\r\n\0")
            and platform_paths.is_specific_folder(folder))


def _common_flags(permission_mode: str, model: str = "",
                  allowed_tools: Collection[str] = (), effort: str = "",
                  allowed_dirs: Collection[str] = ()) -> List[str]:
    permission_mode = normalize_permission_mode(permission_mode)
    if permission_mode not in PERMISSION_MODE_VALUES:
        raise ValueError(f"Unknown permission mode: {permission_mode!r}")
    # --replay-user-messages: Claude Code echoes each message as it takes it
    # in, which is how a turn knows whether a steered message joined the work
    # in progress or is being answered after it (#238).
    flags = ["-p", "--input-format", "stream-json", "--output-format", "stream-json",
             "--verbose", "--replay-user-messages", "--permission-mode", permission_mode,
             "--permission-prompts", "host", "--permission-prompt-tool", "stdio"]
    if model:
        if not is_safe_model(model):
            raise ValueError(f"Not a valid model name: {model!r}")
        # Every turn, not just the first. Checked with Claude Code: a resumed
        # session keeps its model without this, but saying it each time keeps
        # the session on Kelly's choice whatever Claude Code does later.
        flags += ["--model", model]
    if effort:
        if effort not in EFFORT_LABELS:
            raise ValueError(f"Not a valid effort level: {effort!r}")
        # Every turn, as with the model: each turn is a new process (#189).
        flags += ["--effort", effort]
    rules = [rule for rule in allowed_tools if is_safe_rule(rule)]
    if rules:
        # "Allow for This Chat" (#187, #329). Claude Code keeps a session-scoped
        # rule only for the life of the process, and every turn here is a new
        # process, so the rules are kept with the session and given again
        # each turn. The option takes several values; the flag after it ends
        # the list.
        flags += ["--allowedTools", *rules]
    dirs = [folder for folder in allowed_dirs if is_safe_dir(folder)]
    if dirs:
        # Folders kept with "Allow for This Chat" (#332). Checked with Claude
        # Code 2.1: a command that writes, such as mkdir, asks again on the
        # next turn unless both its rule and the folder it writes in are
        # given; a folder lasts only for the process, as a rule does.
        flags += ["--add-dir", *dirs]
    return flags


#: Tools that change a file, whose path an agent conversation lists with the
#: reply (phase 3 of #248).
FILE_EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})


def new_session_id() -> str:
    return str(uuid.uuid4())


def build_new_command(executable: str, session_id: str, title: str,
                      permission_mode: str, model: str = "",
                      allowed_tools: Collection[str] = (), effort: str = "",
                      allowed_dirs: Collection[str] = ()) -> List[str]:
    """Command for the first turn of a new session. The prompt goes on stdin."""
    if not platform_paths.is_safe_id(session_id):
        raise ValueError("Not a valid session id.")
    command = [executable, *_common_flags(permission_mode, model, allowed_tools, effort,
                                          allowed_dirs),
               "--session-id", session_id]
    title = " ".join((title or "").split())
    if title:
        command += ["--name", title]
    return command


def check_resume_allowed(session_id: str, own_ids: Collection[str],
                         desktop_ids: Collection[str]) -> None:
    """Raise ResumeRefused unless ``session_id`` is one of The Chat Place's own."""
    if not session_id or not platform_paths.is_safe_id(session_id):
        raise ResumeRefused("That is not a valid session id.")
    if session_id.startswith("local_"):
        raise ResumeRefused(
            "That is a Claude desktop app session id. Desktop sessions are read-only "
            "in The Chat Place; reply to them in Claude.")
    if session_id in desktop_ids:
        raise ResumeRefused(
            "That session belongs to the Claude desktop app. Sending into it from here "
            "could run two turns at once and tangle its transcript; reply in Claude.")
    if session_id not in own_ids:
        raise ResumeRefused("The Chat Place only sends messages to sessions it started.")


def build_fork_command(executable: str, source_id: str, new_id: str, title: str,
                       permission_mode: str, model: str = "",
                       taken_ids: Collection[str] = (),
                       cowork_ids: Collection[str] = (), effort: str = "") -> List[str]:
    """Command for the first turn of a copy of another session (#189), such
    as a desktop app session, to carry on in The Chat Place.

    ``--resume <source> --fork-session --session-id <new>``: Claude Code reads
    the source's history and writes only to the new session. Checked with
    Claude Code 2.1.286: the source transcript was byte for byte the same
    afterwards, the new id was the one asked for, and Claude knew the earlier
    conversation. So this is allowed for desktop sessions, where a plain
    ``--resume`` is not; the new id must be one nobody has. Never for a
    Cowork session (``cowork_ids``, #91): its history is in the desktop app's
    own files, and its tools ran there.
    """
    if not platform_paths.is_safe_id(source_id) or source_id.startswith("local_"):
        raise ResumeRefused("That is not a session id Claude Code can continue from.")
    if source_id in cowork_ids:
        raise ResumeRefused("A Cowork session can't be continued here; open it in Claude.")
    if not platform_paths.is_safe_id(new_id) or new_id == source_id or new_id in taken_ids:
        raise ValueError("The new session needs an id of its own.")
    command = [executable, *_common_flags(permission_mode, model, effort=effort),
               "--resume", source_id, "--fork-session", "--session-id", new_id]
    title = " ".join((title or "").split())
    if title:
        command += ["--name", title]
    return command


def build_resume_command(executable: str, session_id: str, permission_mode: str,
                         own_ids: Collection[str],
                         desktop_ids: Collection[str], model: str = "",
                         allowed_tools: Collection[str] = (), title: str = "",
                         effort: str = "", allowed_dirs: Collection[str] = ()) -> List[str]:
    """Command for a later turn. Refuses anything but The Chat Place's own sessions.

    ``--name`` goes on every turn, not just the first (#123). Other sessions
    address this one by that name (``SendMessage``); without it each resumed
    turn got a new made-up name ("project-37", then "project-36"), so a reply
    sent to the name seen earlier never arrived. Checked with Claude Code
    2.1.286: ``--resume`` with ``--name`` is accepted and keeps the title.
    """
    check_resume_allowed(session_id, own_ids, desktop_ids)
    command = [executable, *_common_flags(permission_mode, model, allowed_tools, effort,
                                          allowed_dirs)]
    title = " ".join((title or "").split())
    if title:
        command += ["--name", title]
    return command + ["--resume", session_id]


# ---------------------------------------------------------------------------
# stream-json events
# ---------------------------------------------------------------------------


INIT_REQUEST_ID = "thechatplace-init"


def initialize_line() -> bytes:
    """The ``initialize`` control request. Its answer lists the slash commands
    and skills, and the model each model choice resolves to (#58)."""
    return (json.dumps({"type": "control_request", "request_id": INIT_REQUEST_ID,
                        "request": {"subtype": "initialize", "hooks": None}})
            + "\n").encode("utf-8")


#: The request id of a turn's Remote Control request.
RC_REQUEST_ID = "thechatplace-remote-control"


def remote_control_line(name: str, reattach: str = "") -> bytes:
    """Turn on Remote Control for this turn's session, as the desktop app does
    for its headless sessions: named, kept on claude.ai between turns, and
    joined again (``reattach``) so it's the same Remote Control session every
    turn."""
    request = {"subtype": "remote_control", "enabled": True, "name": name,
               "keep_session_on_exit": True}
    if reattach:
        request["reattach_session_id"] = reattach
    return (json.dumps({"type": "control_request", "request_id": RC_REQUEST_ID,
                        "request": request}, ensure_ascii=False) + "\n").encode("utf-8")


def message_line(prompt: str, images: Optional[List[dict]] = None,
                 message_id: str = "") -> bytes:
    """The user's message, with any image blocks after its text (#22).
    ``message_id``: its uuid, which Claude Code echoes back as it takes the
    message in (--replay-user-messages, #238)."""
    content = prompt
    if images:
        content = ([{"type": "text", "text": prompt}] if prompt else []) + list(images)
    message = {"type": "user", "session_id": "", "parent_tool_use_id": None,
               "message": {"role": "user", "content": content}}
    if message_id:
        message["uuid"] = message_id
    return (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")


def stdin_lines(prompt: str, images: Optional[List[dict]] = None) -> bytes:
    """Everything that starts a turn on stdin, in order."""
    return initialize_line() + message_line(prompt, images)


QUESTION_TOOL = "AskUserQuestion"
PLAN_TOOL = "ExitPlanMode"
SHELL_TOOLS = ("Bash", "PowerShell")


def _tool_target(tool_input: dict, limit: int = 160) -> str:
    """The one value that says what a tool call is about: its command, file,
    URL or pattern, on one line."""
    for key in ("command", "file_path", "notebook_path", "path", "url", "pattern", "query"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            target = " ".join(value.split())
            return target if len(target) <= limit else target[: limit - 1] + "…"
    return ""


def describe_tool_use(name: str, tool_input: dict) -> str:
    """A tool call as words: "run npm init -y", "edit README.md"."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    target = _tool_target(tool_input)
    if name in SHELL_TOOLS and target:
        return f"run {target}"
    verbs = {"Write": "write", "Edit": "edit", "MultiEdit": "edit", "NotebookEdit": "edit",
             "Read": "read", "WebFetch": "fetch", "WebSearch": "search the web for"}
    if name in verbs and target:
        if name in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Read"):
            target = os.path.basename(target.rstrip("\\/")) or target
        return f"{verbs[name]} {target}"
    return f"use {name}: {target}" if target else f"use {name}"


def describe_denial(denial: dict) -> str:
    """A permission denial from the result event, as words."""
    if not isinstance(denial, dict):
        return "A tool was refused."
    name = str(denial.get("tool_name") or "A tool")
    tool_input = denial.get("tool_input") if isinstance(denial.get("tool_input"), dict) else {}
    target = _tool_target(tool_input)
    return f"{name} was refused: {target}" if target else f"{name} was refused"


@dataclass
class PermissionRequest:
    """Claude Code asking The Chat Place before a tool runs (a ``can_use_tool``
    control request). Claude's questions and plans come this way too."""

    request_id: str
    tool_name: str
    input: dict = field(default_factory=dict)
    description: str = ""
    suggestions: list = field(default_factory=list)
    tool_use_id: str = ""
    #: For a question: what Claude wrote just before asking it, which the
    #: answer dialog shows (#415). Filled in by the window, not Claude Code.
    said: str = ""

    @property
    def is_question(self) -> bool:
        return self.tool_name == QUESTION_TOOL

    @property
    def is_plan(self) -> bool:
        return self.tool_name == PLAN_TOOL

    def questions(self) -> List[dict]:
        items = self.input.get("questions")
        return [q for q in items if isinstance(q, dict) and isinstance(q.get("question"), str)] \
            if isinstance(items, list) else []

    def plan(self) -> str:
        plan = self.input.get("plan")
        return plan if isinstance(plan, str) else ""

    def summary(self) -> str:
        """One line, for announcements: what Claude is waiting for."""
        if self.is_question:
            questions = self.questions()
            first = questions[0]["question"] if questions else "a question"
            more = f" (and {len(questions) - 1} more)" if len(questions) > 1 else ""
            return f"Claude asks: {first}{more}"
        if self.is_plan:
            return "Claude's plan is ready for you to approve"
        return f"Claude wants to {describe_tool_use(self.tool_name, self.input)}"

    def detail(self) -> str:
        """Everything about the request, to read by line."""
        lines = [self.summary() + "."]
        if self.description and self.description not in lines[0]:
            lines.append(f"Why: {self.description}")
        if self.tool_name in SHELL_TOOLS:
            lines += ["", f"{self.tool_name} command:", str(self.input.get("command", ""))]
            return "\n".join(lines)
        if self.tool_name == "Write":
            lines += ["", f"File: {self.input.get('file_path', '')}", "", "Content:",
                      str(self.input.get("content", ""))]
            return "\n".join(lines)
        if self.tool_name == "Edit":
            lines += ["", f"File: {self.input.get('file_path', '')}", "", "Replace:",
                      str(self.input.get("old_string", "")), "", "With:",
                      str(self.input.get("new_string", ""))]
            return "\n".join(lines)
        lines.append("")
        for key, value in self.input.items():
            if isinstance(value, str):
                lines.append(f"{key}: {value}")
            else:
                lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, indent=2)}")
        return "\n".join(lines)

    def session_rules(self) -> List[str]:
        """The rules Claude Code suggests for not asking again, as
        ``Tool(content)`` strings."""
        rules = []
        for suggestion in self.suggestions:
            if not isinstance(suggestion, dict) or suggestion.get("type") != "addRules":
                continue
            if suggestion.get("behavior", "allow") != "allow":
                continue
            for rule in suggestion.get("rules") or []:
                if not isinstance(rule, dict) or not isinstance(rule.get("toolName"), str):
                    continue
                content = rule.get("ruleContent")
                text = f"{rule['toolName']}({content})" if isinstance(content, str) and content \
                    else rule["toolName"]
                if is_safe_rule(text):
                    rules.append(text)
        return rules

    def session_dirs(self) -> List[str]:
        """The folders Claude Code suggests letting this chat work in."""
        dirs = []
        for suggestion in self.suggestions:
            if not isinstance(suggestion, dict) or suggestion.get("type") != "addDirectories":
                continue
            for folder in suggestion.get("directories") or []:
                if is_safe_dir(folder) and folder not in dirs:
                    dirs.append(folder)
        return dirs

    def session_mode(self) -> str:
        """A permission mode Claude Code suggests switching to instead
        ("acceptEdits" for a file edit), or ""."""
        for suggestion in self.suggestions:
            if isinstance(suggestion, dict) and suggestion.get("type") == "setMode" \
                    and suggestion.get("mode") in PERMISSION_MODE_VALUES:
                return suggestion["mode"]
        return ""

    def allow_for_session_label(self) -> str:
        """What "Allow for This Chat" means for this request, in full for a
        screen reader, or "" when Claude Code has nothing to suggest. The
        rule (with any folder suggested with it) or mode is kept with the chat
        (#187, #329, #332). A rule comes first: it is what Claude Code offers
        first, and a mode switch would allow far more than this request."""
        rules, dirs, mode = self.session_rules(), self.session_dirs(), self.session_mode()
        if rules:
            # Both grants by name: the folder is a working folder for every
            # tool on later turns, not only for this rule (--add-dir).
            label = "Allow, and don't ask again in this chat for " + ", ".join(rules)
            return label + (", and let Claude work in " + _folders_words(dirs) if dirs else "")
        if mode == "acceptEdits":
            return "Allow, and accept all file edits for the rest of this chat"
        if mode:
            return f"Allow, and switch this chat to {mode} mode"
        if dirs:
            return "Allow, and let Claude work in " + _folders_words(dirs)
        return ""

    def kept_for_chat(self):
        """What Allow for This Chat keeps: (rules, folders, mode), as
        allow_for_session_label describes it."""
        rules, dirs, mode = self.session_rules(), self.session_dirs(), self.session_mode()
        if rules:
            return rules, dirs, ""
        if mode:
            return [], [], mode
        return [], dirs, ""


def _folders_words(dirs: List[str]) -> str:
    return ("the folder " if len(dirs) == 1 else "the folders ") + ", ".join(dirs)


def allow_response(request: PermissionRequest, updated_input: Optional[dict] = None,
                   for_session: bool = False, mode: str = "") -> dict:
    """The answer that lets the tool run. ``for_session`` applies Claude
    Code's suggestion for the rest of this turn (the caller keeps it for
    later turns); ``mode`` switches the permission mode (approving a plan)."""
    response = {"behavior": "allow",
                "updatedInput": updated_input if updated_input is not None else request.input}
    updates = []
    if mode:
        updates.append({"type": "setMode", "mode": mode, "destination": "session"})
    elif for_session:
        kept_rules, kept_dirs, session_mode = request.kept_for_chat()
        if session_mode:
            updates.append({"type": "setMode", "mode": session_mode, "destination": "session"})
        rules = []
        for rule in kept_rules:
            name, _, content = rule.partition("(")
            entry = {"toolName": name}
            if content:
                entry["ruleContent"] = content[:-1]
            rules.append(entry)
        if rules:
            updates.append({"type": "addRules", "rules": rules, "behavior": "allow",
                            "destination": "session"})
        if kept_dirs:
            updates.append({"type": "addDirectories", "directories": kept_dirs,
                            "destination": "session"})
    if updates:
        response["updatedPermissions"] = updates
    return response


def deny_response(message: str = "") -> dict:
    """The answer that refuses; Claude reads ``message`` as the reason."""
    return {"behavior": "deny",
            "message": message.strip() or "The user refused this. Don't try it another way "
                                           "without asking."}


def answer_questions_response(request: PermissionRequest, answers: Dict[str, str]) -> dict:
    """Claude's questions answered: the tool runs with the answers added."""
    return allow_response(request, updated_input={**request.input, "answers": answers})


@dataclass
class TurnEvent:
    """One thing that happened during a turn, already made readable."""

    kind: str               # started | text | tool | denied | permission | background |
    #                         waiting | finished | failed | …
    text: str = ""
    session_id: str = ""
    is_error: bool = False
    denials: List[str] = field(default_factory=list)
    raw_type: str = ""
    request: Optional[PermissionRequest] = None
    detail: str = ""
    #: For "limits": the rate_limit_event's rate_limit_info (#19).
    data: Optional[dict] = None


class StreamParser:
    """Turns ``claude -p --output-format stream-json --verbose`` lines into
    ``TurnEvent``s. Unknown event types are ignored; bad lines are counted."""

    def __init__(self) -> None:
        self.model = ""  # the model system/init reports (#8, #58)
        #: Model choice ("default", "opus", …) -> the model it resolves to (#58).
        self.resolved_models: Dict[str, str] = {}
        self.session_id = ""
        self.api_key_source: Optional[str] = None
        self.bad_lines = 0
        self.finished = False
        #: Slash commands and skills, from the answer to ``initialize``:
        #: dicts with ``name``, ``description`` and ``argumentHint``.
        self.commands: List[dict] = []
        #: Control requests The Chat Place can't answer (not ``can_use_tool``);
        #: the runner refuses them so the CLI doesn't wait.
        self.unsupported_requests: List[str] = []
        #: The model's context window, from the result's modelUsage (#19).
        self.context_window = 0
        #: Edit tool uses waiting for their results: tool_use id -> file.
        self._edits: Dict[str, str] = {}

    def feed(self, line: str) -> List[TurnEvent]:
        line = line.strip()
        if not line:
            return []
        try:
            event = json.loads(line)
        except ValueError:
            self.bad_lines += 1
            return []
        if not isinstance(event, dict):
            self.bad_lines += 1
            return []
        try:
            return self._event(event)
        except Exception:  # noqa: BLE001 - a format change must not kill the turn
            self.bad_lines += 1
            return []

    def _event(self, event: dict) -> List[TurnEvent]:
        etype = str(event.get("type") or "")
        subtype = str(event.get("subtype") or "")
        sid = event.get("session_id")
        if isinstance(sid, str) and sid:
            self.session_id = sid

        if etype == "control_cancel_request":
            # A question or permission answered somewhere else (claude.ai, on
            # Remote Control): it's no longer waiting here.
            request_id = str(event.get("request_id") or "")
            return [TurnEvent("permission_cancelled", text=request_id,
                              session_id=self.session_id)] if request_id else []
        if etype == "control_request":
            request_id = str(event.get("request_id") or "")
            request = event.get("request") if isinstance(event.get("request"), dict) else {}
            if request.get("subtype") == "can_use_tool" and request_id:
                tool_input = request.get("input") if isinstance(request.get("input"), dict) else {}
                suggestions = request.get("permission_suggestions")
                permission = PermissionRequest(
                    request_id=request_id,
                    tool_name=str(request.get("tool_name") or "a tool"),
                    input=tool_input,
                    description=str(request.get("description") or ""),
                    suggestions=suggestions if isinstance(suggestions, list) else [],
                    tool_use_id=str(request.get("tool_use_id") or ""))
                return [TurnEvent("permission", text=permission.summary(),
                                  session_id=self.session_id, request=permission)]
            if request_id:
                self.unsupported_requests.append(request_id)
            return []
        if etype == "control_response":
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            if response.get("request_id") == INIT_REQUEST_ID:
                body = response.get("response") if isinstance(response.get("response"), dict) \
                    else {}
                commands = body.get("commands")
                if isinstance(commands, list):
                    self.commands = [c for c in commands
                                     if isinstance(c, dict) and isinstance(c.get("name"), str)]
                models = body.get("models")
                if isinstance(models, list):
                    self.resolved_models = {
                        str(m.get("value")): str(m.get("resolvedModel") or "")
                        for m in models if isinstance(m, dict) and m.get("value")}
                return [TurnEvent("initialized", session_id=self.session_id)]
            if response.get("request_id") == RC_REQUEST_ID:
                body = response.get("response") if isinstance(response.get("response"), dict) \
                    else {}
                if response.get("subtype") == "error":
                    body = {"error": str(response.get("error") or "it was refused")}
                return [TurnEvent("remote_control", session_id=self.session_id, data=body)]
            return []
        if etype == "rate_limit_event":
            info = event.get("rate_limit_info")
            if isinstance(info, dict) and info:
                return [TurnEvent("limits", session_id=self.session_id, data=info)]
            return []
        if etype == "system" and subtype == "session_state_changed":
            # "idle" is Claude Code's own turn-over signal: it comes after the
            # last result, background agents' included (#76).
            return [TurnEvent("state", text=str(event.get("state") or ""),
                              session_id=self.session_id)]
        if etype == "system" and subtype == "background_tasks_changed":
            # The whole list of background work still running (shell commands,
            # agents), after every change; empty once it has all finished.
            # Checked with Claude Code 2.1.296 (#161). Ambient ones (watchers,
            # tasks kept out of the transcript) aren't work, Claude Code says,
            # and are left out: one would keep the turn open for good.
            tasks = event.get("tasks")
            names = ([str(t.get("description") or "a background task")
                      for t in tasks if isinstance(t, dict) and not t.get("ambient")]
                     if isinstance(tasks, list) else [])
            return [TurnEvent("background", session_id=self.session_id,
                              data={"tasks": names})]
        if etype == "system" and subtype == "compact_boundary":
            meta = event.get("compactMetadata")
            post = meta.get('postTokens') if isinstance(meta, dict) else None
            return [TurnEvent("compacted", text=compaction_text(meta), session_id=self.session_id,
                              data={'post_tokens': post if type(post) is int and post >= 0 else 0,
                                    'message_key': event.get('uuid') if isinstance(event.get('uuid'), str) else ''})]
        if etype == "system" and subtype == "init":
            source = event.get("apiKeySource")
            self.api_key_source = source if isinstance(source, str) else None
            model = event.get("model")
            self.model = model if isinstance(model, str) else ""
            # The model actually in use (#8), which may not be the one asked for.
            return [TurnEvent("started", session_id=self.session_id, raw_type="init",
                              text=str(event.get("permissionMode") or ""),
                              data={"model": model if isinstance(model, str) else ""})]
        if etype == "system" and subtype == "permission_denied":
            name = str(event.get("tool_name") or "A tool")
            message = str(event.get("message") or "").strip()
            text = f"{name}: {message}" if message else f"{name} was refused"
            return [TurnEvent("denied", text=text, session_id=self.session_id)]
        if etype == "assistant" and not event.get("parent_tool_use_id"):
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            events: List[TurnEvent] = []
            if isinstance(content, str) and content.strip():
                events.append(TurnEvent("text", text=content.strip()))
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and str(block.get("text") or "").strip():
                        events.append(TurnEvent("text", text=str(block["text"]).strip()))
                    elif block.get("type") == "tool_use":
                        name = str(block.get("name") or "tool")
                        tool_input = block.get("input") if isinstance(block.get("input"), dict) \
                            else {}
                        target = _tool_target(tool_input)
                        # detail is what it acts on, as the messages list shows
                        # it ("Bash: git status"), for announcing it (#12).
                        # The file an edit tool works on, for an agent
                        # conversation's list of what a turn changed: kept
                        # until its result says the edit worked.
                        path = (tool_input.get("file_path") or tool_input.get("notebook_path")
                                if name in FILE_EDIT_TOOLS else None)
                        use_id = block.get("id")
                        if (isinstance(path, str) and path and isinstance(use_id, str)
                                and len(self._edits) < 500):
                            self._edits[use_id] = path
                        events.append(TurnEvent("tool", text=name,
                                                detail=f"{name}: {target}" if target else name))
            return events
        if etype == "user" and event.get("isReplay"):
            # One of our own messages, echoed as Claude Code takes it in
            # (--replay-user-messages). The transcript has it already.
            echoed = event.get("uuid")
            return [TurnEvent("taken", text=echoed if isinstance(echoed, str) else "",
                              session_id=self.session_id)]
        if etype == "user" and not event.get("parent_tool_use_id") and self._edits:
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            events = []
            for block in content if isinstance(content, list) else ():
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    path = self._edits.pop(str(block.get("tool_use_id")), None)
                    if path and not block.get("is_error"):
                        events.append(TurnEvent("edited", data={"file_path": path}))
            return events
        if etype == "result":
            self.finished = True
            models = event.get("modelUsage")
            if isinstance(models, dict):
                windows = [m.get("contextWindow") for m in models.values()
                           if isinstance(m, dict) and isinstance(m.get("contextWindow"), int)]
                if windows:
                    self.context_window = max(windows)
            denials = event.get("permission_denials")
            denial_text = [describe_denial(d) for d in denials] if isinstance(denials, list) else []
            is_error = bool(event.get("is_error")) or (subtype not in ("", "success"))
            text = event.get("result")
            if not isinstance(text, str) or not text.strip():
                errors = event.get("errors")
                if isinstance(errors, list) and errors:
                    text = "; ".join(str(e) for e in errors)
                else:
                    text = "" if not is_error else f"The turn ended with an error ({subtype or 'unknown'})."
            return [TurnEvent("finished", text=text.strip(), session_id=self.session_id,
                              is_error=is_error, denials=denial_text, raw_type=subtype)]
        return []


def api_key_problem(source: Optional[str]) -> Optional[str]:
    """A message if the CLI is about to bill an API key instead of the subscription."""
    if source is None or source in ("", "none"):
        return None
    return (f"Claude would have used an API key ({source}) instead of your subscription "
            "login, so The Chat Place stopped the turn before anything was sent. Remove "
            "that key from the environment or settings that set it, then try again.")


def fable_problem(actual: str, chosen: str, sent: bool = False) -> Optional[str]:
    """A message if the turn would run on Fable without it having been chosen
    (#58): Claude Code's default, or its fallback from the chosen model. Some
    plans bill Fable to usage credits, and in ``-p`` turns without asking.
    ``sent``: the message had already gone when this was found."""
    if "fable" not in (actual or "").lower() or "fable" in (chosen or "").lower():
        return None
    which = (f"instead of {model_label(chosen)}, the model this session chose" if chosen
             else "Claude Code's default model")
    done = ("stopped the turn as soon as it started" if sent
            else "didn't send your message")
    return (f"Claude Code would have used Fable, {which}. Some plans bill Fable to usage "
            f"credits, so The Chat Place {done}. Choose another model with File, "
            "Change Model, then send again.")


#: How long a finished turn waits for Claude Code to say it's idle (its SDK's
#: own ceiling) before ending the turn anyway.
IDLE_AFTER_RESULT_WAIT = 600.0
#: How long a turn waits, once its background work has all finished, for the
#: turn Claude Code starts to hear about it (#161). It comes at once (Claude
#: Code 2.1.296); this is only in case it never does.
BACKGROUND_DONE_WAIT = 60.0

#: How long a turn's message waits for Claude Code to answer initialize.
INIT_ANSWER_WAIT = 10.0


def resolved_model(models: Dict[str, str], chosen: str) -> str:
    """The model a turn will use, from the ``initialize`` answer: what the
    chosen value (or "default") resolves to; a full name stands for itself."""
    return models.get(chosen or "default") or chosen


def chosen_model(command: List[str]) -> str:
    """The ``--model`` a turn's command asks for, "" for Claude Code's default."""
    try:
        return command[command.index("--model") + 1]
    except (ValueError, IndexError):
        return ""


# ---------------------------------------------------------------------------
# Running a turn
# ---------------------------------------------------------------------------


def describe_elapsed(seconds: float) -> str:
    """'40 seconds', '3 minutes 5 seconds', '1 hour 2 minutes'."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)

    def unit(n, word):
        return f"{n} {word}" + ("s" if n != 1 else "")
    if hours:
        return unit(hours, "hour") + (" " + unit(minutes, "minute") if minutes else "")
    if minutes:
        return unit(minutes, "minute") + (" " + unit(secs, "second") if secs else "")
    return unit(secs, "second")


_UNKNOWN_OPTION = re.compile(r"unknown option '?(--[\w-]+)'?")


def exit_message(returncode, detail: str) -> str:
    """Why a turn ended before its result, in words. An older Claude Code
    that doesn't know an option The Chat Place passes (``--permission-prompts``
    is newer than 2.1.258, say) gets told to update, not shown the raw error."""
    unknown = _UNKNOWN_OPTION.search(detail or "")
    if unknown:
        return (f"Claude Code is too old for The Chat Place: it doesn't know the option "
                f"{unknown.group(1)}. Update it (in a terminal, run: claude update), then "
                "send again. The Chat Place is checked with Claude Code 2.1.286.")
    message = f"Claude exited without finishing the turn (exit code {returncode})."
    return f"{message} {detail}" if detail else message


class TurnRunner:
    """Runs one turn in a background thread and reports ``TurnEvent``s.

    ``on_event`` is called from the worker thread; the UI marshals it onto the
    main thread (``wx.CallAfter``). Exactly one ``finished`` or ``failed``
    event is always delivered last, whatever happens.

    There is deliberately no time limit: a long build or test run is a normal
    turn. ``elapsed`` and ``last_activity`` let the UI say how long it has been
    going and what it was last doing, when asked; Stop ends it.
    """

    def __init__(self, command: List[str], cwd: str, prompt: str,
                 on_event: Callable[[TurnEvent], None],
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 env: Optional[Dict[str, str]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 images: Optional[List[dict]] = None,
                 remote_control: Optional[Dict[str, str]] = None,
                 timer: Callable[..., threading.Timer] = threading.Timer,
                 task_list: bool = False, mcp_config: Optional[str] = None) -> None:
        self.command = command
        #: ``--mcp-config`` for this turn's process only (the session tools,
        #: #165), or None. Kept apart from ``command``, which is the turn's
        #: own: it ends with ``--resume <id>``, and tests read it.
        self.mcp_config = mcp_config
        self.cwd = cwd
        self.prompt = prompt
        self.images = list(images or [])
        #: {"name": …, "reattach": …} to turn on Remote Control, or None.
        self.remote_control = remote_control
        self.on_event = on_event
        self._popen = popen
        #: Makes the turn's timers (the idle ceiling, the initialize wait), so
        #: a test can fire them when it likes instead of waiting minutes.
        self._timer = timer
        self._env = dict(env if env is not None else child_environment())
        # Ask for session_state_changed, so the turn ends when Claude Code says
        # it's idle, not at the first result (#76).
        self._env["CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"] = "1"
        if task_list:
            # Claude's task list (TaskCreate and TaskUpdate, #231). Claude
            # Code leaves those tools out on newer models unless asked; the
            # desktop app's sessions have them, so its sessions and these
            # work alike. Documented in Claude Code's env-vars page (2.1.233
            # and later). Off, the environment is left as it was, so a value
            # the user set themselves still applies.
            self._env[TASK_LIST_VAR] = "1"
        self._process: Optional[subprocess.Popen] = None
        self._tree = platform_paths.ProcessTree()
        self._lock = threading.Lock()
        self._cancelled = False
        self._stopped_for_key = False
        #: Messages written to Claude Code (the turn's own, then each Send
        #: Now) whose result hasn't come yet. Each message gets one result,
        #: and they come in order, so while this is above zero a result
        #: belongs to work a later message replaced (#83): its
        #: error_during_execution is the interrupt, not a failure, and the
        #: turn isn't over. A count, not a flag, so that work finishing on its
        #: own just as Send Now goes can't pass for the new message's answer.
        self._unanswered = 0
        #: Each message written and not yet taken in, oldest first, as
        #: [kind, text, late, uuid] ("prompt", "now", "steer" or "follow_up").
        #: Claude Code echoes each one, with its uuid, as it takes it in
        #: (--replay-user-messages; checked with 2.1.296, a slash command
        #: included). Echoes are matched by uuid, so anything else echoed
        #: can't be taken for one of ours. A result answers every message
        #: taken in before it: a
        #: steered message taken in mid-turn shares its turn's one result,
        #: and one taken in after a result gets its own (#238). ``late``: a
        #: steer that came after Claude had answered, so it's answered next
        #: rather than joining the work.
        self._untaken: List[list] = []
        self._taken_since_result = 0
        #: True once any echo has come. Until then (a Claude Code without
        #: the echo, or a test's fake), each result answers one message.
        self._echoes = False
        #: True from a result until Claude Code says it's running again: a
        #: steer now is answered after, not taken into work in progress.
        self._answered = False
        #: The latest result's reply, until it's said: a late steer's answer
        #: replaces it as the turn's, so it's handed on with ``steer_taken``.
        self._unsaid_reply = ""
        #: True once the turn's own message is written. Send Now or a steer
        #: before then waits in ``_waiting`` so it can't overtake it (#83).
        self._prompt_sent = False
        self._waiting: List[tuple] = []
        #: Stopped by The Chat Place before Claude answered (an API key or an
        #: unchosen Fable): the message goes back to the reply box.
        self.stopped_before_answer = False
        self._thread: Optional[threading.Thread] = None
        self._clock = clock
        self.started_at = clock()
        self.last_activity = "starting"
        #: True once the CLI reported the session (system/init): from then on
        #: the session exists and later turns must --resume it.
        self.session_started = False
        self.parser = StreamParser()
        #: Permission requests sent to the UI and not yet answered.
        self.pending: Dict[str, PermissionRequest] = {}
        self._stdin_open = False
        #: What Claude Code says is running in the background, by description
        #: (#161). While it isn't empty, nothing ends the turn but Stop: not an
        #: idle session, and not the idle ceiling.
        self.background_tasks: List[str] = []
        #: Claude has answered and is idle, and the turn is kept open only
        #: for background work. A message sent now goes straight in
        #: (``send_follow_up``) instead of waiting for the turn to end.
        self.waiting_on_background = False

    def elapsed(self) -> float:
        return self._clock() - self.started_at

    def send_now(self, prompt: str) -> bool:
        """Send a message into the running turn at once, as the desktop app's
        Send Now does: Claude stops what it's doing (``interrupt``) and takes
        the message next, in this same process. False if the turn is over."""
        with self._lock:
            if not self._stdin_open or self._cancelled or self.stopped_before_answer:
                return False  # over, or being stopped: it would go nowhere
            if not self._prompt_sent:
                # Still waiting for initialize's answer: the turn's own
                # message goes first, then this one (``_send_prompt``).
                self._waiting.append(("now", prompt))
                ok = True
            else:
                ok = self._write_send_now(prompt)
            if ok:
                self.last_activity = "starting on your new message"
            return ok

    def send_follow_up(self, prompt: str, images: Optional[List[dict]] = None) -> bool:
        """Send a message while Claude is idle, waiting on background work
        (#161): a plain next message, nothing to interrupt. False unless the
        turn is waiting that way, so the caller queues it as usual."""
        with self._lock:
            if (not self._stdin_open or self._cancelled or self.stopped_before_answer
                    or not self.waiting_on_background):
                return False
            ok = self._write_message("follow_up", prompt, images)
            if ok:
                self.waiting_on_background = False
                self.last_activity = "starting on your new message"
            return ok

    def steer(self, prompt: str) -> bool:
        """Send a message into the running turn without stopping it (#238).
        Checked with Claude Code 2.1.296: Claude takes it in at its next step
        (after the tool call it's on) and carries on, and the turn ends with
        one result. If Claude is already writing its last reply, it answers
        the message straight after, in this same process. A ``steer_taken``
        event says which happened. False if the turn is over."""
        with self._lock:
            if not self._stdin_open or self._cancelled or self.stopped_before_answer:
                return False
            if not self._prompt_sent:
                self._waiting.append(("steer", prompt))
                return True
            late = self._answered or self.waiting_on_background
            ok = self._write_message("steer", prompt, late=late)
            if ok and self.waiting_on_background:
                # Claude was idle, waiting on background work: nothing to
                # join, so it's simply the next message (#161).
                self.waiting_on_background = False
                self.last_activity = "starting on your new message"
            return ok

    @property
    def steering(self) -> List[str]:
        """Steered messages Claude hasn't taken in yet, for turn status."""
        with self._lock:
            return [m[1] for m in self._untaken if m[0] == "steer"] + [
                prompt for kind, prompt in self._waiting if kind == "steer"]

    def _write_message(self, kind: str, prompt: str, images: Optional[List[dict]] = None,
                       late: bool = False) -> bool:
        """Write a message, to be answered. Call with ``_lock`` held."""
        message_id = str(uuid.uuid4())
        ok = self._write_raw(message_line(prompt, images, message_id))
        if ok:
            self._unanswered += 1
            self._untaken.append([kind, prompt, late, message_id])
        return ok

    def _taken(self, message_id: str) -> Optional[tuple]:
        """Claude Code echoed one of our messages: it has taken it in. Its
        (kind, text, late, uuid), or None if the echo isn't one of ours."""
        with self._lock:
            for index, message in enumerate(self._untaken):
                if message[3] == message_id:
                    self._echoes = True
                    self._taken_since_result += 1
                    return tuple(self._untaken.pop(index))
            return None

    def _end_or_wait(self) -> str:
        """Claude Code is idle with the turn answered: end the turn by closing
        stdin ("closed"), or keep it open while background work runs
        ("waiting", or "started waiting" the first time). "unanswered" if a
        message sent since is still to be answered. One step under
        ``_lock``, so nothing can be sent in between."""
        with self._lock:
            if self._unanswered:
                return "unanswered"
            if self.background_tasks:
                started = not self.waiting_on_background
                self.waiting_on_background = True
                return "started waiting" if started else "waiting"
            self._close_stdin_locked()
            return "closed"

    def _write_send_now(self, prompt: str) -> bool:
        """Interrupt, then the message. Call with ``_lock`` held."""
        ok = self._write_line({"type": "control_request",
                               "request_id": f"thechatplace-interrupt-{uuid.uuid4().hex[:8]}",
                               "request": {"subtype": "interrupt"}})
        # Until it's answered, the turn isn't over, idle or not.
        return ok and self._write_message("now", prompt)

    def _send_prompt(self) -> None:
        """Write the turn's own message, then any Send Now or steer that came
        while it waited, in order. Called once."""
        with self._lock:
            self._prompt_sent = True
            self._write_message("prompt", self.prompt, self.images)
            waiting, self._waiting = self._waiting, []
            for kind, prompt in waiting:
                if kind == "now":
                    self._write_send_now(prompt)
                else:
                    self._write_message(kind, prompt)

    def _result_came(self) -> bool:
        """Count a result against the oldest unanswered message. True if a
        later message is still to be answered, so this result was for work
        Send Now replaced."""
        with self._lock:
            # A result answers the messages taken in since the last one. With
            # the echoes that's exact, and a result with nothing taken in (a
            # background agent's notification, say) answers nothing; without
            # them, one. Never below zero.
            if self._echoes:
                answered = self._taken_since_result
            else:
                # No echoes (a Claude Code without them): a steer can't be
                # told apart, so the result is taken to answer it too, rather
                # than hold the turn open for an answer that may never come.
                steers = [m for m in self._untaken if m[0] == "steer"]
                self._untaken = [m for m in self._untaken if m[0] != "steer"]
                answered = 1 + len(steers)
            self._taken_since_result = 0
            self._unanswered = max(0, self._unanswered - answered)
            self._answered = True
            for message in self._untaken:
                if message[0] == "steer":
                    message[2] = True  # answered after this result, not in it
            return self._unanswered > 0

    def _close_stdin_if_answered(self) -> bool:
        """End the turn (close stdin, so the CLI exits) unless a Send Now
        message is still to be answered or background work is running. A
        background agent keeps Claude Code "running" after its answer, with
        idle only once the agent is done, so that silence isn't a stuck finish.
        Closing stdin gives the work only 600 s more (Claude Code 2.1.296), so
        closing here stopped agents 10 or 20 minutes in, with nothing left to
        pick them up. The check and the close are one step under ``_lock``, so
        a Send Now can't slip in between. True if closed (or already closed)."""
        with self._lock:
            if self._unanswered or self.background_tasks:
                return False
            self._close_stdin_locked()
            return True

    def respond(self, request_id: str, response: dict) -> bool:
        """Answer a permission request (from the UI thread). False if the turn
        is already over or the request isn't waiting."""
        with self._lock:
            request = self.pending.pop(request_id, None)
            if request is None:
                return False
            ok = self._write_line({"type": "control_response", "response": {
                "subtype": "success", "request_id": request_id, "response": response}})
            if ok:
                self.last_activity = (
                    "working" if response.get("behavior") == "allow"
                    else f"carrying on after you refused {request.tool_name}")
            return ok

    def _write_raw(self, data: bytes) -> bool:
        """Write bytes to the CLI's stdin. Call with ``_lock`` held."""
        process = self._process
        if process is None or not self._stdin_open:
            return False
        try:
            process.stdin.write(data)
            process.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def _write_line(self, obj: dict) -> bool:
        """Write one JSON line to the CLI's stdin. Call with ``_lock`` held."""
        process = self._process
        if process is None or not self._stdin_open:
            return False
        try:
            process.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            process.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def _close_stdin(self) -> None:
        with self._lock:
            self._close_stdin_locked()

    def _close_stdin_locked(self) -> None:
        if not self._stdin_open or self._process is None:
            return
        self._stdin_open = False
        try:
            self._process.stdin.close()
        except (OSError, ValueError):
            pass

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="claude-turn", daemon=True)
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
        self._kill()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def _kill(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                self._tree.kill()
                if process.poll() is None:
                    process.kill()
        except Exception:  # noqa: BLE001
            pass

    def _emit(self, event: TurnEvent) -> None:
        try:
            self.on_event(event)
        except Exception:  # noqa: BLE001 - a UI bug must not wedge the worker
            pass

    def _run(self) -> None:
        stderr_lines: List[str] = []
        final: Optional[TurnEvent] = None
        try:
            if not os.path.isdir(self.cwd):
                raise FileNotFoundError(f"The folder {self.cwd} doesn't exist.")
            if self._cancelled:
                raise _Cancelled()
            # Bytes in and out: text mode on Windows would turn the prompt's
            # newlines into CRLF, and splitting decoded text on every Unicode
            # line break would cut JSON lines that contain U+2028.
            command = list(self.command)
            if self.mcp_config:
                # Last: --mcp-config takes several values, so nothing that
                # isn't one may follow it.
                command += ["--mcp-config", self.mcp_config]
            process = self._popen(
                command,
                cwd=self.cwd,
                env=self._env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **platform_paths.ProcessTree.popen_kwargs(),
            )
            with self._lock:
                self._process = process
                self._tree.attach(process)
                cancelled_early = self._cancelled
            if cancelled_early:
                # cancel() ran between the check above and Popen returning.
                self._kill()

            def drain_stderr() -> None:
                try:
                    for raw in iter(process.stderr.readline, b""):
                        if len(stderr_lines) < 200:
                            stderr_lines.append(_decode(raw).rstrip())
                except Exception:  # noqa: BLE001
                    pass

            err_thread = threading.Thread(target=drain_stderr, daemon=True)
            err_thread.start()
            with self._lock:
                try:
                    # Only initialize: its answer says which model the turn
                    # would use, and the message waits for that (#58).
                    process.stdin.write(initialize_line())
                    if self.remote_control:
                        process.stdin.write(remote_control_line(
                            self.remote_control.get("name", ""),
                            self.remote_control.get("reattach", "")))
                    process.stdin.flush()
                    self._stdin_open = True
                except (OSError, ValueError):
                    pass  # the process died early; its exit code tells us why
            chosen = chosen_model(self.command)
            message_sent = False
            states_seen = False
            last_state = ""
            # After a result, idle should follow; if it never does (a stuck
            # hook, say), end the turn anyway after a while, as Claude Code's
            # own SDK does. Paused while Claude waits for an answer, while it
            # works on a Send Now message, however long that takes (#83), and
            # while background work runs.
            ceiling: List[threading.Timer] = []

            def arm_ceiling(unanswered_too: bool = False,
                            wait: float = IDLE_AFTER_RESULT_WAIT) -> None:
                """``unanswered_too``: Claude Code says it's idle with a Send
                Now message seemingly unanswered; end the turn even so, after
                the same while, rather than leave it open for ever."""
                disarm_ceiling()
                timer = self._timer(wait,
                                    self._close_stdin if unanswered_too
                                    else self._close_stdin_if_answered)
                timer.daemon = True
                ceiling.append(timer)
                timer.start()

            def disarm_ceiling() -> None:
                while ceiling:
                    ceiling.pop().cancel()
            #: True from saying Claude answered with background work running
            #: until the next result. Meanwhile, a turn Claude starts when the
            #: work finishes is new work, however long it takes, not a stuck
            #: finish for the ceiling to end; and if the turn ends with no
            #: result since, the reply was said already (#161).
            said_while_waiting = False

            def settle(outcome: str, final_event: TurnEvent) -> None:
                """After ``_end_or_wait`` said "closed" or "…waiting": the turn
                ended, or it now waits for background work (said once, with
                the reply so far)."""
                nonlocal said_while_waiting
                disarm_ceiling()
                if outcome == "started waiting":
                    said_while_waiting = True
                    self._unsaid_reply = ""  # said now, with the background work
                    self.last_activity = ("waiting for background work: "
                                          + "; ".join(self.background_tasks))
                    self._emit(TurnEvent("waiting", text=final_event.text,
                                         session_id=final_event.session_id,
                                         is_error=final_event.is_error,
                                         denials=list(final_event.denials),
                                         raw_type=final_event.raw_type,
                                         data={"tasks": list(self.background_tasks)}))
                    # Said now; the turn's end mustn't say them again.
                    final_event.denials = []
                    final_event.is_error = False
            sent_lock = threading.Lock()

            def send_message() -> bool:
                """Send the message once; False if it already went."""
                nonlocal message_sent
                with sent_lock:
                    if message_sent:
                        return False
                    message_sent = True
                self._send_prompt()
                return True

            # A Claude Code that never answers initialize still gets the
            # message (the system/init check still stands behind it).
            unanswered = self._timer(INIT_ANSWER_WAIT, send_message)
            unanswered.daemon = True
            unanswered.start()

            for raw in iter(process.stdout.readline, b""):
                for event in self.parser.feed(_decode(raw)):
                    if event.kind == "initialized" and not message_sent:
                        problem = fable_problem(
                            resolved_model(self.parser.resolved_models, chosen), chosen)
                        if problem:
                            with sent_lock:
                                stopping = not message_sent
                                message_sent = True  # and never: the timer can't send it
                            if stopping:
                                unanswered.cancel()
                                self.stopped_before_answer = True
                                self._kill()
                                final = TurnEvent("failed", text=problem, is_error=True,
                                                  session_id=self.parser.session_id)
                                break
                    if event.kind == "remote_control":
                        self._emit(event)  # never a reason to send the message
                        continue
                    if event.kind == "taken":
                        taken = self._taken(event.text)
                        if taken is not None and taken[0] == "steer":
                            self.last_activity = ("starting on your new message" if taken[2]
                                                  else "taking in your new message")
                            # Late: the reply before it will be replaced as
                            # the turn's by the steer's answer, so it's said
                            # now, with the news.
                            reply = ""
                            if taken[2]:
                                reply, self._unsaid_reply = self._unsaid_reply, ""
                            self._emit(TurnEvent("steer_taken", text=taken[1],
                                                 session_id=self.parser.session_id,
                                                 data={"late": taken[2], "reply": reply}))
                        continue
                    # The answer, or anything else of the conversation first
                    # (an older Claude Code): the message goes now. A state
                    # change isn't the conversation: "running" can come
                    # before initialize's answer, and must not skip the
                    # model check.
                    if event.kind not in ("state", "background") and send_message():
                        unanswered.cancel()
                    if event.kind == "initialized":
                        continue
                    if event.kind == "started":
                        self.session_started = True
                        problem = (api_key_problem(self.parser.api_key_source)
                                   or fable_problem(self.parser.model, chosen, sent=True))
                        if problem:
                            self._stopped_for_key = True
                            self.stopped_before_answer = True
                            self._kill()
                            final = TurnEvent("failed", text=problem, is_error=True,
                                              session_id=self.parser.session_id)
                            break
                    if event.kind in ("tool", "text") and final is not None:
                        # After an answer, this is Claude carrying on, however
                        # long it takes, not a stuck finish: the next result
                        # starts the ceiling again.
                        disarm_ceiling()
                    if event.kind == "tool":
                        self.last_activity = f"using {event.text}"
                    elif event.kind == "text":
                        self.last_activity = "writing a reply"
                    elif event.kind == "permission_cancelled":
                        with self._lock:
                            self.pending.pop(event.text, None)
                        self.last_activity = "working"
                    elif event.kind == "permission":
                        with self._lock:
                            self.pending[event.request.request_id] = event.request
                        self.last_activity = f"waiting for you: {event.request.summary()}"
                    if event.kind == "background":
                        tasks = list((event.data or {}).get("tasks") or [])
                        with self._lock:
                            had_tasks = bool(self.background_tasks)
                            self.background_tasks = tasks
                            if not tasks:
                                self.waiting_on_background = False
                        if tasks and self.waiting_on_background:
                            self.last_activity = ("waiting for background work: "
                                                  + "; ".join(tasks))
                        elif not tasks and final is not None and last_state == "idle":
                            # All done while Claude was idle: the turn it
                            # starts to hear about it ("running") should
                            # follow at once. If it never does, end the turn,
                            # even with a message seemingly unanswered.
                            if self._unanswered:
                                arm_ceiling(unanswered_too=True)
                            else:
                                arm_ceiling(wait=BACKGROUND_DONE_WAIT)
                        elif (had_tasks and not tasks and final is not None
                              and last_state == "running" and not self._unanswered):
                            # A background agent is done, with Claude Code
                            # "running" all along. Claude's follow-up should
                            # start now, and its first tool or text calls this
                            # off; if none comes, end the turn. Read without
                            # the lock, as "running" does.
                            arm_ceiling()
                        self._emit(event)
                        continue
                    if event.kind == "state":
                        states_seen = True
                        last_state = event.text
                        if event.text == "idle" and final is not None:
                            outcome = self._end_or_wait()
                            if outcome == "unanswered":
                                # Idle between the stopped work and the new
                                # message; "running" should follow.
                                arm_ceiling(unanswered_too=True)
                            else:
                                # Over (closing stdin lets the CLI exit), or
                                # kept open for the background work.
                                settle(outcome, final)
                        elif event.text == "requires_action":
                            disarm_ceiling()  # waiting for you, however long
                        elif event.text == "running" and final is not None:
                            with self._lock:
                                self.waiting_on_background = False
                                self._answered = False
                            # Read without the lock on purpose: a Send Now
                            # landing just after only meets the ceiling that
                            # checks again before closing.
                            if self._unanswered or said_while_waiting:
                                # On the Send Now message, or on what the
                                # background work brought back.
                                disarm_ceiling()
                            else:
                                arm_ceiling()
                        continue
                    if event.kind == "finished":
                        follows_waiting = said_while_waiting
                        said_while_waiting = False
                        # The latest result is the turn's, carrying every
                        # refusal and error of the turn. With state events,
                        # more can follow (a background agent's notification
                        # runs as its own result), so the turn waits for idle;
                        # an older Claude Code without them ends here.
                        replaced = self._result_came()
                        if replaced and event.raw_type == "error_during_execution":
                            # The work Send Now stopped: not the turn
                            # failing, and the message is still to come. The
                            # new message's own error, once nothing later is
                            # waiting, is a real one and stands.
                            event.is_error = False
                        if (final is not None and not follows_waiting and not event.is_error
                                and not (event.text or "").strip()):
                            # An empty result after the real answer (Claude
                            # Code sends one after a background agent's
                            # follow-up): the answer is still the turn's.
                            event.text = final.text
                        if final is not None:
                            event.denials = list(final.denials) + [
                                d for d in event.denials if d not in final.denials]
                            event.is_error = event.is_error or final.is_error
                        final = event
                        if not event.is_error:
                            self._unsaid_reply = event.text or ""
                        idle = states_seen and last_state == "idle"
                        if (not replaced and states_seen and not idle
                                and self.background_tasks):
                            # Answered with a background agent still running:
                            # Claude Code stays "running" with no idle until
                            # it's done, but takes a message at once and leaves
                            # the agent alone (checked with 2.1.296). So wait
                            # as for a background command, saying each answer;
                            # an empty result has nothing to say.
                            if (event.text or "").strip():
                                with self._lock:
                                    self.waiting_on_background = False
                                outcome = self._end_or_wait()
                                if outcome != "unanswered":
                                    settle(outcome, final)
                            else:
                                said_while_waiting = follows_waiting
                                disarm_ceiling()
                        elif not replaced and states_seen and not idle:
                            arm_ceiling()  # idle should follow
                        else:
                            # Answered, and idle (or no state events): closed,
                            # or kept open for background work. Unless a Send
                            # Now message is still to be answered (one may
                            # have gone just now): Claude moves on to it; if
                            # it has already said idle, "running" should
                            # follow, and the ceiling stands behind it.
                            outcome = "unanswered" if replaced else self._end_or_wait()
                            if outcome != "unanswered":
                                settle(outcome, final)
                            elif idle:
                                arm_ceiling(unanswered_too=True)
                            else:
                                disarm_ceiling()
                    else:
                        self._emit(event)
                self._refuse_unsupported()
                if self.stopped_before_answer:
                    break
            disarm_ceiling()
            self._close_stdin()
            process.wait()
            err_thread.join(timeout=2)
            if said_while_waiting and final is not None:
                # Over with no result since the reply was said, with the
                # background work running (#161): don't say it again.
                if self._cancelled:
                    final = None  # "Stopped.": Stop ended the work
                elif process.returncode:
                    # Claude Code died while it waited.
                    detail = next((x for x in reversed(stderr_lines) if x.strip()), "")
                    final = TurnEvent("failed", text=exit_message(process.returncode, detail),
                                      is_error=True, session_id=self.parser.session_id)
                else:
                    # The work finished and no turn came after it (the
                    # BACKGROUND_DONE_WAIT): nothing new to say.
                    final = TurnEvent("finished", session_id=final.session_id,
                                      raw_type=final.raw_type,
                                      data={"after_background": True})
            if final is None:
                if self._cancelled:
                    final = TurnEvent("failed", text="Stopped.", is_error=True,
                                      session_id=self.parser.session_id)
                else:
                    detail = next((x for x in reversed(stderr_lines) if x.strip()), "")
                    message = exit_message(process.returncode, detail)
                    final = TurnEvent("failed", text=message, is_error=True,
                                      session_id=self.parser.session_id)
        except _Cancelled:
            final = TurnEvent("failed", text="Stopped.", is_error=True)
        except FileNotFoundError as exc:
            final = TurnEvent("failed", text=str(exc) or "The claude command was not found.",
                              is_error=True)
        except Exception as exc:  # noqa: BLE001
            final = TurnEvent("failed", text=f"Couldn't run claude: {exc}", is_error=True)
        finally:
            self._tree.close()
            with self._lock:
                self.pending.clear()
        if not final.session_id:
            final.session_id = self.parser.session_id
        self._emit(final)

    def _refuse_unsupported(self) -> None:
        """Control requests other than permission checks (hooks, MCP
        messages): The Chat Place registers none, so say so at once rather than
        leave the CLI waiting."""
        while self.parser.unsupported_requests:
            request_id = self.parser.unsupported_requests.pop(0)
            with self._lock:
                self._write_line({"type": "control_response", "response": {
                    "subtype": "error", "request_id": request_id,
                    "error": "The Chat Place doesn't support this request."}})


class _Cancelled(Exception):
    pass


def _decode(raw) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


# ---------------------------------------------------------------------------
# Slash commands and skills (#23)
# ---------------------------------------------------------------------------


def usable_commands(commands) -> List[dict]:
    """The commands worth offering, from an ``initialize`` answer: named,
    not internal ("__..."), yours (skills and custom commands) first, then
    Claude Code's own, each group by name."""
    good = [c for c in commands or [] if isinstance(c, dict) and isinstance(c.get("name"), str)
            and c["name"] and not c["name"].startswith("__")]
    return sorted(good, key=lambda c: (bool(c.get("builtin")), c["name"].lower()))


def fetch_commands(executable: str, cwd: str, timeout: float = 20.0,
                   popen: Callable[..., subprocess.Popen] = subprocess.Popen) -> List[dict]:
    """The slash commands and skills Claude Code offers in ``cwd``, without
    running a turn: ``claude -p`` is sent only the ``initialize`` request,
    and stdin is closed once it answers, so no session is made and nothing
    is billed (checked with Claude Code 2.1.286: 1.4 seconds, no transcript).
    [] if it can't be had."""
    if not os.path.isdir(cwd):
        return []
    command = [executable, "-p", "--input-format", "stream-json", "--output-format",
               "stream-json", "--verbose", "--permission-prompts", "none"]
    try:
        process = popen(command, cwd=cwd, env=child_environment(), stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        creationflags=platform_paths.hidden_window_flags())
    except OSError:
        return []
    timer = threading.Timer(timeout, lambda: _quietly(process.kill))
    timer.start()
    try:
        process.stdin.write((json.dumps({"type": "control_request", "request_id": INIT_REQUEST_ID,
                                         "request": {"subtype": "initialize", "hooks": None}})
                             + "\n").encode("utf-8"))
        process.stdin.flush()
        parser = StreamParser()
        for raw in iter(process.stdout.readline, b""):
            parser.feed(_decode(raw))
            if parser.commands:
                break
        return usable_commands(parser.commands)
    except (OSError, ValueError):
        return []
    finally:
        timer.cancel()
        _quietly(process.stdin.close)
        try:
            process.wait(timeout=5)
        except Exception:  # noqa: BLE001
            _quietly(process.kill)
            _quietly(lambda: process.wait(timeout=2))


def _quietly(action) -> None:
    try:
        action()
    except Exception:  # noqa: BLE001
        pass
