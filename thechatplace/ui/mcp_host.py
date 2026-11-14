"""What Tools, Connected Tools does for a Claude chat (#468).

``ConnectedToolsDialog`` shows; this does the work. Claude Code is asked in an
idle ``claude -p`` in the chat's folder (``claude_mcp.McpSession``), on the
window's worker threads, and every answer comes back on the UI thread. The
process stays while the dialog is open, because a server's own sign-in
(OAuth) waits on it for the browser to come back, and ends when it closes.

Turning a server off is kept with the chat (``OwnSession.disabled_mcp``) and
given to each later turn, so only The Chat Place's own chats can do it. The
idle process gets the same list, so the tools a chat turns off aren't started
just to show the list, and the chat's session tools, as its turns have them.
"""
from __future__ import annotations

import threading

import wx

from .. import claude_mcp, platform_paths
from ..claude_mcp import McpError, McpServer, with_chat_settings

NOTE = ("Connected tools are the MCP servers and claude.ai connectors Claude can use in this "
        "chat's folder: from your Claude Code settings, the project, plugins and claude.ai. "
        "You can't add one here: add or remove them in Claude Code or on claude.ai. Turning one off for this chat keeps "
        "it out of this chat's turns from the next one; Claude Code's own settings and other "
        "chats are left alone. Turning one off for new chats keeps it out of every chat you "
        "start from now on, and The Chat Place stops telling you Claude can't use it; chats you "
        "already have keep their own setting. Reading this list starts the tools that are on; a "
        "tool turned off for this chat isn't started.")

NOTE_READ_ONLY = ("Connected tools are the MCP servers and claude.ai connectors Claude can use "
                  "in this chat's folder. This chat isn't one The Chat Place started, so tools "
                  "can't be turned off for it here. Turning one off for new chats keeps it out "
                  "of every chat you start from now on, and The Chat Place stops telling you "
                  "Claude can't use it.")


class ClaudeConnectedTools:
    """The host ``ConnectedToolsDialog`` works through: ``load``,
    ``sign_in``, ``reconnect`` and ``set_off`` each call ``done`` on the UI
    thread once the work is finished."""

    #: Shows Turn Off for New Chats, in any Claude chat: it's about the chats
    #: to come, so it doesn't matter who started this one.
    can_set_new_chats = True

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
        #: Turned on for new chats while this list is open, and not in this
        #: folder: kept in the list until it's read again.
        self._turned_back_on = []
        #: What the idle process was started without.
        self._denied = []
        # Worked out here, on the UI thread, as a turn's is. Only a chat that
        # has them lists them: a desktop chat's turns aren't run here.
        self._mcp_config = (frame._session_tools_config(self.record.cli_session_id)
                            if self.record is not None else None)

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
            if self._session is None or self._session.closed:
                # First use, or the last one ended (MAX_LIFETIME, or it died).
                lookup = platform_paths.find_claude()
                if not lookup.path:
                    raise McpError(lookup.problem or "Claude Code isn't installed.")
                if self._session is not None:
                    # One that died by itself still holds its job: any server
                    # it started is ended by this.
                    self._session.close()
                self._denied = self._disabled()
                self._session = claude_mcp.open_session(lookup.path, self.cwd,
                                                        disabled=self._denied,
                                                        mcp_config=self._mcp_config)
                self._session.on_expired = self._on_expired
            return self._session

    def _on_expired(self, names):
        """A server's sign-in was still waiting when its process ended (on
        the timer's thread)."""
        wx.CallAfter(self._say_expired, names)

    def _say_expired(self, names):
        if not self._closed and not self.frame._gone():
            self.frame._feedback(claude_mcp.expired_text(names))

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
            # Closed meanwhile, the answer is for nobody: above all, a sign-in
            # must not open a browser after the dialog that asked has gone.
            if not self._closed and not self.frame._gone():
                done(result, error)
        # A thread of its own, not the window's small pool: a load can wait
        # 15 seconds for connectors and a reconnect a minute, and the pool
        # reads the session list.
        threading.Thread(target=work, name="connected-tools", daemon=True).start()

    def _rows(self):
        return with_chat_settings([McpServer(**{**s.__dict__}) for s in self._servers],
                                  self._disabled(), self.frame.speech.mcp_off_for_new_chats,
                                  self._turned_back_on)

    def load(self, done):
        self._turned_back_on = []

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
            try:
                platform_paths.open_url(answer.url)
            except (OSError, ValueError) as exc:
                # ValueError: os.startfile on a character it can't take.
                done(None, f"Couldn't open your browser ({exc}). The page is {answer.url}")
                return
            if answer.callback_expected:
                done(f"Your browser opened the sign-in page for {server.label}. Sign in there, "
                     "then come back and press Refresh. Keep this dialog open until you've "
                     "finished: Claude Code waits up to "
                     f"{claude_mcp.lifetime_minutes()} minutes for the browser to come back "
                     "to it.", "")
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
        if not off and server.name in self._denied:
            # This process was started without it, so it can say nothing about
            # it: a new one, which starts it, says how it's doing.
            self._end_session()
            self.load(done)
            return
        done(self._rows(), "")

    def set_off_for_new(self, server: McpServer, off: bool, done):
        """Keep ``server`` out of every chat started from now on, and stop
        warning about it, or undo that. Saved by The Chat Place alone: Claude
        Code's settings, the desktop app and the terminal are left as they
        are, and so are chats that already exist."""
        speech = self.frame.speech
        before = list(speech.mcp_off_for_new_chats)
        speech.mcp_off_for_new_chats = [n for n in before if n != server.name] \
            + ([server.name] if off else [])
        try:
            speech.save()
        except OSError:
            speech.mcp_off_for_new_chats = before
            done(None, "It couldn't be saved, so nothing changed.")
            return
        self._turned_back_on = [n for n in self._turned_back_on if n != server.name]
        if not off and server.not_in_folder:
            self._turned_back_on.append(server.name)
        self.frame._feedback(
            f"{server.label} is off for new chats. You won't hear about it again." if off
            else f"{server.label} is on for new chats.")
        done(self._rows(), "")

    def _end_session(self):
        """End the idle Claude Code (on a worker: it may take a moment)."""
        with self._lock:
            session, self._session = self._session, None
        if session is not None:
            threading.Thread(target=session.close, daemon=True).start()

    def close(self):
        with self._lock:
            self._closed = True
        self._end_session()
