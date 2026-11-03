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
  puts back every file Claude Code has a checkpoint for in that chat, not only
  those edited since the message, from the checkpoints it keeps when
  ``claude_cli.CHECKPOINT_VAR`` is on (The Chat Place's own turns, from this
  release). Measured with 2.1.316: a file Claude edited in turn 1 and you
  edited by hand afterwards went back to Claude's turn 1 contents on rewinding
  to turn 3, even when nothing changed after turn 1; a file created since the
  message was deleted; a file Claude never touched was left alone. So What
  Happens has to warn about all of that. It opens the session, so only The
  Chat Place's own chats can do it.

This module is the part without wx: where to cut, what each choice will do in
words, and running the files rewind and saying how it went.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
from dataclasses import dataclass, field, replace
from typing import Callable, Collection, Iterable, List, Optional

from . import platform_paths
from .changes import FileChanges, FileEdit, by_file
from .claude_cli import (CHECKPOINT_VAR, ResumeRefused, _decode, build_rewind_files_command,
                         child_environment)
from .sessions import NEEDS_YOU, WORKING
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

#: What putting the files back does, whatever the message (see the top).
FILES_WARNING = (
    "Every file Claude changed with its edit tools anywhere in this chat goes back to the last "
    "version Claude Code saved before your message, which is usually Claude's own. Anything you "
    "edited by hand since Claude last changed that file is lost, even edits from before this "
    "message. Files Claude created since your message are removed, and files Claude never "
    "touched stay as they are. This can't be undone.")
_COMMANDS_NOTE = "Changes made by commands it ran (a build, a script) are never put back."
#: Said whenever a restore didn't finish cleanly: it may have got part way.
MAY_BE_PARTLY_DONE = "Some files may already have gone back; check them before carrying on."


def turn_in_progress(state) -> bool:
    """Whether a Codex or Copilot chat's state has a turn under way. One waiting
    on an approval says Needs You, not Working, and edits files once it's
    answered, during a restore if that had started. One whose connection is in
    doubt says neither, but its turn may still be running, so outcome counts too."""
    return (getattr(state, "state", "") in (WORKING, NEEDS_YOU)
            or getattr(state, "outcome", "") == "inProgress")


def _file_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _files(edits: Iterable[FileEdit]) -> List[FileChanges]:
    """Each file once, whoever changed it, in the order first changed."""
    return by_file([replace(edit, by="") for edit in edits])


def _names(files: List[FileChanges]) -> str:
    """"a.py, b.py, and 4 more". Two files called the same keep their folder,
    so they don't read as one file said twice."""
    parts = [re.split(r"[\\/]", f.path.rstrip("\\/")) for f in files]
    bases = [part[-1] or f.path for part, f in zip(parts, files)]
    names = [("/".join(part[-2:]) if bases.count(base) > 1 else base)
             for part, base in zip(parts, bases)]
    more = f", and {len(names) - NAMED_FILES} more" if len(names) > NAMED_FILES else ""
    return ", ".join(names[:NAMED_FILES]) + more


