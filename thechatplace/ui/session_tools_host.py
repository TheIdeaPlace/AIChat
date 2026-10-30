"""The window's side of the session tools (#165): starting the connection
the MCP server asks over, the ``--mcp-config`` each Claude turn gets, and
answering each tool call from the window's own list. See
``thechatplace.session_tools`` for the design.
"""
from __future__ import annotations

import threading
from copy import deepcopy
from typing import Callable, Optional

import wx

from .. import session_tools, updater
from ..sessions import WORKING, SessionInfo


class SessionToolsHost:
    """Mixed into MainFrame."""

    #: The running ``session_tools.Host``; None until the first Claude turn,
    #: False if it couldn't start (turns then run without the tools).
    _session_tools = None
    #: Session ids Claude replaced with its own at a turn's start (old: new):
    #: that turn's server still says the old one (#165).
    _session_tools_renamed: dict = {}

    def _session_tools_config(self, session_id: str) -> Optional[str]:
        """The ``--mcp-config`` value for a turn in ``session_id``, starting
        the connection the first time. None if the tools can't be offered:
        the turn runs as before, without them."""
        if self._session_tools is None:
            host = session_tools.Host(self._session_tools_answer)
            try:
                host.start()
            except Exception as exc:  # noqa: BLE001 - the turn must still run
                updater.logger.error("session tools couldn't start: %s", exc)
                self._session_tools = False
                return None
            self._session_tools = host
        if not self._session_tools:
            return None
        try:
            return session_tools.mcp_config(session_id)
        except Exception as exc:  # noqa: BLE001
            updater.logger.error("session tools config failed: %s", exc)
            return None

    def _close_session_tools(self) -> None:
        host, self._session_tools = self._session_tools, False
        if host:
            host.close()

    # -- answering: called on the connection's thread ---------------------------

    def _session_tools_answer(self, request: dict) -> dict:
        return session_tools.answer_request(
            request,
            gather=lambda self_id: self._on_window_thread(
                lambda: self._session_tools_view(self_id)),
            hide=lambda info: self._on_window_thread(lambda: self._session_tools_hide(info)))

    def _on_window_thread(self, work: Callable, timeout: float = session_tools.CALL_TIMEOUT):
        """``work()`` run on the window's thread, and what it returned. The
        window's lists and stores are only ever touched there."""
        done = threading.Event()
        lock = threading.Lock()
        box = {}

        def run():
            with lock:
                if box.get("abandoned"):
                    return  # the caller gave up: doing it now would go unreported
                box["started"] = True
            try:
                if self._gone() or self._closing:
                    raise RuntimeError("The Chat Place is closing.")
                box["value"] = work()
            except Exception as exc:  # noqa: BLE001 - handed to the caller
                box["error"] = exc
            finally:
                done.set()

        wx.CallAfter(run)
        if not done.wait(timeout):
            with lock:
                started = box.get("started")
                box["abandoned"] = not started
            if not started or not done.wait(timeout):
                raise TimeoutError("The Chat Place's window didn't answer in time.")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    # -- on the window's thread --------------------------------------------------

    def _session_tools_view(self, self_id: str) -> session_tools.View:
        """The list as the window has it now, a copy the tool can read on
        another thread."""
        sessions = deepcopy(list(self._snapshot.sessions))
        current = None
        self_id = self._session_tools_renamed.get(self_id, self_id)
        if self_id:
            current = next((info for info in sessions
                            if info.is_own and info.provider == "claude"
                            and info.cli_session_id == self_id), None)
            if current is None:
                # A new session the list hasn't caught up with, or a member
                # of an agent conversation, which the list shows as the
                # conversation (#248).
                own = self.store.get(self_id)
                if own is not None:
                    current = own.to_info()
        running = {f"own:{session_id}" for session_id in self._runners}
        return session_tools.View(sessions=sessions, hidden=set(self.hidden.keys()),
                                  running=running, current=current,
                                  read_history=self.sessions.read,
                                  cancelled=lambda: self._closing or self._gone())

    def _session_tools_hide(self, info: SessionInfo) -> Optional[str]:
        """File, Hide Session for ``info``, asked for by a tool: why it
        wasn't hidden, or None once it is. Unlike File, Hide Session it
        happens in the background, so it never moves you: a chat you have
        open is left alone, and the list stays on the session you're on."""
        listed = self._current_info(info.key)
        if (info.is_own and info.cli_session_id in self._runners) or (
                listed is not None and listed.state == WORKING):
            return "a turn is running in it."
        if self._open is not None and self._open.key == info.key:
            return ("it's open in The Chat Place's window. Hide it there with File, Hide "
                    "Session once you're done with it.")
        if info.key in self.hidden:
            return None
        try:
            self.hidden.hide(info.key)
        except OSError as exc:
            return f"The Chat Place couldn't save your hidden sessions: {exc}"
        if info.key in self._list_keys:
            index = self.session_list.GetSelection()
            selected = (self._list_keys[index]
                        if 0 <= index < len(self._list_keys) else None)
            gone = self._list_keys.index(info.key)
            self.session_list.Delete(gone)
            del self._list_keys[gone]
            if self._list_keys:
                if selected is not None and selected != info.key:
                    self.session_list.SetSelection(self._list_keys.index(selected))
                elif selected is not None:
                    self.session_list.SetSelection(min(gone, len(self._list_keys) - 1))
            self._update_list_label(len(self._list_keys))
        self.refresh_sessions()
        self._status(f"Hid {info.title}, archived by a Claude session. File, Bring Back "
                     "Session returns it.")
        return None
