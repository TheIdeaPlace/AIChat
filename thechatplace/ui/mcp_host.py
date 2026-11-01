"""What View, Connected Tools does for a Claude chat (#468).

``ConnectedToolsDialog`` shows; this does the work. Claude Code is asked in an
idle ``claude -p`` in the chat's folder (``claude_mcp.McpSession``), on the
window's worker threads, and every answer comes back on the UI thread. The
process stays while the dialog is open, because a server's own sign-in
(OAuth) waits on it for the browser to come back, and ends when it closes.

Turning a server off is kept with the chat (``OwnSession.disabled_mcp``) and
given to each later turn, so only The Chat Place's own chats can do it.
"""
from __future__ import annotations

import threading

import wx

from .. import claude_mcp, platform_paths
from ..claude_mcp import McpError, McpServer, with_chat_settings

NOTE = ("Connected tools are the MCP servers and claude.ai connectors Claude can use in this "
        "chat's folder: from your Claude Code settings, the project, plugins and claude.ai. "
        "Add or remove them in Claude Code or on claude.ai. Turning one off for this chat keeps "
        "it out of this chat's turns from the next one; Claude Code's own settings and other "
        "chats are left alone.")

NOTE_READ_ONLY = ("Connected tools are the MCP servers and claude.ai connectors Claude can use "
                  "in this chat's folder. This chat isn't one The Chat Place started, so tools "
                  "can't be turned off for it here.")


class ClaudeConnectedTools:
    """The host ``ConnectedToolsDialog`` works through: ``load``,
    ``sign_in``, ``reconnect`` and ``set_off`` each call ``done`` on the UI
    thread once the work is finished."""

    def __init__(self, frame, info):
        self.frame = frame
        self.cwd = info.cwd
        # The record, not its id: a new chat's id can change on its first
        # turn, and the dialog may be open then.
        self.record = frame.store.get(info.cli_session_id) if info.is_own else None
        self.can_turn_off = self.record is not None
        self._session = None
        self._lock = threading.Lock()
        self._closed = False
        self._servers = []

    def note(self) -> str:
        return NOTE if self.can_turn_off else NOTE_READ_ONLY

    def _disabled(self):
        record = self.record
        if record is None or self.frame.store.get(record.cli_session_id) is not record:
            return []
        return list(record.disabled_mcp)

    def _open(self):
        """The idle Claude Code, started on first use (on a worker)."""
        with self._lock:
            if self._closed:
                raise McpError("The dialog has closed.")
            if self._session is None:
                lookup = platform_paths.find_claude()
                if not lookup.path:
                    raise McpError(lookup.problem or "Claude Code isn't installed.")
                self._session = claude_mcp.open_session(lookup.path, self.cwd)
            return self._session

    def _work(self, action, done):
        """Run ``action`` on a worker; ``done(result, error)`` on the UI thread."""
        def work():
            try:
                result, error = action(), ""
            except McpError as exc:
                result, error = None, str(exc)
            except Exception as exc:  # noqa: BLE001 - said, never raised into wx
                result, error = None, f"Something went wrong: {exc}"
            wx.CallAfter(finish, result, error)

        def finish(result, error):
            if not self.frame._gone():
                done(result, error)
        self.frame._pool.submit(work)

    def _rows(self):
        return with_chat_settings([McpServer(**{**s.__dict__}) for s in self._servers],
                                  self._disabled())

    def load(self, done):
        def read():
            self._servers = self._open().status()
            return self._rows()
        self._work(read, done)

    def sign_in(self, server: McpServer, done):
        """Ask Claude Code for the sign-in page and open it; ``done(text,
        error)`` with what to do next."""
        def ask():
            return self._open().sign_in(server.name)

        def opened(answer, error):
            if error:
                done(None, error)
                return
            if not answer.url:
                done(f"{server.label} is signed in already. Refresh shows how it's doing.", "")
                return
            platform_paths.open_url(answer.url)
            if answer.callback_expected:
                done(f"Your browser opened the sign-in page for {server.label}. Sign in there, "
                     "then come back and press Refresh. Keep this dialog open until you've "
                     "finished: Claude Code is waiting for the browser to come back to it.", "")
            else:
                done(f"Your browser opened claude.ai to sign in to {server.label}. Sign in "
                     "there, then come back and press Refresh. The next turn of every chat "
                     "can use it.", "")
        self._work(ask, opened)

    def reconnect(self, server: McpServer, done):
        def again():
            session = self._open()
            session.reconnect(server.name)
            self._servers = session.status()
            return self._rows()
        self._work(again, done)

    def set_off(self, server: McpServer, off: bool, done):
        """Turn ``server`` off (or on again) for this chat, from its next
        turn. Done at once, on the UI thread: it's only the chat's record."""
        record = self.record
        if record is None or self.frame.store.get(record.cli_session_id) is not record:
            done(None, "This chat is no longer in The Chat Place.")
            return
        names = [n for n in record.disabled_mcp if n != server.name]
        if off:
            names.append(server.name)
        if not self.frame._store_write(self.frame.store.update, record.cli_session_id,
                                       disabled_mcp=names):
            done(None, "It couldn't be saved, so nothing changed.")
            return
        running = record.cli_session_id in self.frame._runners
        when = "from the next turn" if running else "from this chat's next turn"
        self.frame._feedback(f"{server.label} is off for this chat {when}." if off
                             else f"{server.label} is on again for this chat {when}.")
        done(self._rows(), "")

    def close(self):
        """End the idle Claude Code (on a worker: it may take a moment)."""
        with self._lock:
            self._closed = True
            session, self._session = self._session, None
        if session is not None:
            try:
                self.frame._pool.submit(session.close)
            except RuntimeError:  # the window is closing and its pool with it
                threading.Thread(target=session.close, daemon=True).start()
