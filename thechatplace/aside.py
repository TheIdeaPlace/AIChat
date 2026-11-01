"""Ask Aside (#468): a quick question about a Claude chat, answered without
the question or its answer joining the chat, as Claude Code's ``/btw`` does.

How it's asked
--------------
A read-only copy of the conversation that is never saved::

    claude -p --resume <chat> --fork-session --no-session-persistence
              --tools Read,Grep,Glob --strict-mcp-config
              --permission-mode manual --permission-prompts none

Checked with Claude Code 2.1.296 and real Haiku runs:

* The source transcript was byte for byte the same afterwards (and kept its
  modification time), and no transcript appeared for the copy anywhere in
  ``~/.claude/projects``. That held for a Chat Place chat, a desktop app
  session and a terminal session. So, like Continue Here (which also forks),
  it is allowed for sessions The Chat Place doesn't own; a plain ``--resume``
  never is (``claude_cli.check_resume_allowed``).
* It answered from the history ("the code word is ZEPHYR-42") and could read
  a file in the folder to answer.
* It worked while the source chat had a turn running: it answers from what
  the transcript holds so far.
* ``--settings {"disableAllHooks": true}`` keeps your and the project's hooks
  from running: without it a project's Stop and UserPromptSubmit hooks ran
  (and wrote their file) on every aside.
* ``--tools Read,Grep,Glob`` leaves only those tools; ``--strict-mcp-config``
  with no ``--mcp-config`` leaves out MCP servers and claude.ai connectors,
  which would otherwise load (and some of which can change things). Nothing
  asks: ``--permission-prompts none`` refuses anything that would.

Claude Code's own ``side_question`` control request needs a turn's process
running, and every turn here is its own process, so it would only work
during a turn; the copy works whenever.

Like any message, it uses your plan. The Fable and API-key checks of a turn
apply here too: the question is sent only once ``initialize`` says which
model will answer, and the run is stopped if ``system/init`` reports an API
key or an unchosen Fable.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Collection, Dict, List, Optional

from . import platform_paths
from .claude_cli import (INIT_ANSWER_WAIT, ResumeRefused, StreamParser, api_key_problem,
                         child_environment, exit_message, fable_problem, initialize_line,
                         is_safe_model, message_line, resolved_model, _decode)

#: The tools an aside may use: it can look at files, never change anything.
ASIDE_TOOLS = "Read,Grep,Glob"

#: Settings for the aside's process only: hooks off (see build_aside_command).
ASIDE_SETTINGS = '{"disableAllHooks": true}'

#: Said when an aside is refused for a Cowork session.
COWORK_REFUSED = ("Ask Aside isn't available for Cowork sessions: their conversation is in "
                  "the desktop app's own files. Open the session in Claude to ask there.")


def build_aside_command(executable: str, source_id: str, model: str = "",
                        cowork_ids: Collection[str] = ()) -> List[str]:
    """The command that asks a side question about session ``source_id``.
    Never a plain ``--resume``: always a fork that isn't saved."""
    if not platform_paths.is_safe_id(source_id) or source_id.startswith("local_"):
        raise ResumeRefused("That is not a session id Claude Code can read from.")
    if source_id in cowork_ids:
        raise ResumeRefused(COWORK_REFUSED)
    command = [executable, "-p", "--input-format", "stream-json", "--output-format",
               "stream-json", "--verbose", "--include-partial-messages",
               "--permission-mode", "manual", "--permission-prompts", "none",
               "--tools", ASIDE_TOOLS, "--strict-mcp-config",
               # No hooks: a project's Stop or UserPromptSubmit hook ran on
               # every aside (checked: it wrote its file), and could change
               # things in a folder that's read-only here.
               "--settings", ASIDE_SETTINGS]
    if model:
        if not is_safe_model(model):
            raise ValueError(f"Not a valid model name: {model!r}")
        command += ["--model", model]
    return command + ["--resume", source_id, "--fork-session", "--no-session-persistence"]


@dataclass
class AsideEvent:
    """``kind``: "text" (the answer so far, while it streams), "done" (the
    whole answer) or "failed" (why, in words)."""

    kind: str
    text: str = ""


_MISSING = ("Claude Code couldn't find this chat's conversation on disk, so there's nothing "
            "to ask about yet.")


def _missing_or(text: str) -> str:
    """Claude Code's "No conversation found with session ID: …" in words."""
    return _MISSING if "No conversation found" in (text or "") else text


def _plain_problem(returncode, detail: str) -> str:
    """Why an aside ended without an answer, in words."""
    if "No conversation found" in (detail or ""):
        return _MISSING
    return exit_message(returncode, detail).replace("the turn", "the question")


