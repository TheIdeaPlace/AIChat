"""Rewind to Here and Branch from Here (#468): going back in a Claude chat.

Two things can go back, separately or together:

* **The conversation.** Never cut in place: the chat you started from keeps
  its whole history. A new chat of The Chat Place's own is made instead, a
  copy whose history ends where you chose (``--resume <source>
  --resume-session-at <cut> --fork-session``, ``claude_cli.build_fork_command``).
  That's a read of the source, so it works for any Claude chat whose
  transcript is on this computer (desktop and terminal sessions too), as
  Continue Here does. The copy is made unstarted (``OwnSession.started`` is
  False, with ``fork_source`` and ``fork_at``): Claude Code makes it with
  its first turn, when you press Send, and a first turn that never got going
  makes it again the same way.
* **The files.** ``claude -p --resume <id> --rewind-files <message uuid>``
  puts back the files Claude changed with its edit tools since that message,
  from the checkpoints Claude Code keeps when ``claude_cli.CHECKPOINT_VAR`` is
  on (The Chat Place's own turns, from this release). It opens the session,
  so only The Chat Place's own chats can do it.

This module is the part without wx: where to cut, what each choice will do in
words, and running the files rewind and saying how it went.
"""
from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, Collection, List, Optional

from . import platform_paths
from .changes import FileEdit, by_file
from .claude_cli import (CHECKPOINT_VAR, ResumeRefused, _decode, build_rewind_files_command,
                         child_environment)
from .transcript import USER, ChatMessage, Transcript

#: What Rewind to Here can put back.
BOTH, CONVERSATION, FILES = "both", "conversation", "files"
CHOICES = [
    (BOTH, "Conversation and files"),
    (CONVERSATION, "Conversation only"),
    (FILES, "Files only"),
]
CHOICE_LABELS = dict(CHOICES)

#: Long enough for a large set of files on a slow disk; no model call is made.
REWIND_TIMEOUT = 120.0
#: How many changed files are named before "and N more".
NAMED_FILES = 8


def first_line(message: ChatMessage, limit: int = 80) -> str:
    return message.first_line(limit)


@dataclass
class RewindPlan:
    """What Rewind to Here can do for one of your messages in one chat."""
    chat_title: str
    message: ChatMessage
    #: The record the new chat's history ends with: the one before your
    #: message. "" when your message was the first: the new chat then starts
    #: with no history, as a new session in the same folder.
    cut: str
    #: Files Claude changed with its edit tools from this message on.
    edits: List[FileEdit] = field(default_factory=list)
    #: Why the files can't be put back, or "" when they can.
    files_problem: str = ""
    #: Why the conversation can't be branched, or "" when it can.
    conversation_problem: str = ""

    @property
    def files_possible(self) -> bool:
        return not self.files_problem

    @property
    def conversation_possible(self) -> bool:
        return not self.conversation_problem

    def default_choice(self) -> str:
        if self.files_possible and self.conversation_possible:
            return BOTH
        return FILES if self.files_possible else CONVERSATION

    def allowed(self, choice: str) -> bool:
        if choice == BOTH:
            return self.files_possible and self.conversation_possible
        if choice == FILES:
            return self.files_possible
        return self.conversation_possible

    def changed_files_text(self) -> str:
        files = by_file(self.edits)
        if not files:
            return ("Claude hasn't changed any files with its edit tools since this message. "
                    "Changes made by commands it ran (a build, a script) are never put back.")
        names = [os.path.basename(f.path.rstrip("\\/")) or f.path for f in files[:NAMED_FILES]]
        more = f", and {len(files) - NAMED_FILES} more" if len(files) > NAMED_FILES else ""
        count = len(files)
        return (f"Claude changed {count} file{'s' if count != 1 else ''} since this message: "
                f"{', '.join(names)}{more}. Changes made by commands it ran (a build, a "
                "script) are never put back.")

    def describe(self, choice: str) -> str:
        """What ``choice`` will do, in words, for the dialog's read-only box."""
        said = first_line(self.message)
        parts: List[str] = []
        if choice in (BOTH, CONVERSATION):
            if self.conversation_problem:
                parts.append(self.conversation_problem)
            else:
                start = ("with the conversation up to just before it"
                         if self.cut else "with no history, as your message was the first")
                parts.append(
                    f"A new chat, {rewound_title(self.chat_title)}, starts {start}, and your "
                    f"message (\"{said}\") goes in its reply box to change and send. "
                    f"{self.chat_title} is kept as it is, in the list.")
        if choice in (BOTH, FILES):
            if self.files_problem:
                parts.append(self.files_problem)
            else:
                parts.append("The files go back to how they were just before your message. "
                             + self.changed_files_text())
                if choice == FILES:
                    parts.append(f"The conversation in {self.chat_title} isn't changed.")
        elif self.files_problem:
            # Conversation only, perhaps because the files can't go back:
            # say why here too, as it's what the dialog opens on.
            parts.append("The files stay as they are. " + self.files_problem)
        return "\n\n".join(parts)