def _count(files: list, one: str, many: str) -> str:
    return f"{len(files)} {one if len(files) == 1 else many}"


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
    #: Files it changed before this message, in turns with checkpoints:
    #: --rewind-files puts those back too (see the top of this module).
    earlier_edits: List[FileEdit] = field(default_factory=list)
    #: Files its subagents changed from this message on (#468). Claude Code
    #: doesn't put these back: checked with 2.1.296, a file a subagent
    #: wrote stayed as it left it after --rewind-files. One Claude changed
    #: too has a checkpoint, so the subagent's changes in it do go back.
    subagent_edits: List[FileEdit] = field(default_factory=list)
    #: False until the window's first read of the subagents has finished:
    #: ``subagent_edits`` can't be trusted to be all of them yet.
    subagents_known: bool = True
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
        """The conversation alone wherever it can go back. It changes nothing,
        and Rewind is the default button: Enter on files or both would start
        what the dialog says can't be undone. Files take an arrow press."""
        if self.conversation_possible or not self.files_possible:
            return CONVERSATION
        return FILES

    def allowed(self, choice: str) -> bool:
        if choice == BOTH:
            return self.files_possible and self.conversation_possible
        if choice == FILES:
            return self.files_possible
        return self.conversation_possible

    @property
    def file_count(self) -> int:
        """How many files putting the files back can change."""
        return len(_files(self.edits + self.earlier_edits))

    def changed_files_text(self) -> str:
        """The files that go back, each named once, by what happens to it."""
        since = _files(self.edits)
        earlier = _files(self.earlier_edits)
        had_before = {_file_key(f.path) for f in earlier}
        created = [f for f in since if f.created and _file_key(f.path) not in had_before]
        changed = [f for f in since if f not in created]
        seen_since = {_file_key(f.path) for f in since}
        earlier = [f for f in earlier if _file_key(f.path) not in seen_since]
        if not since and not earlier:
            return ("Claude hasn't changed any files with its edit tools in this chat, so none "
                    f"go back. {_COMMANDS_NOTE}")
        lines = []
        if changed:
            lines.append(f"Claude changed {_count(changed, 'file', 'files')} since this message: "
                         f"{_names(changed)}.")
        if created:
            removed = "which is removed" if len(created) == 1 else "which are removed"
            lines.append(f"Claude created {_count(created, 'file', 'files')} since this message, "
                         f"{removed}: {_names(created)}.")
        if earlier:
            also = "which goes back too" if len(earlier) == 1 else "which go back too"
            lines.append(f"Claude changed {_count(earlier, 'file', 'files')} earlier in this "
                         f"chat, {also}: {_names(earlier)}.")
        if not since:
            lines.append("Its edit tools have changed nothing since this message, so this only "
                         "undoes changes made some other way, such as your own edits.")
        lines.append(_COMMANDS_NOTE)
        return " ".join(lines)

    def subagent_files_text(self) -> str:
        """What happens to the files subagents changed, each named once, or
        "". One Claude changed too has a checkpoint and goes back, so it's
        never also said to stay."""
        if not self.subagents_known:
            return ("Which files subagents changed isn't known yet, as their conversations are "
                    "still being read. A file only a subagent changed stays as it is, since "
                    "Claude Code doesn't put those back.")
        files = _files(self.subagent_edits)
        claudes = {_file_key(e.path) for e in self.edits + self.earlier_edits}
        stay = [f for f in files if _file_key(f.path) not in claudes]
        also = [f for f in files if _file_key(f.path) in claudes]
        said = []
        if stay:
            what = ("1 file changed by a subagent stays as it is" if len(stay) == 1
                    else f"{len(stay)} files changed by subagents stay as they are")
            said.append(f"{what}, since Claude Code doesn't put those back: {_names(stay)}.")
        if also:
            which = "that file" if len(also) == 1 else "those files"
            said.append(f"Subagents' changes in {_names(also)} go back too, as Claude changed "
                        f"{which} as well.")
        return " ".join(said)

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
                if self.file_count:
                    parts.append(FILES_WARNING)
                parts.append(self.changed_files_text())
                if self.subagent_files_text():
                    parts.append(self.subagent_files_text())
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
                conversation_reason: str = "",
                subagent_edits: Iterable[FileEdit] = (),
                subagents_known: bool = True) -> RewindPlan:
    """What Rewind to Here can do for ``message``. ``can_rewind_files``:
    the chat is one of The Chat Place's own and no turn is running in it
    (``files_reason`` says why not); ``conversation_reason`` says why the
    conversation can't be branched ("" when it can). ``subagents_known``:
    the window has finished reading the subagents once."""
    edits = [e for e in transcript.edits if e.turn >= message.turn > 0]
    # Earlier edits go back too, but only from the first turn with a
    # checkpoint: Claude Code has no copy of a file before that.
    first_kept = min((m.turn for m in transcript.messages if m.uuid in transcript.checkpoints),
                     default=message.turn)
    earlier = [e for e in transcript.edits if first_kept <= e.turn < message.turn]
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
                      edits=edits, earlier_edits=earlier, files_problem=files_problem,
                      subagent_edits=[e for e in subagent_edits if e.turn >= message.turn > 0],
                      subagents_known=subagents_known,
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
        return RewindResult(True, f"Files in {chat_title} restored to the versions saved before "
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
    return RewindResult(False, f"Claude Code couldn't restore the files: {reason}. "
                               f"{MAY_BE_PARTLY_DONE}")


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
        return RewindResult(False, f"Claude Code couldn't restore the files: {exc}. "
                                   f"{MAY_BE_PARTLY_DONE}")
    finally:
        timer.cancel()
        tree.close()
    if timed_out:
        return RewindResult(False, "Claude Code couldn't restore the files: it took too long "
                                   f"and was stopped. {MAY_BE_PARTLY_DONE}")
    return result_from_output(process.returncode, _decode(out or b""), _decode(err or b""),
                              chat_title, message, file_count)