class AsideRunner:
    """Asks one side question in a background thread. ``on_event`` is called
    from that thread (the window marshals it with ``wx.CallAfter``); exactly
    one "done" or "failed" comes last, whatever happens. ``cancel`` ends the
    process and everything it started."""

    def __init__(self, command: List[str], cwd: str, question: str,
                 on_event: Callable[[AsideEvent], None],
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 env: Optional[Dict[str, str]] = None,
                 timer: Callable[..., threading.Timer] = threading.Timer) -> None:
        self.command = list(command)
        self.cwd = cwd
        self.question = question
        self.on_event = on_event
        self._popen = popen
        self._timer = timer
        self._env = dict(env if env is not None else child_environment())
        self._process: Optional[subprocess.Popen] = None
        self._tree = platform_paths.ProcessTree()
        self._lock = threading.Lock()
        self._cancelled = False
        self._sent = False
        self._thread: Optional[threading.Thread] = None
        self.parser = StreamParser()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="claude-aside", daemon=True)
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
        self._kill()

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

    def _emit(self, event: AsideEvent) -> None:
        try:
            self.on_event(event)
        except Exception:  # noqa: BLE001 - a UI bug must not wedge the worker
            pass

    def _write(self, data: bytes) -> bool:
        with self._lock:
            process = self._process
            if process is None:
                return False
            try:
                process.stdin.write(data)
                process.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def _send_question(self) -> bool:
        """Write the question once; False if it already went."""
        with self._lock:
            if self._sent:
                return False
            self._sent = True
        self._write(message_line(self.question))
        return True

    def _close_stdin(self) -> None:
        with self._lock:
            process = self._process
            if process is None:
                return
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

    def _run(self) -> None:
        final: Optional[AsideEvent] = None
        stderr_lines: List[str] = []
        streamed = ""
        try:
            if not os.path.isdir(self.cwd):
                raise FileNotFoundError(f"The folder {self.cwd} doesn't exist.")
            if self._cancelled:
                raise _Cancelled()
            process = self._popen(self.command, cwd=self.cwd, env=self._env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  **platform_paths.ProcessTree.popen_kwargs())
            with self._lock:
                self._process = process
                self._tree.attach(process)
                cancelled_early = self._cancelled
            if cancelled_early:
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
            self._write(initialize_line())
            chosen = _chosen_model(self.command)
            # A Claude Code that never answers initialize still gets the
            # question (the system/init check stands behind it), as a turn does.
            fallback = self._timer(INIT_ANSWER_WAIT, self._send_question)
            fallback.daemon = True
            fallback.start()
            for raw in iter(process.stdout.readline, b""):
                line = _decode(raw)
                streamed = self._stream_text(line, streamed)
                for event in self.parser.feed(line):
                    if event.kind == "initialized":
                        problem = fable_problem(
                            resolved_model(self.parser.resolved_models, chosen), chosen)
                        if problem:
                            final = AsideEvent("failed", problem.replace(
                                "didn't send your message", "didn't ask your question"))
                            fallback.cancel()
                            with self._lock:
                                self._sent = True  # and never
                            self._kill()
                            break
                        if self._send_question():
                            fallback.cancel()
                    elif event.kind == "started":
                        problem = (api_key_problem(self.parser.api_key_source)
                                   or fable_problem(self.parser.model, chosen, sent=True))
                        if problem:
                            final = AsideEvent("failed", problem)
                            self._kill()
                            break
                    elif event.kind == "finished":
                        if event.is_error:
                            final = AsideEvent("failed", _missing_or(
                                event.text or "Claude couldn't answer that."))
                        else:
                            answer = (event.text or streamed).strip()
                            final = AsideEvent("done", answer or "(Claude gave no answer.)")
                        self._close_stdin()
                if final is not None and final.kind == "failed":
                    break
                self._refuse_unsupported()
            fallback.cancel()
            self._close_stdin()
            process.wait()
            err_thread.join(timeout=2)
            if self._cancelled:
                final = AsideEvent("failed", "Stopped.")
            elif final is None:
                detail = next((x for x in reversed(stderr_lines) if x.strip()), "")
                final = AsideEvent("failed", _plain_problem(process.returncode, detail))
        except _Cancelled:
            final = AsideEvent("failed", "Stopped.")
        except FileNotFoundError as exc:
            final = AsideEvent("failed", str(exc) or "The claude command was not found.")
        except Exception as exc:  # noqa: BLE001
            final = AsideEvent("failed", f"Couldn't run claude: {exc}")
        finally:
            self._tree.close()
        self._emit(final)

    def _stream_text(self, line: str, so_far: str) -> str:
        """The answer so far, from Claude Code's partial messages
        (``--include-partial-messages``). A new message (after Claude used a
        tool) starts the text again; the result has the last one in full."""
        if '"stream_event"' not in line:
            return so_far
        try:
            event = json.loads(line)
        except ValueError:
            return so_far
        if not isinstance(event, dict) or event.get("parent_tool_use_id"):
            return so_far
        inner = event.get("event") if isinstance(event.get("event"), dict) else {}
        if inner.get("type") == "message_start":
            return ""
        delta = inner.get("delta") if isinstance(inner.get("delta"), dict) else {}
        if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            text = delta.get("text")
            if isinstance(text, str) and text:
                so_far += text
                self._emit(AsideEvent("text", so_far))
        return so_far

    def _refuse_unsupported(self) -> None:
        """Control requests other than initialize's answer (a hook, an MCP
        message): nothing here answers them, so say so at once."""
        while self.parser.unsupported_requests:
            request_id = self.parser.unsupported_requests.pop(0)
            self._write((json.dumps({"type": "control_response", "response": {
                "subtype": "error", "request_id": request_id,
                "error": "The Chat Place doesn't support this request."}}) + "\n")
                .encode("utf-8"))


def _chosen_model(command: List[str]) -> str:
    try:
        return command[command.index("--model") + 1]
    except (ValueError, IndexError):
        return ""


class _Cancelled(Exception):
    pass


@dataclass
class AsideItem:
    """One question asked in the dialog, and its answer once it comes."""

    question: str
    answer: str = ""
    failed: bool = False
    asking: bool = True

    def row(self, limit: int = 160) -> str:
        """The row the dialog's list shows: question, then the answer's start."""
        question = " ".join(self.question.split())
        if self.asking:
            answer = "asking…"
        else:
            answer = " ".join(self.answer.split()) or "no answer"
            if self.failed:
                answer = "not answered: " + answer
        text = f"Q: {question} A: {answer}"
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    def full(self) -> str:
        """The question and answer in full, for the Answer box (read by line)."""
        if self.asking:
            answer = self.answer or "Asking Claude…"
        elif self.failed:
            answer = "Not answered. " + self.answer
        else:
            answer = self.answer
        return f"Question: {self.question}\n\nAnswer:\n{answer}"