def rewound_title(title: str) -> str:
    return f"{title} (rewound)"


def branch_title(title: str) -> str:
    return f"{title} (branch)"


def unstarted_line(own) -> str:
    """What the messages list says for a branch not sent to yet, or "" for
    any other chat. Its history is only made by its first turn, so there's
    nothing of its own to show until then."""
    if own is None or own.started or not own.forked_from:
        return ""
    if own.fork_at:
        return (f"No messages yet. This chat is a branch of {own.forked_from}, with its "
                "conversation up to the message you chose. Send a message to start it.")
    if not own.fork_source:
        return (f"No messages yet. This chat goes back to before your first message in "
                f"{own.forked_from}, so it starts with no history. Send a message to start it.")
    return ""


#: What the transcript reader writes for a picture in your message.
_PICTURE = "(image attached)"


def draft_text(message: ChatMessage):
    """Your message as it goes back in the reply box, and how many pictures
    it had: those aren't carried over, so the placeholder lines are left out
    and the caller says to attach them again."""
    lines = message.text.split("\n")
    kept = [line for line in lines if line.strip() != _PICTURE]
    return "\n".join(kept).strip(), len(lines) - len(kept)


def rewindable(message: Optional[ChatMessage]) -> bool:
    """Your own message, from a user record that has a uuid."""
    return message is not None and message.kind == USER and bool(message.uuid)


def plan_rewind(chat_title: str, transcript: Transcript, message: ChatMessage, *,
                can_rewind_files: bool, files_reason: str = "",
                conversation_reason: str = "") -> RewindPlan:
    """What Rewind to Here can do for ``message``. ``can_rewind_files``:
    the chat is one of The Chat Place's own and no turn is running in it
    (``files_reason`` says why not); ``conversation_reason`` says why the
    conversation can't be branched ("" when it can)."""
    edits = [e for e in transcript.edits if e.turn >= message.turn > 0]
    if not can_rewind_files:
        files_problem = files_reason or "The files can't be put back for this chat."
    elif message.uuid not in transcript.checkpoints:
        files_problem = (
            "There are no file checkpoints for this message, so the files can't be put "
            "back. The Chat Place keeps them from this version on; messages sent before "
            "that, or in another app, have none.")
    else:
        files_problem = ""
    return RewindPlan(chat_title=chat_title, message=message, cut=message.parent_uuid,
                      edits=edits, files_problem=files_problem,
                      conversation_problem=conversation_reason)


# ---------------------------------------------------------------------------
# Putting the files back
# ---------------------------------------------------------------------------


_REWOUND = "Files rewound to state at message"


@dataclass
class RewindResult:
    ok: bool
    #: What to say: what happened, or why not, in plain words.
    text: str


