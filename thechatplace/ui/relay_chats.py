"""File, Send Reply to Another Chat (#248): the window's side of relay.py.

A reply in the loaded chat goes, labelled and after you've read it, to
another of The Chat Place's own chats, of either provider, as an ordinary
message. The window then goes to that chat, so its answer is the next thing
heard, and the same key sends that answer back.
"""
import wx

from .. import relay
from ..sessions import WORKING
from .dialogs import RelayDialog


class RelayChats:
    def on_send_reply_to(self):
        info = self._open
        if info is not None and info.provider == "agents":
            self.on_agent_hand_off()  # an agent conversation's hand-off (#248)
            return
        if info is None:
            self._feedback("Load a chat first, then send one of its replies to another chat.")
            return
        if not self._chat_loaded:
            self._feedback(f"{info.title} is still loading.")
            return
        message = relay.reply_to_send(self._chat_messages, self._selected_message())
        if message is None:
            self._feedback(f"{info.title} has no reply to send yet.")
            return
        targets = relay.targets(self._snapshot.sessions, info.key, self._can_edit,
                                self._relay_partners.get(info.key))
        if not targets:
            wx.MessageBox("There's no other chat to send it to. A reply can go to The Chat "
                          "Place's own chats, of any provider; start one with File, New "
                          "Session.", "Send Reply to Another Chat",
                          wx.OK | wx.ICON_INFORMATION, self)
            return
        dialog = RelayDialog(self, info.title, [relay.describe(t) for t in targets],
                             relay.message_text(message, info.title),
                             lambda index, text: relay.too_long(targets[index].provider, text),
                             working=self._relay_source_working(info))
        try:
            if dialog.ShowModal() != wx.ID_OK or dialog.chosen is None:
                return
            index, text = dialog.chosen
        finally:
            dialog.Destroy()
        self._relay(info, targets[index], text)

    def _relay_source_working(self, info) -> bool:
        """Still working, its latest reply may be a step on the way ("Let me
        check the tests"), not its answer: the dialog says so. A chat of The
        Chat Place's own is working only while it has a turn here; the list's
        state lags a few seconds, and would call a just-finished one busy."""
        if info.provider == "claude":
            runner = self._runners.get(info.cli_session_id)
            if runner is not None:
                return not runner.waiting_on_background
        elif info.provider == "codex":
            state = self._codex_states.get(info.key)
            if state is not None:
                return state.outcome == "inProgress" or state.state == WORKING
        if info.is_own:
            return False
        return info.state == WORKING

    def _relay(self, source, target, text):
        """Go to ``target`` and send ``text`` there, as Send would. Text that
        can't go is put in the target's reply box, or on the clipboard when
        there's no reply box for it, never dropped."""
        self._relay_partners[target.key] = source.key
        self.open_session(target)
        if target.provider == "codex":
            record = self.store.get(target.cli_session_id, provider="codex")
            if record is None:
                self._relay_kept(text, f"Couldn't find {target.title} in The Chat Place's chats, "
                                       "so the message wasn't sent.")
                return
            if target.key in self._codex_preparing:
                # Codex must get a chat ready (reading its history first)
                # before it takes a message; _prepare_codex_owned sends it.
                self._relay_waiting[target.key] = text
                self._feedback(f"Getting {target.title} ready. The message goes as soon as it is.")
                return
            self._send_codex_owned(record, text)
            return
        if target.provider != "claude":
            # Only Claude's runner is left, and another provider's ID must
            # never reach it (CLAUDE.md); a new provider adds its own branch.
            self._relay_kept(text, f"The Chat Place can't send to {target.title} yet.")
            return
        if not self._send_claude(target, text, []):
            self._give_back(target.cli_session_id, text, True)
            self._save_draft()
            self._feedback(f"Not sent to {target.title}. The message is in its reply box.")
            self.reply_text.SetFocus()

    def _relay_waiting_text(self) -> str:
        """What quitting or updating now would lose, "" if nothing: "the
        message forwarded to Reviewer"."""
        titles = []
        for key in self._relay_waiting:
            info = self._current_info(key)
            titles.append(info.title if info is not None else "a Codex chat")
        # An agent conversation's message waiting for its Codex member (#248).
        waiting = getattr(self, "_agent_waiting_names", None)
        titles += waiting() if waiting is not None else []
        if not titles:
            return ""
        if len(titles) == 1:
            return f"the message forwarded to {titles[0]}"
        return f"the messages forwarded to {', '.join(titles[:-1])} and {titles[-1]}"

    def _relay_kept(self, text, why):
        """Somewhere to keep a message that has no reply box to go in."""
        if self._copy_text(text):
            self._feedback(f"{why} It's copied to the clipboard.")
        else:
            self._feedback(f"{why} It couldn't be kept.")

    def _relay_prepared(self, key, record, failed) -> str:
        """A Codex chat a message was waiting for is ready, or failed to be.
        Ready, the message goes. Failed, it goes in the chat's reply box, and
        what to say about it comes back to go before the reason."""
        text = self._relay_waiting.pop(key, None)
        if text is None:
            return ""
        if failed:
            self._restore_codex_text(key, (text,))
            return f"The message for {record.title} wasn't sent. It's in that chat's reply box. "
        self._send_codex_owned(record, text)
        return ""

    def _drop_waiting_relay(self, going_to=None):
        """Leaving a Codex chat before it was ready: its message stays as
        that chat's draft, and you hear that it wasn't sent. Loading the same
        chat again keeps it waiting. ``going_to`` None means the chat is being
        forgotten, with no reply box to keep it in: it's copied instead."""
        info = self._open
        if info is None or (going_to is not None and going_to.key == info.key):
            return
        text = self._relay_waiting.pop(info.key, None)
        if text is None:
            return
        if going_to is None:
            self._relay_kept(text, f"The message for {info.title} wasn't sent.")
            return
        self._restore_codex_text(info.key, (text,))
        self._feedback(f"The message for {info.title} wasn't sent. It's in that chat's reply box.")
