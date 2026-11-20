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
  request is made. Before that, the answer to ``initialize`` (which comes
  before the message is written) has an ``account`` that's checked too, so
  a key is caught before anything is sent (billing.account_problem, #658).

On API credits (#658)
---------------------
A chat made to be charged to an API key (``TurnRunner(billing="api")``)
gets the key only through ``apiKeyHelper`` in its one ``--settings``, never
in an environment, with the address it may go to pinned there too
(billing.API_ENV, #663), and a ``--max-budget-usd`` reserved from the spend
ledger. Its message goes only once initialize's ``account`` says the app's
key at Anthropic's own API: no answer means no message. Each message it
writes is then said with an "api_sent" event, which is when the window says
"Sent on API credits". What each result adds to ``total_cost_usd`` is
settled in the ledger: the total can include the cost state Claude Code
restores on ``--resume``, so every turn of every chat records its last
total as the chat's baseline, and the result's tokens say whether it was
restored (spend_ledger, "What a result cost"). A result without a usable
cost, or a Stop before the last result, leaves the cost unknown and the
reservation held. A refused key (an ``api_retry`` of 401) ends the turn at
once. Everything an API turn says is scrubbed of key-shaped text. See
billing.py and spend_ledger.py.

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
from typing import Callable, Collection, Dict, List, Optional, Tuple

from . import platform_paths
from .billing import (KEY_REFUSED, ROUTE_API, account_problem, budget_flags, failure_text,
                      key_refused, normalize_route, scrub, source_problem)
from .chat_options import ChatChoices, ChatOptions, choices_from_init
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

#: Turns on Claude Code's file checkpoints for a ``-p`` turn (#468): before
#: each of your messages it saves the files Claude is about to change
#: (``~/.claude/file-history/<session>``) and writes a
#: ``file-history-snapshot`` record, which is what ``--rewind-files`` puts
#: back. Checked with Claude Code 2.1.296: without it no snapshots are kept,
#: and ``--rewind-files`` itself refuses ("File rewinding is not enabled").
#: It's among the host-session variables stripped by name, so it's set again
#: after ``child_environment``, never inherited.
CHECKPOINT_VAR = "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING"


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
#: The choices for a chat on API credits (#658): Fable too. On an API key
#: Fable is charged like any other model, at its own price, to the credits
#: the chat was made to spend, so the reason it's left out above doesn't
#: apply. Subscription chats never get it.
API_MODELS = MODELS + [("fable", "Fable")]
API_MODEL_LABELS = dict(API_MODELS)


def models_for(billing: str) -> list:
    """The model choices for a chat charged ``billing`` (billing.ROUTE_*)."""
    return API_MODELS if billing == "api" else MODELS


def model_allowed(model: str, billing: str) -> bool:
    """Whether a chat charged ``billing`` may keep ``model``."""
    return model in dict(models_for(billing))

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
    if chosen in API_MODEL_LABELS:
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
    return API_MODEL_LABELS.get(model, model) if model else "the default model"


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


APPEND_PROMPT = "--append-system-prompt="
#: Told to Claude on every turn (#319). Claude Code's own skills describe
#: Claude in Chrome, computer use and the desktop app's browser pane, but a
#: chat run here has none of them: turns start without --chrome, and the
#: other two exist only in the desktop app. Without this, a chat asked
#: anything that needs a browser told Kelly, three times over, to turn on
#: Claude in Chrome, which was already on.
#:
#: Claude Code records the system prompt on a chat's first request and
#: reuses the record (--system-prompt-snapshot). So a chat started before
#: this sees the note only once it's compacted, and a copy (Continue Here,
#: Rewind or Branch from Here, Continue on API Credits) is made with
#: prompt_fresh, or it would keep its source's record without the note.
#: When #319 passes --chrome, the Chrome part must go, and chats recorded
#: with it need their prompt made afresh too, or they'd be told Chrome
#: isn't there when it is.
UNAVAILABLE_NOTE = (
    "This chat is run by The Chat Place, which starts Claude Code in print mode, not the "
    "Claude desktop app. Claude in Chrome (mcp__claude-in-chrome__ tools), computer use "
    "(mcp__computer-use__ tools) and the desktop app's built-in browser pane "
    "(mcp__Claude_Browser__ tools) aren't available in this chat, even if a skill describes "
    "them or the user's settings turn them on, and no setting the user can change here "
    "turns them on. Don't tell the user to enable them. If a task needs one, say plainly "
    "that it isn't available in The Chat Place, then use what this chat does have: web "
    "fetch or search tools, or any browser or computer tools the user's own MCP servers "
    "provide. Otherwise, ask the user to check in their own browser, or say the Claude "
    "desktop app can do it.")


def _common_flags(permission_mode: str, model: str = "",
                  allowed_tools: Collection[str] = (), effort: str = "",
                  allowed_dirs: Collection[str] = (),
                  options: Optional[ChatOptions] = None) -> List[str]:
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
    # Agent, instructions and output style (#468), every turn, as the model
    # is. Before the lists below, whose values run to the next flag.
    option_flags = options.flags() if options is not None else []
    # What this chat can't do (#319), after any instructions of the chat's
    # own, in the one --append-system-prompt Claude Code takes.
    for index, flag in enumerate(option_flags):
        if flag.startswith(APPEND_PROMPT):
            option_flags[index] = flag + "\n\n" + UNAVAILABLE_NOTE
            break
    else:
        option_flags.append(APPEND_PROMPT + UNAVAILABLE_NOTE)
    flags += option_flags
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
                      allowed_dirs: Collection[str] = (),
                      options: Optional[ChatOptions] = None) -> List[str]:
    """Command for the first turn of a new session. The prompt goes on stdin."""
    if not platform_paths.is_safe_id(session_id):
        raise ValueError("Not a valid session id.")
    command = [executable, *_common_flags(permission_mode, model, allowed_tools, effort,
                                          allowed_dirs, options),
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
                       cowork_ids: Collection[str] = (), effort: str = "",
                       resume_at: str = "",
                       allowed_dirs: Collection[str] = (),
                       options: Optional[ChatOptions] = None) -> List[str]:
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

    ``resume_at`` (Rewind to Here and Branch from Here, #468) is the uuid of
    the last transcript record the copy keeps: ``--resume-session-at`` copies
    the history only up to and including it. Checked with Claude Code
    2.1.296: asked to list every request so far, the copy listed the turns
    before the cut and none after, and the source was unchanged. Cuts at a
    system record (``stop_hook_summary``, Rewind's usual cut), at an
    assistant reply and at a user message were all accepted. The cut is
    always a copy, never ``--resume-drops-turn`` in place: the source keeps
    its whole history, and nothing is ever written into a desktop session.
    """
    if resume_at and not platform_paths.is_safe_id(resume_at):
        raise ValueError("That isn't a message Claude Code can go back to.")
    if not platform_paths.is_safe_id(source_id) or source_id.startswith("local_"):
        raise ResumeRefused("That is not a session id Claude Code can continue from.")
    if source_id in cowork_ids:
        raise ResumeRefused("A Cowork session can't be continued here; open it in Claude.")
    if not platform_paths.is_safe_id(new_id) or new_id == source_id or new_id in taken_ids:
        raise ValueError("The new session needs an id of its own.")
    command = [executable, *_common_flags(permission_mode, model, effort=effort,
                                          allowed_dirs=allowed_dirs, options=options),
               "--resume", source_id, "--fork-session", "--session-id", new_id]
    if resume_at:
        command += ["--resume-session-at", resume_at]
    title = " ".join((title or "").split())
    if title:
        command += ["--name", title]
    return command


def build_rewind_files_command(executable: str, session_id: str, message_uuid: str,
                               own_ids: Collection[str],
                               desktop_ids: Collection[str]) -> List[str]:
    """Command that puts the files Claude changed back to the versions saved
    before one of your messages (Rewind to Here, #468). No turn runs: Claude
    Code restores the files from its checkpoints, says "Files rewound to
    state at message <uuid>" and exits (checked with Claude Code 2.1.296).

    Only for The Chat Place's own sessions (``check_resume_allowed``): Claude
    Code opens the session to do it and may write to its transcript. Needs
    ``CHECKPOINT_VAR`` in the environment, as the turns that made the
    checkpoints did (``run_rewind_files`` sets it).
    """
    check_resume_allowed(session_id, own_ids, desktop_ids)
    if not platform_paths.is_safe_id(message_uuid):
        raise ValueError("That isn't a message Claude Code can go back to.")
    return [executable, "-p", "--resume", session_id, "--rewind-files", message_uuid]


def build_resume_command(executable: str, session_id: str, permission_mode: str,
                         own_ids: Collection[str],
                         desktop_ids: Collection[str], model: str = "",
                         allowed_tools: Collection[str] = (), title: str = "",
                         effort: str = "", allowed_dirs: Collection[str] = (),
                         options: Optional[ChatOptions] = None) -> List[str]:
    """Command for a later turn. Refuses anything but The Chat Place's own sessions.

    ``--name`` goes on every turn, not just the first (#123). Other sessions
    address this one by that name (``SendMessage``); without it each resumed
    turn got a new made-up name ("project-37", then "project-36"), so a reply
    sent to the name seen earlier never arrived. Checked with Claude Code
    2.1.286: ``--resume`` with ``--name`` is accepted and keeps the title.
    """
    check_resume_allowed(session_id, own_ids, desktop_ids)
    command = [executable, *_common_flags(permission_mode, model, allowed_tools, effort,
                                          allowed_dirs, options)]
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


#: Request ids of the live control requests a turn sends (#468): set_model,
#: set_permission_mode, stop_task, get_context_usage. Their answers come back
#: as ``control_answer`` events, matched by id.
CONTROL_REQUEST_PREFIX = "thechatplace-ctl-"


def control_line(request_id: str, subtype: str, **fields) -> bytes:
    """A control request into the running turn (#468)."""
    return (json.dumps({"type": "control_request", "request_id": request_id,
                        "request": {"subtype": subtype, **fields}}, ensure_ascii=False)
            + "\n").encode("utf-8")


def _int(value) -> int:
    return value if type(value) is int else 0


def _live_system_event(etype: str, subtype: str, event: dict,
                       session_id: str) -> Optional["TurnEvent"]:
    """System messages about a turn in progress that The Chat Place says or
    keeps (#468), or None. Fields checked against Claude Code 2.1.296's own
    schema and, for ``status``, a real capture.

    * ``status`` with ``permissionMode``: the mode changed, by a live Change
      Permission Mode or by Claude itself (approving a plan, EnterPlanMode).
    * ``api_retry``: a request failed and will be tried again.
    * ``model_fallback``: the turn moved to the fallback model.
    * ``memory_saved``: Claude wrote to its memory files.
    """
    if etype != "system":
        return None
    if subtype == "status":
        mode = event.get("permissionMode")
        if isinstance(mode, str) and mode:
            # "default" is what The Chat Place calls manual.
            return TurnEvent("mode", text=normalize_permission_mode(mode),
                             session_id=session_id)
        return None
    if subtype == "api_retry":
        error = event.get("error")
        status = event.get("error_status")
        return TurnEvent("retry", session_id=session_id, data={
            "attempt": _int(event.get("attempt")), "max_retries": _int(event.get("max_retries")),
            "retry_delay_ms": _int(event.get("retry_delay_ms")),
            "error_status": status if type(status) is int else None,
            "error": error if isinstance(error, str) else ""})
    if subtype == "model_fallback":
        return TurnEvent("fallback", session_id=session_id, data={
            key: str(event.get(key) or "") for key in
            ("trigger", "original_model", "fallback_model", "content")})
    if subtype == "memory_saved":
        paths = event.get("written_paths")
        verb = event.get("verb")
        return TurnEvent("memory", session_id=session_id, data={
            "paths": [p for p in paths if isinstance(p, str) and p]
            if isinstance(paths, list) else [],
            "verb": verb if isinstance(verb, str) and verb.strip() else "Saved"})
    return None


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
    #: In a chat on API credits (#663): what's shown and said about the
    #: request (``summary``, ``detail``, ``plan``) has key-shaped text
    #: hidden. ``input``, which the answer is made from, stays as it is.
    hide_keys: bool = False

    @property
    def is_question(self) -> bool:
        return self.tool_name == QUESTION_TOOL

    @property
    def is_plan(self) -> bool:
        return self.tool_name == PLAN_TOOL

    def _shown(self, text: str) -> str:
        return scrub(text) if self.hide_keys else text

    def questions(self) -> List[dict]:
        items = self.input.get("questions")
        return [q for q in items if isinstance(q, dict) and isinstance(q.get("question"), str)] \
            if isinstance(items, list) else []

    def plan(self) -> str:
        """The plan, to show (an answer approving it sends ``input``)."""
        plan = self.input.get("plan")
        return self._shown(plan) if isinstance(plan, str) else ""

    def summary(self) -> str:
        """One line, for announcements: what Claude is waiting for."""
        return self._shown(self._summary())

    def _summary(self) -> str:
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
        return self._shown(self._detail())

    def _detail(self) -> str:
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
    #                         waiting | finished | failed | mode | retry | fallback |
    #                         memory | context (#468) | api_sent (#663) | …
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
        #: The whole answer to ``initialize`` (agents and output styles
        #: too, #468), or None until it comes.
        self.init_body: Optional[dict] = None
        #: Control requests The Chat Place can't answer (not ``can_use_tool``);
        #: the runner refuses them so the CLI doesn't wait.
        self.unsupported_requests: List[str] = []
        #: The model's context window, from the result's modelUsage (#19).
        self.context_window = 0
        #: Edit tool uses waiting for their results: tool_use id -> file.
        self._edits: Dict[str, str] = {}
        #: The model of the latest top-level assistant message (#468).
        self.reply_model = ""
        #: Top-level assistant messages from a model (not Claude Code's own
        #: ``<synthetic>`` error text): each is a request that went through,
        #: and so may have cost something (#663).
        self.assistant_messages = 0
        #: The latest top-level assistant message with text: its record
        #: uuid and API message id, which the transcript keys the reply by
        #: (a reply merged from several records goes by the message id). A
        #: turn on API credits keeps its cost with that reply (#692).
        self.reply_uuid = ""
        self.reply_message_id = ""

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
            if request.get("subtype") == "elicitation" and request_id:
                # An MCP server asking you for input (#468): waits for an
                # answer like a permission request.
                from .claude_mcp import elicitation_from
                asked = elicitation_from(request_id, request)
                return [TurnEvent("permission", text=asked.summary(),
                                  session_id=self.session_id, request=asked)]
            if request_id:
                self.unsupported_requests.append(request_id)
            return []
        if etype == "control_response":
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            if response.get("request_id") == INIT_REQUEST_ID:
                body = response.get("response") if isinstance(response.get("response"), dict) \
                    else {}
                self.init_body = body
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
            request_id = str(response.get("request_id") or "")
            if request_id.startswith(CONTROL_REQUEST_PREFIX):
                # The answer to a live control request The Chat Place sent
                # into the running turn (#468): set_model, stop_task, …
                ok = response.get("subtype") == "success"
                body = response.get("response")
                return [TurnEvent("control_answer", session_id=self.session_id, data={
                    "request_id": request_id, "ok": ok,
                    "response": body if isinstance(body, dict) else {},
                    "error": "" if ok else str(response.get("error") or "it was refused")})]
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
            work = ([t for t in tasks if isinstance(t, dict) and not t.get("ambient")]
                    if isinstance(tasks, list) else [])
            names = [str(t.get("description") or "a background task") for t in work]
            # Each one's task_id too, which stop_task takes (#468): checked
            # with Claude Code 2.1.296 ("b375k20qt" for a background Bash).
            entries = [{"task_id": str(t.get("task_id") or ""),
                        "description": str(t.get("description") or "a background task"),
                        "task_type": str(t.get("task_type") or "")} for t in work]
            return [TurnEvent("background", session_id=self.session_id,
                              data={"tasks": names, "entries": entries})]
        live = _live_system_event(etype, subtype, event, self.session_id)
        if live is not None:
            return [live]
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
            # Its MCP servers and how each is doing, so a turn can say once
            # which ones Claude can't use (#468).
            data = {"model": model if isinstance(model, str) else ""}
            if isinstance(event.get("mcp_servers"), list):
                data["mcp_servers"] = event["mcp_servers"]
            return [TurnEvent("started", session_id=self.session_id, raw_type="init",
                              text=str(event.get("permissionMode") or ""), data=data)]
        if etype == "system" and subtype == "permission_denied":
            name = str(event.get("tool_name") or "A tool")
            message = str(event.get("message") or "").strip()
            text = f"{name}: {message}" if message else f"{name} was refused"
            return [TurnEvent("denied", text=text, session_id=self.session_id)]
        if etype == "assistant" and not event.get("parent_tool_use_id"):
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            events: List[TurnEvent] = []
            reply_model = message.get("model") if isinstance(message, dict) else None
            if isinstance(reply_model, str) and reply_model:
                # The model that actually wrote this, which can differ from
                # system/init's mid-turn: a fallback, a live Change Model, or
                # plan mode's own model (#468). The Fable check reads it.
                self.reply_model = reply_model
                if reply_model != "<synthetic>":
                    self.assistant_messages += 1
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
            if any(e.kind == "text" for e in events) and reply_model != "<synthetic>":
                record_id = event.get("uuid")
                message_id = message.get("id") if isinstance(message, dict) else None
                self.reply_uuid = record_id if isinstance(record_id, str) else ""
                self.reply_message_id = message_id if isinstance(message_id, str) else ""
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
            # What Claude Code says the process has cost so far (#658), and
            # by model. On the subscription it's only what it would have
            # cost; a turn on API credits settles its ledger with it. A
            # cost that's missing or isn't a usable number is None (#663):
            # the turn's cost is then unknown, and its reservation held.
            cost = _usable_cost(event.get("total_cost_usd"))
            model_costs = {name: _usable_cost(m.get("costUSD")) for name, m in models.items()
                           if isinstance(name, str) and isinstance(m, dict)
                           and _usable_cost(m.get("costUSD")) is not None} \
                if isinstance(models, dict) else {}
            reported = {"reported_cost_usd": cost, "reported_model_costs": model_costs,
                        "reported_tokens": result_tokens(event)}
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
                              is_error=is_error, denials=denial_text, raw_type=subtype,
                              data=reported)]
        return []


def scrub_deep(value, depth: int = 0):
    """``value`` with every string in it scrubbed of key-shaped text
    (billing.scrub), through dicts and lists; anything else as it is."""
    if isinstance(value, str):
        return scrub(value)
    if depth > 20:
        return value
    if isinstance(value, dict):
        return {key: scrub_deep(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_deep(item, depth + 1) for item in value]
    return value


def scrub_event(event: "TurnEvent") -> "TurnEvent":
    """An API turn's event with no key in anything said or shown (#663): its
    text, detail, refusals and data. A permission request's ``input`` is
    left as it is, as it's what's answered; what it shows and says (its
    summary, detail and plan) is scrubbed (``hide_keys``)."""
    event.text, event.detail = scrub(event.text), scrub(event.detail)
    event.denials = [scrub(d) for d in event.denials]
    if event.data is not None:
        event.data = scrub_deep(event.data)
    if event.request is not None:
        event.request.hide_keys = True
    return event


def _usable_cost(value) -> Optional[float]:
    """A reported cost as a float, or None if it isn't a finite number of at
    least zero (#663)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return None
    return value if value == value and value not in (float("inf"), float("-inf")) \
        and value >= 0 else None


_OWN_TOKENS = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
               "cache_read_input_tokens")
_MODEL_TOKENS = ("inputTokens", "outputTokens", "cacheCreationInputTokens",
                 "cacheReadInputTokens")


def _token_sum(counts, names) -> Optional[int]:
    if not isinstance(counts, dict):
        return None
    found = [counts[name] for name in names if type(counts.get(name)) is int]
    return sum(found) if found else None


def result_tokens(event: dict) -> Optional[tuple]:
    """(the result's own tokens, modelUsage's cumulative tokens), or None
    when a result doesn't have both (#663). Checked with Claude Code 2.1.325:
    ``usage`` is that turn's own and ``modelUsage`` includes the cost state
    a ``--resume`` or a fork restored, so the second being larger means this
    process's totals include earlier spending (spend_ledger.CostTracker)."""
    own = _token_sum(event.get("usage"), _OWN_TOKENS)
    models = event.get("modelUsage")
    if own is None or not isinstance(models, dict):
        return None
    counts = [_token_sum(m, _MODEL_TOKENS) for m in models.values()]
    if not counts or any(c is None for c in counts):
        return None
    return own, sum(counts)


def api_key_problem(source: Optional[str]) -> Optional[str]:
    """A message if the CLI is about to bill an API key instead of the subscription."""
    if source is None or source in ("", "none"):
        return None
    return (f"Claude would have used an API key ({source}) instead of your subscription "
            "login, so The Chat Place stopped the turn before anything was sent. Remove "
            "that key from the environment or settings that set it, then try again.")


def fable_problem(actual: str, chosen: str, sent: bool = False,
                  midway: bool = False, billing: str = "subscription") -> Optional[str]:
    """A message if the turn would run on Fable without it having been chosen
    (#58): Claude Code's default, or its fallback from the chosen model. Some
    plans bill Fable to usage credits, and in ``-p`` turns without asking.
    ``sent``: the message had already gone when this was found; ``midway``:
    the turn was under way, moved to Fable by a fallback or the like (#468).
    Never for a chat on API credits (#658): there Fable is charged to the
    credits like any model, and the per-message limit bounds it."""
    if billing == "api":
        return None
    if "fable" not in (actual or "").lower() or "fable" in (chosen or "").lower():
        return None
    which = (f"instead of {model_label(chosen)}, the model this session chose" if chosen
             else "Claude Code's default model")
    done = ("stopped the turn when it moved to Fable" if midway
            else "stopped the turn as soon as it started" if sent
            else "didn't send your message")
    return (f"Claude Code would have used Fable, {which}. Some plans bill Fable to usage "
            f"credits, so The Chat Place {done}. Choose another model with Chat, "
            "Chat Settings, Change Model, then send again.")


#: How long a finished turn waits for Claude Code to say it's idle (its SDK's
#: own ceiling) before ending the turn anyway.
IDLE_AFTER_RESULT_WAIT = 600.0
#: How long a turn waits, once its background work has all finished, for the
#: turn Claude Code starts to hear about it (#161). It comes at once (Claude
#: Code 2.1.296); this is only in case it never does.
BACKGROUND_DONE_WAIT = 60.0

#: How long a turn's message waits for Claude Code to answer initialize.
INIT_ANSWER_WAIT = 10.0
#: How long Stop waits, on API credits, for the interrupted turn's result
#: (and so its cost) before ending the process anyway (#658). It came in
#: 0.4 seconds when checked.
STOP_COST_WAIT = 3.0
#: Said when an API turn's initialize went unanswered (#658): its account
#: couldn't be checked, so the message wasn't sent.
INIT_UNANSWERED_API = ("Claude Code didn't say how this message would be charged, so The Chat "
                       "Place didn't send it on API credits. Nothing was sent, so nothing was "
                       "charged. Try again; if it keeps happening, update Claude Code (in a "
                       "terminal, run: claude update).")

#: How long a live control request (Change Model, Stop Background Task, …)
#: waits for its answer before it counts as refused (#468). They're
#: answered at once (Claude Code 2.1.296).
CONTROL_WAIT = 10.0
#: How long a finished turn waits for get_context_usage's answer before
#: ending anyway (#468): long enough for the 735 ms seen, short enough that
#: the end isn't held up.
CONTEXT_WAIT = 1.5
#: The error a live control request's caller hears when the turn ended
#: before Claude Code answered (#468).
TURN_ENDED = "the turn ended"


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


def _session_of(command: List[str]) -> str:
    """The chat a turn's command is for: its new ``--session-id``, or the one
    it resumes. "" if neither."""
    for flag in ("--session-id", "--resume"):
        try:
            return command[command.index(flag) + 1]
        except (ValueError, IndexError):
            continue
    return ""


def _resumed_of(command: List[str]) -> str:
    """The chat whose cost state a turn's process restores (#658): the one it
    ``--resume``s, which for a ``--fork-session`` is the source, not the new
    ``--session-id``. "" for a new chat, which starts from nothing."""
    try:
        return command[command.index("--resume") + 1]
    except (ValueError, IndexError):
        return ""


def cost_tracker_for(command: List[str], baselines=None, cap: Optional[float] = None):
    """A CostTracker for one process (#658; spend_ledger, "What a result
    cost"): a new chat starts from nothing; one that resumes or forks starts
    from the baselines ``baselines`` (a SpendLedger) has for the chat it
    restores (the latest and the clean one, #663), or from "not known"
    without them. ``cap``: the reservation."""
    from .spend_ledger import CostTracker
    source = _resumed_of(command)
    if not source:
        return CostTracker(start=0.0, cap=cap, resumed=False)
    found = None
    if baselines is not None:
        try:
            found = baselines.baselines(source)
        except Exception:  # noqa: BLE001 - not known: counted up to the cap
            found = None
    if found is None:
        return CostTracker(start=None, cap=cap, resumed=True)
    latest, clean = found
    return CostTracker(start=latest.total, start_models=latest.models,
                       start_tokens=latest.tokens, clean=clean, cap=cap, resumed=True)


def build_key_test_command(executable: str) -> List[str]:
    """Test Key (#658): one tiny request on Haiku, the cheapest model, run as
    an API turn (TurnRunner with billing "api", purpose "test"), so it goes
    through the same checks and its cost is in the total. Nothing is kept
    (``--no-session-persistence``), no tools, no MCP servers and no hooks or
    slash commands, so the request is only the question."""
    return [executable, "-p", "--input-format", "stream-json", "--output-format",
            "stream-json", "--verbose", "--permission-mode", "manual",
            "--permission-prompts", "none", "--model", "haiku", "--tools", "",
            "--strict-mcp-config", "--disable-slash-commands",
            "--settings", json.dumps({"disableAllHooks": True}),
            "--no-session-persistence"]


#: What Test Key asks.
KEY_TEST_PROMPT = "Reply with the single word OK."


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
                 task_list: bool = False, mcp_config: Optional[str] = None,
                 disabled_mcp: Collection[str] = (),
                 context_usage: bool = False,
                 billing: str = "subscription", api_key_name: str = "",
                 ledger=None, chat_id: str = "", purpose: str = "turn",
                 cost_baselines=None) -> None:
        self.command = command
        #: How this turn is charged (#658): billing.ROUTE_SUBSCRIPTION or
        #: ROUTE_API, fixed for the process. Anything but "api" is the
        #: subscription.
        self.billing = normalize_route(billing)
        #: The kept secret an API turn's apiKeyHelper prints.
        self.api_key_name = api_key_name or platform_paths.CLAUDE_API_KEY_SECRET
        #: The spend ledger an API turn reserves from and settles to.
        if self.billing == ROUTE_API and ledger is None:
            from .spend_ledger import default_ledger
            ledger = default_ledger()
        self.ledger = ledger
        #: Where each chat's last reported cost is kept (a SpendLedger), so
        #: the next process for it counts only what it adds (#658). Every
        #: turn the window runs has one, subscription chats too; the
        #: ledger's own for an API turn.
        self.baselines = cost_baselines if cost_baselines is not None else ledger
        self.chat_id = chat_id or _session_of(command)
        self.purpose = purpose
        #: This turn's reservation (an API turn's, once it's started).
        self.reservation = None
        #: What this process has cost so far, from its results (#658); made
        #: again when the process starts, from the chat's baseline.
        from .spend_ledger import CostTracker
        self.costs = CostTracker()
        #: Stopped because Claude Code never answered initialize, on API
        #: credits, where the message only goes once the account is checked.
        self._init_refused = False
        #: False from writing a message until a result has said what
        #: everything so far cost; an API turn that ends while it's False
        #: keeps the rest of its reservation counted (spend_ledger).
        self._cost_known = True
        #: A result on API credits came without a usable cost (#663): what
        #: it cost is never known, so the reservation stays counted.
        self._cost_lost = False
        #: (chat, total, models, tokens) from the latest result, recorded as
        #: the chat's clean baseline if the process ends cleanly (#663).
        self._last_reported: Optional[tuple] = None
        #: The process was killed (Stop, a refusal): it saved no cost state.
        self._killed = False
        #: How many messages have actually been written to Claude Code
        #: (#663): only a turn that sent something can have cost anything.
        self.messages_written = 0
        #: Messages written whose "api_sent" event is still to go out, sent
        #: once the lock is let go (``_flush_sent``).
        self._sent_notes: List[str] = []
        #: ``parser.assistant_messages`` at the last result: more since
        #: means a request after it went through, so it may have cost
        #: something no result has counted yet (#663).
        self._answers_at_result = 0
        #: MCP servers this chat turns off (#468), kept out of this turn's
        #: process with ``--settings`` (claude_mcp.settings_args).
        self.disabled_mcp = list(disabled_mcp or ())
        #: The model this turn is meant to run on: its ``--model``, then
        #: whatever a live Change Model set (#468). The Fable checks read it.
        self.chosen = chosen_model(command)
        #: Ask Claude Code what fills the context (get_context_usage) as the
        #: turn ends, for Usage and Context (#468). Off, nothing is asked.
        self._want_context = context_usage
        #: The latest get_context_usage answer (categories, totalTokens, …).
        self.context_usage: Optional[dict] = None
        #: Live control requests waiting for their answer (#468):
        #: request id -> (callback, timeout timer or None).
        self._controls: Dict[str, tuple] = {}
        #: The background work as {task_id, description, task_type, since},
        #: ``since`` on the turn's clock when it was first seen (#468).
        self.background_entries: List[dict] = []
        #: The latest api_retry while Claude Code is retrying (#468), with
        #: ``at`` on the turn's clock; None once the turn moves on.
        self.retry: Optional[dict] = None
        #: Stopped by The Chat Place mid-turn, when it moved to Fable (#468):
        #: unlike ``stopped_before_answer``, Claude may have answered already,
        #: so the message isn't given back.
        self.stopped_mid_turn = False
        #: The permission mode Claude Code last said the turn is in (#468).
        try:
            self._mode = normalize_permission_mode(
                command[command.index("--permission-mode") + 1])
        except (ValueError, IndexError):
            self._mode = ""
        #: A close waiting on the context breakdown (#468). The answer or its
        #: timeout only clears this by closing; anything else that calls the
        #: close off is the worker's to clear, so whichever order they run in,
        #: the worker still sees it and goes on to wait for the work.
        self._closing = False
        #: ``--mcp-config`` for this turn's process only (the session tools,
        #: #165), or None. Kept apart from ``command``, which is the turn's
        #: own: it ends with ``--resume <id>``, and tests read it.
        self.mcp_config = mcp_config
        self.cwd = cwd
        self.prompt = prompt
        self.images = list(images or [])
        #: {"name": …, "reattach": …} to turn on Remote Control, or None.
        #: Never on API credits (#658): Remote Control is a claude.ai
        #: feature, so it fails closed there whatever the caller asked.
        self.remote_control = remote_control if self.billing != ROUTE_API else None
        self.on_event = on_event
        self._popen = popen
        #: Makes the turn's timers (the idle ceiling, the initialize wait), so
        #: a test can fire them when it likes instead of waiting minutes.
        self._timer = timer
        self._env = dict(env if env is not None else child_environment())
        if self.billing == ROUTE_API:
            # No certificate authority from the environment (#663).
            from .billing import api_environment
            self._env = api_environment(self._env)
        # Ask for session_state_changed, so the turn ends when Claude Code says
        # it's idle, not at the first result (#76).
        self._env["CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"] = "1"
        # File checkpoints (#468), so Rewind to Here can put files back.
        # Always on for The Chat Place's own turns: Claude Code keeps them
        # beside its other per-session data and cleans them up with it.
        self._env[CHECKPOINT_VAR] = "1"
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
        #: Reading stopped at Stop's result, with output perhaps still unread
        #: (``untaken_sure``).
        self._read_cut_short = False
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
        self._flush_sent()
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
        self._flush_sent()
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
        self._flush_sent()
        return ok

    @property
    def steering(self) -> List[str]:
        """Steered messages Claude hasn't taken in yet, for turn status."""
        with self._lock:
            return [m[1] for m in self._untaken if m[0] == "steer"] + [
                prompt for kind, prompt in self._waiting if kind == "steer"]

    def untaken(self) -> List[Tuple[str, str]]:
        """Steers and Send Now messages Claude hasn't taken in, oldest
        first, as (kind, text), for the window to give back when the turn
        ends, after a Stop most of all, rather than lose them (#238, #663).
        Without echoes (``echoes``) it's a guess: a result is taken to
        answer the oldest message other than a steer."""
        with self._lock:
            return [(m[0], m[1]) for m in self._untaken if m[0] in ("steer", "now")] + [
                (kind, prompt) for kind, prompt in self._waiting if kind in ("steer", "now")]

    @property
    def untaken_sure(self) -> bool:
        """Whether every message ``untaken`` lists certainly didn't go in
        (#663 review 5). One never written certainly didn't. One written is
        certain only with Claude Code's echoes, and only if reading wasn't
        cut short at Stop, which leaves any echo after it unread."""
        with self._lock:
            written = any(m[0] in ("steer", "now") for m in self._untaken)
            return not written or (self._echoes and not self._read_cut_short)

    @property
    def echoes(self) -> bool:
        """True once Claude Code has echoed a message it took in, so what
        ``untaken`` says is known; False (a Claude Code without the echoes,
        or none taken in yet) when a message it lists may have gone in."""
        with self._lock:
            return self._echoes

    def _write_message(self, kind: str, prompt: str, images: Optional[List[dict]] = None,
                       late: bool = False) -> bool:
        """Write a message, to be answered. Call with ``_lock`` held."""
        message_id = str(uuid.uuid4())
        ok = self._write_raw(message_line(prompt, images, message_id))
        if ok:
            self._cost_known = False  # until its result says what it cost
            self._unanswered += 1
            self._untaken.append([kind, prompt, late, message_id])
            self.messages_written += 1
            self._sent_notes.append(kind)
        return ok

    def _flush_sent(self) -> None:
        """Say each message just written, on API credits (#663): an
        "api_sent" event, which is when the window says "Sent on API
        credits". Only once it's really been written, after the account
        was checked; never under ``_lock``, as the event's handler may call
        back in."""
        with self._lock:
            notes, self._sent_notes = self._sent_notes, []
        if self.billing != ROUTE_API:
            return
        for kind in notes:
            self._emit(TurnEvent("api_sent", session_id=self.parser.session_id,
                                 data={"kind": kind}))

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
            self._close_after_context_locked()
            return "closed"

    # -- Live control of the running turn (#468) --------------------------------

    def control(self, subtype: str, on_done: Callable[[bool, dict, str], None],
                timeout: float = None, **fields) -> bool:
        """Send a control request into the running turn. ``on_done(ok,
        response, error)`` is called once, from the worker thread (or a
        timer's), with Claude Code's answer, or ``ok=False`` if it doesn't
        answer within ``timeout`` or the turn ends first. False, and no
        call, if the turn is over or being stopped."""
        request_id = f"{CONTROL_REQUEST_PREFIX}{subtype}-{uuid.uuid4().hex[:8]}"
        with self._lock:
            if not self._stdin_open or self._cancelled or self.stopped_before_answer:
                return False
            if not self._write_raw(control_line(request_id, subtype, **fields)):
                return False
            timer = self._timer(CONTROL_WAIT if timeout is None else timeout,
                                lambda: self._control_done(request_id, False, {},
                                                           "Claude Code didn't answer"))
            timer.daemon = True
            self._controls[request_id] = (on_done, timer)
        timer.start()
        return True

    def _control_done(self, request_id: str, ok: bool, response: dict, error: str) -> None:
        with self._lock:
            entry = self._controls.pop(request_id, None)
        if entry is None:
            return  # answered already, or timed out
        on_done, timer = entry
        if timer is not None:
            timer.cancel()
        try:
            on_done(ok, response, error)
        except Exception:  # noqa: BLE001 - a UI bug must not wedge the worker
            pass

    def _fail_controls(self) -> None:
        """The turn is over: nothing still waiting will be answered."""
        with self._lock:
            waiting = list(self._controls)
        for request_id in waiting:
            self._control_done(request_id, False, {}, TURN_ENDED)

    def set_model(self, model: str, on_done: Callable[[bool, dict, str], None]) -> bool:
        """Change the running turn's model (Change Model, #468). Checked with
        Claude Code 2.1.296: ``set_model`` is answered at once, a model it
        doesn't know with an error ("Model 'x' not found"). "" is Claude
        Code's default. Never to a model that resolves to Fable unchosen
        (#58): False, and the change waits for the next turn, whose check
        stops it before anything is sent."""
        if not model and not self.parser.resolved_models:
            # What the default is isn't known until initialize is answered.
            return False
        resolved = resolved_model(self.parser.resolved_models, model)
        if fable_problem(resolved, model, billing=self.billing):
            return False

        def done(ok, response, error):
            if ok:
                self.chosen = model
            on_done(ok, response, error)
        return self.control("set_model", done, model=model or "default")

    @property
    def permission_mode(self) -> str:
        """The mode Claude Code last said the turn is in, "" if it's not known."""
        return self._mode

    def set_permission_mode(self, mode: str,
                            on_done: Callable[[bool, dict, str], None]) -> bool:
        """Change the running turn's permission mode (#468). Checked with
        Claude Code 2.1.296: answered with ``{"mode": …}`` and followed by a
        system/status message; "manual" is taken as Claude Code's
        "default"."""
        mode = normalize_permission_mode(mode)
        if mode not in PERMISSION_MODE_VALUES:
            return False
        return self.control("set_permission_mode", on_done, mode=mode)

    def stop_task(self, task_id: str, on_done: Callable[[bool, dict, str], None]) -> bool:
        """Stop one background task, keeping the turn (#468). Checked with
        Claude Code 2.1.296: the task is killed, task_notification says
        "stopped" and the background list drops it, all before the
        (empty) success. An id it doesn't know is answered with success too,
        so the caller checks the background list, not the answer."""
        if not task_id:
            return False
        return self.control("stop_task", on_done, task_id=task_id)

    def _close_after_context_locked(self) -> None:
        """Close stdin, ending the turn; first, if asked, ask what fills the
        context (#468), waiting at most CONTEXT_WAIT for the answer. Measured
        with Claude Code 2.1.296: 16 to 735 ms. Call with ``_lock`` held.
        Until it closes, anything sent now still goes in and is answered."""
        if not self._want_context or not self._stdin_open:
            self._close_stdin_locked()
            return
        request_id = f"{CONTROL_REQUEST_PREFIX}context-{uuid.uuid4().hex[:8]}"
        if not self._write_raw(control_line(request_id, "get_context_usage")):
            self._close_stdin_locked()
            return

        def answered(ok, response, _error):
            if ok and isinstance(response.get("categories"), list):
                self.context_usage = response
                self._emit(TurnEvent("context", session_id=self.parser.session_id,
                                     data=response))
            self._close_if_done()
        timer = self._timer(CONTEXT_WAIT, lambda: self._control_done(
            request_id, False, {}, "no answer in time"))
        timer.daemon = True
        self._controls[request_id] = (answered, timer)
        self._closing = True
        timer.start()

    def _close_if_done(self) -> None:
        """End the turn, unless the worker has called the close off, something
        sent since is still to be answered, or background work started
        meanwhile. That last leaves ``_closing`` set: the worker, on the same
        background event, waits for the work and says the reply."""
        with self._lock:
            if not self._closing or self._unanswered or self.background_tasks:
                return
            self._closing = False
            self._close_stdin_locked()

    def _call_off_close(self) -> bool:
        """The worker's: stop a close waiting on the context breakdown. True
        if one was waiting."""
        with self._lock:
            waiting, self._closing = self._closing, False
            return waiting

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
        self._flush_sent()

    def _result_came(self) -> bool:
        """Count a result against the oldest unanswered message. True if a
        later message is still to be answered, so this result was for work
        Send Now replaced."""
        with self._lock:
            # A result answers the messages taken in since the last one. With
            # the echoes that's exact, and a result with nothing taken in (a
            # background agent's notification, say) answers nothing; without
            # them, one. Never below zero.
            if not self._echoes:
                # No echoes: a result answers one message, taken to be the
                # oldest that isn't a steer (a steer joins work; it never
                # has a result of its own), so ``untaken`` doesn't list
                # a Send Now that was answered (#663).
                for index, message in enumerate(self._untaken):
                    if message[0] != "steer":
                        del self._untaken[index]
                        break
            if self._echoes:
                answered = self._taken_since_result
            elif self._cancelled:
                # No echoes, and stopped (#663): whether a steer went in
                # can't be told, so it stays unanswered (its cost not known)
                # and goes back to the reply box rather than being lost.
                answered = 1
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

    def _close_stdin_if_no_work(self) -> None:
        """End the turn, answered or not, unless background work is running."""
        with self._lock:
            if not self.background_tasks:
                self._close_stdin_locked()

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
                # An MCP server's question answered (#468) has an action, not
                # a behavior.
                self.last_activity = (
                    "working" if response.get("behavior") == "allow"
                    or "action" in response
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
        """Stop the turn. On API credits, with requests not yet costed, it's
        interrupted first (#658): checked with Claude Code 2.1.325, an
        interrupted turn sends its result, with ``total_cost_usd``, within
        half a second. That settles the ledger with what was really spent,
        rather than keeping the whole reservation counted; the result's
        handling ends the process, and STOP_COST_WAIT ends it regardless.

        Never blocks (#663): it runs on the UI thread, and a write to a
        Claude Code that isn't reading its input would wait for good, as
        would taking ``_lock`` while another write holds it. So the flag is
        set without the lock, the timer that ends the process starts
        first, and the interrupt is written on a thread of its own; the
        kill is what frees a stuck write."""
        self._cancelled = True
        process = self._process
        graceful = (self.billing == ROUTE_API and not self._cost_known
                    and process is not None and process.poll() is None)
        if not graceful:
            self._kill()
            return
        ender = self._timer(STOP_COST_WAIT, self._kill)
        ender.daemon = True
        ender.start()

        def interrupt() -> None:
            with self._lock:
                ok = self._write_line({
                    "type": "control_request",
                    "request_id": f"thechatplace-interrupt-{uuid.uuid4().hex[:8]}",
                    "request": {"subtype": "interrupt"}})
            if not ok:
                self._kill()  # nothing to wait for
        threading.Thread(target=interrupt, name="claude-stop", daemon=True).start()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def _kill(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                # Killed, it saves no cost state (#663): not a clean end.
                self._killed = True
                self._tree.kill()
                if process.poll() is None:
                    process.kill()
        except Exception:  # noqa: BLE001
            pass

    def _emit(self, event: TurnEvent) -> None:
        if self.billing == ROUTE_API:
            # On API credits, nothing the turn says may carry a key (#663):
            # Claude's own text can quote one (a <synthetic> error, a file
            # it read), not only an error. A permission request's own
            # display (its summary and detail) is scrubbed too, while what
            # it answers with stays as Claude Code sent it.
            scrub_event(event)
        elif event.is_error or event.kind in ("failed", "retry"):
            # Error text can quote Claude Code's stderr or an API error:
            # never a key in the window, a log or a bug report (#658).
            event.text, event.detail = scrub(event.text), scrub(event.detail)
        try:
            self.on_event(event)
        except Exception:  # noqa: BLE001 - a UI bug must not wedge the worker
            pass

    def _api_command(self, command: List[str]) -> List[str]:
        """An API turn's command (#658): the key's helper merged into its one
        ``--settings``, and the budget reserved for it. Raises _Refused, with
        nothing started, if the ledger won't give one."""
        from .billing import api_settings
        from .claude_mcp import merge_settings
        from .spend_ledger import SpendRefused
        command = merge_settings(command, api_settings(self.api_key_name))
        try:
            self.reservation = self.ledger.reserve(self.chat_id, self.purpose)
        except SpendRefused as exc:
            raise _Refused(str(exc)) from None
        return command + budget_flags(self.reservation.amount)

    def _settle(self, event: TurnEvent) -> None:
        """A result on API credits: what it added to the total, and the
        turn's cost so far on the event for the window (#658). Over the
        budget, out of credit or a refused key said in words."""
        from .spend_ledger import valid_cost
        data = event.data or {}
        if not valid_cost(data.get("reported_cost_usd")):
            # Missing, null, NaN, infinite or negative (#663): what this
            # result cost can't be known, so the turn's cost never is, and
            # its reservation stays held rather than being freed.
            self._cost_lost = True
        else:
            added, by_model = self.costs.add(data.get("reported_cost_usd"),
                                             data.get("reported_model_costs"),
                                             data.get("reported_tokens"))
            if self.reservation is not None and (added or by_model):
                self.ledger.settle(self.reservation, added, by_model)
        if event.is_error:
            plain = failure_text(event.raw_type, event.text, self.costs.known_total,
                                 self.reservation.amount if self.reservation else None,
                                 bool(self.reservation and self.reservation.overall_cap),
                                 bool(self.reservation and self.reservation.others_held))
            if plain:
                event.text = plain

    def _note_baseline(self, event: TurnEvent) -> None:
        """After every result, whatever the chat is charged to: what Claude
        Code reported it has cost, the baseline its next process (or a copy
        of it) counts from (#658). Not for a process that saves nothing."""
        if self.baselines is None or "--no-session-persistence" in self.command:
            return
        data = event.data or {}
        if "reported_cost_usd" not in data:
            return
        chat = self.parser.session_id or event.session_id or _session_of(self.command)
        tokens = data.get("reported_tokens")
        reported = (chat, data.get("reported_cost_usd"), data.get("reported_model_costs"),
                    tokens[1] if isinstance(tokens, (tuple, list)) and len(tokens) == 2
                    else None)
        self._last_reported = reported
        try:
            self.baselines.set_baseline(*reported)
        except Exception:  # noqa: BLE001 - a lost baseline is counted up to the cap
            pass

    def _note_clean_end(self, process) -> None:
        """The process ended cleanly (stdin closed, exit code 0, never
        killed): Claude Code saved the chat's cost as its last result said,
        so that's the chat's clean baseline (#663; spend_ledger, "Which
        baseline was restored"). A stopped or killed process saved nothing,
        and leaves the clean one as it was."""
        if (self._last_reported is None or self._killed or process is None
                or process.returncode != 0 or self.baselines is None):
            return
        try:
            self.baselines.set_baseline(*self._last_reported, clean=True)
        except Exception:  # noqa: BLE001 - a lost baseline is counted from the latest
            pass

    @property
    def cost_complete(self) -> bool:
        """Whether every request so far is in what the turn's results said
        it cost (#663)."""
        return self._cost_known and not self._cost_lost

    def _cost_data(self, event: TurnEvent) -> None:
        """The turn's cost and route on an event that says it (#658): its
        last, and "waiting" when Claude answers with background work left
        (#663). ``cost_complete`` False when some of it isn't known (a Stop
        whose result didn't come, a result without a usable cost), and
        ``sent`` whether any message went, so nothing is said of the cost
        of a message that never did."""
        if self.billing != ROUTE_API:
            return
        # Counted up to the reservation because what was restored couldn't
        # be told (#663): the ledger keeps that conservative count, but it's
        # no cost to say, so the event says the cost isn't known.
        guessed = self.costs.guessed
        event.data = {**(event.data or {}), "billing": ROUTE_API,
                      "cost_usd": None if guessed else round(self.costs.total, 6),
                      "cost_complete": self.cost_complete and not guessed,
                      "sent": self.messages_written > 0,
                      "model_costs": {m: round(c, 6) for m, c in self.costs.models.items()},
                      # The reply the cost is kept with, as its last line (#692).
                      "reply_uuid": self.parser.reply_uuid,
                      "reply_message_id": self.parser.reply_message_id}
        if event.is_error and event.kind == "failed":
            plain = failure_text(event.raw_type, event.text, self.costs.known_total,
                                 self.reservation.amount if self.reservation else None,
                                 bool(self.reservation and self.reservation.overall_cap),
                                 bool(self.reservation and self.reservation.others_held))
            if plain:
                event.text = plain

    def _close_reservation(self) -> None:
        """The process is over: free what's left of the reservation if its
        whole cost is known, else keep it counted (spend_ledger)."""
        if self.reservation is None or self.ledger is None:
            return
        try:
            self.ledger.close(self.reservation,
                              complete=self.cost_complete and not self.background_tasks)
        except Exception:  # noqa: BLE001 - left open is counted: the safe way
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
            if self.disabled_mcp:
                # Merged into Chat Options' --settings if there is one: a
                # second --settings would replace the first (#468).
                from .claude_mcp import with_settings
                command = with_settings(command, self.disabled_mcp)
            if self.billing == ROUTE_API:
                # After the servers, so the key's helper is merged last and
                # wins; before --mcp-config, which must stay last.
                command = self._api_command(command)
            # Counted from the chat's baseline, as Claude Code's totals
            # include the cost state it restores (#658).
            self.costs = cost_tracker_for(
                self.command, self.baselines,
                self.reservation.amount if self.reservation is not None else None)
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
                            # Never a key past here (#658).
                            stderr_lines.append(scrub(_decode(raw).rstrip()))
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
                the same while, rather than leave it open for ever. Running
                background work holds it off either way, and the ceiling armed
                when that's done takes over."""
                disarm_ceiling()
                timer = self._timer(wait,
                                    self._close_stdin_if_no_work if unanswered_too
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
            #: True from a tool or text after an answer until the next result:
            #: Claude is carrying on, so nothing starts the ceiling meanwhile.
            carrying_on = False
            #: Stop on API credits ended the process at its interrupt's
            #: result (#663): anything still in the pipe is never read.
            stop_reading = False

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
                    waiting = TurnEvent("waiting", text=final_event.text,
                                        session_id=final_event.session_id,
                                        is_error=final_event.is_error,
                                        denials=list(final_event.denials),
                                        raw_type=final_event.raw_type,
                                        data={"tasks": list(self.background_tasks)})
                    # What the reply cost so far, said with it (#663).
                    self._cost_data(waiting)
                    self._emit(waiting)
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

            def refuse_unanswered() -> None:
                """On API credits, no answer to initialize means no account to
                check, so the message never goes (#658): fail closed."""
                nonlocal message_sent
                with sent_lock:
                    if message_sent:
                        return
                    message_sent = True
                self._init_refused = True
                self.stopped_before_answer = True
                self._kill()

            # A Claude Code that never answers initialize still gets the
            # message on the subscription (the system/init check still
            # stands behind it); on API credits it's stopped instead.
            api = self.billing == ROUTE_API
            unanswered = self._timer(INIT_ANSWER_WAIT,
                                     refuse_unanswered if api else send_message)
            unanswered.daemon = True
            unanswered.start()

            for raw in iter(process.stdout.readline, b""):
                for event in self.parser.feed(_decode(raw)):
                    if event.kind == "initialized" and not message_sent:
                        # How the message would be charged, checked before
                        # it's written (#658), then the model (#58).
                        account = account_problem(self.billing, self.parser.init_body)
                        if account:
                            self._stopped_for_key = True
                        problem = account or fable_problem(
                            resolved_model(self.parser.resolved_models, self.chosen),
                            self.chosen, billing=self.billing)
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
                    if event.kind == "control_answer":
                        # A live control request's answer (#468): to its
                        # caller only, and never a reason to send the message.
                        data = event.data or {}
                        self._control_done(data.get("request_id", ""), bool(data.get("ok")),
                                           data.get("response") or {}, data.get("error", ""))
                        continue
                    if event.kind == "mode":
                        # The mode changed (#468); not the conversation. Only
                        # a real change: a status repeating the mode the turn
                        # is in must not undo a change kept for later turns.
                        if event.text != self._mode:
                            self._mode = event.text
                            self._emit(event)
                        continue
                    if event.kind == "retry" and api and key_refused(event.data):
                        # Anthropic refused the key (#663): Claude Code would
                        # retry for about four minutes before saying so. A
                        # refused request costs nothing, so if no request
                        # went through since the last result, the cost is
                        # all known and the message goes back to you.
                        self._stopped_for_key = True
                        if self.parser.assistant_messages == self._answers_at_result:
                            self._cost_known = True
                            self.stopped_before_answer = True
                        else:
                            self.stopped_mid_turn = True
                        self._kill()
                        final = TurnEvent("failed", text=KEY_REFUSED, is_error=True,
                                          session_id=self.parser.session_id)
                        break
                    if event.kind == "retry":
                        # Claude Code is retrying (#468), for Turn Status too,
                        # which says so itself: what Claude last did stands.
                        # It also sends these as a heartbeat, about every 30
                        # seconds, while a subagent waits on a rate limit
                        # (seen in 2.1.316's code), so with the turn answered
                        # and nothing new under way one doesn't mean the turn
                        # is retrying.
                        if not (self.waiting_on_background
                                or (final is not None and not carrying_on
                                    and not self._unanswered)):
                            self.retry = {**(event.data or {}), "at": self._clock()}
                        self._emit(event)
                        continue
                    if event.kind == "fallback":
                        # A fallback mid-turn gets no new system/init, so the
                        # Fable check (#58) is made here too (#468).
                        problem = fable_problem((event.data or {}).get("fallback_model", ""),
                                                self.chosen, midway=True, billing=self.billing)
                        if problem:
                            self.stopped_mid_turn = True
                            self._kill()
                            final = TurnEvent("failed", text=problem, is_error=True,
                                              session_id=self.parser.session_id)
                            break
                        self._emit(event)
                        continue
                    if event.kind in ("text", "tool"):
                        self.retry = None  # it got through
                        # Whatever wrote this, Fable unchosen is stopped
                        # (#58), however it came to be the model (#468).
                        problem = fable_problem(self.parser.reply_model, self.chosen,
                                                midway=True, billing=self.billing)
                        if problem:
                            self.stopped_mid_turn = True
                            self._kill()
                            final = TurnEvent("failed", text=problem, is_error=True,
                                              session_id=self.parser.session_id)
                            break
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
                    # model check. On API credits only initialize's answer,
                    # checked above, lets it go (#658).
                    if (event.kind not in ("state", "background")
                            and (not api or self.parser.init_body is not None)
                            and send_message()):
                        unanswered.cancel()
                    if event.kind == "initialized":
                        continue
                    if event.kind == "started":
                        self.session_started = True
                        if event.text:
                            self._mode = normalize_permission_mode(event.text)
                        problem = (source_problem(self.billing, self.parser.api_key_source)
                                   or fable_problem(self.parser.model, self.chosen, sent=True,
                                                    billing=self.billing))
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
                        carrying_on = True
                        self._cost_known = False  # more requests, not yet in a result
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
                        if api and event.request is not None:
                            event.request.hide_keys = True  # before it's shown (#663)
                        with self._lock:
                            self.pending[event.request.request_id] = event.request
                        self.last_activity = f"waiting for you: {event.request.summary()}"
                    if event.kind == "background":
                        tasks = list((event.data or {}).get("tasks") or [])
                        since = {e["task_id"]: e.get("since", 0.0)
                                 for e in self.background_entries}
                        now = self._clock()
                        entries = [{**e, "since": since.get(e.get("task_id"), now)}
                                   for e in (event.data or {}).get("entries") or []
                                   if isinstance(e, dict)]
                        with self._lock:
                            had_tasks = bool(self.background_tasks)
                            self.background_tasks = tasks
                            self.background_entries = entries
                            if not tasks:
                                self.waiting_on_background = False
                        if tasks and (self.waiting_on_background
                                      or (final is not None and not carrying_on
                                          and not self._unanswered)):
                            self.last_activity = ("waiting for background work: "
                                                  + "; ".join(tasks))
                        elif (had_tasks and not tasks
                              and self.last_activity.startswith("waiting for background")):
                            self.last_activity = "working"
                        if had_tasks and not tasks and final is not None and last_state == "idle":
                            # All done while Claude was idle: the turn it
                            # starts to hear about it ("running") should
                            # follow at once. If it never does, end the turn,
                            # even with a message seemingly unanswered.
                            if self._unanswered:
                                arm_ceiling(unanswered_too=True)
                            else:
                                arm_ceiling(wait=BACKGROUND_DONE_WAIT)
                        elif (had_tasks and not tasks and final is not None
                              and last_state == "running" and not self._unanswered
                              and not said_while_waiting and not carrying_on):
                            # A background agent is done, with Claude Code
                            # "running" all along. Claude's follow-up should
                            # start now, and its first tool or text calls this
                            # off; if none comes, end the turn. Read without
                            # the lock, as "running" does.
                            arm_ceiling()
                        self._emit(event)
                        if tasks and final is not None and self._call_off_close():
                            # Work started while the end waited for the
                            # context breakdown (#468): wait for it instead,
                            # saying the reply, as ``_end_or_wait`` would.
                            outcome = self._end_or_wait()
                            if outcome != "unanswered":
                                settle(outcome, final)
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
                            # Claude is at work again, on requests no result
                            # has counted yet (#658).
                            self._cost_known = False
                            with self._lock:
                                self.waiting_on_background = False
                                self._answered = False
                                # Back at work: the next idle ends the turn.
                                self._closing = False
                            # Read without the lock on purpose: a Send Now
                            # landing just after only meets the ceiling that
                            # checks again before closing.
                            if self._unanswered or said_while_waiting or carrying_on:
                                # On the Send Now message, on what the
                                # background work brought back, or on Claude's
                                # own follow-up after a permission answer.
                                disarm_ceiling()
                            else:
                                arm_ceiling()
                        continue
                    if event.kind == "finished":
                        self.retry = None
                        follows_waiting = said_while_waiting
                        said_while_waiting = False
                        carrying_on = False
                        # The latest result is the turn's, carrying every
                        # refusal and error of the turn. With state events,
                        # more can follow (a background agent's notification
                        # runs as its own result), so the turn waits for idle;
                        # an older Claude Code without them ends here.
                        replaced = self._result_came()
                        self._answers_at_result = self.parser.assistant_messages
                        if api:
                            self._settle(event)
                        self._note_baseline(event)  # every turn's, every result (#658)
                        if not self._unanswered:
                            # Every message so far has its result, and so
                            # its cost (#658).
                            self._cost_known = True
                        if api and self._cancelled:
                            # Stop on API credits: the interrupt's result says
                            # what the turn cost, so now it ends (#658), at
                            # once. A message still unanswered (a Send Now or
                            # a steer not yet taken in, #663) isn't waited
                            # for: Claude Code would take it in and start a
                            # new paid request after Stop. Its cost is then
                            # not known (``_cost_known`` stays False), so the
                            # reservation stays held, and a steer not taken in
                            # goes back to the reply box (``steering``).
                            # Nothing it wrote after is read: it's over.
                            self._kill()
                            stop_reading = True
                            # An echo still in the pipe is never read, so a
                            # message written may have gone in (#663 review 5).
                            self._read_cut_short = True
                            break
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
                        if not replaced and states_seen and not idle:
                            if self.background_tasks:
                                # Answered with a background agent going: Claude
                                # Code stays "running" until it's done, and the
                                # ceiling won't end the turn meanwhile.
                                self.last_activity = (
                                    "waiting for background work: "
                                    + "; ".join(self.background_tasks))
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
                if self.stopped_before_answer or self.stopped_mid_turn or stop_reading:
                    break
            disarm_ceiling()
            self._close_stdin()
            process.wait()
            err_thread.join(timeout=2)
            self._note_clean_end(process)
            if (final is not None and not final.is_error and not said_while_waiting
                    and not self._cancelled and process.returncode
                    and self.background_tasks):
                # Claude Code died with an agent going: the answer before it
                # isn't how the turn ended.
                detail = next((x for x in reversed(stderr_lines) if x.strip()), "")
                final = TurnEvent("failed", text=exit_message(process.returncode, detail),
                                  is_error=True, session_id=self.parser.session_id)
            if (said_while_waiting and final is not None and not self.stopped_mid_turn
                    and not self._stopped_for_key):
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
            if self._cancelled and final is not None and final.is_error \
                    and final.raw_type == "error_during_execution":
                # The result Stop's interrupt brought (#658): the turn was
                # stopped, not failed.
                final = None
            if self._init_refused and not self._cancelled:
                final = TurnEvent("failed", text=INIT_UNANSWERED_API, is_error=True,
                                  session_id=self.parser.session_id)
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
        except _Refused as exc:
            # The ledger wouldn't give an API turn a budget (#658): nothing
            # started, and the message goes back to the reply box.
            self.stopped_before_answer = True
            final = TurnEvent("failed", text=str(exc), is_error=True)
        except FileNotFoundError as exc:
            final = TurnEvent("failed", text=str(exc) or "The claude command was not found.",
                              is_error=True)
        except Exception as exc:  # noqa: BLE001
            final = TurnEvent("failed", text=f"Couldn't run claude: {exc}", is_error=True)
        finally:
            self._tree.close()
            with self._lock:
                self.pending.clear()
            self._fail_controls()  # before the final event: their callers hear first
            self._close_reservation()
        if not final.session_id:
            final.session_id = self.parser.session_id
        self._cost_data(final)
        self._emit(final)

    def _refuse_unsupported(self) -> None:
        """Control requests other than permission checks and MCP servers'
        questions (hooks, MCP messages): The Chat Place registers none, so say
        so at once rather than leave the CLI waiting."""
        while self.parser.unsupported_requests:
            request_id = self.parser.unsupported_requests.pop(0)
            with self._lock:
                self._write_line({"type": "control_response", "response": {
                    "subtype": "error", "request_id": request_id,
                    "error": "The Chat Place doesn't support this request."}})


class _Cancelled(Exception):
    pass


class _Refused(Exception):
    """An API turn the spend ledger wouldn't start (#658)."""


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
    running a turn (see fetch_initialize). [] if they can't be had."""
    return usable_commands((fetch_initialize(executable, cwd, timeout, popen) or {})
                           .get("commands"))


def fetch_chat_choices(executable: str, cwd: str, timeout: float = 20.0,
                       popen: Callable[..., subprocess.Popen] = subprocess.Popen
                       ) -> Optional[ChatChoices]:
    """The agents and output styles Claude Code offers in ``cwd`` (#468),
    asked for as the commands are, or None."""
    return choices_from_init(fetch_initialize(executable, cwd, timeout, popen))


def fetch_initialize(executable: str, cwd: str, timeout: float = 20.0,
                     popen: Callable[..., subprocess.Popen] = subprocess.Popen
                     ) -> Optional[dict]:
    """Claude Code's answer to ``initialize`` in ``cwd``: its slash commands
    and skills (#23), and its agents and output styles (#468). Without
    running a turn: ``claude -p`` is sent only the ``initialize`` request,
    and stdin is closed once it answers, so no session is made and nothing
    is billed (checked with Claude Code 2.1.286: 1.4 seconds, no transcript).
    None if it can't be had."""
    if not os.path.isdir(cwd):
        return None
    command = [executable, "-p", "--input-format", "stream-json", "--output-format",
               "stream-json", "--verbose", "--permission-prompts", "none"]
    try:
        process = popen(command, cwd=cwd, env=child_environment(), stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        creationflags=platform_paths.hidden_window_flags())
    except OSError:
        return None
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
            if parser.init_body is not None:
                break
        return parser.init_body
    except (OSError, ValueError):
        return None
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