def result_from_output(returncode: Optional[int], stdout: str, stderr: str,
                       chat_title: str, message: ChatMessage,
                       file_count: int) -> RewindResult:
    """How a ``--rewind-files`` run went, in words. Claude Code 2.1.296
    prints "Files rewound to state at message <uuid>" and exits 0 on success,
    and on failure exits 1 with "Error: <reason>" (or "No conversation found
    …", or "No file checkpoint found for this message") on stderr, after any settings warnings it prints first."""
    said = first_line(message, 60)
    if returncode == 0 and _REWOUND in stdout:
        files = (f": {file_count} file{'s' if file_count != 1 else ''}" if file_count
                 else "")
        return RewindResult(True, f"Files in {chat_title} restored to how they were before "
                                  f"your message \"{said}\"{files}.")
    reason = _reason(stderr) or _reason(stdout)
    if "not enabled" in reason.lower():
        reason = "file checkpoints weren't kept for this chat"
    elif "no file checkpoint" in reason.lower():
        # Seen with Claude Code 2.1.296 for a message sent without them.
        reason = "there's no file checkpoint for that message"
    elif "not a user message" in reason.lower():
        reason = "it doesn't know that message in this chat"
    elif "no conversation found" in reason.lower():
        reason = "it couldn't find this chat's conversation"
    elif "unknown option" in reason.lower():
        reason = "this version of Claude Code can't rewind files; update it and try again"
    if not reason:
        reason = (f"it stopped with exit code {returncode}" if returncode is not None
                  else "it didn't finish")
    return RewindResult(False, f"Claude Code couldn't restore the files: {reason}.")


def _reason(text: str) -> str:
    """The line that says what went wrong: an "Error:" line, else the last
    line that isn't one of Claude Code's settings warnings."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in lines:
        if line.lower().startswith("error:"):
            return line[len("error:"):].strip().rstrip(".")
    lines = [line for line in lines if not line.startswith(("Permission ", "Warning", "Settings"))]
    return lines[-1].rstrip(".") if lines else ""


def run_rewind_files(executable: str, session_id: str, message: ChatMessage, cwd: str,
                     chat_title: str, file_count: int, own_ids: Collection[str],
                     desktop_ids: Collection[str], *,
                     popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                     timeout: float = REWIND_TIMEOUT,
                     env: Optional[dict] = None) -> RewindResult:
    """Put the files back (blocking: call it from a worker thread). The
    process runs as a turn's does: the stripped environment, in a
    kill-on-close job, its window hidden, killed if it takes too long."""
    try:
        command = build_rewind_files_command(executable, session_id, message.uuid,
                                             own_ids, desktop_ids)
    except (ResumeRefused, ValueError) as exc:
        return RewindResult(False, str(exc))
    if not os.path.isdir(cwd):
        return RewindResult(False, f"The chat's folder ({cwd}) doesn't exist any more, so its "
                                   "files can't be put back.")
    environment = dict(env if env is not None else child_environment())
    environment[CHECKPOINT_VAR] = "1"
    tree = platform_paths.ProcessTree()
    try:
        process = popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        **platform_paths.ProcessTree.popen_kwargs())
    except OSError as exc:
        return RewindResult(False, f"Claude Code couldn't restore the files: couldn't start "
                                   f"claude ({exc}).")
    timed_out = []

    def kill():
        timed_out.append(True)
        try:
            tree.kill()
        finally:
            try:
                process.kill()
            except OSError:
                pass

    timer = threading.Timer(timeout, kill)
    try:
        tree.attach(process)
        timer.start()
        out, err = process.communicate()
    except OSError as exc:
        return RewindResult(False, f"Claude Code couldn't restore the files: {exc}.")
    finally:
        timer.cancel()
        tree.close()
    if timed_out:
        return RewindResult(False, "Claude Code couldn't restore the files: it took too long "
                                   "and was stopped.")
    return result_from_output(process.returncode, _decode(out or b""), _decode(err or b""),
                              chat_title, message, file_count)
